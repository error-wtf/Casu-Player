import AVFoundation
import Foundation

enum RecordingSplitMode: String, CaseIterable, Identifiable {
    case continuous, time, track, tags
    var id: String { rawValue }
    var label: String {
        switch self {
        case .continuous: return "Single recording"
        case .time: return "Split by time"
        case .track: return "Split by track"
        case .tags: return "Split by tags"
        }
    }
}

@MainActor
final class RecordingController: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var status = ""
    @Published var mode: RecordingSplitMode {
        didSet { UserDefaults.standard.set(mode.rawValue, forKey: "recordSplitMode") }
    }
    @Published var intervalMinutes: Int {
        didSet { UserDefaults.standard.set(intervalMinutes, forKey: "recordSplitMinutes") }
    }
    private var job: Task<Void, Never>?

    init() {
        mode = RecordingSplitMode(rawValue: UserDefaults.standard.string(forKey: "recordSplitMode") ?? "") ?? .continuous
        intervalMinutes = max(1, UserDefaults.standard.integer(forKey: "recordSplitMinutes"))
    }

    func toggle(_ occurrence: QueueOccurrence?) {
        if isRecording { stop(); return }
        guard let occurrence else { status = "Open playable media first."; return }
        isRecording = true
        status = "Recording…"
        export(occurrence)
    }

    func sourceChanged(_ occurrence: QueueOccurrence) {
        guard isRecording, mode == .track || mode == .tags else { return }
        job?.cancel()
        export(occurrence)
    }

    func stop() {
        job?.cancel(); job = nil; isRecording = false; status = "Recording stopped."
    }

    private func export(_ occurrence: QueueOccurrence) {
        let selectedMode = mode
        let minutes = intervalMinutes
        job = Task {
            do {
                let asset = AVURLAsset(url: occurrence.url)
                let duration = try await asset.load(.duration)
                let total = max(0, CMTimeGetSeconds(duration))
                let ranges = Self.segmentRanges(duration: total, mode: selectedMode,
                                                intervalMinutes: minutes)
                for (offset, range) in ranges.enumerated() {
                    try Task.checkCancellation()
                    let url = try Self.destination(title: occurrence.title, mode: selectedMode, part: offset + 1)
                    try await Self.export(asset: asset, range: CMTimeRange(
                        start: CMTime(seconds: range.0, preferredTimescale: 600),
                        duration: CMTime(seconds: range.1, preferredTimescale: 600)), to: url)
                }
                if !Task.isCancelled { status = "Saved \(ranges.count) recording file(s)." }
            } catch is CancellationError {
                // A track/tag boundary intentionally replaces the active export.
            } catch {
                status = "Recording failed: \(error.localizedDescription)"
                isRecording = false
            }
        }
    }

    nonisolated static func segmentRanges(duration: Double, mode: RecordingSplitMode,
                                           intervalMinutes: Int) -> [(Double, Double)] {
        let total = max(0.001, duration)
        guard mode == .time else { return [(0, total)] }
        let size = Double(max(1, intervalMinutes) * 60)
        var out: [(Double, Double)] = [], start = 0.0
        while start < total {
            out.append((start, min(size, total - start)))
            start += size
        }
        return out
    }

    private static func destination(title: String, mode: RecordingSplitMode, part: Int) throws -> URL {
        let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MPCASU Recordings", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let safe = title.replacingOccurrences(of: "[^A-Za-z0-9._-]+", with: "-", options: .regularExpression)
        let stamp = Int(Date().timeIntervalSince1970)
        let suffix = mode == .continuous ? "" : String(format: "-part%03d", part)
        return root.appendingPathComponent("\(stamp)-\(safe.isEmpty ? "recording" : safe)\(suffix).m4a")
    }

    private static func export(asset: AVAsset, range: CMTimeRange, to url: URL) async throws {
        guard let session = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetAppleM4A) else {
            throw CocoaError(.featureUnsupported)
        }
        session.outputURL = url
        session.outputFileType = .m4a
        session.timeRange = range
        await withCheckedContinuation { continuation in
            session.exportAsynchronously { continuation.resume() }
        }
        if session.status != .completed {
            throw session.error ?? CocoaError(.fileWriteUnknown)
        }
    }
}

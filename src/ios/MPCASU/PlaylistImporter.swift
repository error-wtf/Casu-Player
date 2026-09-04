import Foundation

struct PlaylistEntry: Equatable {
    let title: String
    let url: URL
}

enum PlaylistImporter {
    static let maximumEntries = 10_000

    static func load(_ source: URL) async throws -> [PlaylistEntry] {
        let data: Data
        if source.isFileURL { data = try Data(contentsOf: source) }
        else { (data, _) = try await URLSession.shared.data(from: source) }
        guard let text = String(data: data, encoding: .utf8) else { throw CocoaError(.fileReadInapplicableStringEncoding) }
        return try source.pathExtension.lowercased() == "pls"
            ? parsePLS(text, relativeTo: source)
            : parseM3U(text, relativeTo: source)
    }

    static func parseM3U(_ text: String, relativeTo source: URL) throws -> [PlaylistEntry] {
        var entries: [PlaylistEntry] = []
        var pendingTitle: String?
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if line.hasPrefix("#EXTINF:") {
                pendingTitle = line.split(separator: ",", maxSplits: 1).last.map(String.init)
            } else if !line.isEmpty && !line.hasPrefix("#") {
                guard entries.count < maximumEntries else { throw PlaylistError.entryLimitReached }
                let url = URL(string: line, relativeTo: source.deletingLastPathComponent())?.absoluteURL
                if let url { entries.append(PlaylistEntry(title: pendingTitle ?? url.deletingPathExtension().lastPathComponent, url: url)) }
                pendingTitle = nil
            }
        }
        return entries
    }

    static func parsePLS(_ text: String, relativeTo source: URL) throws -> [PlaylistEntry] {
        var files: [Int: String] = [:], titles: [Int: String] = [:]
        for line in text.components(separatedBy: .newlines) {
            let parts = line.split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            let key = parts[0].lowercased()
            if key.hasPrefix("file"), let index = Int(key.dropFirst(4)) { files[index] = parts[1] }
            if key.hasPrefix("title"), let index = Int(key.dropFirst(5)) { titles[index] = parts[1] }
        }
        guard files.count <= maximumEntries else { throw PlaylistError.entryLimitReached }
        return files.keys.sorted().compactMap { index in
            guard let value = files[index], let url = URL(string: value, relativeTo: source.deletingLastPathComponent())?.absoluteURL else { return nil }
            return PlaylistEntry(title: titles[index] ?? url.deletingPathExtension().lastPathComponent, url: url)
        }
    }
}

enum PlaylistError: LocalizedError {
    case entryLimitReached
    var errorDescription: String? { "Playlist exceeds the explicit 10,000-entry safety ceiling." }
}

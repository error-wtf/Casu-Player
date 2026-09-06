import AVFoundation
import MediaPlayer
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class PlayerModel: ObservableObject {
    @Published private(set) var queue = QueueSnapshot()
    @Published private(set) var currentArtwork: UIImage?
    @Published private(set) var currentHasVideo = true
    @Published private(set) var isPlaying = false
    @Published var errorMessage: String?
    @Published private(set) var position: Double = 0
    @Published private(set) var duration: Double = 0
    @Published var volume: Float = UserDefaults.standard.object(forKey: "playbackVolume") as? Float ?? 1 {
        didSet { player.volume = volume; UserDefaults.standard.set(volume, forKey: "playbackVolume") }
    }
    @Published var shuffle = UserDefaults.standard.bool(forKey: "queueShuffle") {
        didSet { UserDefaults.standard.set(shuffle, forKey: "queueShuffle") }
    }
    @Published var repeatMode = UserDefaults.standard.string(forKey: "queueRepeat") ?? "off" {
        didSet { UserDefaults.standard.set(repeatMode, forKey: "queueRepeat") }
    }
    @Published var playbackRate: Float = UserDefaults.standard.object(forKey: "playbackRate") as? Float ?? 1 {
        didSet {
            player.rate = isPlaying ? playbackRate : 0
            UserDefaults.standard.set(playbackRate, forKey: "playbackRate")
            refreshNowPlaying()
        }
    }
    let player = AVPlayer()
    private let storageURL: URL
    private var timeObserver: Any?

    init(storageURL: URL? = nil) {
        self.storageURL = storageURL ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MPCASU/queue-v1.json")
        restore()
        player.volume = volume
        configureRemoteCommands()
        NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime, object: nil, queue: .main) {
            [weak self] _ in Task { @MainActor in self?.advance() }
        }
        NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) {
            [weak self] notification in Task { @MainActor in self?.handleInterruption(notification) }
        }
        NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) {
            [weak self] notification in Task { @MainActor in self?.handleRouteChange(notification) }
        }
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.25, preferredTimescale: 600), queue: .main) {
            [weak self] time in Task { @MainActor in
                self?.position = max(0, time.seconds.isFinite ? time.seconds : 0)
                let value = self?.player.currentItem?.duration.seconds ?? 0
                self?.duration = value.isFinite ? max(0, value) : 0
                self?.refreshNowPlaying()
            }
        }
    }

    private func accessibleCopy(_ url: URL) throws -> URL {
        guard url.isFileURL else { return url }
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        if url.standardizedFileURL.path.hasPrefix(documents.standardizedFileURL.path + "/") { return url }
        let folder = documents.appendingPathComponent("ImportedMedia/" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let destination = folder.appendingPathComponent(url.lastPathComponent)
        try FileManager.default.copyItem(at: url, to: destination)
        return destination
    }

    func importURLs(_ urls: [URL]) {
        for url in urls {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            do {
                let readable = try accessibleCopy(url)
                append(title: url.deletingPathExtension().lastPathComponent, url: readable,
                       kind: url.isFileURL ? .local : .network)
            } catch { errorMessage = "Media import failed: \(error.localizedDescription)" }
        }
    }

    func importDocuments(_ urls: [URL]) async {
        for url in urls {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            if PlaylistImporter.extensions.contains(url.pathExtension.lowercased()) {
                await importPlaylist(url)
            } else {
                importURLs([url])
            }
        }
    }

    func append(title: String, url: URL, kind: MediaIdentity.Kind = .network, play: Bool = false, playlistID: String? = nil, playlistTitle: String? = nil) {
        let identity = MediaIdentity(kind: kind, canonicalKey: url.absoluteString)
        let occurrence = QueueOccurrence(media: identity, title: title, url: url, playlistID: playlistID, playlistTitle: playlistTitle)
        queue.occurrences.append(occurrence)
        Task { await hydrate(occurrence) }
        if queue.currentOccurrenceID == nil || play { select(occurrence) }
        persist()
        if play { player.play(); isPlaying = true }
    }

    func importPlaylist(_ url: URL) async {
        do {
            let entries = try await PlaylistImporter.load(url)
            let groupID = UUID().uuidString
            let groupTitle = url.deletingPathExtension().lastPathComponent
            for entry in entries {
                let readable = try accessibleCopy(entry.url)
                append(title: entry.title, url: readable, kind: readable.isFileURL ? .local : .network,
                       playlistID: groupID, playlistTitle: groupTitle)
            }
        } catch { errorMessage = "Playlist import failed: \(error.localizedDescription)" }
    }

    func exportPlaylist(format: PlaylistExportFormat = .m3u) throws -> URL {
        let target = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MPCASU-Queue.\(format.rawValue)")
        try format.render(queue.occurrences).write(to: target, atomically: true, encoding: .utf8)
        return target
    }

    func moveGroups(from source: IndexSet, to destination: Int) {
        var groups = QueueDisplayGroup.make(queue.occurrences)
        groups.move(fromOffsets: source, toOffset: destination)
        queue.occurrences = groups.flatMap(\.items)
        persist()
    }

    func moveWithinGroup(_ group: QueueDisplayGroup, from source: IndexSet, to destination: Int) {
        var children = group.items
        children.move(fromOffsets: source, toOffset: destination)
        guard let start = queue.occurrences.firstIndex(where: { $0.id == group.id }) else { return }
        queue.occurrences.replaceSubrange(start..<(start + children.count), with: children)
        persist()
    }

    private var providerGeneration = 0

    func select(_ occurrence: QueueOccurrence) {
        providerGeneration += 1
        let generation = providerGeneration
        queue.currentOccurrenceID = occurrence.id
        currentArtwork = nil
        currentHasVideo = true
        Task { await hydrate(occurrence, generation: generation) }
        if let videoID = YouTubeClient.videoID(occurrence.url) {
            player.replaceCurrentItem(with: nil)
            Task {
                do {
                    let resolved = try await YouTubeClient.resolve(videoID)
                    guard providerGeneration == generation, queue.currentOccurrenceID == occurrence.id else { return }
                    player.replaceCurrentItem(with: AVPlayerItem(url: resolved))
                    if isPlaying { player.playImmediately(atRate: playbackRate) }
                } catch {
                    guard providerGeneration == generation else { return }
                    isPlaying = false
                    errorMessage = "YouTube: \(error.localizedDescription)"
                }
            }
        } else { player.replaceCurrentItem(with: AVPlayerItem(url: occurrence.url)) }
        updateNowPlaying(occurrence)
        persist()
    }

    private func hydrate(_ occurrence: QueueOccurrence, generation: Int? = nil) async {
        guard let metadata = await MediaMetadata.load(occurrence.url),
              let index = queue.occurrences.firstIndex(where: { $0.id == occurrence.id }) else { return }
        if let title = metadata.title { queue.occurrences[index].title = title }
        if let artist = metadata.artist { queue.occurrences[index].artist = artist }
        if let album = metadata.album { queue.occurrences[index].album = album }
        persist()
        if queue.currentOccurrenceID == occurrence.id && (generation == nil || generation == providerGeneration) {
            currentArtwork = metadata.artwork ?? currentArtwork
            currentHasVideo = metadata.hasVideo
            refreshNowPlaying()
        }
    }

    func importLibraryTrack(_ track: LibraryTrack, selectItem: Bool = true) {
        guard let url = track.assetURL else {
            errorMessage = "This protected media item cannot be played."
            return
        }
        append(title: track.title, url: url, kind: .local)
        guard let index = queue.occurrences.indices.last else { return }
        queue.occurrences[index].artist = track.artist
        queue.occurrences[index].album = track.album
        if selectItem {
            select(queue.occurrences[index])
            currentArtwork = track.artworkData.flatMap(UIImage.init(data:))
            currentHasVideo = false
            refreshNowPlaying()
        }
        persist()
    }

    func togglePlayback() {
        guard let current = current else { return }
        if player.currentItem == nil { select(current) }
        if isPlaying { player.pause() } else { player.playImmediately(atRate: playbackRate) }
        isPlaying.toggle()
        refreshNowPlaying()
    }

    func stop() {
        providerGeneration += 1
        player.pause()
        player.seek(to: .zero)
        isPlaying = false
        position = 0
        refreshNowPlaying()
    }

    func remove(_ occurrence: QueueOccurrence) {
        let wasCurrent = occurrence.id == queue.currentOccurrenceID
        queue.occurrences.removeAll { $0.id == occurrence.id }
        if wasCurrent {
            providerGeneration += 1
            player.pause(); player.replaceCurrentItem(with: nil); isPlaying = false
            queue.currentOccurrenceID = queue.occurrences.first?.id
        }
        persist()
    }

    func move(from source: IndexSet, to destination: Int) {
        queue.occurrences.move(fromOffsets: source, toOffset: destination)
        persist()
    }

    func advance() {
        guard let id = queue.currentOccurrenceID,
              let index = queue.occurrences.firstIndex(where: { $0.id == id }),
              !queue.occurrences.isEmpty else { isPlaying = false; return }
        if repeatMode == "one" { player.seek(to: .zero); player.playImmediately(atRate: playbackRate); return }
        let next: Int
        if shuffle { next = Int.random(in: 0..<queue.occurrences.count) }
        else if index + 1 < queue.occurrences.count { next = index + 1 }
        else if repeatMode == "all" { next = 0 }
        else { isPlaying = false; return }
        select(queue.occurrences[next]); player.playImmediately(atRate: playbackRate); isPlaying = true
        refreshNowPlaying()
    }

    func previous() {
        guard let id = queue.currentOccurrenceID,
              let index = queue.occurrences.firstIndex(where: { $0.id == id }),
              !queue.occurrences.isEmpty else { return }
        let previous = index > 0 ? index - 1 : (repeatMode == "all" ? queue.occurrences.count - 1 : 0)
        select(queue.occurrences[previous]); player.playImmediately(atRate: playbackRate); isPlaying = true
        refreshNowPlaying()
    }

    func seek(to seconds: Double) {
        player.seek(to: CMTime(seconds: max(0, seconds), preferredTimescale: 600))
    }

    func cycleRepeat() {
        let modes = ["off", "all", "one"]
        repeatMode = modes[((modes.firstIndex(of: repeatMode) ?? 0) + 1) % modes.count]
    }

    var current: QueueOccurrence? {
        queue.occurrences.first { $0.id == queue.currentOccurrenceID }
    }

    private func persist() {
        do {
            let data = try QueuePersistence.encode(queue)
            try FileManager.default.createDirectory(at: storageURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: storageURL, options: .atomic)
        } catch { errorMessage = "Queue could not be saved." }
    }

    private func restore() {
        guard let data = try? Data(contentsOf: storageURL) else { return }
        do { queue = try QueuePersistence.decode(data) }
        catch { queue = QueueSnapshot(); errorMessage = "Saved queue was invalid and was not restored." }
    }

    private func updateNowPlaying(_ occurrence: QueueOccurrence) {
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: occurrence.title,
            MPMediaItemPropertyArtist: occurrence.artist ?? "",
            MPMediaItemPropertyAlbumTitle: occurrence.album ?? "",
            MPNowPlayingInfoPropertyElapsedPlaybackTime: position,
            MPMediaItemPropertyPlaybackDuration: duration,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? playbackRate : 0,
        ]
        if let artwork = currentArtwork {
            info[MPMediaItemPropertyArtwork] = MPMediaItemArtwork(boundsSize: artwork.size) { _ in artwork }
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func refreshNowPlaying() {
        if let current { updateNowPlaying(current) }
        MPNowPlayingInfoCenter.default().playbackState = isPlaying ? .playing : .paused
    }

    private func configureRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.addTarget { [weak self] _ in Task { @MainActor in self?.togglePlayback() }; return .success }
        center.pauseCommand.addTarget { [weak self] _ in Task { @MainActor in self?.togglePlayback() }; return .success }
        center.nextTrackCommand.addTarget { [weak self] _ in Task { @MainActor in self?.advance() }; return .success }
        center.previousTrackCommand.addTarget { [weak self] _ in Task { @MainActor in self?.previous() }; return .success }
        center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            Task { @MainActor in self?.seek(to: event.positionTime) }
            return .success
        }
    }

    private func handleInterruption(_ notification: Notification) {
        guard let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        if type == .began { player.pause(); isPlaying = false }
        else if let optionsRaw = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt,
                AVAudioSession.InterruptionOptions(rawValue: optionsRaw).contains(.shouldResume) {
            player.playImmediately(atRate: playbackRate); isPlaying = true
            refreshNowPlaying()
        }
    }

    private func handleRouteChange(_ notification: Notification) {
        guard let raw = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
              AVAudioSession.RouteChangeReason(rawValue: raw) == .oldDeviceUnavailable else { return }
        player.pause(); isPlaying = false
    }
}

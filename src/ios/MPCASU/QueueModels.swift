import Foundation

struct MediaIdentity: Codable, Hashable {
    enum Kind: String, Codable { case local, network, provider }
    let mediaID: String
    let kind: Kind
    let canonicalKey: String

    init(mediaID: String = "med_" + UUID().uuidString.lowercased(), kind: Kind, canonicalKey: String) {
        precondition(!canonicalKey.isEmpty)
        self.mediaID = mediaID; self.kind = kind; self.canonicalKey = canonicalKey
    }
}

struct QueueOccurrence: Codable, Identifiable, Equatable {
    let id: UUID
    let media: MediaIdentity
    var title: String
    var artist: String?
    var album: String?
    let url: URL
    let playlistID: String?
    let playlistTitle: String?

    init(id: UUID = UUID(), media: MediaIdentity, title: String, url: URL, playlistID: String? = nil, playlistTitle: String? = nil) {
        self.id = id; self.media = media; self.title = title; self.url = url
        self.playlistID = playlistID; self.playlistTitle = playlistTitle
    }
}

struct QueueSnapshot: Codable, Equatable {
    static let schemaVersion = 1
    let schemaVersion: Int
    var occurrences: [QueueOccurrence]
    var currentOccurrenceID: UUID?

    init(occurrences: [QueueOccurrence] = [], currentOccurrenceID: UUID? = nil) {
        self.schemaVersion = Self.schemaVersion
        self.occurrences = occurrences
        self.currentOccurrenceID = currentOccurrenceID
    }

    func validated() throws -> QueueSnapshot {
        guard schemaVersion == Self.schemaVersion else { throw QueueError.unsupportedVersion }
        guard Set(occurrences.map(\.id)).count == occurrences.count else { throw QueueError.duplicateOccurrence }
        guard currentOccurrenceID == nil || occurrences.contains(where: { $0.id == currentOccurrenceID }) else {
            throw QueueError.invalidCurrentOccurrence
        }
        return self
    }
}

enum QueueError: Error { case unsupportedVersion, duplicateOccurrence, invalidCurrentOccurrence }

enum QueuePersistence {
    static func encode(_ snapshot: QueueSnapshot) throws -> Data {
        try JSONEncoder().encode(snapshot.validated())
    }

    static func decode(_ data: Data) throws -> QueueSnapshot {
        try JSONDecoder().decode(QueueSnapshot.self, from: data).validated()
    }
}


struct QueueDisplayGroup: Identifiable {
    let id: UUID
    let title: String?
    var items: [QueueOccurrence]

    static func make(_ occurrences: [QueueOccurrence]) -> [QueueDisplayGroup] {
        var groups: [QueueDisplayGroup] = []
        for item in occurrences {
            if let playlistID = item.playlistID,
               let last = groups.last, last.items.last?.playlistID == playlistID {
                groups[groups.count - 1].items.append(item)
            } else {
                groups.append(QueueDisplayGroup(id: item.id,
                    title: item.playlistID == nil ? nil : item.playlistTitle ?? "YouTube playlist",
                    items: [item]))
            }
        }
        return groups
    }
}

enum PlaylistExportFormat: String, CaseIterable, Identifiable {
    case m3u, m3u8, pls
    var id: String { rawValue }

    func render(_ items: [QueueOccurrence]) -> String {
        func title(_ item: QueueOccurrence) -> String {
            item.title.replacingOccurrences(of: "\r", with: " ").replacingOccurrences(of: "\n", with: " ")
        }
        if self == .pls {
            var lines = ["[playlist]", "NumberOfEntries=\(items.count)"]
            for (index, item) in items.enumerated() {
                lines += ["File\(index + 1)=\(item.url.absoluteString)", "Title\(index + 1)=\(title(item))", "Length\(index + 1)=-1"]
            }
            return (lines + ["Version=2"]).joined(separator: "\n") + "\n"
        }
        return (["#EXTM3U"] + items.flatMap { ["#EXTINF:-1,\(title($0))", $0.url.absoluteString] }).joined(separator: "\n") + "\n"
    }
}

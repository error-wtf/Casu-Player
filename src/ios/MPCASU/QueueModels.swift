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
    let title: String
    let url: URL

    init(id: UUID = UUID(), media: MediaIdentity, title: String, url: URL) {
        self.id = id; self.media = media; self.title = title; self.url = url
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

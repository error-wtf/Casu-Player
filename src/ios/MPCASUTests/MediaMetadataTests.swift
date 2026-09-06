import XCTest
@testable import MPCASU

@MainActor
final class MediaMetadataTests: XCTestCase {
    func testTaggedMP3AndCover() async throws {
        let bundle = Bundle(for: Self.self)
        let url = try XCTUnwrap(bundle.url(forResource: "tagged-cover", withExtension: "mp3"))
        let result = await MediaMetadata.load(url)
        let metadata = try XCTUnwrap(result)
        XCTAssertEqual(metadata.title, "Überall – Test")
        XCTAssertEqual(metadata.artist, "Casu Artist")
        XCTAssertEqual(metadata.album, "Cover Album")
        XCTAssertNotNil(metadata.artwork)
        XCTAssertLessThanOrEqual(metadata.artwork?.size.width ?? 9999, 640)
        XCTAssertFalse(metadata.hasVideo)
    }
    func testQueueTagsPersistWithoutBreakingOldEntries() throws {
        var entry = QueueOccurrence(media: MediaIdentity(kind: .local, canonicalKey: "file:///track.mp3"), title: "Track", url: URL(fileURLWithPath: "/track.mp3"))
        entry.artist = "Artist"; entry.album = "Album"
        let decoded = try QueuePersistence.decode(QueuePersistence.encode(QueueSnapshot(occurrences: [entry])))
        XCTAssertEqual(decoded.occurrences.first?.artist, "Artist")
        XCTAssertEqual(decoded.occurrences.first?.album, "Album")
    }
}

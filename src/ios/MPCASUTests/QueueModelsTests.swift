import XCTest
@testable import MPCASU

final class QueueModelsTests: XCTestCase {
    func testDuplicateMediaRemainDistinctOccurrencesAndRoundTrip() throws {
        let url = URL(string: "https://example.test/media.mp3")!
        let media = MediaIdentity(mediaID: "med_0123456789abcdef", kind: .network, canonicalKey: url.absoluteString)
        let first = QueueOccurrence(media: media, title: "First", url: url)
        let second = QueueOccurrence(media: media, title: "Second", url: url)
        let state = QueueSnapshot(occurrences: [first, second], currentOccurrenceID: second.id)
        let restored = try QueuePersistence.decode(QueuePersistence.encode(state))
        XCTAssertEqual(restored, state)
        XCTAssertNotEqual(restored.occurrences[0].id, restored.occurrences[1].id)
        XCTAssertEqual(restored.currentOccurrenceID, second.id)
    }

    func testCorruptOrInconsistentStateIsRejected() throws {
        XCTAssertThrowsError(try QueuePersistence.decode(Data("{}".utf8)))
        let url = URL(string: "https://example.test/media.mp3")!
        let media = MediaIdentity(kind: .network, canonicalKey: url.absoluteString)
        let item = QueueOccurrence(media: media, title: "Item", url: url)
        let invalid = QueueSnapshot(occurrences: [item], currentOccurrenceID: UUID())
        XCTAssertThrowsError(try QueuePersistence.encode(invalid))
    }

    func testLibraryGroupsAreRealSortedSelections() {
        let tracks = [
            LibraryTrack(id: 1, title: "Zulu", artist: "Beta", album: "Second", genre: "Rock", trackNumber: 2, assetURL: nil),
            LibraryTrack(id: 2, title: "Alpha", artist: "alpha", album: "First", genre: "Jazz", trackNumber: 1, assetURL: nil),
            LibraryTrack(id: 3, title: "Bravo", artist: "Beta", album: "Second", genre: "Rock", trackNumber: 1, assetURL: nil),
        ]
        XCTAssertEqual(MediaLibraryModel.groups(in: tracks, by: .artists), ["alpha", "Beta"])
        XCTAssertEqual(MediaLibraryModel.tracks(in: tracks, section: .artists, group: "Beta").map(\.title),
                       ["Bravo", "Zulu"])
        XCTAssertEqual(MediaLibraryModel.groups(in: tracks, by: .genres), ["Jazz", "Rock"])
        XCTAssertEqual(MediaLibraryModel.groups(in: tracks, by: .favorites), [])
    }

    func testRecordingSplitPolicyCreatesRealTimeSegments() {
        let ranges = RecordingController.segmentRanges(duration: 125, mode: .time,
                                                        intervalMinutes: 1)
        XCTAssertEqual(ranges.count, 3)
        XCTAssertEqual(ranges[0].0, 0)
        XCTAssertEqual(ranges[0].1, 60)
        XCTAssertEqual(ranges[2].1, 5)
        XCTAssertEqual(RecordingController.segmentRanges(
            duration: 125, mode: .continuous, intervalMinutes: 1).count, 1)
    }

    func testPlaylistImporterKeepsAllEntriesAndRelativeURLs() throws {
        let text = (0..<250).map { "#EXTINF:-1,Track \($0)\ntrack-\($0).mp3" }.joined(separator: "\n")
        let entries = try PlaylistImporter.parseM3U(text, relativeTo: URL(fileURLWithPath: "/tmp/list.m3u"))
        XCTAssertEqual(entries.count, 250)
        XCTAssertEqual(entries.first?.title, "Track 0")
        XCTAssertEqual(entries.last?.url.lastPathComponent, "track-249.mp3")
    }

    func testPlaylistImporterMakesSafetyCeilingExplicit() {
        let text = (0...PlaylistImporter.maximumEntries).map { "https://example.test/\($0).mp3" }.joined(separator: "\n")
        XCTAssertThrowsError(try PlaylistImporter.parseM3U(text, relativeTo: URL(string: "https://example.test/list.m3u")!))
    }

    func testPLSImporterKeepsOrderTitlesAndRelativeURLs() throws {
        let text = """
        [playlist]
        File2=second.mp3
        Title2=Second
        File1=https://example.test/first.mp3
        Title1=First
        NumberOfEntries=2
        """
        let entries = try PlaylistImporter.parsePLS(text, relativeTo: URL(string: "https://example.test/lists/list.pls")!)
        XCTAssertEqual(entries.map(\.title), ["First", "Second"])
        XCTAssertEqual(entries[1].url.absoluteString, "https://example.test/lists/second.mp3")
    }
}

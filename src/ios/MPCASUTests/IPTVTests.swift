import XCTest
@testable import MPCASU

@MainActor
final class IPTVTests: XCTestCase {
    func testImportGroupsSearchFavoritesAndRestore() async throws {
        let suite = "IPTVTests." + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m3u")
        defer { try? FileManager.default.removeItem(at: url) }
        try "#EXTM3U\n#EXTINF:-1 group-title=\"News\",World News\nhttps://example.org/live.m3u8\n#EXTINF:-1 group-title=\"Sport\",Match\nhttps://example.org/sport.m3u8\n".write(to: url, atomically: true, encoding: .utf8)
        let model = IPTVModel(defaults: defaults)
        await model.load(url)
        XCTAssertNil(model.error)
        XCTAssertEqual(model.channels.count, 2)
        model.group = "News"
        XCTAssertEqual(model.visible.map(\.title), ["World News"])
        model.search = "no match"
        XCTAssertTrue(model.visible.isEmpty)
        model.search = ""; model.group = ""
        model.toggleFavorite(model.channels[1]); model.favoritesOnly = true
        XCTAssertEqual(model.visible.map(\.title), ["Match"])
        let restored = IPTVModel(defaults: defaults)
        XCTAssertEqual(restored.channels, model.channels)
        XCTAssertEqual(restored.favorites, model.favorites)
    }
}

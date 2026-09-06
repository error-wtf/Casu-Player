import Foundation

enum YouTubeSearchKind: String, CaseIterable, Identifiable {
    case videos = "Videos"
    case playlists = "Playlists"
    var id: String { rawValue }
}

struct YouTubeResult: Identifiable, Equatable {
    enum Kind { case video, playlist }
    let id: String
    let title: String
    let subtitle: String
    let kind: Kind
}

@MainActor
final class YouTubeModel: ObservableObject {
    @Published var query = ""
    @Published var kind: YouTubeSearchKind = .videos
    @Published private(set) var results: [YouTubeResult] = []
    @Published private(set) var isLoading = false
    @Published var error: String?

    func search() {
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return }
        isLoading = true; error = nil
        Task {
            do { results = try await YouTubeClient.search(term, kind: kind) }
            catch { self.error = error.localizedDescription; results = [] }
            isLoading = false
        }
    }

    func importResult(_ result: YouTubeResult, into player: PlayerModel) {
        isLoading = true; error = nil
        Task {
            do {
                if result.kind == .playlist {
                    let videos = try await YouTubeClient.playlist(result.id)
                    for video in videos {
                        let url = URL(string: "https://www.youtube.com/watch?v=" + video.id)!
                        player.append(title: video.title, url: url, playlistID: result.id, playlistTitle: result.title)
                    }
                } else {
                    let url = URL(string: "https://www.youtube.com/watch?v=" + result.id)!
                    player.append(title: result.title, url: url, play: true)
                }
            } catch { self.error = error.localizedDescription }
            isLoading = false
        }
    }
}

enum YouTubeClient {
    static func videoID(_ url: URL) -> String? {
        let host = url.host?.lowercased() ?? ""
        let candidate: String?
        if host == "youtu.be" { candidate = url.pathComponents.dropFirst().first }
        else if host == "youtube.com" || host.hasSuffix(".youtube.com") {
            if url.path.hasPrefix("/shorts/") || url.path.hasPrefix("/embed/") { candidate = url.lastPathComponent }
            else { candidate = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "v" })?.value }
        } else { return nil }
        guard let id = candidate, id.range(of: "^[A-Za-z0-9_-]{11}$", options: .regularExpression) != nil else { return nil }
        return id
    }

    private static let endpoint = URL(string: "https://www.youtube.com/youtubei/v1/")!
    private static let context: [String: Any] = ["client": ["clientName": "ANDROID_VR", "clientVersion": "1.60.19", "androidSdkVersion": 32]]

    static func search(_ query: String, kind: YouTubeSearchKind) async throws -> [YouTubeResult] {
        let filter = kind == .playlists ? "EgIQAw%3D%3D" : "EgIQAQ%3D%3D"
        let root = try await request("search", body: ["context": context, "query": query, "params": filter])
        var output: [YouTubeResult] = []
        walk(root) { object in
            if kind == .videos, let renderer = object["videoRenderer"] as? [String: Any],
               let id = renderer["videoId"] as? String {
                output.append(YouTubeResult(id: id, title: text(renderer["title"]) ?? id,
                                            subtitle: text(renderer["ownerText"]) ?? "YouTube", kind: .video))
            } else if kind == .playlists, let renderer = object["playlistRenderer"] as? [String: Any],
                      let id = renderer["playlistId"] as? String {
                output.append(YouTubeResult(id: id, title: text(renderer["title"]) ?? id,
                                            subtitle: text(renderer["shortBylineText"]) ?? "YouTube playlist", kind: .playlist))
            }
        }
        return unique(output).prefix(50).map { $0 }
    }

    static func playlist(_ id: String) async throws -> [YouTubeResult] {
        var root = try await request("browse", body: ["context": context, "browseId": "VL" + id])
        var output: [YouTubeResult] = []
        var seenTokens = Set<String>()
        while output.count < PlaylistImporter.maximumEntries {
            var token: String?
            walk(root) { object in
                let renderer = (object["playlistVideoRenderer"] ?? object["videoRenderer"]) as? [String: Any]
                if let renderer, let videoID = renderer["videoId"] as? String {
                    output.append(YouTubeResult(id: videoID, title: text(renderer["title"]) ?? videoID,
                                                subtitle: text(renderer["shortBylineText"]) ?? "YouTube", kind: .video))
                }
                if token == nil, let command = object["continuationCommand"] as? [String: Any] {
                    token = command["token"] as? String
                }
            }
            guard let next = token, seenTokens.insert(next).inserted else { break }
            root = try await request("browse", body: ["context": context, "continuation": next])
        }
        let result = unique(output)
        guard result.count < PlaylistImporter.maximumEntries else { throw PlaylistError.entryLimitReached }
        return result
    }

    static func resolve(_ videoID: String) async throws -> URL {
        let root = try await request("player", body: ["context": context, "videoId": videoID])
        guard let streaming = root["streamingData"] as? [String: Any] else { throw YouTubeError.unplayable }
        let formats = ((streaming["formats"] as? [[String: Any]]) ?? []) + ((streaming["adaptiveFormats"] as? [[String: Any]]) ?? [])
        let preferred = formats.first { ($0["mimeType"] as? String)?.hasPrefix("video/mp4") == true && $0["url"] != nil }
            ?? formats.first { ($0["mimeType"] as? String)?.hasPrefix("audio/") == true && $0["url"] != nil }
        guard let value = preferred?["url"] as? String, let url = URL(string: value) else { throw YouTubeError.unplayable }
        return url
    }

    private static func request(_ method: String, body: [String: Any]) async throws -> [String: Any] {
        var components = URLComponents(url: endpoint.appendingPathComponent(method), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "prettyPrint", value: "false")]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("com.google.android.youtube/19.29.37", forHTTPHeaderField: "User-Agent")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw YouTubeError.network }
        return value
    }

    private static func walk(_ value: Any, visit: ([String: Any]) -> Void) {
        if let object = value as? [String: Any] { visit(object); object.values.forEach { walk($0, visit: visit) } }
        else if let array = value as? [Any] { array.forEach { walk($0, visit: visit) } }
    }

    private static func text(_ value: Any?) -> String? {
        guard let object = value as? [String: Any] else { return nil }
        if let simple = object["simpleText"] as? String { return simple }
        return (object["runs"] as? [[String: Any]])?.compactMap { $0["text"] as? String }.joined()
    }

    private static func unique(_ values: [YouTubeResult]) -> [YouTubeResult] {
        var seen = Set<String>()
        return values.filter { seen.insert("\($0.kind)-\($0.id)").inserted }
    }
}

enum YouTubeError: LocalizedError {
    case network, unplayable
    var errorDescription: String? { self == .network ? "YouTube request failed." : "YouTube item has no directly playable format." }
}

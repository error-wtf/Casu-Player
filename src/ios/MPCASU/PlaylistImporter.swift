import Foundation

struct PlaylistEntry: Equatable {
    let title: String
    let url: URL
}

enum PlaylistImporter {
    static let maximumEntries = 10_000
    static let extensions: Set<String> = ["m3u", "m3u8", "pls", "xspf", "jspf", "json", "wpl", "asx", "wmx", "wvx", "ram", "rmp", "cue"]

    static func load(_ source: URL) async throws -> [PlaylistEntry] {
        let data: Data
        if source.isFileURL { data = try Data(contentsOf: source) }
        else { (data, _) = try await URLSession.shared.data(from: source) }
        guard let text = String(data: data, encoding: .utf8) else { throw CocoaError(.fileReadInapplicableStringEncoding) }
        switch source.pathExtension.lowercased() {
        case "pls": return try parsePLS(text, relativeTo: source)
        case "json", "jspf":
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            let playlist = object["playlist"] as? [String: Any] ?? object
            let items = playlist["items"] as? [Any] ?? playlist["track"] as? [Any] ?? []
            guard items.count <= maximumEntries else { throw PlaylistError.entryLimitReached }
            return items.compactMap { item in
                let fields = item as? [String: Any] ?? [:]
                guard let value = item as? String ?? fields["sourceUrl"] as? String ?? fields["url"] as? String
                    ?? fields["path"] as? String ?? (fields["location"] as? [String])?.first,
                    let url = URL(string: value, relativeTo: source.deletingLastPathComponent())?.absoluteURL else { return nil }
                return PlaylistEntry(title: fields["title"] as? String ?? url.deletingPathExtension().lastPathComponent, url: url)
            }
        case "xspf", "wpl", "asx", "wmx", "wvx":
            let delegate = PlaylistXMLReader(source: source)
            let parser = XMLParser(data: data)
            parser.shouldResolveExternalEntities = false
            parser.delegate = delegate
            guard parser.parse() else { throw parser.parserError ?? CocoaError(.fileReadCorruptFile) }
            guard !delegate.exceededLimit else { throw PlaylistError.entryLimitReached }
            return delegate.entries
        case "cue":
            let pattern = try NSRegularExpression(pattern: #"(?im)^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+\S+\s*$"#)
            let input = text as NSString
            let lines = pattern.matches(in: text, range: NSRange(location: 0, length: input.length)).map { match in
                input.substring(with: match.range(at: match.range(at: 1).location == NSNotFound ? 2 : 1))
            }
            return try parseM3U(lines.joined(separator: "\n"), relativeTo: source)
        default: return try parseM3U(text, relativeTo: source)
        }
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

private final class PlaylistXMLReader: NSObject, XMLParserDelegate {
    let source: URL
    var entries: [PlaylistEntry] = []
    var exceededLimit = false
    private var value = ""
    private var location: String?
    private var title: String?
    init(source: URL) { self.source = source }
    private func append(_ raw: String) {
        guard entries.count < PlaylistImporter.maximumEntries else { exceededLimit = true; return }
        guard let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines),
                            relativeTo: source.deletingLastPathComponent())?.absoluteURL else { return }
        entries.append(PlaylistEntry(title: title ?? url.deletingPathExtension().lastPathComponent, url: url))
    }
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName qName: String?, attributes: [String: String]) {
        value = ""
        let name = elementName.lowercased().split(separator: ":").last.map(String.init) ?? ""
        if name == "track" || name == "entry" { location = nil; title = nil }
        if name == "media", let src = attributes["src"] { append(src) }
        if name == "ref", let href = attributes["href"] ?? attributes["HREF"] { location = href }
    }
    func parser(_ parser: XMLParser, foundCharacters string: String) { value += string }
    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName qName: String?) {
        let name = elementName.lowercased().split(separator: ":").last.map(String.init) ?? ""
        if name == "location" { location = value }
        if name == "title" { title = value }
        if name == "track" || name == "entry", let location { append(location) }
        value = ""
    }
}

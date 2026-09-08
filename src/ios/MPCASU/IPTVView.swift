import SwiftUI
import AVKit
import UniformTypeIdentifiers

struct IPTVChannel: Codable, Identifiable, Equatable {
    var id: String { url.absoluteString }
    let title: String
    let url: URL
    let group: String
}

@MainActor
final class IPTVModel: ObservableObject {
    @Published private(set) var channels: [IPTVChannel] = []
    @Published var group = ""
    @Published var search = ""
    @Published var favoritesOnly = false
    @Published private(set) var favorites: Set<String>
    @Published private(set) var loading = false
    @Published var error: String?
    private let defaults: UserDefaults
    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        favorites = Set(defaults.stringArray(forKey: "iptvFavorites") ?? [])
        if let data = defaults.data(forKey: "iptvChannels"),
           let saved = try? JSONDecoder().decode([IPTVChannel].self, from: data) { channels = saved }
    }
    var groups: [String] { Set(channels.map(\.group)).sorted() }
    var visible: [IPTVChannel] {
        channels.filter { (group.isEmpty || $0.group == group)
            && (!favoritesOnly || favorites.contains($0.id))
            && (search.isEmpty || ($0.title + " " + $0.group).localizedCaseInsensitiveContains(search)) }
    }
    func toggleFavorite(_ channel: IPTVChannel) {
        if !favorites.insert(channel.id).inserted { favorites.remove(channel.id) }
        defaults.set(Array(favorites), forKey: "iptvFavorites")
    }
    func load(_ url: URL) async {
        guard !loading else { return }
        loading = true; error = nil
        defer { loading = false }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let entries = try await PlaylistImporter.load(url)
            var seen = Set<URL>()
            let loaded = entries.filter { seen.insert($0.url).inserted }.map {
                IPTVChannel(title: $0.title, url: $0.url, group: $0.group.isEmpty ? "Ungrouped" : $0.group)
            }
            guard !loaded.isEmpty else { error = "No channels found in this playlist."; return }
            channels = loaded
            if !groups.contains(group) { group = "" }
            defaults.set(try JSONEncoder().encode(loaded), forKey: "iptvChannels")
        } catch { self.error = "Playlist import failed: \(error.localizedDescription)" }
    }
}

struct IPTVView: View {
    @EnvironmentObject private var player: PlayerModel
    @StateObject private var catalog = IPTVModel()
    @State private var importing = false
    @State private var enteringURL = false
    @State private var source = ""
    @State private var watching = false
    var body: some View {
        NavigationSplitView {
            List {
                Button("All groups · \(catalog.channels.count)") { catalog.group = "" }
                Toggle("Favorites only", isOn: $catalog.favoritesOnly)
                ForEach(catalog.groups, id: \.self) { group in
                    Button { catalog.group = group } label: {
                        HStack { Text(group); Spacer(); if catalog.group == group { Image(systemName: "checkmark") } }
                    }
                }
            }.navigationTitle("IPTV groups")
            .toolbar {
                Button("Open M3U") { importing = true }
                Button("Playlist URL") { enteringURL = true }
            }
        } detail: {
            VStack(alignment: .leading) {
                TextField("Search channels or groups", text: $catalog.search).textFieldStyle(.roundedBorder).remoteControlFocus()
                Text("\(catalog.visible.count) / \(catalog.channels.count) channels").font(.subheadline).foregroundStyle(.secondary)
                if catalog.loading { ProgressView("Loading playlist…") }
                if let error = catalog.error { Text(error).foregroundStyle(.red) }
                if catalog.channels.isEmpty {
                    ContentUnavailableView("Add your IPTV playlist", systemImage: "tv", description: Text("Open an M3U file or enter its URL. Channels are kept separately from your music queue."))
                } else {
                    List(catalog.visible) { channel in
                        HStack {
                            Button {
                                player.append(title: channel.title, url: channel.url, play: true)
                                watching = true
                            } label: {
                                HStack {
                                    Image(systemName: "play.tv")
                                    VStack(alignment: .leading) {
                                        Text(channel.title).font(.headline)
                                        Text(channel.group).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                }.frame(minHeight: 52)
                            }.accessibilityIdentifier("iptv.channel.\(channel.id)")
                            Button { catalog.toggleFavorite(channel) } label: {
                                Image(systemName: catalog.favorites.contains(channel.id) ? "star.fill" : "star")
                            }.accessibilityLabel("Favorite \(channel.title)")
                        }
                    }.listStyle(.plain)
                }
            }.padding().navigationTitle(catalog.group.isEmpty ? "IPTV" : catalog.group)
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.playlist, .data]) { result in
            switch result {
            case .success(let url): Task { await catalog.load(url) }
            case .failure(let error): catalog.error = error.localizedDescription
            }
        }
        .alert("IPTV playlist URL", isPresented: $enteringURL) {
            TextField("https://…/playlist.m3u", text: $source).textInputAutocapitalization(.never)
            Button("Load") {
                guard let url = URL(string: source.trimmingCharacters(in: .whitespacesAndNewlines)),
                      ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { catalog.error = "Enter an HTTP or HTTPS playlist URL."; return }
                Task { await catalog.load(url) }
            }
            Button("Cancel", role: .cancel) { }
        }
        .sheet(isPresented: $watching) {
            NavigationStack {
                VideoPlayer(player: player.player).navigationTitle(player.current?.title ?? "IPTV")
                    .toolbar { Button("Back to channels") { watching = false } }
            }.buttonStyle(RemoteButtonStyle())
        }
    }
}

struct RemoteButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { RemoteButtonLabel(configuration: configuration) }
}
private struct RemoteButtonLabel: View {
    let configuration: ButtonStyle.Configuration
    @Environment(\.isFocused) private var focused
    @Environment(\.isEnabled) private var enabled
    @State private var hovering = false
    var body: some View {
        configuration.label.padding(.horizontal, 10).padding(.vertical, 8)
            .background((focused || hovering || configuration.isPressed) ? Color.yellow.opacity(0.25) : Color.secondary.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(focused || hovering ? Color.yellow : Color.clear, lineWidth: 3))
            .opacity(enabled ? 1 : 0.45)
            .contentShape(Rectangle()).onHover { hovering = $0 }.hoverEffect(.highlight)
    }
}

private struct RemoteControlFocus: ViewModifier {
    @FocusState private var focused: Bool
    @State private var hovering = false
    func body(content: Content) -> some View {
        content.focused($focused).onHover { hovering = $0 }
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(focused || hovering ? Color.yellow : Color.clear, lineWidth: 3).allowsHitTesting(false))
    }
}
extension View {
    func remoteControlFocus() -> some View { modifier(RemoteControlFocus()) }
}

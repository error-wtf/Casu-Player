import AVKit
import SwiftUI
import SafariServices
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var model: PlayerModel
    @State private var importing = false
    @StateObject private var library = MediaLibraryModel()
    @StateObject private var recording = RecordingController()
    @StateObject private var youtube = YouTubeModel()
    @State private var networkURL = ""
    @State private var exportedPlaylist: URL?
    @State private var providerURL: URL?
    @State private var showingProvider = false

    var body: some View {
        TabView {
            playerView
                .tabItem { Label("Player", systemImage: "play.circle") }
            libraryView
                .tabItem { Label("Library", systemImage: "music.note.list") }
            searchView
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
            settingsView
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
        .sheet(isPresented: $showingProvider) {
            if let url = providerURL { ProviderBrowserView(url: url).ignoresSafeArea() }
        }
    }

    private func queueRow(_ item: QueueOccurrence) -> some View {
        Text(item.title).tag(item.id)
            .accessibilityIdentifier("queue.occurrence.\(item.id)")
            .swipeActions { Button(role: .destructive) { model.remove(item) } label: { Label("Remove", systemImage: "trash") } }
    }

    private var playerView: some View {
        NavigationSplitView {
            List(selection: Binding(get: { model.queue.currentOccurrenceID }, set: { id in
                if let item = model.queue.occurrences.first(where: { $0.id == id }) { model.select(item) }
            })) {
                ForEach(QueueDisplayGroup.make(model.queue.occurrences)) { group in
                    if let title = group.title {
                        DisclosureGroup("\(title) · \(group.items.count)") {
                            ForEach(group.items) { item in queueRow(item) }
                                .onMove { source, destination in model.moveWithinGroup(group, from: source, to: destination) }
                        }
                        .contextMenu {
                            Button("Play playlist") { if let item = group.items.first { model.select(item); if !model.isPlaying { model.togglePlayback() } } }
                            Button("Remove playlist", role: .destructive) { for item in group.items { model.remove(item) } }
                        }
                    } else if let item = group.items.first { queueRow(item) }
                }.onMove(perform: model.moveGroups)
            }
            .navigationTitle("Queue")
            .toolbar {
                EditButton()
                Menu {
                    ForEach(PlaylistExportFormat.allCases) { format in
                        Button(format.rawValue.uppercased()) {
                            do { exportedPlaylist = try model.exportPlaylist(format: format) }
                            catch { model.errorMessage = "Playlist export failed: \(error.localizedDescription)" }
                        }
                    }
                } label: { Label("Save playlist", systemImage: "square.and.arrow.up") }
                Button { importing = true } label: { Label("Open", systemImage: "folder") }.accessibilityIdentifier("queue.open")
            }
            .sheet(isPresented: Binding(get: { exportedPlaylist != nil }, set: { if !$0 { exportedPlaylist = nil } })) {
                if let url = exportedPlaylist {
                    ShareLink(item: url) { Label("Share MPCASU queue", systemImage: "square.and.arrow.up") }.padding()
                }
            }
        } detail: {
            VStack(spacing: 20) {
                if let artwork = model.currentArtwork, !model.currentHasVideo {
                    Image(uiImage: artwork).resizable().scaledToFit()
                        .frame(maxHeight: 320).accessibilityLabel("Album cover")
                } else {
                    VideoPlayer(player: model.player).accessibilityLabel("Video player")
                }
                Text(model.current?.title ?? "Open media to begin").font(.headline).lineLimit(2)
                Text([model.current?.artist, model.current?.album].compactMap { $0 }.joined(separator: " · "))
                    .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                Slider(value: Binding(get: { model.position }, set: model.seek), in: 0...max(1, model.duration))
                    .accessibilityIdentifier("playback.seek")
                HStack {
                    Button(action: model.previous) { Label("Previous", systemImage: "backward.fill") }.labelStyle(.iconOnly)
                    Button(action: model.togglePlayback) {
                        Label(model.isPlaying ? "Pause" : "Play", systemImage: model.isPlaying ? "pause.fill" : "play.fill")
                    }.buttonStyle(.borderedProminent).disabled(model.current == nil).accessibilityIdentifier("playback.toggle")
                    Button(action: model.stop) { Label("Stop", systemImage: "stop.fill") }.labelStyle(.iconOnly)
                        .disabled(model.current == nil)
                    Button(action: model.advance) { Label("Next", systemImage: "forward.fill") }.labelStyle(.iconOnly)
                }
                HStack {
                    Button(model.shuffle ? "Shuffle on" : "Shuffle off") { model.shuffle.toggle() }
                    Button("Repeat \(model.repeatMode)") { model.cycleRepeat() }
                }
                Picker("Recording split", selection: $recording.mode) {
                    ForEach(RecordingSplitMode.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("recording.split-mode")
                if recording.mode == .time {
                    Stepper("Every \(recording.intervalMinutes) minutes", value: $recording.intervalMinutes, in: 1...1440)
                }
                Button { recording.toggle(model.current) } label: {
                    Label(recording.isRecording ? "Stop recording" : "Record",
                          systemImage: recording.isRecording ? "stop.circle.fill" : "record.circle")
                }
                .disabled(model.current == nil)
                .accessibilityIdentifier("recording.toggle")
                if !recording.status.isEmpty { Text(recording.status).font(.caption) }
            }.padding()
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.audio, .movie, .playlist, .data], allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls): Task { await model.importDocuments(urls) }
            case .failure: model.errorMessage = "The selected document could not be opened."
            }
        }
        .alert("MPCASU", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("OK") { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "") }
        .onChange(of: model.queue.currentOccurrenceID) {
            if let item = model.current { recording.sourceChanged(item) }
        }
    }

    private var searchView: some View {
        NavigationStack {
            VStack {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        providerLink("Spotify", "https://open.spotify.com/")
                        providerLink("HearThis", "https://hearthis.at/")
                        providerLink("Tidal", "https://tidal.com/")
                        providerLink("Netflix", "https://www.netflix.com/")
                        providerLink("Browse", "https://www.google.com/")
                    }
                }
                Picker("Search type", selection: $youtube.kind) {
                    ForEach(YouTubeSearchKind.allCases) { Text($0.rawValue).tag($0) }
                }.pickerStyle(.segmented).accessibilityIdentifier("youtube.search-kind")
                HStack {
                    TextField("YouTube search or media URL", text: $youtube.query)
                        .textFieldStyle(.roundedBorder).onSubmit(youtube.search)
                    Button("Search", action: youtube.search).buttonStyle(.borderedProminent)
                }
                if youtube.isLoading { ProgressView() }
                if let error = youtube.error { Text(error).foregroundStyle(.red) }
                List(youtube.results) { result in
                    Button { youtube.importResult(result, into: model) } label: {
                        VStack(alignment: .leading) {
                            Text(result.title).lineLimit(2)
                            Text(result.subtitle).font(.caption).foregroundStyle(.secondary)
                        }
                    }.accessibilityIdentifier("youtube.result.\(result.id)")
                }.accessibilityIdentifier("youtube.results.scrollable")
            }.padding(.horizontal).navigationTitle("YouTube")
        }
    }

    private var settingsView: some View {
        NavigationStack {
            Form {
                Section("Playback") {
                    Slider(value: $model.volume, in: 0...1) { Text("Volume") }
                    Picker("Playback rate", selection: $model.playbackRate) {
                        Text("0.5×").tag(Float(0.5)); Text("1×").tag(Float(1))
                        Text("1.25×").tag(Float(1.25)); Text("1.5×").tag(Float(1.5)); Text("2×").tag(Float(2))
                    }
                    Toggle("Shuffle", isOn: $model.shuffle)
                    Picker("Repeat", selection: $model.repeatMode) {
                        Text("Off").tag("off"); Text("All").tag("all"); Text("One").tag("one")
                    }
                }
                Section("Open network media or playlist") {
                    TextField("https://…", text: $networkURL).textInputAutocapitalization(.never)
                    Button("Add URL") {
                        guard let url = URL(string: networkURL) else { model.errorMessage = "Invalid URL."; return }
                        if ["m3u", "m3u8", "pls"].contains(url.pathExtension.lowercased()) {
                            Task { await model.importPlaylist(url) }
                        } else { model.append(title: url.deletingPathExtension().lastPathComponent, url: url) }
                    }
                }
                Section("Recording") {
                    Picker("Split mode", selection: $recording.mode) {
                        ForEach(RecordingSplitMode.allCases) { Text($0.label).tag($0) }
                    }
                    if recording.mode == .time {
                        Stepper("Every \(recording.intervalMinutes) minutes", value: $recording.intervalMinutes, in: 1...1440)
                    }
                }
                Section("About") { Text("MPCASU 7.0.0 · Native iOS port of MPCASU Android") }
            }.navigationTitle("Settings")
        }
    }

    private func providerLink(_ name: String, _ address: String) -> some View {
        Button {
            providerURL = URL(string: address)
            showingProvider = true
        } label: {
            Label(name, systemImage: "safari").padding(.horizontal, 8).padding(.vertical, 6)
        }
        .buttonStyle(.bordered)
        .accessibilityIdentifier("provider.\(name.lowercased())")
    }

    private var libraryView: some View {
        NavigationStack {
            VStack(spacing: 8) {
                Picker("Library section", selection: $library.section) {
                    ForEach(LibrarySection.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("library.sections")
                TextField("Search library", text: $library.search)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("library.search")
                if library.authorizationDenied {
                    ContentUnavailableView("Media Library Access Required",
                                           systemImage: "music.note",
                                           description: Text("Allow media-library access in Settings."))
                } else if library.section != .songs && library.section != .favorites && library.selectedGroup == nil {
                    List(library.groups, id: \.self) { group in
                        Button {
                            library.selectedGroup = group
                        } label: {
                            HStack {
                                Text(group)
                                Spacer()
                                Text("\(MediaLibraryModel.tracks(in: library.tracks, section: library.section, group: group).count)")
                                    .foregroundStyle(.secondary)
                                Image(systemName: "chevron.right").foregroundStyle(.secondary)
                            }
                        }
                        .accessibilityIdentifier("library.group.\(group)")
                    }
                } else {
                    List(library.visibleTracks) { track in
                        Button {
                            model.importLibraryTrack(track)
                        } label: {
                            HStack {
                                if let data = track.artworkData, let image = UIImage(data: data) {
                                    Image(uiImage: image).resizable().scaledToFill()
                                        .frame(width: 48, height: 48).clipped().accessibilityLabel("Album cover")
                                } else {
                                    Image(systemName: "music.note").frame(width: 48, height: 48)
                                }
                                VStack(alignment: .leading) {
                                Text(track.title)
                                Text([track.artist, track.album, track.genre].joined(separator: " · "))
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                            }
                        }
                        .contextMenu {
                            Button("Add to queue") { model.importLibraryTrack(track, selectItem: false) }
                        }
                        .swipeActions(edge: .leading) {
                            Button { library.toggleFavorite(track) } label: {
                                Label(library.isFavorite(track) ? "Unfavorite" : "Favorite",
                                      systemImage: library.isFavorite(track) ? "star.slash" : "star")
                            }.tint(.yellow)
                        }
                        .accessibilityIdentifier("library.track.\(track.id)")
                    }
                }
            }
            .padding(.horizontal)
            .navigationTitle(library.selectedGroup ?? "Library")
            .toolbar {
                if library.selectedGroup != nil {
                    Button("All \(library.section.rawValue)") { library.selectedGroup = nil }
                }
                Button("Add to queue") {
                    for track in library.visibleTracks { model.importLibraryTrack(track, selectItem: false) }
                }.disabled(library.visibleTracks.isEmpty)
                Button { library.refresh() } label: { Image(systemName: "arrow.clockwise") }
            }
            .task { library.refresh() }
        }
    }
}


/// Safari's browser controller is presented inside MPCASU, never via openURL.
private struct ProviderBrowserView: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }
    func updateUIViewController(_ controller: SFSafariViewController, context: Context) { }
}

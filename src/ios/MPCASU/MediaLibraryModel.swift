import MediaPlayer
import SwiftUI

struct LibraryTrack: Identifiable, Equatable {
    let id: UInt64
    let title: String
    let artist: String
    let album: String
    let genre: String
    let trackNumber: Int
    let assetURL: URL?
}

enum LibrarySection: String, CaseIterable, Identifiable {
    case songs = "Tracks"
    case artists = "Artists"
    case albums = "Albums"
    case genres = "Genres"
    case favorites = "Favorites"
    var id: String { rawValue }
}

@MainActor
final class MediaLibraryModel: ObservableObject {
    @Published private(set) var tracks: [LibraryTrack] = []
    @Published var section: LibrarySection = .songs { didSet { selectedGroup = nil } }
    @Published var selectedGroup: String?
    @Published var search = ""
    @Published private(set) var authorizationDenied = false
    @Published private(set) var favoriteIDs: Set<UInt64> = Set(
        (UserDefaults.standard.stringArray(forKey: "libraryFavoriteIDs") ?? []).compactMap(UInt64.init)
    )

    func toggleFavorite(_ track: LibraryTrack) {
        if favoriteIDs.contains(track.id) { favoriteIDs.remove(track.id) }
        else { favoriteIDs.insert(track.id) }
        UserDefaults.standard.set(favoriteIDs.map(String.init), forKey: "libraryFavoriteIDs")
    }

    func isFavorite(_ track: LibraryTrack) -> Bool { favoriteIDs.contains(track.id) }

    func refresh() {
        MPMediaLibrary.requestAuthorization { [weak self] status in
            Task { @MainActor in
                guard let self else { return }
                self.authorizationDenied = status != .authorized
                guard status == .authorized else { self.tracks = []; return }
                self.tracks = (MPMediaQuery.songs().items ?? []).map {
                    LibraryTrack(id: $0.persistentID,
                                 title: $0.title ?? "Unknown title",
                                 artist: $0.artist ?? "Unknown Artist",
                                 album: $0.albumTitle ?? "Unknown Album",
                                 genre: $0.genre ?? "Unknown Genre",
                                 trackNumber: $0.albumTrackNumber,
                                 assetURL: $0.assetURL)
                }.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
            }
        }
    }

    var groups: [String] {
        Self.groups(in: tracks, by: section, search: search)
    }

    var visibleTracks: [LibraryTrack] {
        let base = section == .favorites
            ? tracks.filter { favoriteIDs.contains($0.id) }
            : selectedGroup.map { Self.tracks(in: tracks, section: section, group: $0) } ?? tracks
        guard !search.isEmpty else { return base }
        return base.filter {
            [$0.title, $0.artist, $0.album, $0.genre].contains {
                $0.localizedCaseInsensitiveContains(search)
            }
        }
    }

    nonisolated static func groups(in tracks: [LibraryTrack], by section: LibrarySection,
                                   search: String = "") -> [String] {
        guard section != .songs && section != .favorites else { return [] }
        let values = tracks.map { value(for: $0, section: section) }
            .filter { search.isEmpty || $0.localizedCaseInsensitiveContains(search) }
        return Array(Set(values)).sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    nonisolated static func tracks(in tracks: [LibraryTrack], section: LibrarySection,
                                   group: String) -> [LibraryTrack] {
        tracks.filter { value(for: $0, section: section).caseInsensitiveCompare(group) == .orderedSame }
            .sorted {
                if section == .albums && $0.trackNumber != $1.trackNumber {
                    return $0.trackNumber < $1.trackNumber
                }
                return $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
            }
    }

    nonisolated private static func value(for track: LibraryTrack, section: LibrarySection) -> String {
        switch section {
        case .songs: return track.title
        case .artists: return track.artist
        case .albums: return track.album
        case .genres: return track.genre
        case .favorites: return track.title
        }
    }
}

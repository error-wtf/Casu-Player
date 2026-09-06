import AVFoundation
import UIKit
import ImageIO

/// Local ID3/iTunes tags and bounded artwork, shared by imports and playback.
@MainActor
final class MediaMetadata {
    let title: String?
    let artist: String?
    let album: String?
    let artwork: UIImage?
    let hasVideo: Bool
    private static let cache: NSCache<NSString, MediaMetadata> = {
        let result = NSCache<NSString, MediaMetadata>()
        result.countLimit = 128
        result.totalCostLimit = 32 * 1024 * 1024
        return result
    }()

    init(title: String?, artist: String?, album: String?, artwork: UIImage?, hasVideo: Bool) {
        self.title = title; self.artist = artist; self.album = album
        self.artwork = artwork; self.hasVideo = hasVideo
    }

    static func load(_ url: URL) async -> MediaMetadata? {
        guard url.isFileURL || url.scheme == "ipod-library" else { return nil }
        if let cached = cache.object(forKey: url.absoluteString as NSString) { return cached }
        let asset = AVURLAsset(url: url)
        guard let items = try? await asset.load(.commonMetadata) else { return nil }
        func string(_ key: AVMetadataKey) async -> String? {
            guard let item = AVMetadataItem.metadataItems(from: items, withKey: key, keySpace: .common).first,
                  let raw = try? await item.load(.stringValue) else { return nil }
            let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            return value.isEmpty ? nil : value
        }
        let title = await string(.commonKeyTitle)
        let artist = await string(.commonKeyArtist)
        let album = await string(.commonKeyAlbumName)
        var artwork: UIImage?
        if let item = AVMetadataItem.metadataItems(from: items, withKey: AVMetadataKey.commonKeyArtwork, keySpace: .common).first,
           let data = try? await item.load(.dataValue), data.count <= 32 * 1024 * 1024,
           let source = CGImageSourceCreateWithData(data as CFData, nil),
           let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 640
           ] as CFDictionary) {
            artwork = UIImage(cgImage: image)
        }
        let tracks = (try? await asset.loadTracks(withMediaType: .video)) ?? []
        let value = MediaMetadata(title: title, artist: artist, album: album, artwork: artwork, hasVideo: !tracks.isEmpty)
        let cost = artwork?.cgImage.map { $0.bytesPerRow * $0.height } ?? 1024
        cache.setObject(value, forKey: url.absoluteString as NSString, cost: cost)
        return value
    }
}

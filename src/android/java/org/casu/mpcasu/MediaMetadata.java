// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
package org.casu.mpcasu;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.LruCache;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.function.Consumer;

/** Shared, bounded local metadata/artwork loader for queue and recycled library rows. */
public final class MediaMetadata {
    public final String title, artist, album;
    public final Bitmap artwork;
    private static final ExecutorService WORKERS = Executors.newFixedThreadPool(2);
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final LruCache<String, MediaMetadata> CACHE = new LruCache<String, MediaMetadata>(12 * 1024 * 1024) {
        @Override protected int sizeOf(String key, MediaMetadata value) {
            return 1024 + (value.artwork == null ? 0 : value.artwork.getAllocationByteCount());
        }
    };
    private MediaMetadata(String title, String artist, String album, Bitmap artwork) {
        this.title = title; this.artist = artist; this.album = album; this.artwork = artwork;
    }
    public static void load(Context context, String source, Consumer<MediaMetadata> callback) {
        if (source == null) return;
        MediaMetadata cached = CACHE.get(source);
        if (cached != null) { callback.accept(cached); return; }
        // Never probe live/network streams while rendering library rows.
        if (!(source.startsWith("/") || source.startsWith("file:") || source.startsWith("content:"))) return;
        Context app = context.getApplicationContext();
        WORKERS.execute(() -> {
            MediaMetadata value = CACHE.get(source);
            if (value == null) { value = read(app, source); CACHE.put(source, value); }
            MediaMetadata result = value;
            MAIN.post(() -> callback.accept(result));
        });
    }
    static MediaMetadata read(Context context, String source) {
        String title = null, artist = null, album = null;
        Bitmap artwork = null;
        MediaMetadataRetriever reader = new MediaMetadataRetriever();
        try {
            if (source.startsWith("/")) reader.setDataSource(source);
            else reader.setDataSource(context, Uri.parse(source));
            title = reader.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE);
            artist = reader.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST);
            album = reader.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ALBUM);
            byte[] bytes = reader.getEmbeddedPicture();
            if (bytes != null && bytes.length <= 32 * 1024 * 1024) {
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                options.inSampleSize = 1;
                while (Math.max(options.outWidth, options.outHeight) / options.inSampleSize > 640) options.inSampleSize *= 2;
                options.inJustDecodeBounds = false;
                artwork = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
            }
        } catch (Exception ignored) {
        } finally { try { reader.release(); } catch (Exception ignored) {} }
        return new MediaMetadata(title, artist, album, artwork);
    }
    public void apply(MediaItem item) {
        if (!item.metadataLoaded) {
            if (title != null && !title.trim().isEmpty()) item.title = title.trim();
            item.metadataLoaded = true;
        }
        if (artist != null && !artist.trim().isEmpty()) item.artist = artist.trim();
        if (album != null && !album.trim().isEmpty()) item.album = album.trim();
    }
}

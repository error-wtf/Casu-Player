// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.content.ContentUris;
import android.content.Context;
import android.net.Uri;
import android.provider.MediaStore;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.HashSet;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Media library backed by MediaStore (audio + video) with search,
 *  grouping fields and JSON-persisted favorites — the Android equivalent
 *  of the Linux reference's MediaLibrary. */
public final class Library {

    public static final class Track {
        public final long id;
        public final String uri;      // content:// URI (directly playable)
        public final String title;
        public final String artist;
        public final String album;
        public final String genre;
        public final long durationMs;
        public final boolean video;
        public final int trackNumber;

        Track(long id, String uri, String title, String artist, String album,
              String genre, long durationMs, boolean video) {
            this(id, uri, title, artist, album, genre, durationMs, video, 0);
        }

        Track(long id, String uri, String title, String artist, String album,
              String genre, long durationMs, boolean video, int trackNumber) {
            this.id = id;
            this.uri = uri;
            this.title = title;
            this.artist = artist;
            this.album = album;
            this.genre = genre;
            this.durationMs = durationMs;
            this.video = video;
            this.trackNumber = trackNumber;
        }

        public MediaItem toItem() {
            MediaItem item = new MediaItem(uri, title, video ? "video" : "audio",
                    video ? "VIDEO" : "AUDIO");
            item.artist = artist != null && !artist.isEmpty() ? artist : null;
            item.album = album;
            return item;
        }
    }

    private final Context context;
    private Set<String> favorites = new HashSet<>();
    private final File favoritesFile;

    public Library(Context context) {
        this.context = context.getApplicationContext();
        favoritesFile = new File(this.context.getFilesDir(), "favorites.json");
        loadFavorites();
    }

    public List<Track> query(String search, boolean includeAudio, boolean includeVideo) {
        List<Track> out = new ArrayList<>();
        if (includeAudio) out.addAll(queryStore(false, search, false));
        if (includeVideo) out.addAll(queryStore(true, search, false));
        return out;
    }

    /** Distinct, alphabetically sorted values used by the Artists/Albums/Genres
     * navigation. Blank MediaStore metadata is deliberately grouped as Unknown. */
    public static List<String> groups(List<Track> tracks, String field, String search) {
        Set<String> values = new LinkedHashSet<>();
        String needle = search == null ? "" : search.trim().toLowerCase(Locale.ROOT);
        for (Track track : tracks) {
            if (track.video) continue;
            String value = groupValue(track, field);
            if (value == null || value.trim().isEmpty()) value = unknownFor(field);
            if (needle.isEmpty() || value.toLowerCase(Locale.ROOT).contains(needle)) {
                values.add(value);
            }
        }
        List<String> out = new ArrayList<>(values);
        Collections.sort(out, String.CASE_INSENSITIVE_ORDER);
        return out;
    }

    public static List<Track> tracksInGroup(List<Track> tracks, String field, String value) {
        List<Track> out = new ArrayList<>();
        for (Track track : tracks) {
            String candidate = groupValue(track, field);
            if (candidate == null || candidate.trim().isEmpty()) candidate = unknownFor(field);
            if (!track.video && candidate.equalsIgnoreCase(value)) out.add(track);
        }
        out.sort(Comparator.comparingInt((Track t) -> "albums".equals(field)
                        ? t.trackNumber : 0)
                .thenComparing(t -> t.album == null ? "" : t.album,
                        String.CASE_INSENSITIVE_ORDER)
                .thenComparing(t -> t.title, String.CASE_INSENSITIVE_ORDER));
        return out;
    }

    private static String groupValue(Track track, String field) {
        if ("artists".equals(field)) return track.artist;
        if ("albums".equals(field)) return track.album;
        if ("genres".equals(field)) return track.genre;
        throw new IllegalArgumentException("Unsupported library group: " + field);
    }

    private static String unknownFor(String field) {
        if ("artists".equals(field)) return "Unknown Artist";
        if ("albums".equals(field)) return "Unknown Album";
        if ("genres".equals(field)) return "Unknown Genre";
        return "Unknown";
    }

    public void rescan() {
        // Force MediaStore re-read on next query (for refresh button).
        // MediaStore is a ContentProvider — each query is fresh by default;
        // this method exists as a semantic marker for UI refresh triggers.
    }

    private List<Track> queryStore(boolean video, String search, boolean skip) {
        List<Track> out = new ArrayList<>();
        if (skip) return out;
        try {
            Uri collection = video
                    ? MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                    : MediaStore.Audio.Media.EXTERNAL_CONTENT_URI;
            boolean inlineGenre = !video && android.os.Build.VERSION.SDK_INT >= 30;
            String[] projection = video
                    ? new String[]{MediaStore.Video.Media._ID,
                       MediaStore.Video.Media.TITLE, MediaStore.Video.Media.DURATION,
                       MediaStore.Video.Media.BUCKET_DISPLAY_NAME}
                    : inlineGenre ? new String[]{MediaStore.Audio.Media._ID,
                       MediaStore.Audio.Media.TITLE, MediaStore.Audio.Media.ARTIST,
                       MediaStore.Audio.Media.ALBUM, MediaStore.Audio.Media.GENRE,
                       MediaStore.Audio.Media.TRACK, MediaStore.Audio.Media.DURATION}
                    : new String[]{MediaStore.Audio.Media._ID,
                       MediaStore.Audio.Media.TITLE, MediaStore.Audio.Media.ARTIST,
                       MediaStore.Audio.Media.ALBUM, MediaStore.Audio.Media.TRACK,
                       MediaStore.Audio.Media.DURATION};
            Map<Long, String> legacyGenres = !video && !inlineGenre
                    ? queryLegacyGenres() : Collections.emptyMap();
            String selection = null;
            String[] args = null;
            if (search != null && !search.isEmpty()) {
                selection = video
                        ? MediaStore.Video.Media.TITLE + " LIKE ?"
                        : MediaStore.Audio.Media.TITLE + " LIKE ? OR "
                          + MediaStore.Audio.Media.ARTIST + " LIKE ? OR "
                          + MediaStore.Audio.Media.ALBUM + " LIKE ?";
                String like = "%" + search + "%";
                args = video ? new String[]{like}
                        : new String[]{like, like, like};
            }
            try (android.database.Cursor cursor = context.getContentResolver().query(
                    collection, projection, selection, args,
                    MediaStore.MediaColumns.DATE_ADDED + " DESC")) {
                if (cursor == null) return out;
                while (cursor.moveToNext()) {
                    long id = cursor.getLong(0);
                    String title = cursor.getString(1);
                    String artist = !video ? cursor.getString(2) : null;
                    String album = !video ? cursor.getString(3) : null;
                    String genre = !video ? (inlineGenre ? cursor.getString(4)
                            : legacyGenres.get(id)) : null;
                    int trackNumber = !video ? cursor.getInt(inlineGenre ? 5 : 4) : 0;
                    long duration = cursor.getLong(video ? 2 : (inlineGenre ? 6 : 5));
                    if (duration <= 0 && video) duration = cursor.getLong(2);
                    String uri = ContentUris.withAppendedId(collection, id).toString();
                    out.add(new Track(id, uri, title == null || title.isEmpty()
                            ? MediaItem.fallbackTitle(uri) : title,
                            artist, album, genre, duration, video, trackNumber));
                }
            }
        } catch (Exception ignored) {
            // MediaStore unavailable (weird profiles): library stays empty.
        }
        return out;
    }

    private Map<Long, String> queryLegacyGenres() {
        Map<Long, String> genres = new HashMap<>();
        try (android.database.Cursor genreCursor = context.getContentResolver().query(
                MediaStore.Audio.Genres.EXTERNAL_CONTENT_URI,
                new String[]{MediaStore.Audio.Genres._ID, MediaStore.Audio.Genres.NAME},
                null, null, null)) {
            if (genreCursor == null) return genres;
            while (genreCursor.moveToNext()) {
                long genreId = genreCursor.getLong(0);
                String name = genreCursor.getString(1);
                Uri members = MediaStore.Audio.Genres.Members.getContentUri("external", genreId);
                try (android.database.Cursor memberCursor = context.getContentResolver().query(
                        members, new String[]{MediaStore.Audio.Genres.Members.AUDIO_ID},
                        null, null, null)) {
                    if (memberCursor == null) continue;
                    while (memberCursor.moveToNext()) genres.put(memberCursor.getLong(0), name);
                }
            }
        } catch (Exception ignored) {
            // Metadata availability varies by vendor; tracks remain under Unknown.
        }
        return genres;
    }

    // ------------------------------------------------------------------ favorites

    public boolean isFavorite(String uri) {
        return favorites.contains(uri);
    }

    public void toggleFavorite(String uri) {
        if (favorites.contains(uri)) favorites.remove(uri);
        else favorites.add(uri);
        saveFavorites();
    }

    public List<Track> filterFavorites(List<Track> tracks) {
        List<Track> out = new ArrayList<>();
        for (Track track : tracks) {
            if (favorites.contains(track.uri)) out.add(track);
        }
        return out;
    }

    private void loadFavorites() {
        try (FileInputStream in = new FileInputStream(favoritesFile)) {
            byte[] buf = new byte[(int) favoritesFile.length()];
            int read = in.read(buf);
            JSONObject root = new JSONObject(new String(buf, 0, Math.max(0, read)));
            JSONArray array = root.optJSONArray("favorites");
            if (array != null) {
                for (int i = 0; i < array.length(); i++) {
                    String value = array.optString(i, "");
                    if (!value.isEmpty()) favorites.add(value);
                }
            }
        } catch (Exception ignored) {
        }
    }

    private void saveFavorites() {
        try {
            JSONObject root = new JSONObject();
            JSONArray array = new JSONArray();
            for (String value : favorites) array.put(value);
            root.put("favorites", array);
            File tmp = new File(favoritesFile.getParentFile(), favoritesFile.getName() + ".tmp");
            try (FileOutputStream out = new FileOutputStream(tmp)) {
                out.write(root.toString().getBytes());
            }
            if (!tmp.renameTo(favoritesFile)) {
                try (FileOutputStream out = new FileOutputStream(favoritesFile)) {
                    out.write(root.toString().getBytes());
                }
                tmp.delete();
            }
        } catch (Exception ignored) {
        }
    }
}

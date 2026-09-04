// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.content.Context;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Playlist import/export for every format the Linux reference supports:
 *  M3U/M3U8, PLS, XSPF, JSPF, ASX/WMX/WVX, WPL, RMP/RAM, MPCASU JSON.
 *  Entries resolve relative paths against the playlist location. */
public final class PlaylistIO {
    public static final class Entry {
        public final String url;
        public final String title;
        public Entry(String url, String title) {
            this.url = url;
            this.title = title == null ? "" : title;
        }
    }

    public static final class Playlist {
        public final List<Entry> items = new ArrayList<>();
        public String name = "";
    }

    public static final String[] SUFFIXES = {
            ".m3u", ".m3u8", ".pls", ".xspf", ".jspf", ".asx", ".wmx", ".wvx",
            ".wpl", ".rmp", ".ram", ".json"
    };

    public static boolean isPlaylistPath(String path) {
        if (path == null) return false;
        String low = path.toLowerCase();
        if (low.startsWith("http://") || low.startsWith("https://")) {
            // Remote playlist-like names (stream.m3u8) are streams, not groups.
            return low.contains(".xspf") && !low.contains(".m3u8");
        }
        for (String suffix : SUFFIXES) if (low.endsWith(suffix)) return true;
        return false;
    }

    public interface Fetcher {
        /** Read a text resource (file path or URL); throws on failure. */
        String fetch(String location) throws Exception;
    }

    public static Playlist load(String location, Fetcher fetcher) throws Exception {
        String text = fetcher.fetch(location);
        String low = location == null ? "" : location.toLowerCase();
        Playlist playlist;
        if (text.contains("#EXTM3U") || low.endsWith(".m3u") || low.endsWith(".m3u8")) {
            playlist = parseM3u(text);
        } else if (low.endsWith(".pls") || text.startsWith("[playlist]")) {
            playlist = parsePls(text);
        } else if (low.endsWith(".jspf") || (text.contains("\"playlist\"") && text.trim().startsWith("{"))) {
            playlist = parseJspf(text);
        } else if (low.endsWith(".xspf") || text.contains("<playlist") || text.contains("<trackList")) {
            playlist = parseXspf(text);
        } else if (low.endsWith(".wpl") || (text.contains("<smil>") && text.contains("<body"))) {
            playlist = parseWpl(text);
        } else if (low.endsWith(".asx") || low.endsWith(".wmx") || low.endsWith(".wvx")
                || text.contains("<asx ")) {
            playlist = parseAsx(text);
        } else if (low.endsWith(".ram") || low.endsWith(".rmp")) {
            playlist = parseRam(text);
        } else if (text.trim().startsWith("{")) {
            playlist = parseCasuJson(text);
        } else {
            playlist = parseM3u(text); // last resort: line-per-entry
        }
        if (playlist.name.isEmpty()) {
            playlist.name = PlaylistIO.fallbackName(location);
        }
        // Resolve relative paths against the playlist folder.
        String base = baseOf(location);
        for (int i = 0; i < playlist.items.size(); i++) {
            Entry entry = playlist.items.get(i);
            playlist.items.set(i, new Entry(resolve(entry.url, base), entry.title));
        }
        return playlist;
    }

    // ------------------------------------------------------------------ parsers

    private static Playlist parseM3u(String text) {
        Playlist out = new Playlist();
        String pending = null;
        for (String raw : text.split("\\r?\\n")) {
            String line = cleanEntry(raw);
            if (line.isEmpty()) continue;
            if (line.startsWith("#EXTINF:")) {
                int comma = line.indexOf(',');
                pending = comma >= 0 ? line.substring(comma + 1).trim() : null;
                continue;
            }
            if (line.startsWith("#")) continue;
            out.items.add(new Entry(line, pending));
            pending = null;
        }
        return out;
    }

    private static Playlist parsePls(String text) {
        Playlist out = new Playlist();
        Map<Integer, String> files = new HashMap<>();
        Map<Integer, String> titles = new HashMap<>();
        Pattern fileRe = Pattern.compile("^File(\\d+)\\s*=\\s*(.*)$");
        Pattern titleRe = Pattern.compile("^Title(\\d+)\\s*=\\s*(.*)$");
        for (String raw : text.split("\\r?\\n")) {
            String line = raw.trim();
            Matcher m = fileRe.matcher(line);
            if (m.matches()) files.put(Integer.parseInt(m.group(1)), m.group(2).trim());
            m = titleRe.matcher(line);
            if (m.matches()) titles.put(Integer.parseInt(m.group(1)), m.group(2).trim());
        }
        List<Integer> keys = new ArrayList<>(files.keySet());
        java.util.Collections.sort(keys);
        for (int key : keys) {
            String url = files.get(key);
            if (url == null || url.isEmpty()) continue;
            out.items.add(new Entry(url, titles.get(key)));
        }
        return out;
    }

    private static Playlist parseXspf(String text) {
        Playlist out = new Playlist();
        Matcher tracklist = Pattern.compile("<trackList>([\\s\\S]*?)</trackList>").matcher(text);
        String body = tracklist.find() ? tracklist.group(1) : text;
        Matcher tracks = Pattern.compile("<track>([\\s\\S]*?)</track>").matcher(body);
        while (tracks.find()) {
            String track = tracks.group(1);
            String location = tag(track, "location");
            String title = tag(track, "title");
            if (location != null && !location.isEmpty()) {
                out.items.add(new Entry(unescapeXml(location), unescapeXml(orEmpty(title))));
            }
        }
        return out;
    }

    private static Playlist parseWpl(String text) {
        Playlist out = new Playlist();
        Matcher media = Pattern.compile("<media\\s+src\\s*=\\s*\"([^\"]+)\"").matcher(text);
        while (media.find()) {
            out.items.add(new Entry(unescapeXml(media.group(1)), ""));
        }
        return out;
    }

    private static Playlist parseAsx(String text) {
        Playlist out = new Playlist();
        Matcher refs = Pattern.compile("<ref\\s+href\\s*=\\s*\"([^\"]+)\"").matcher(text);
        while (refs.find()) {
            out.items.add(new Entry(unescapeXml(refs.group(1)), ""));
        }
        if (out.items.isEmpty()) {
            Matcher entries = Pattern.compile("<entry[^>]*>\\s*<title>([^<]*)</title>", Pattern.CASE_INSENSITIVE).matcher(text);
            while (entries.find()) out.items.add(new Entry("", unescapeXml(entries.group(1))));
        }
        return out;
    }

    private static Playlist parseRam(String text) {
        Playlist out = new Playlist();
        for (String raw : text.split("\\r?\\n")) {
            String line = raw.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            if (line.startsWith("http") || line.startsWith("rtsp") || line.startsWith("/")) {
                out.items.add(new Entry(line, ""));
            }
        }
        return out;
    }

    private static Playlist parseJspf(String text) {
        Playlist out = new Playlist();
        JSONObject root;
        try {
            root = new JSONObject(text);
        } catch (Exception e) {
            return out;
        }
        JSONObject pl = root.optJSONObject("playlist");
        if (pl == null) return out;
        out.name = pl.optString("title", "");
        JSONArray tracks = pl.optJSONArray("track");
        if (tracks != null) {
            for (int i = 0; i < tracks.length(); i++) {
                JSONObject track = tracks.optJSONObject(i);
                if (track == null) continue;
                String location = track.optString("location", "");
                if (!location.isEmpty()) {
                    out.items.add(new Entry(location, track.optString("title", "")));
                }
            }
        }
        return out;
    }

    private static Playlist parseCasuJson(String text) {
        Playlist out = new Playlist();
        JSONObject root;
        try {
            root = new JSONObject(text);
        } catch (Exception e) {
            return out;
        }
        JSONArray items = root.optJSONArray("items");
        if (items == null) return out;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String url = item.optString("url", item.optString("path", ""));
            if (!url.isEmpty()) {
                out.items.add(new Entry(url, item.optString("title", "")));
            }
        }
        return out;
    }

    // ------------------------------------------------------------------ writers

    public static String writeM3u(String name, List<MediaItem> items) {
        StringBuilder sb = new StringBuilder("#EXTM3U\n");
        if (name != null && !name.isEmpty()) sb.append("#PLAYLIST:").append(name).append('\n');
        for (MediaItem item : items) {
            long seconds = 0; // durations are not tracked per-queue-entry
            sb.append("#EXTINF:").append(seconds).append(',').append(orEmpty(item.title)).append('\n');
            sb.append(item.url).append('\n');
        }
        return sb.toString();
    }

    public static String writePls(List<MediaItem> items) {
        StringBuilder sb = new StringBuilder("[playlist]\n");
        for (int i = 0; i < items.size(); i++) {
            MediaItem item = items.get(i);
            sb.append("File").append(i + 1).append('=').append(item.url).append('\n');
            sb.append("Title").append(i + 1).append('=').append(orEmpty(item.title)).append('\n');
            sb.append("Length").append(i + 1).append("=-1\n");
        }
        sb.append("NumberOfEntries=").append(items.size()).append('\n');
        sb.append("Version=2\n");
        return sb.toString();
    }

    public static String writeXspf(String name, List<MediaItem> items) {
        StringBuilder sb = new StringBuilder();
        sb.append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
        sb.append("<playlist version=\"1\" xmlns=\"http://xspf.org/ns/0/\">\n");
        sb.append("  <title>").append(escapeXml(orEmpty(name))).append("</title>\n  <trackList>\n");
        for (MediaItem item : items) {
            sb.append("    <track><location>").append(escapeXml(item.url))
              .append("</location><title>").append(escapeXml(orEmpty(item.title)))
              .append("</title></track>\n");
        }
        sb.append("  </trackList>\n</playlist>\n");
        return sb.toString();
    }

    public static String writeJspf(String name, List<MediaItem> items) {
        JSONObject root = new JSONObject();
        JSONObject pl = new JSONObject();
        try {
            pl.put("title", orEmpty(name));
            JSONArray tracks = new JSONArray();
            for (MediaItem item : items) {
                JSONObject track = new JSONObject();
                track.put("location", item.url);
                track.put("title", orEmpty(item.title));
                tracks.put(track);
            }
            pl.put("track", tracks);
            root.put("playlist", pl);
        } catch (Exception ignored) {}
        return root.toString() + "\n";
    }

    public static String writeCasuJson(String name, List<MediaItem> items) {
        JSONObject root = new JSONObject();
        try {
            root.put("type", "mpcasu-playlist");
            root.put("name", orEmpty(name));
            JSONArray array = new JSONArray();
            for (MediaItem item : items) array.put(item.toJson());
            root.put("items", array);
        } catch (Exception ignored) {}
        return root.toString() + "\n";
    }

    // ------------------------------------------------------------------ helpers

    public static String fetchText(String location) throws Exception {
        if (location.startsWith("http://") || location.startsWith("https://")) {
            HttpURLConnection conn = (HttpURLConnection) new URL(location).openConnection();
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(15000);
            conn.setRequestProperty("User-Agent", "MPCASU/5.0");
            int code = conn.getResponseCode();
            if (code < 200 || code >= 300) throw new Exception("HTTP " + code + " (http-error)");
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream buf = new ByteArrayOutputStream();
                byte[] chunk = new byte[16 * 1024];
                int n;
                while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
                return new String(buf.toByteArray(), StandardCharsets.UTF_8);
            } finally {
                conn.disconnect();
            }
        }
        String path = location != null && location.startsWith("file://")
                ? java.net.URLDecoder.decode(location.substring("file://".length()), "UTF-8")
                : location;
        try (InputStream in = new java.io.FileInputStream(path)) {
            return readUtf8(in);
        }
    }

    /** Read URLs, ordinary paths and Storage Access Framework content URIs. */
    public static String fetchText(Context context, String location) throws Exception {
        if (location != null && location.startsWith("content://")) {
            InputStream opened = context.getContentResolver().openInputStream(Uri.parse(location));
            if (opened == null) throw new Exception("Playlist konnte nicht gelesen werden");
            try (InputStream in = opened) {
                return readUtf8(in);
            }
        }
        return fetchText(location);
    }

    private static String readUtf8(InputStream in) throws Exception {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[16 * 1024];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            return new String(buf.toByteArray(), StandardCharsets.UTF_8);
    }

    public static void writeText(String path, String text) throws Exception {
        try (OutputStream out = new java.io.FileOutputStream(path)) {
            out.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }

    private static String tag(String xml, String name) {
        Matcher m = Pattern.compile("<" + name + "[^>]*>([\\s\\S]*?)</" + name + ">").matcher(xml);
        return m.find() ? m.group(1).trim() : null;
    }

    private static String unescapeXml(String value) {
        if (value == null) return "";
        return value.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", "\"").replace("&apos;", "'");
    }

    private static String escapeXml(String value) {
        if (value == null) return "";
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\"", "&quot;").replace("'", "&apos;");
    }

    private static String orEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String baseOf(String location) {
        if (location == null) return null;
        if (location.startsWith("content://")) return null;
        int slash = location.lastIndexOf('/');
        return slash > 0 ? location.substring(0, slash + 1) : null;
    }

    private static String resolve(String entry, String base) {
        entry = cleanEntry(entry);
        if (entry.isEmpty()) return entry;
        String low = entry.toLowerCase();
        if (low.startsWith("http://") || low.startsWith("https://") || low.startsWith("rtsp://")
                || low.startsWith("content://") || low.startsWith("file://") || entry.startsWith("/")) {
            return entry;
        }
        if (base != null) return base + entry;
        return entry;
    }

    /** Normalize text copied out of real-world M3U files.
     *
     * UTF BOMs, zero-width characters and quoted URLs are accepted by many
     * desktop players.  Android Uri treats such a prefix as part of the path,
     * which previously sent an actual HTTP stream to the local MediaPlayer.
     */
    static String cleanEntry(String value) {
        if (value == null) return "";
        String cleaned = value.replace("\uFEFF", "")
                .replace("\u200B", "")
                .replace("\u200C", "")
                .replace("\u200D", "")
                .replace("\u2060", "")
                .trim();
        if (cleaned.length() >= 2) {
            char first = cleaned.charAt(0);
            char last = cleaned.charAt(cleaned.length() - 1);
            if ((first == '"' && last == '"') || (first == '\'' && last == '\'')) {
                cleaned = cleaned.substring(1, cleaned.length() - 1).trim();
            }
        }
        return cleaned;
    }

    /** Canonical source passed to playback and the optional recorder. */
    static String normalizePlayableLocation(String value) {
        String cleaned = cleanEntry(value);
        if (cleaned.startsWith("//")) return "https:" + cleaned;
        int colon = cleaned.indexOf(':');
        if (colon > 0) {
            String scheme = cleaned.substring(0, colon);
            if (scheme.matches("[A-Za-z][A-Za-z0-9+.-]*")) {
                return scheme.toLowerCase(java.util.Locale.ROOT) + cleaned.substring(colon);
            }
        }
        return cleaned;
    }

    private static String fallbackName(String location) {
        if (location == null) return "";
        int slash = location.lastIndexOf('/');
        String name = slash >= 0 ? location.substring(slash + 1) : location;
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }
}

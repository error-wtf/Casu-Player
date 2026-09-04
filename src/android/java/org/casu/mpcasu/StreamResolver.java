package org.casu.mpcasu;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Resolves a network stream/playlist URL into a playable direct source before
 * playback. Android's native MediaPlayer cannot interpret radio playlist files
 * (.pls/.m3u/.xspf/...), so a remote playlist URL is fetched and the first real
 * media entry is extracted. HLS master/media playlists (.m3u8) and direct ICY
 * streams are passed through untouched (MediaPlayer handles them natively, but
 * many servers need a proper User-Agent).
 *
 * Runs on a background thread; never touches the UI thread.
 */
public final class StreamResolver {

    private static final String UA = "MPCASU/5.0 (Android; radio)";

    public interface Callback {
        void onResolved(Resolved result);
    }

    /** Outcome of a resolve attempt. */
    public static final class Resolved {
        /** The URL to pass to MediaPlayer (never null on success). */
        public final String url;
        /** A friendly display label, may be null. */
        public final String title;
        /** true when we actually fetched and rewrote the URL. */
        public final boolean rewritten;
        /** Optional extra headers to send (User-Agent etc.), may be empty. */
        public final java.util.Map<String, String> headers;

        Resolved(String url, String title, boolean rewritten,
                 java.util.Map<String, String> headers) {
            this.url = url;
            this.title = title;
            this.rewritten = rewritten;
            this.headers = headers;
        }
    }

    private static final String[] AUDIO_SUFFIXES = {
            ".mp3", ".aac", ".aacp", ".ogg", ".oga", ".opus", ".wav", ".flac",
            ".m4a", ".mp4", ".mkv", ".webm", ".ts", ".mov", ".aif", ".aiff"
    };

    private static final String[] PLAYLIST_SUFFIXES = {
            ".pls", ".m3u", ".xspf", ".jspf", ".asx", ".wmx", ".wvx", ".wpl",
            ".ram", ".rmp"
    };

    /** True when the location is a remote playlist file that must be resolved. */
    public static boolean isRemotePlaylist(String url) {
        if (url == null) return false;
        String low = url.toLowerCase(Locale.ROOT);
        if (!low.startsWith("http://") && !low.startsWith("https://")) return false;
        if (low.endsWith(".m3u8")) return false; // HLS handled natively
        for (String s : PLAYLIST_SUFFIXES) if (low.endsWith(s)) return true;
        return false;
    }

    /** True when the URL looks like a direct media stream/file. */
    public static boolean isDirectMedia(String url) {
        if (url == null) return false;
        String low = url.toLowerCase(Locale.ROOT);
        if (!low.startsWith("http://") && !low.startsWith("https://")) return false;
        if (low.endsWith(".m3u8")) return true; // HLS is a direct-feed source
        for (String s : AUDIO_SUFFIXES) if (low.endsWith(s)) return true;
        return false;
    }

    /**
     * Resolve an http(s) URL. If it is a remote playlist file, fetch it and
     * return the first playable entry (recursively resolving .pls/.m3u chains).
     * Otherwise return the original URL unmodified, but still probe it so we can
     * follow redirects and attach the User-Agent before MediaPlayer.
     */
    public static void resolve(final String original, final Callback callback) {
        resolveChain(original, original, 0, callback);
    }

    private static void resolveChain(final String original, final String current,
                                     final int depth, final Callback callback) {
        Thread t = new Thread(() -> {
            try {
                if (!isRemotePlaylist(current) || depth >= 3) {
                    // Direct media (or too-deep chain): return as-is, but with UA headers.
                    callback.onResolved(passThroughResolved(current));
                    return;
                }
                String text = fetchText(current);
                String first = firstEntryOf(current, text);
                if (first == null || first.isEmpty()) {
                    callback.onResolved(passThroughResolved(current));
                    return;
                }
                String abs = absolutize(first, current);
                if (isRemotePlaylist(abs)) {
                    // Another playlist: recurse to the inner stream.
                    resolveChain(original, abs, depth + 1, callback);
                } else {
                    java.util.Map<String, String> headers = new java.util.HashMap<>();
                    headers.put("User-Agent", UA);
                    callback.onResolved(new Resolved(abs, guessTitle(current), true, headers));
                }
            } catch (Exception e) {
                // Resolution failed: fall back to letting MediaPlayer try original.
                callback.onResolved(passThroughResolved(original));
            }
        });
        t.setDaemon(true);
        t.start();
    }

    private static Resolved passThroughResolved(String url) {
        java.util.Map<String, String> headers = new java.util.HashMap<>();
        headers.put("User-Agent", UA);
        return new Resolved(url, null, false, headers);
    }

    /** Pull the first real media location out of a playlist body. */
    private static String firstEntryOf(String location, String text) {
        String low = text.toLowerCase(Locale.ROOT);
        if (text.startsWith("[playlist]") || location.toLowerCase(Locale.ROOT).endsWith(".pls")) {
            Pattern p = Pattern.compile("(?im)^\\s*File\\d+\\s*=\\s*(.*?)\\s*$");
            Matcher m = p.matcher(text);
            if (m.find()) return m.group(1);
        } else if (low.contains("#extm3u") || location.toLowerCase(Locale.ROOT).endsWith(".m3u")) {
            for (String raw : text.split("\\r?\\n")) {
                String line = raw.trim();
                if (line.isEmpty() || line.startsWith("#")) continue;
                if (line.startsWith("http://") || line.startsWith("https://")
                        || line.startsWith("rtsp://") || line.startsWith("/")) {
                    return line;
                }
            }
        } else if (low.contains("<tracklist>") || low.contains("<playlist")
                || location.toLowerCase(Locale.ROOT).endsWith(".xspf")) {
            Matcher tm = Pattern.compile("<location>([\\s\\S]*?)</location>").matcher(text);
            if (tm.find()) {
                return tm.group(1).trim().replace("&amp;", "&").replace("&lt;", "<")
                        .replace("&gt;", ">").replace("&quot;", "\"").replace("&apos;", "'");
            }
        } else if (low.contains("<asx") || location.toLowerCase(Locale.ROOT).endsWith(".asx")) {
            Matcher rm = Pattern.compile("<ref\\s+href\\s*=\\s*\"([^\"]+)\"").matcher(text);
            if (rm.find()) return unescape(rm.group(1));
        }
        return null;
    }

    private static String unescape(String s) {
        return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", "\"").replace("&apos;", "'");
    }

    /** Resolve a relative media path against the playlist's base URL. */
    static String absolutize(String entry, String base) {
        if (entry.startsWith("http://") || entry.startsWith("https://")
                || entry.startsWith("rtsp://")) return entry;
        if (entry.startsWith("/")) {
            try {
                URL u = new URL(base);
                return new URL(u.getProtocol(), u.getHost(),
                        u.getPort() < 0 ? u.getDefaultPort() : 0,
                        entry).toExternalForm();
            } catch (Exception e) {
                return entry;
            }
        }
        try { return new URL(new URL(base), entry).toExternalForm(); }
        catch (Exception e) { return entry; }
    }

    private static String guessTitle(String url) {
        try {
            String path = new URL(url).getPath();
            String name = path.substring(path.lastIndexOf('/') + 1);
            if (name.isEmpty()) return null;
            return name.replaceAll("\\.[A-Za-z0-9]+$", "");
        } catch (Exception e) {
            return null;
        }
    }

    private static String fetchText(String location) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(location).openConnection();
        conn.setInstanceFollowRedirects(true);
        conn.setConnectTimeout(10000);
        conn.setReadTimeout(15000);
        conn.setRequestProperty("User-Agent", UA);
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) throw new IOException("HTTP " + code);
        InputStream in = code == 204 ? null : conn.getInputStream();
        try {
            if (in == null) return "";
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[16 * 1024];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            return new String(buf.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            try { if (in != null) in.close(); } catch (IOException ignored) {}
            conn.disconnect();
        }
    }
}

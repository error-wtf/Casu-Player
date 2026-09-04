// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/** YouTube over the public Innertube API — the same backend the Linux
 *  reference forces through yt-dlp (player_client=android). Search +
 *  player resolve, no auth, clear errors.
 *
 *  Client: ANDROID_VR (the latest unblocked client family — the plain
 *  ANDROID client was rotated out by YouTube in mid-2025). */
public final class YouTubeClient {

    public static final class Video {
        public String id;
        public String title;
        public String channel;
        public long durationSeconds;
        public String thumbnail;
    }

    public static final class YouTubeException extends Exception {
        public final String code;
        public YouTubeException(String code, String message) {
            super(message);
            this.code = code;
        }
    }

    private static final String UA =
            "com.google.android.apps.youtube.vr.oculus/1.60.19 (Linux; U; Android 14; Quest 3) gzip";

    private static final String CLIENT_NAME = "ANDROID_VR";
    private static final String CLIENT_VERSION = "1.60.19";

    private static JSONObject contextBody() {
        try {
            JSONObject ctx = new JSONObject();
            JSONObject client = new JSONObject();
            client.put("clientName", CLIENT_NAME);
            client.put("clientVersion", CLIENT_VERSION);
            client.put("androidSdkVersion", 34);
            client.put("hl", "de");
            client.put("gl", "DE");
            ctx.put("client", client);
            JSONObject body = new JSONObject();
            body.put("context", ctx);
            return body;
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    // ------------------------------------------------------------------ search

    public static List<Video> search(String query, int limit) throws YouTubeException {
        try {
            JSONObject body = contextBody();
            body.put("query", query);
            JSONObject response = post(
                    "https://www.youtube.com/youtubei/v1/search",
                    body);
            List<Video> out = new ArrayList<>();
            // Response structure varies by client. Walk ALL nested structures.
            walkAll(response, out, 0);
            if (out.isEmpty()) {
                throw new YouTubeException("resolver-changed",
                        "YouTube-Antwort enthielt keine Ergebnisse (veralteter Client oder leere Antwort)");
            }
            while (out.size() > limit) out.remove(out.size() - 1);
            return out;
        } catch (YouTubeException e) {
            throw e;
        } catch (Exception e) {
            throw new YouTubeException("network-offline",
                    "Suche fehlgeschlagen: " + rootMessage(e));
        }
    }

    /** Fetch the videos of a complete YouTube playlist (``list=`` id) via the
     *  Innertube browse endpoint. Returns a flat list of individual videos so
     *  a playlist expands into separate queue entries. */
    public static List<Video> fetchPlaylist(String playlistId) throws YouTubeException {
        if (playlistId == null || playlistId.isEmpty()) {
            throw new YouTubeException("invalid-playlist", "Keine Playlist-ID erkannt");
        }
        try {
            JSONObject body = contextBody();
            body.put("browseId", "VL" + playlistId);
            JSONObject response = post(
                    "https://www.youtube.com/youtubei/v1/browse",
                    body);
            List<Video> out = new ArrayList<>();
            walkAll(response, out, 0);
            if (out.isEmpty()) {
                throw new YouTubeException("resolver-changed",
                        "Playlist enthielt keine Videos (veralteter Client oder leere Playlist)");
            }
            return out;
        } catch (YouTubeException e) {
            throw e;
        } catch (Exception e) {
            throw new YouTubeException("network-offline",
                    "Playlist laden fehlgeschlagen: " + rootMessage(e));
        }
    }

    /** Extract the ``list=`` playlist id from a URL, or null when absent. */
    public static String extractPlaylistId(String input) {
        if (input == null) return null;
        try {
            java.net.URI uri = java.net.URI.create(input.trim());
            String query = uri.getQuery();
            if (query == null) return null;
            for (String pair : query.split("&")) {
                if (pair.startsWith("list=")) {
                    String id = pair.substring(5);
                    return id.isEmpty() ? null : id;
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    /** Recursively walk ANY JSON structure looking for videoRenderer nodes. */
    private static void walkAll(Object node, List<Video> out, int depth) {
        if (out.size() >= 40 || depth > 12 || node == null) return;
        if (node instanceof JSONObject) {
            JSONObject obj = (JSONObject) node;
            // Direct hit: videoRenderer
            if (obj.has("videoRenderer")) {
                Video v = parseVideoRenderer(obj.optJSONObject("videoRenderer"));
                if (v != null) out.add(v);
                return;
            }
            // Also check compactVideoRenderer (search results sometimes)
            if (obj.has("compactVideoRenderer")) {
                Video v = parseCompactVideoRenderer(obj.optJSONObject("compactVideoRenderer"));
                if (v != null) out.add(v);
                return;
            }
            // Playlist detail responses use playlistVideoRenderer
            if (obj.has("playlistVideoRenderer")) {
                Video v = parsePlaylistVideoRenderer(obj.optJSONObject("playlistVideoRenderer"));
                if (v != null) out.add(v);
                return;
            }
            // Walk every value in this object
            java.util.Iterator<String> keys = obj.keys();
            while (keys.hasNext()) {
                walkAll(obj.opt(keys.next()), out, depth + 1);
            }
        } else if (node instanceof JSONArray) {
            JSONArray arr = (JSONArray) node;
            for (int i = 0; i < arr.length() && out.size() < 40; i++) {
                walkAll(arr.opt(i), out, depth + 1);
            }
        }
    }

    private static Video parseVideoRenderer(JSONObject r) {
        if (r == null) return null;
        Video v = new Video();
        v.id = r.optString("videoId", "");
        if (v.id.isEmpty()) return null;
        JSONObject title = r.optJSONObject("title");
        v.title = title != null ? textRuns(title.optJSONArray("runs"))
                : r.optString("title", v.id);
        if (v.title.isEmpty()) v.title = v.id;
        JSONObject owner = r.optJSONObject("ownerText");
        if (owner == null) owner = r.optJSONObject("longBylineText");
        v.channel = owner != null ? textRuns(owner.optJSONArray("runs")) : null;
        JSONObject length = r.optJSONObject("lengthText");
        v.durationSeconds = parseDuration(length != null ? length.optString("simpleText", "") : "");
        if (v.durationSeconds == 0) {
            JSONObject d = r.optJSONObject("lengthText");
            if (d != null) {
                try { v.durationSeconds = Long.parseLong(d.optString("simpleText", "0")); } catch (Exception ignored) {}
            }
        }
        JSONArray thumbs = r.optJSONObject("thumbnail") != null
                ? r.optJSONObject("thumbnail").optJSONArray("thumbnails") : null;
        if (thumbs != null && thumbs.length() > 0) {
            v.thumbnail = thumbs.optJSONObject(thumbs.length() - 1).optString("url", "");
        }
        return v;
    }

    private static Video parseCompactVideoRenderer(JSONObject r) {
        if (r == null) return null;
        Video v = new Video();
        v.id = r.optString("videoId", "");
        if (v.id.isEmpty()) return null;
        // title can be JSONObject {simpleText:...} or {runs:[...]} or a plain string
        Object titleRaw = r.opt("title");
        if (titleRaw instanceof JSONObject) {
            JSONObject titleObj = (JSONObject) titleRaw;
            v.title = textRuns(titleObj.optJSONArray("runs"));
            if (v.title.isEmpty()) v.title = titleObj.optString("simpleText", v.id);
        } else if (titleRaw instanceof String) {
            v.title = (String) titleRaw;
        } else {
            v.title = r.optString("title", v.id);
        }
        if (v.title.isEmpty()) v.title = v.id;
        // channel: shortBylineText can be {simpleText:...} or {runs:[...]}
        Object channelRaw = r.opt("shortBylineText");
        if (channelRaw instanceof JSONObject) {
            v.channel = textRuns(((JSONObject) channelRaw).optJSONArray("runs"));
            if (v.channel.isEmpty()) v.channel = ((JSONObject) channelRaw).optString("simpleText", null);
        } else if (channelRaw instanceof String) {
            v.channel = (String) channelRaw;
        }
        JSONObject length = r.optJSONObject("lengthText");
        v.durationSeconds = parseDuration(length != null ? length.optString("simpleText", "") : "");
        JSONArray thumbs = r.optJSONObject("thumbnail") != null
                ? r.optJSONObject("thumbnail").optJSONArray("thumbnails") : null;
        if (thumbs != null && thumbs.length() > 0) {
            v.thumbnail = thumbs.optJSONObject(thumbs.length() - 1).optString("url", "");
        }
        return v;
    }

    private static Video parsePlaylistVideoRenderer(JSONObject r) {
        if (r == null) return null;
        Video v = new Video();
        v.id = r.optString("videoId", "");
        if (v.id.isEmpty()) return null;
        Object titleRaw = r.opt("title");
        if (titleRaw instanceof JSONObject) {
            JSONObject titleObj = (JSONObject) titleRaw;
            v.title = textRuns(titleObj.optJSONArray("runs"));
            if (v.title.isEmpty()) v.title = titleObj.optString("simpleText", v.id);
        } else if (titleRaw instanceof String) {
            v.title = (String) titleRaw;
        } else {
            v.title = r.optString("title", v.id);
        }
        if (v.title.isEmpty()) v.title = v.id;
        Object ownerRaw = r.opt("shortBylineText");
        if (ownerRaw instanceof JSONObject) {
            v.channel = textRuns(((JSONObject) ownerRaw).optJSONArray("runs"));
            if (v.channel.isEmpty()) v.channel = ((JSONObject) ownerRaw).optString("simpleText", null);
        } else if (ownerRaw instanceof String) {
            v.channel = (String) ownerRaw;
        }
        JSONObject length = r.optJSONObject("lengthText");
        v.durationSeconds = parseDuration(length != null
                ? length.optString("simpleText", "") : "");
        JSONArray thumbs = r.optJSONObject("thumbnail") != null
                ? r.optJSONObject("thumbnail").optJSONArray("thumbnails") : null;
        if (thumbs != null && thumbs.length() > 0) {
            v.thumbnail = thumbs.optJSONObject(thumbs.length() - 1).optString("url", "");
        }
        return v;
    }

    private static String textRuns(JSONArray runs) {
        if (runs == null) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < runs.length(); i++) {
            JSONObject run = runs.optJSONObject(i);
            if (run != null) sb.append(run.optString("text", ""));
        }
        return sb.toString();
    }

    // ------------------------------------------------------------------ resolve

    /** Returns a direct playable media URL for a video id/URL. */
    public static String resolveMediaUrl(String videoIdOrUrl) throws YouTubeException {
        String id = extractVideoId(videoIdOrUrl);
        if (id == null || id.isEmpty()) {
            throw new YouTubeException("invalid-url", "Keine YouTube-Video-ID erkannt (invalid-url)");
        }
        try {
            // ANDROID client returns direct URLs for player (ANDROID_VR gets
            // LOGIN_REQUIRED on the player endpoint despite working for search).
            JSONObject body = new JSONObject();
            JSONObject client = new JSONObject();
            client.put("clientName", "ANDROID");
            client.put("clientVersion", "20.03.04");
            client.put("androidSdkVersion", 34);
            client.put("hl", "de");
            client.put("gl", "DE");
            JSONObject ctx = new JSONObject();
            ctx.put("client", client);
            body.put("context", ctx);
            body.put("videoId", id);
            body.put("contentCheckOk", true);
            body.put("racyCheckOk", true);
            JSONObject response = post(
                    "https://www.youtube.com/youtubei/v1/player",
                    body, "com.google.android.youtube/20.03.04 (Linux; U; Android 14) gzip");
            String status = response.optJSONObject("playabilityStatus") != null
                    ? response.optJSONObject("playabilityStatus").optString("status", "") : "";
            if (!"OK".equals(status)) {
                String reason = response.optJSONObject("playabilityStatus") != null
                        ? response.optJSONObject("playabilityStatus").optString("reason", status)
                        : status;
                String code = "resolver-changed";
                if (reason != null && reason.toLowerCase().contains("sign in")) code = "auth-required";
                else if (reason != null && reason.toLowerCase().contains("age")) code = "auth-required";
                else if (reason != null && reason.toLowerCase().contains("not available")) code = "geo-blocked";
                throw new YouTubeException(code, "YouTube: " + reason + " (" + code + ")");
            }
            JSONObject streamingData = response.optJSONObject("streamingData");
            if (streamingData == null) {
                throw new YouTubeException("resolver-changed", "YouTube: keine streamingData (resolver-changed)");
            }
            // Progressive formats (video+audio combined) — the ANDROID/ANDROID_VR
            // client always returns direct URLs (no cipher/signature).
            JSONArray formats = streamingData.optJSONArray("formats");
            String best = null;
            long bestPixels = -1;
            if (formats != null) {
                for (int i = 0; i < formats.length(); i++) {
                    JSONObject format = formats.optJSONObject(i);
                    if (format == null) continue;
                    String url = format.optString("url", "");
                    if (url.isEmpty()) continue;
                    String mime = format.optString("mimeType", "");
                    if (!mime.startsWith("video/mp4") && !mime.startsWith("video/webm")) continue;
                    long width = format.optLong("width", 0);
                    long height = format.optLong("height", 0);
                    long pixels = width * height;
                    if (pixels > bestPixels) {
                        bestPixels = pixels;
                        best = url;
                    }
                }
            }
            if (best == null) {
                // Fallback: adaptive audio-only so radio-style playback works.
                JSONArray adaptive = streamingData.optJSONArray("adaptiveFormats");
                if (adaptive != null) {
                    long bestBitrate = -1;
                    for (int i = 0; i < adaptive.length(); i++) {
                        JSONObject format = adaptive.optJSONObject(i);
                        if (format == null) continue;
                        String mime = format.optString("mimeType", "");
                        if (!mime.startsWith("audio/")) continue;
                        String url = format.optString("url", "");
                        long bitrate = format.optLong("bitrate", 0);
                        if (!url.isEmpty() && bitrate > bestBitrate) {
                            bestBitrate = bitrate;
                            best = url;
                        }
                    }
                }
            }
            if (best == null) {
                throw new YouTubeException("resolver-changed",
                        "YouTube: keine abspielbaren Formate (resolver-changed)");
            }
            return best;
        } catch (YouTubeException e) {
            throw e;
        } catch (Exception e) {
            throw new YouTubeException("network-offline",
                    "Resolve fehlgeschlagen: " + rootMessage(e));
        }
    }

    public static String extractVideoId(String input) {
        if (input == null) return null;
        String value = input.trim();
        if (value.matches("[\\w-]{11}")) return value;
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("(?:v=|youtu\\.be/|/shorts/|/embed/)([\\w-]{11})")
                .matcher(value);
        return m.find() ? m.group(1) : null;
    }

    // ------------------------------------------------------------------ http

    private static JSONObject post(String url, JSONObject body) throws Exception {
        return post(url, body, UA);
    }

    private static JSONObject post(String url, JSONObject body, String userAgent) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(25000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("User-Agent", userAgent);
        byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
        try (java.io.OutputStream out = conn.getOutputStream()) {
            out.write(payload);
        }
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            byte[] errBody = readStream(conn.getErrorStream());
            throw new Exception("HTTP " + code + ": " + new String(errBody, StandardCharsets.UTF_8).substring(0, Math.min(200, errBody.length)));
        }
        byte[] respBody = readStream(conn.getInputStream());
        return new JSONObject(new String(respBody, StandardCharsets.UTF_8));
    }

    private static byte[] readStream(InputStream in) throws Exception {
        if (in == null) return new byte[0];
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        byte[] chunk = new byte[16 * 1024];
        int n;
        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
        return buf.toByteArray();
    }

    private static long parseDuration(String text) {
        if (text == null || text.isEmpty()) return 0;
        long total = 0;
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("(?:(\\d+):)?(\\d+):(\\d+)").matcher(text);
        if (m.matches()) {
            if (m.group(1) != null) total += Long.parseLong(m.group(1)) * 3600;
            total += Long.parseLong(m.group(2)) * 60;
            total += Long.parseLong(m.group(3));
        }
        return total;
    }

    private static String rootMessage(Throwable t) {
        String msg = t.getMessage() == null ? t.getClass().getSimpleName() : t.getMessage();
        if (msg.contains("Unable to resolve") || msg.contains("UnknownHost")) return "Netzwerk nicht erreichbar (network-offline)";
        if (msg.toLowerCase().contains("timeout")) return "Zeitüberschreitung (timeout)";
        return msg;
    }
}

// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import org.json.JSONObject;

/** One queue entry. URLs may be file paths, content://, http(s)/rtsp URLs
 *  or resolved YouTube media URLs; kind drives badge + stage decisions. */
public final class MediaItem {
    public String url;          // playable source (path/URI)
    public String title;        // display title
    public String kind;         // audio|video|stream|youtube|casu|mp5|playlist
    public String badge;        // short badge text (MP3/STREAM/YT/CASU/…)
    public String artist;       // optional metadata
    public String playlist;     // playlist group name when part of a loaded playlist
    public String subtitle;     // optional external subtitle path/uri
    public long addedAt;

    public MediaItem() {}

    public MediaItem(String url, String title, String kind, String badge) {
        this.url = url;
        this.title = title == null || title.isEmpty() ? fallbackTitle(url) : title;
        this.kind = kind == null ? "audio" : kind;
        this.badge = badge == null || badge.isEmpty() ? defaultBadge(url, this.kind) : badge;
        this.addedAt = System.currentTimeMillis();
    }

    public static String fallbackTitle(String url) {
        if (url == null) return "Media";
        int slash = Math.max(url.lastIndexOf('/'), url.lastIndexOf('\\'));
        String name = slash >= 0 ? url.substring(slash + 1) : url;
        int query = name.indexOf('?');
        if (query > 0) name = name.substring(0, query);
        try { name = java.net.URLDecoder.decode(name, "UTF-8"); } catch (Exception ignored) {}
        return name.isEmpty() ? url : name;
    }

    public static String defaultBadge(String url, String kind) {
        String low = url == null ? "" : url.toLowerCase();
        if ("youtube".equals(kind)) return "YT";
        if ("casu".equals(kind)) return "CASU";
        if ("mp5".equals(kind)) return "MP5";
        if ("stream".equals(kind) || low.startsWith("http") || low.startsWith("rtsp")) return "STREAM";
        int dot = low.lastIndexOf('.');
        String ext = dot >= 0 ? low.substring(dot + 1) : "";
        if (ext.isEmpty()) return "MEDIA";
        return ext.toUpperCase();
    }

    public boolean isVideo() {
        return "video".equals(kind);
    }

    public boolean isLocalFile() {
        return url != null && !url.startsWith("http://") && !url.startsWith("https://")
                && !url.startsWith("rtsp://") && !url.startsWith("content://");
    }

    public JSONObject toJson() {
        JSONObject o = new JSONObject();
        try {
            o.putOpt("url", url);
            o.putOpt("title", title);
            o.putOpt("kind", kind);
            o.putOpt("badge", badge);
            o.putOpt("artist", artist);
            o.putOpt("playlist", playlist);
            o.putOpt("subtitle", subtitle);
            o.putOpt("addedAt", addedAt);
        } catch (Exception ignored) {}
        return o;
    }

    public static MediaItem fromJson(JSONObject o) {
        if (o == null) return null;
        String url = o.optString("url", "");
        if (url == null || url.isEmpty()) return null;
        MediaItem item = new MediaItem();
        item.url = url;
        item.title = o.optString("title", fallbackTitle(url));
        item.kind = o.optString("kind", "audio");
        item.badge = o.optString("badge", defaultBadge(url, item.kind));
        item.artist = o.optString("artist", null);
        item.playlist = o.optString("playlist", null);
        item.subtitle = o.optString("subtitle", null);
        item.addedAt = o.optLong("addedAt", System.currentTimeMillis());
        return item;
    }
}

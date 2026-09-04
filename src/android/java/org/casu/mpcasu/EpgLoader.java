// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** IPTV channel list (Extended M3U with group-title/tvg-id) + minimal
 *  XMLTV guide lookup for "now" — the Android twin of the EPG page. */
public final class EpgLoader {

    public static final class Channel {
        public String name;
        public String url;
        public String group;
        public String tvgId;
        public String logo;
    }

    public static final class Programme {
        public String channel;
        public String title;
        public long startMs;
        public long stopMs;
        public boolean current;
    }

    public static List<Channel> parseM3u(String text) {
        List<Channel> out = new ArrayList<>();
        Channel pending = null;
        Pattern attr = Pattern.compile("(\\w[\\w-]*)=\"([^\"]*)\"");
        for (String raw : text.split("\\r?\\n")) {
            String line = raw.trim();
            if (line.isEmpty()) continue;
            if (line.startsWith("#EXTINF:")) {
                pending = new Channel();
                int comma = line.lastIndexOf(',');
                if (comma >= 0 && comma + 1 < line.length()) {
                    pending.name = line.substring(comma + 1).trim();
                }
                Matcher attrs = attr.matcher(line);
                while (attrs.find()) {
                    String key = attrs.group(1);
                    String value = attrs.group(2);
                    if ("tvg-id".equals(key)) pending.tvgId = value;
                    else if ("tvg-logo".equals(key)) pending.logo = value;
                    else if ("group-title".equals(key)) pending.group = value;
                }
                continue;
            }
            if (line.startsWith("#")) continue;
            Channel channel = pending != null ? pending : new Channel();
            channel.url = line;
            if (channel.name == null || channel.name.isEmpty()) {
                channel.name = MediaItem.fallbackTitle(line);
            }
            out.add(channel);
            pending = null;
        }
        return out;
    }

    /** Minimal XMLTV: programme start/stop/title per channel. */
    public static List<Programme> parseXmltv(String text) {
        return parseXmltv(text, System.currentTimeMillis());
    }

    static List<Programme> parseXmltv(String text, long nowMs) {
        List<Programme> out = new ArrayList<>();
        Pattern programme = Pattern.compile(
                "<programme\\s+[^>]*channel=\"([^\"]*)\"[^>]*start=\"(\\d{14})[\\s\\S]*?stop=\"(\\d{14})[\\s\\S]*?>"
                + "[\\s\\S]*?<title[^>]*>([\\s\\S]*?)</title>");
        DateTimeFormatter fmt = DateTimeFormatter.ofPattern("yyyyMMddHHmmss");
        Matcher m = programme.matcher(text);
        while (m.find() && out.size() < 5000) {
            Programme programme1 = new Programme();
            programme1.channel = m.group(1);
            programme1.title = m.group(4).replaceAll("<[^>]+>", "").trim();
            try {
                programme1.startMs = LocalDateTime.parse(m.group(2), fmt)
                        .toInstant(java.time.ZoneOffset.UTC).toEpochMilli();
                programme1.stopMs = LocalDateTime.parse(m.group(3), fmt)
                        .toInstant(java.time.ZoneOffset.UTC).toEpochMilli();
                programme1.current = nowMs >= programme1.startMs && nowMs < programme1.stopMs;
            } catch (Exception ignored) {
                continue;
            }
            out.add(programme1);
        }
        return out;
    }

    public static String fetchText(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(10000);
        conn.setReadTimeout(20000);
        conn.setRequestProperty("User-Agent", "MPCASU/7.0");
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) throw new Exception("HTTP " + code);
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

    public static String nowFor(List<Programme> guide, Channel channel) {
        if (guide == null || channel == null) return "";
        for (Programme programme : guide) {
            if (programme.current
                    && (programme.channel.equals(channel.tvgId)
                        || programme.channel.equals(channel.name))) {
                return programme.title;
            }
        }
        return "";
    }
}

// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import java.io.BufferedReader;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** SRT + WebVTT subtitle parsing and cue lookup (Android twin of the
 *  external-subtitle feature of the Linux reference). */
public final class SubtitleLoader {

    public static final class Cue {
        public final long startMs;
        public final long endMs;
        public final String text;
        public Cue(long startMs, long endMs, String text) {
            this.startMs = startMs;
            this.endMs = endMs;
            this.text = text;
        }
    }

    private final List<Cue> cues = new ArrayList<>();
    private long offsetMs = 0; // subtitle delay (positive = later)

    public static SubtitleLoader load(String path) throws Exception {
        try (InputStream in = new FileInputStream(path)) {
            return load(in);
        }
    }

    public static SubtitleLoader load(InputStream in) throws Exception {
        SubtitleLoader loader = new SubtitleLoader();
        StringBuilder sb = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8))) {
            char[] chunk = new char[8192];
            int n;
            while ((n = reader.read(chunk)) > 0) sb.append(chunk, 0, n);
        }
        String text = sb.toString().replace("\uFEFF", "");
        if (text.startsWith("WEBVTT")) {
            loader.parseVtt(text);
        } else {
            loader.parseSrt(text);
        }
        return loader;
    }

    private void parseSrt(String text) {
        Pattern timing = Pattern.compile(
                "(\\d{2}):(\\d{2}):(\\d{2})[,.](\\d{1,3})\\s*-->\\s*(\\d{2}):(\\d{2}):(\\d{2})[,.](\\d{1,3})");
        String[] blocks = text.split("\\r?\\n\\r?\\n");
        for (String block : blocks) {
            String[] lines = block.split("\\r?\\n");
            for (int i = 0; i < lines.length; i++) {
                Matcher m = timing.matcher(lines[i]);
                if (!m.find()) continue;
                long start = timecode(m.group(1), m.group(2), m.group(3), m.group(4));
                long end = timecode(m.group(5), m.group(6), m.group(7), m.group(8));
                StringBuilder body = new StringBuilder();
                for (int j = i + 1; j < lines.length; j++) {
                    if (lines[j].trim().isEmpty()) break;
                    if (body.length() > 0) body.append('\n');
                    body.append(stripTags(lines[j].trim()));
                }
                if (body.length() > 0) cues.add(new Cue(start, end, body.toString()));
                break;
            }
        }
    }

    private void parseVtt(String text) {
        Pattern timing = Pattern.compile(
                "(\\d{2}):(\\d{2}):(\\d{2})[.,](\\d{1,3})\\s*-->\\s*(\\d{2}):(\\d{2}):(\\d{2})[.,](\\d{1,3})");
        String[] blocks = text.split("\\r?\\n\\r?\\n");
        for (String block : blocks) {
            String[] lines = block.split("\\r?\\n");
            for (int i = 0; i < lines.length; i++) {
                if (lines[i].trim().startsWith("WEBVTT")) continue;
                Matcher m = timing.matcher(lines[i]);
                if (!m.find()) continue;
                long start = timecode(m.group(1), m.group(2), m.group(3), m.group(4));
                long end = timecode(m.group(5), m.group(6), m.group(7), m.group(8));
                StringBuilder body = new StringBuilder();
                for (int j = i + 1; j < lines.length; j++) {
                    if (lines[j].trim().isEmpty()) break;
                    if (body.length() > 0) body.append('\n');
                    body.append(stripTags(lines[j].trim()));
                }
                if (body.length() > 0) cues.add(new Cue(start, end, body.toString()));
                break;
            }
        }
    }

    public String cueAt(long positionMs) {
        long pos = positionMs - offsetMs;
        for (Cue cue : cues) {
            if (pos >= cue.startMs && pos <= cue.endMs) return cue.text;
            if (cue.startMs > pos) break;
        }
        return null;
    }

    public int count() {
        return cues.size();
    }

    public void setOffsetMs(long offsetMs) {
        this.offsetMs = offsetMs;
    }

    public long getOffsetMs() {
        return offsetMs;
    }

    private static long timecode(String h, String m, String s, String ms) {
        return Long.parseLong(h) * 3600000L + Long.parseLong(m) * 60000L
                + Long.parseLong(s) * 1000L + Long.parseLong(pad3(ms));
    }

    private static String pad3(String value) {
        if (value.length() >= 3) return value.substring(0, 3);
        StringBuilder sb = new StringBuilder(value);
        while (sb.length() < 3) sb.append('0');
        return sb.toString();
    }

    private static String stripTags(String line) {
        return line.replaceAll("</?[^>]+>", "");
    }
}

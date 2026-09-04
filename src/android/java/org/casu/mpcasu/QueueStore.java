// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/** Atomic JSON persistence for the queue + engine state. Lives in the
 *  SERVICE process context so the queue survives Activity death, force
 *  stops of the UI, and app restarts — the old APK's queue-reset defect. */
public final class QueueStore {
    private static final String TAG = "MPCASU-Queue";
    private static final String FILE = "queue.json";
    private static final int MAX_ITEMS = 500;

    public static final class Saved {
        public final List<MediaItem> items;
        public final int index;
        public final long positionMs;
        public final boolean playing;
        public final boolean shuffle;
        public final String repeat; // off|all|one

        Saved(List<MediaItem> items, int index, long positionMs, boolean playing,
              boolean shuffle, String repeat) {
            this.items = items;
            this.index = index;
            this.positionMs = positionMs;
            this.playing = playing;
            this.shuffle = shuffle;
            this.repeat = repeat;
        }
    }

    private final File file;

    public QueueStore(Context context) {
        this.file = new File(context.getFilesDir(), FILE);
    }

    /** Atomic write: temp file then rename. Never throws. */
    public void save(List<MediaItem> items, int index, long positionMs,
                     boolean playing, boolean shuffle, String repeat) {
        try {
            JSONObject root = new JSONObject();
            root.put("version", 2);
            JSONArray array = new JSONArray();
            int count = Math.min(items == null ? 0 : items.size(), MAX_ITEMS);
            for (int i = 0; i < count; i++) {
                MediaItem item = items.get(i);
                if (item != null && item.url != null && !item.url.startsWith("blob:")) {
                    array.put(item.toJson());
                }
            }
            root.put("items", array);
            root.put("index", index);
            root.put("positionMs", Math.max(0, positionMs));
            root.put("playing", playing);
            root.put("shuffle", shuffle);
            root.put("repeat", repeat == null ? "off" : repeat);
            File tmp = new File(file.getParentFile(), FILE + ".tmp");
            FileOutputStream out = new FileOutputStream(tmp);
            out.write(root.toString().getBytes(StandardCharsets.UTF_8));
            out.flush();
            out.getFD().sync();
            out.close();
            if (!tmp.renameTo(file)) {
                // rename can fail across filesystem quirks; fall back to copy
                copy(tmp, file);
                tmp.delete();
            }
        } catch (Exception exc) {
            Log.w(TAG, "save failed (playback continues)", exc);
        }
    }

    /** Read the saved state; null when nothing valid exists. Never throws. */
    public Saved load() {
        try (InputStream in = new FileInputStream(file)) {
            byte[] buf = readAll(in);
            JSONObject root = new JSONObject(new String(buf, StandardCharsets.UTF_8));
            if (root.optInt("version", 0) < 1) return null;
            JSONArray array = root.optJSONArray("items");
            List<MediaItem> items = new ArrayList<>();
            if (array != null) {
                for (int i = 0; i < array.length() && items.size() < MAX_ITEMS; i++) {
                    MediaItem item = MediaItem.fromJson(array.optJSONObject(i));
                    if (item != null) items.add(item);
                }
            }
            int index = root.optInt("index", -1);
            if (index >= items.size()) index = items.isEmpty() ? -1 : items.size() - 1;
            String repeat = root.optString("repeat", "off");
            if (!"all".equals(repeat) && !"one".equals(repeat)) repeat = "off";
            return new Saved(items, index, Math.max(0, root.optLong("positionMs", 0)),
                    root.optBoolean("playing", false), root.optBoolean("shuffle", false),
                    repeat);
        } catch (Exception exc) {
            Log.i(TAG, "load: no valid queue (" + exc.getMessage() + ")");
            return null;
        }
    }

    private static byte[] readAll(InputStream in) throws Exception {
        java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
        byte[] chunk = new byte[16 * 1024];
        int n;
        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
        return buf.toByteArray();
    }

    private static void copy(File src, File dst) throws Exception {
        try (FileInputStream in = new FileInputStream(src);
             FileOutputStream out = new FileOutputStream(dst)) {
            byte[] chunk = new byte[16 * 1024];
            int n;
            while ((n = in.read(chunk)) > 0) out.write(chunk, 0, n);
        }
    }
}

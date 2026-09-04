// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;

import java.io.File;

/**
 * Records a source (any stream / file / playlist entry) to a playable file —
 * the Android twin of the Linux/Windows ffmpeg recorder.
 *
 * Encoding runs through the NDK MediaCodec API (the Android libVLC firmware
 * ships without its transcode/sout mux modules):
 *  - MP4: video AND audio are recorded, the video picture is never switched
 *         off (AAC audio + H.264 video browser into an .mp4 container).
 *  - M4A: audio-only AAC output.
 *  - MP3/OGG: deliberately not advertised because Android does not provide a
 *    portable platform encoder for either format.
 *  - COPY: byte-exact stream copy for progressive radio/TS streams.
 *
 * The recorder owns a HandlerThread (its media work is done off the UI thread).
 */
public final class StreamRecorder {

    static {
        System.err.println("StreamRecorder class loaded");
        android.util.Log.i("StreamRecorder", "StreamRecorder class loaded");
    }

    public interface Listener {
        void onStarted(String info);
        void onProgress(long seconds, long bytes);
        void onFinished(String fileName, long bytes, String error);
    }

    public static final String FMT_MP4 = "mp4";   // video + audio (transcode)
    public static final String FMT_M4A = "m4a";   // audio only (MP4 container)
    public static final String FMT_OGG = "ogg";   // audio only
    public static final String FMT_MP3 = "mp3";   // audio only
    public static final String FMT_COPY = "copy"; // raw stream copy

    /** Output extensions per format. */
    public static String extensionFor(String format, String sourceUrl) {
        switch (format) {
            case FMT_MP4: return "mp4";
            case FMT_M4A: return "m4a";
            case FMT_OGG: return "ogg";
            case FMT_MP3: return "mp3";
            default: {
                // COPY keeps the source container: guess from URL/content.
                String u = sourceUrl == null ? "" : sourceUrl.toLowerCase();
                if (u.contains(".mp3")) return "mp3";
                if (u.contains(".aac")) return "aac";
                if (u.contains(".ogg")) return "ogg";
                if (u.contains(".mp4")) return "mp4";
                if (u.contains(".m4a")) return "m4a";
                if (u.contains(".flac")) return "flac";
                return "ts";
            }
        }
    }

    /** Whether the running Android version supports the muxer format. */
    public static boolean formatSupported(String format) {
        switch (format) {
            case FMT_MP4:
            case FMT_M4A:
            case FMT_COPY:
                return true;
            case FMT_MP3:
            case FMT_OGG:
                return false;
            default:
                return false;
        }
    }

    private final android.content.Context context;
    private final String sourceUrl;
    private final File destination;
    private final String format;
    private final Listener listener;
    private volatile boolean stopped = false;
    private HandlerThread thread;
    private volatile MediaTranscoder activeTranscoder;
    private long totalBytes;
    private long startedAtMs;

    public StreamRecorder(android.content.Context context, String sourceUrl,
                          File destination, String format, Listener listener) {
        this.context = context.getApplicationContext();
        // Use exactly the same canonical source as playback. Recording still
        // starts only when the user presses the existing record button.
        this.sourceUrl = PlaylistIO.normalizePlayableLocation(sourceUrl);
        this.destination = destination;
        this.format = format;
        this.listener = listener;
    }

    public void start() {
        thread = new HandlerThread("StreamRecorder");
        thread.start();
        new Handler(thread.getLooper()).post(this::run);
    }

    public void stop() {
        System.err.println("StreamRecorder stop() called, activeTranscoder=" + activeTranscoder + " thread=" + Thread.currentThread().getName());
        android.util.Log.i("StreamRecorder", "stop() called, activeTranscoder=" + activeTranscoder + " thread=" + Thread.currentThread().getName());
        stopped = true;
        if (activeTranscoder != null) {
            System.err.println("StreamRecorder calling activeTranscoder.cancel()");
            android.util.Log.i("StreamRecorder", "calling activeTranscoder.cancel()");
            activeTranscoder.cancel();
        }
    }

    public boolean isStopped() {
        return stopped;
    }

    private void run() {
        System.err.println("StreamRecorder run() starting on thread=" + Thread.currentThread().getName());
        android.util.Log.i("StreamRecorder", "run() starting on thread=" + Thread.currentThread().getName());
        String error = null;
        String finalName = destination != null ? destination.getName()
                : "recording.mp4";
        try {
            if (FMT_COPY.equals(format)) {
                finalName = runCopy(extensionFor(FMT_COPY, sourceUrl));
            } else {
                if (!formatSupported(format)) {
                    throw new IllegalStateException(
                            "Format " + format + " nicht unterstützt");
                }
                finalName = runTranscode();
            }
        } catch (Exception e) {
            error = e.getMessage() == null ? e.getClass().getSimpleName()
                    : e.getMessage();
            android.util.Log.e("StreamRecorder", "recording failed", e);
        }
        final String name = finalName;
        final String err = error;
        final long bytes = totalBytes;
        notifyFinished(name, bytes, err);
        if (thread != null) {
            thread.quitSafely();
            thread = null;
        }
    }

    // ------------------------------------------------ MediaCodec transcode

    /**
     * Transcode the source to MP4/M4A via Android MediaCodec.
     * The Android libVLC firmware ships without its transcode/sout mux
     * modules, so we encode natively (parity with the ffmpeg reference).
     */
    private String runTranscode() throws Exception {
        if (destination == null) throw new IllegalStateException("Ziel unbekannt");
        if (destination.getParentFile() != null) destination.getParentFile().mkdirs();

        startedAtMs = System.currentTimeMillis();
        notifyStarted("Aufnahme gestartet · " + format.toUpperCase(java.util.Locale.US));

        MediaTranscoder transcoder = new MediaTranscoder(
                sourceUrl, destination, format,
                (seconds, bytes) -> {
                    totalBytes = bytes;
                    notifyProgress(seconds, bytes);
                });
        activeTranscoder = transcoder;
        System.err.println("StreamRecorder activeTranscoder set, starting transcode");
        android.util.Log.i("StreamRecorder", "activeTranscoder set, starting transcode");

        try {
            String err = transcoder.transcode();
            if (err != null && !"Abgebrochen".equals(err)) {
                if (destination.exists()) destination.delete();
                throw new IllegalStateException(err);
            }
            if (err != null && "Abgebrochen".equals(err)) {
                if (destination.exists() && destination.length() == 0) destination.delete();
                return destination.getName();
            }
            totalBytes = destination != null && destination.exists()
                    ? destination.length() : 0;
            return destination.getName();
        } finally {
            activeTranscoder = null;
        }
    }

    // ------------------------------------------------------------- raw copy

    private String runCopy(String ext) throws Exception {
        File target = destination != null ? destination
                : new File(context.getExternalFilesDir(
                        android.os.Environment.DIRECTORY_MUSIC), "rec.mp3");
        totalBytes = 0;
        startedAtMs = System.currentTimeMillis();

        if (sourceUrl.startsWith("http://") || sourceUrl.startsWith("https://")) {
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection)
                    new java.net.URL(sourceUrl).openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(15000);
            conn.setRequestProperty("User-Agent", "MPCASU/5.0 (Android; radio)");
            conn.setInstanceFollowRedirects(true);
            int code = conn.getResponseCode();
            if (code < 200 || code >= 300) throw new IllegalStateException("HTTP " + code);
            target = targetForContentType(target, conn.getContentType());
            try (java.io.InputStream in = conn.getInputStream();
                 java.io.OutputStream out = new java.io.FileOutputStream(target)) {
                byte[] chunk = new byte[64 * 1024];
                long lastReport = 0;
                int n;
                while (!stopped && (n = in.read(chunk)) > 0) {
                    out.write(chunk, 0, n);
                    totalBytes += n;
                    if (System.currentTimeMillis() - lastReport > 1000) {
                        lastReport = System.currentTimeMillis();
                        notifyProgress((System.currentTimeMillis() - startedAtMs) / 1000L, totalBytes);
                    }
                }
            } finally { conn.disconnect(); }
        } else {
            try (java.io.InputStream in = new java.io.FileInputStream(sourceUrl);
                 java.io.OutputStream out = new java.io.FileOutputStream(target)) {
                byte[] chunk = new byte[64 * 1024];
                int n;
                while (!stopped && (n = in.read(chunk)) > 0) {
                    out.write(chunk, 0, n);
                    totalBytes += n;
                }
            }
        }
        if (totalBytes == 0) {
            target.delete();
            throw new IllegalStateException("Keine Daten empfangen");
        }
        return target.getName();
    }

    private static File targetForContentType(File target, String contentType) {
        if (target == null || !target.getName().toLowerCase(java.util.Locale.ROOT).endsWith(".ts")) {
            return target;
        }
        String type = contentType == null ? "" : contentType.toLowerCase(java.util.Locale.ROOT);
        String extension = null;
        if (type.contains("audio/mpeg") || type.contains("audio/mp3")) extension = "mp3";
        else if (type.contains("audio/aac") || type.contains("audio/aacp")) extension = "aac";
        else if (type.contains("audio/ogg") || type.contains("application/ogg")) extension = "ogg";
        if (extension == null) return target;
        String name = target.getName();
        return new File(target.getParentFile(), name.substring(0, name.length() - 2) + extension);
    }

    // -------------------------------------------------------------- notify

    private void notifyStarted(String info) {
        if (listener == null) return;
        listener.onStarted(info);
    }

    private void notifyProgress(long seconds, long bytes) {
        if (listener == null) return;
        listener.onProgress(seconds, bytes);
    }

    private void notifyFinished(String name, long bytes, String error) {
        if (listener == null) return;
        listener.onFinished(name, bytes, error);
    }
}

// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMuxer;

import java.io.File;
import java.nio.ByteBuffer;
import java.util.HashMap;
import java.util.Map;

/** Reliable, single-threaded Android recorder backend. */
public final class MediaTranscoder {
    public interface Listener { void onProgress(long seconds, long bytes); }

    private static final long POLL_US = 20_000L;
    private final String inputUri;
    private final File output;
    private final String format;
    private final Listener listener;
    private volatile boolean cancelled;
    private long totalBytes;

    public MediaTranscoder(String inputUri, File output, String format,
                           Listener listener) {
        this.inputUri = inputUri;
        this.output = output;
        this.format = format;
        this.listener = listener;
    }

    public void cancel() { cancelled = true; }

    /** Cancellation is success: the current container is finalized and kept. */
    public String transcode() {
        File parent = output.getParentFile();
        if (parent != null) parent.mkdirs();
        try {
            if (StreamRecorder.FMT_MP3.equals(format))
                return "MP3-Transcoding ist auf diesem Android-Gerät nicht verfügbar";
            if (StreamRecorder.FMT_OGG.equals(format))
                return "OGG-Transcoding ist auf diesem Android-Gerät nicht verfügbar";

            MediaExtractor probe = openExtractor();
            boolean hasVideo = findTrack(probe, "video/") >= 0;
            probe.release();
            if (StreamRecorder.FMT_MP4.equals(format) && hasVideo) remuxVideo();
            else transcodeAudioToAac();
            if (totalBytes <= 0 || output.length() <= 0) return "Keine Daten empfangen";
            return null;
        } catch (Exception e) {
            android.util.Log.e("MediaTranscoder", "recording failed", e);
            return e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        }
    }

    /** Preserve encoded video and audio. One thread owns the muxer lifecycle. */
    private void remuxVideo() throws Exception {
        MediaExtractor ex = openExtractor();
        MediaMuxer mux = new MediaMuxer(output.getAbsolutePath(),
                MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        Map<Integer, Integer> tracks = new HashMap<>();
        boolean started = false;
        try {
            for (int i = 0; i < ex.getTrackCount(); i++) {
                MediaFormat f = ex.getTrackFormat(i);
                String mime = f.getString(MediaFormat.KEY_MIME);
                if (mime != null && (mime.startsWith("audio/") || mime.startsWith("video/"))) {
                    tracks.put(i, mux.addTrack(f));
                    ex.selectTrack(i);
                }
            }
            if (!containsKind(ex, tracks, "video/"))
                throw new IllegalStateException("Keine Videospur");
            mux.start();
            started = true;
            ByteBuffer data = ByteBuffer.allocateDirect(4 * 1024 * 1024);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            long lastReport = 0;
            while (!cancelled) {
                int sourceTrack = ex.getSampleTrackIndex();
                if (sourceTrack < 0) break;
                data.clear();
                int size = ex.readSampleData(data, 0);
                if (size < 0) break;
                Integer targetTrack = tracks.get(sourceTrack);
                if (targetTrack != null) {
                    info.set(0, size, Math.max(0, ex.getSampleTime()), ex.getSampleFlags());
                    mux.writeSampleData(targetTrack, data, info);
                    totalBytes += size;
                    lastReport = report(lastReport, info.presentationTimeUs / 1_000_000L);
                }
                ex.advance();
            }
            mux.stop();
            started = false;
        } finally {
            if (started) try { mux.stop(); } catch (Exception ignored) { }
            try { mux.release(); } catch (Exception ignored) { }
            ex.release();
        }
    }

    /** Decode source audio to PCM and encode AAC. A stop request queues decoder
     * EOS, drains it, queues encoder EOS, drains it, then finalizes MediaMuxer. */
    private void transcodeAudioToAac() throws Exception {
        MediaExtractor ex = openExtractor();
        MediaCodec decoder = null;
        MediaCodec encoder = null;
        MediaMuxer mux = null;
        boolean muxStarted = false;
        try {
            int audio = findTrack(ex, "audio/");
            if (audio < 0) throw new IllegalStateException("Keine Audiospur");
            ex.selectTrack(audio);
            MediaFormat input = ex.getTrackFormat(audio);
            decoder = MediaCodec.createDecoderByType(input.getString(MediaFormat.KEY_MIME));
            decoder.configure(input, null, null, 0);
            decoder.start();

            MediaFormat aac = MediaFormat.createAudioFormat(
                    MediaFormat.MIMETYPE_AUDIO_AAC,
                    integer(input, MediaFormat.KEY_SAMPLE_RATE, 44100),
                    Math.min(2, integer(input, MediaFormat.KEY_CHANNEL_COUNT, 2)));
            aac.setInteger(MediaFormat.KEY_AAC_PROFILE,
                    MediaCodecInfo.CodecProfileLevel.AACObjectLC);
            aac.setInteger(MediaFormat.KEY_BIT_RATE, 160_000);
            aac.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 64 * 1024);
            encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC);
            encoder.configure(aac, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            encoder.start();

            mux = new MediaMuxer(output.getAbsolutePath(),
                    MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            MediaCodec.BufferInfo decoded = new MediaCodec.BufferInfo();
            MediaCodec.BufferInfo encoded = new MediaCodec.BufferInfo();
            boolean inputEos = false, decoderEos = false, encoderEosQueued = false;
            boolean encoderEos = false;
            int pendingDecoded = -1;
            int muxTrack = -1;
            long lastPts = 0, lastReport = 0;

            long cancelDeadline = Long.MAX_VALUE;
            while (!encoderEos) {
                if (cancelled && cancelDeadline == Long.MAX_VALUE)
                    cancelDeadline = System.nanoTime() + 2_000_000_000L;
                // A live extractor/decoder is allowed to ignore EOS.  Never
                // leave the recorder thread hanging forever after stop().
                if (cancelled && System.nanoTime() >= cancelDeadline) break;
                if (!inputEos) {
                    int index = decoder.dequeueInputBuffer(POLL_US);
                    if (index >= 0) {
                        ByteBuffer in = decoder.getInputBuffer(index);
                        int size = cancelled ? -1 : ex.readSampleData(in, 0);
                        long pts = size < 0 ? lastPts : Math.max(0, ex.getSampleTime());
                        if (size < 0) {
                            decoder.queueInputBuffer(index, 0, 0, pts,
                                    MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputEos = true;
                        } else {
                            decoder.queueInputBuffer(index, 0, size, pts, 0);
                            lastPts = pts;
                            ex.advance();
                        }
                    }
                }

                // Drain output on every iteration. MediaCodec input buffers
                // form a bounded queue; without this, normal live audio can
                // be mistaken for a permanently blocked encoder.
                int out = encoder.dequeueOutputBuffer(encoded, POLL_US);
                if (out == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    muxTrack = mux.addTrack(encoder.getOutputFormat());
                    mux.start();
                    muxStarted = true;
                } else if (out >= 0) {
                    ByteBuffer data = encoder.getOutputBuffer(out);
                    boolean config = (encoded.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                    if (!config && muxStarted && encoded.size > 0 && data != null) {
                        data.position(encoded.offset);
                        data.limit(encoded.offset + encoded.size);
                        mux.writeSampleData(muxTrack, data, encoded);
                        totalBytes += encoded.size;
                        lastReport = report(lastReport, encoded.presentationTimeUs / 1_000_000L);
                    }
                    encoderEos = (encoded.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                    encoder.releaseOutputBuffer(out, false);
                }

                // Hold one decoder output until an encoder input is free.
                // Temporary input starvation is backpressure, not an error.
                if (!decoderEos && pendingDecoded < 0) {
                    int index = decoder.dequeueOutputBuffer(decoded, POLL_US);
                    if (index >= 0) pendingDecoded = index;
                }
                if (pendingDecoded >= 0) {
                    int encIn = encoder.dequeueInputBuffer(POLL_US);
                    if (encIn >= 0) {
                        ByteBuffer pcm = decoder.getOutputBuffer(pendingDecoded);
                        if (decoded.size > 0 && pcm != null) {
                            ByteBuffer target = encoder.getInputBuffer(encIn);
                            if (target == null || decoded.size > target.capacity())
                                throw new IllegalStateException("PCM-Block zu groß");
                            pcm.position(decoded.offset);
                            pcm.limit(decoded.offset + decoded.size);
                            target.clear();
                            target.put(pcm);
                            encoder.queueInputBuffer(encIn, 0, decoded.size,
                                    decoded.presentationTimeUs, 0);
                        } else {
                            encoder.queueInputBuffer(encIn, 0, 0,
                                    decoded.presentationTimeUs, 0);
                        }
                        decoderEos = (decoded.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                        lastPts = Math.max(lastPts, decoded.presentationTimeUs);
                        decoder.releaseOutputBuffer(pendingDecoded, false);
                        pendingDecoded = -1;
                    }
                }

                if (decoderEos && pendingDecoded < 0 && !encoderEosQueued) {
                    int index = encoder.dequeueInputBuffer(POLL_US);
                    if (index >= 0) {
                        encoder.queueInputBuffer(index, 0, 0, lastPts,
                                MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                        encoderEosQueued = true;
                    }
                }
            }
            if (!muxStarted) throw new IllegalStateException("AAC-Encoder lieferte kein Format");
            mux.stop();
            muxStarted = false;
        } finally {
            if (muxStarted) try { mux.stop(); } catch (Exception ignored) { }
            try { if (mux != null) mux.release(); } catch (Exception ignored) { }
            try { if (decoder != null) decoder.stop(); } catch (Exception ignored) { }
            try { if (decoder != null) decoder.release(); } catch (Exception ignored) { }
            try { if (encoder != null) encoder.stop(); } catch (Exception ignored) { }
            try { if (encoder != null) encoder.release(); } catch (Exception ignored) { }
            ex.release();
        }
    }

    private long report(long last, long seconds) {
        long now = System.currentTimeMillis();
        if (now - last >= 1000) {
            if (listener != null) listener.onProgress(seconds, totalBytes);
            return now;
        }
        return last;
    }

    private MediaExtractor openExtractor() throws Exception {
        MediaExtractor ex = new MediaExtractor();
        Map<String, String> headers = new HashMap<>();
        headers.put("User-Agent", "MPCASU/5.0 (Android; radio)");
        ex.setDataSource(inputUri, headers);
        return ex;
    }

    private static int findTrack(MediaExtractor ex, String prefix) {
        for (int i = 0; i < ex.getTrackCount(); i++) {
            String mime = ex.getTrackFormat(i).getString(MediaFormat.KEY_MIME);
            if (mime != null && mime.startsWith(prefix)) return i;
        }
        return -1;
    }

    private static boolean containsKind(MediaExtractor ex, Map<Integer, Integer> tracks,
                                        String prefix) {
        for (Integer i : tracks.keySet()) {
            String mime = ex.getTrackFormat(i).getString(MediaFormat.KEY_MIME);
            if (mime != null && mime.startsWith(prefix)) return true;
        }
        return false;
    }

    private static int integer(MediaFormat f, String key, int fallback) {
        return f.containsKey(key) ? f.getInteger(key) : fallback;
    }
}

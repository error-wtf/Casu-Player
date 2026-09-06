// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.media.PlaybackParams;
import android.media.audiofx.Visualizer;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.videolan.libvlc.LibVLC;
import org.videolan.libvlc.Media;
import org.videolan.libvlc.interfaces.IVLCVout;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.Locale;

/**
 * Single playback engine with a dual backend:
 *
 *  - Network streams (http/https, HLS, ICY/Shoutcast, remote playlists/chains)
 *    are played through libVLC — exactly like the Linux/Windows builds — because
 *    Android's MediaPlayer cannot reliably decode the full variety of stream
 *    formats and transport wrappers.
 *  - Local files, content:// and local video files keep using Android's
 *    MediaPlayer so video surface, gain, metadata retriever and local playback
 *    behave exactly as before.
 *
 * The rest (queue, modes, A-B, rate, audio focus, persistence) is identical for
 * both backends. All public methods run on the main thread; both backends
 * deliver their events asynchronously and are marshalled onto the main loop.
 * libVLC events originate on VLC's own thread, so they are posted into `main`.
 */
public final class PlayerEngine implements
        MediaPlayer.OnPreparedListener, MediaPlayer.OnCompletionListener,
        MediaPlayer.OnErrorListener, MediaPlayer.OnInfoListener,
        MediaPlayer.OnSeekCompleteListener,
        AudioManager.OnAudioFocusChangeListener {

    public interface Listener {
        void onStateChanged(boolean playing);
        void onItemChanged(MediaItem item, int index);
        void onPosition(long positionMs, long durationMs);
        void onEnded(int finishedIndex);          // EOF before auto-advance
        void onError(String userMessage);
        void onQueueChanged();
        void onTracksReady();                     // tracks/stream ready (any backend)
        void onVideoSizeChanged(int width, int height);  // for aspect-ratio
    }

    private static final String TAG = "MPCASU-Engine";
    private static final float[] RATES = {0.5f, 0.75f, 1.0f, 1.25f, 1.5f, 2.0f};

    private final Context context;
    private final QueueStore store;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Random random = new Random();
    private final List<Listener> listeners = new ArrayList<>();

    // libVLC backend (network streams). Created on first use.
    private LibVLC libvlc;
    private org.videolan.libvlc.MediaPlayer vlc;

    // Android MediaPlayer backend (local/video).
    private MediaPlayer player;

    // Which backend owns the current open().
    private boolean usingVlc;

    private android.view.Surface surface;   // kept across player recreation
    private final List<MediaItem> items = new ArrayList<>();
    private int index = -1;
    private boolean prepared;
    private boolean playing;
    private boolean pausedByUser;
    private float rate = 1.0f;
    private String repeat = "off";   // off|all|one
    private boolean shuffle;
    private long pendingSeekMs = -1;
    private float pendingRate = 1.0f;
    private long abStartMs = -1;
    private long abEndMs = -1;
    private String lastError;
    private AudioManager audio;
    private AudioFocusRequest focusRequest;
    private Visualizer visualizer;
    private boolean hasFocus;
    private long openSeq = 0;
    private int consecutiveFailures;

    private final Runnable abTicker = new Runnable() {
        @Override public void run() {
            if (abEndMs > 0 && playing) {
                long pos = position();
                if (pos >= abEndMs) {
                    seekRaw(abStartMs < 0 ? 0L : abStartMs);
                }
            }
            main.postDelayed(this, 250);
        }
    };

    public PlayerEngine(Context context) {
        this.context = context.getApplicationContext();
        this.store = new QueueStore(this.context);
        audio = (AudioManager) this.context.getSystemService(Context.AUDIO_SERVICE);
        restore();
        main.postDelayed(abTicker, 250);
    }

    // ------------------------------------------------------------------ listeners

    public void addListener(Listener l) {
        if (l != null && !listeners.contains(l)) listeners.add(l);
    }

    public void removeListener(Listener l) {
        listeners.remove(l);
    }

    private void fireStateChanged() {
        for (Listener l : new ArrayList<>(listeners)) l.onStateChanged(playing);
    }

    private void fireItemChanged() {
        MediaItem item = current();
        for (Listener l : new ArrayList<>(listeners)) l.onItemChanged(item, index);
    }

    private void fireQueueChanged() {
        for (Listener l : new ArrayList<>(listeners)) l.onQueueChanged();
    }

    private void fireError(String message) {
        lastError = message;
        for (Listener l : new ArrayList<>(listeners)) l.onError(message);
    }

    private void fireVideoSizeChanged(int width, int height) {
        for (Listener l : new ArrayList<>(listeners)) l.onVideoSizeChanged(width, height);
    }

    private void fireTracksReady() {
        for (Listener l : new ArrayList<>(listeners)) l.onTracksReady();
    }

    // ------------------------------------------------------------------ state

    public MediaItem current() {
        if (index >= 0 && index < items.size()) return items.get(index);
        return null;
    }

    public List<MediaItem> items() { return items; }
    public int index() { return index; }
    public boolean isPlaying() { return playing; }
    public boolean isPausedByUser() { return pausedByUser; }
    public String repeat() { return repeat; }
    public boolean shuffle() { return shuffle; }
    public float rate() { return rate; }
    public String lastError() { return lastError; }

    public long position() {
        try {
            if (usingVlc) return vlc != null ? Math.max(0, vlc.getTime()) : 0;
            return player != null ? Math.max(0, player.getCurrentPosition()) : 0;
        } catch (Exception e) { return 0; }
    }

    public long duration() {
        try {
            if (usingVlc) return vlc != null ? Math.max(0, vlc.getLength()) : 0;
            return player != null && prepared ? Math.max(0, player.getDuration()) : 0;
        } catch (Exception e) { return 0; }
    }

    /** Poll tick (200 ms) from the service: pushes position to listeners. */
    public void pollPosition() {
        if (prepared) {
            long pos = position();
            long dur = duration();
            for (Listener l : new ArrayList<>(listeners)) l.onPosition(pos, dur);
        }
    }

    public Visualizer attachVisualizer(int rateHz, Visualizer.OnDataCaptureListener l) {
        try {
            releaseVisualizer();
            int sessionId = 0;
            if (!usingVlc && player != null) sessionId = player.getAudioSessionId();
            // With libVLC the audio is decoded inside VLC, which does not expose
            // an AudioTrack session. The visualizer therefore only binds to the
            // MediaPlayer backend (local playback); for network streams it is
            // skipped so the UI never attaches a dead audio session.
            if (sessionId == 0) return null;
            visualizer = new Visualizer(sessionId);
            visualizer.setDataCaptureListener(l, rateHz, true, false);
            visualizer.setEnabled(true);
            return visualizer;
        } catch (Exception e) {
            Log.i(TAG, "visualizer unavailable: " + e.getMessage());
            return null;
        }
    }

    public void releaseVisualizer() {
        if (visualizer != null) {
            try { visualizer.setEnabled(false); visualizer.release(); } catch (Exception ignored) {}
            visualizer = null;
        }
    }

    // ------------------------------------------------------------------ queue ops

    public int add(MediaItem item) {
        if (item == null || item.url == null) return -1;
        items.add(item);
        persist();
        fireQueueChanged();
        return items.size() - 1;
    }

    public void addAll(List<MediaItem> list) {
        if (list == null) return;
        for (MediaItem item : list) {
            if (item != null && item.url != null) items.add(item);
        }
        persist();
        fireQueueChanged();
    }

    public void removeAt(int position) {
        if (position < 0 || position >= items.size()) return;
        items.remove(position);
        if (position < index) {
            index--;
        } else if (position == index) {
            stopInternal(false);
            if (index >= items.size()) index = items.size() - 1;
        }
        persist();
        fireQueueChanged();
        fireItemChanged();
    }

    public void removeAll(List<Integer> positions) {
        if (positions == null || positions.isEmpty()) return;
        List<Integer> sorted = new ArrayList<>(positions);
        java.util.Collections.sort(sorted, java.util.Collections.reverseOrder());
        for (int p : sorted) removeAt(p);
    }

    public void move(int from, int to) {
        if (from < 0 || from >= items.size() || to < 0 || to >= items.size() || from == to) return;
        MediaItem item = items.remove(from);
        items.add(to, item);
        if (index == from) index = to;
        else if (from < index && to >= index) index--;
        else if (from > index && to <= index) index++;
        persist();
        fireQueueChanged();
    }

    public void clear() {
        stopInternal(false);
        items.clear();
        index = -1;
        persist();
        fireQueueChanged();
        fireItemChanged();
    }

    public void rename(int position, String title) {
        if (position < 0 || position >= items.size() || title == null || title.trim().isEmpty()) return;
        items.get(position).title = title.trim();
        items.get(position).metadataLoaded = true;
        persist();
        fireQueueChanged();
        if (position == index) fireItemChanged();
    }

    public void setShuffle(boolean on) {
        shuffle = on;
        persist();
        fireQueueChanged();
    }

    public void cycleRepeat() {
        repeat = "off".equals(repeat) ? "all" : ("all".equals(repeat) ? "one" : "off");
        persist();
        fireQueueChanged();
    }

    // ------------------------------------------------------------------ transport

    public void playIndex(int position) {
        playIndex(position, 0);
    }

    public void playIndex(int position, long startMs) {
        if (position < 0 || position >= items.size()) return;
        index = position;
        pausedByUser = false;
        openCurrent(startMs);
    }

    public void openExternal(MediaItem item, boolean enqueue, long startMs) {
        if (item == null) return;
        int existing = indexOfUrl(item.url);
        if (existing >= 0) {
            playIndex(existing, startMs);
            return;
        }
        if (enqueue) {
            items.add(item);
            index = items.size() - 1;
            persist();
            fireQueueChanged();
        }
        pausedByUser = false;
        openCurrent(startMs);
    }

    public void playPause() {
        if (!prepared) {
            if (index < 0 && !items.isEmpty()) playIndex(0);
            else if (current() != null) openCurrent(0);
            return;
        }
        try {
            if (playing) {
                pauseBackend();
                setPlaying(false, true);
            } else {
                requestFocus();
                if (usingVlc) { if (vlc != null) vlc.play(); }
                else if (player != null) { player.start(); applyRatePlayer(); }
                setPlaying(true, false);
            }
            persist();
        } catch (Exception e) {
            fireError(userError(e));
        }
    }

    public void pause() {
        if (playing) {
            pauseBackend();
            setPlaying(false, true);
            persist();
        }
    }

    private void pauseBackend() {
        try {
            if (usingVlc) { if (vlc != null) vlc.pause(); }
            else if (player != null) player.pause();
        } catch (Exception ignored) {}
    }

    public void stop() {
        stopInternal(true);
    }

    private void stopInternal(boolean persist) {
        ++openSeq; // Cancel pending provider/network opens before stopping.
        releaseVisualizer();
        releaseVlc();
        if (player != null) {
            try { player.stop(); } catch (Exception ignored) {}
            try { player.release(); } catch (Exception ignored) {}
            player = null;
        }
        usingVlc = false;
        prepared = false;
        if (playing) {
            playing = false;
            fireStateChanged();
        }
        if (persist) persist();
    }

    private void releaseVlc() {
        if (vlc != null) {
            try {
                IVLCVout vout = vlc.getVLCVout();
                try { vout.detachViews(); } catch (Exception ignored) {}
            } catch (Exception ignored) {}
            try { vlc.stop(); } catch (Exception ignored) {}
            try { vlc.release(); } catch (Exception ignored) {}
            vlc = null;
        }
        if (libvlc != null) {
            try { libvlc.release(); } catch (Exception ignored) {}
            libvlc = null;
        }
    }

    public void next() {
        nextInternal(false);
    }

    public void previous() {
        int count = items.size();
        if (count == 0) return;
        int target = index - 1;
        if (target < 0) {
            if ("all".equals(repeat)) target = count - 1;
            else return;
        }
        playIndex(target);
    }

    public void seekTo(long ms) {
        if (prepared) {
            seekRaw(ms);
            if (!playing && !pausedByUser) {
                requestFocus();
                if (usingVlc) { if (vlc != null) vlc.play(); }
                else if (player != null) { player.start(); applyRatePlayer(); }
                setPlaying(true, false);
            }
        } else {
            pendingSeekMs = ms;
        }
    }

    public void seekBy(long deltaMs) {
        seekTo(position() + deltaMs);
    }

    private void seekRaw(long ms) {
        try {
            if (usingVlc) { if (vlc != null) vlc.setTime(Math.max(0, ms)); }
            else if (player != null) player.seekTo((int) Math.max(0, ms));
        } catch (Exception ignored) {}
    }

    public void cycleRate() {
        int at = 0;
        for (int i = 0; i < RATES.length; i++) if (RATES[i] == rate) at = i;
        rate = RATES[(at + 1) % RATES.length];
        applyRate();
        persist();
    }

    private void applyRate() {
        try {
            if (usingVlc) { if (vlc != null) vlc.setRate(rate); }
            else applyRatePlayer();
        } catch (Exception e) {
            Log.i(TAG, "rate " + rate + " unavailable: " + e.getMessage());
        }
    }

    private void applyRatePlayer() {
        if (player == null) return;
        try {
            PlaybackParams params = new PlaybackParams();
            params.setSpeed(rate);
            player.setPlaybackParams(params);
        } catch (Exception e) {
            Log.i(TAG, "rate " + rate + " unavailable: " + e.getMessage());
        }
    }

    // ------------------------------------------------------------------ A-B loop

    /** Returns a short status text for the UI. */
    public String cycleAbLoop() {
        long pos = position();
        if (abStartMs < 0) {
            abStartMs = pos;
            abEndMs = -1;
            return "A gesetzt · " + fmt(pos);
        }
        if (abEndMs < 0) {
            if (pos <= abStartMs) return "B muss nach A liegen";
            abEndMs = pos;
            return "A–B aktiv · " + fmt(abStartMs) + " – " + fmt(abEndMs);
        }
        abStartMs = abEndMs = -1;
        return "A–B aus";
    }

    public boolean abActive() { return abStartMs >= 0; }

    private static String fmt(long ms) {
        long s = ms / 1000;
        return String.format("%d:%02d", s / 60, s % 60);
    }

    // ------------------------------------------------------------------ internals

    private int indexOfUrl(String url) {
        if (url == null) return -1;
        for (int i = 0; i < items.size(); i++) {
            if (url.equals(items.get(i).url)) return i;
        }
        return -1;
    }

    private boolean isNetworkSource(String source) {
        if (source == null) return false;
        String value = source.trim();
        if (value.startsWith("//")) return true;
        String scheme = android.net.Uri.parse(value).getScheme();
        if (scheme == null) return false;
        switch (scheme.toLowerCase(Locale.ROOT)) {
            case "http":
            case "https":
            case "rtsp":
            case "rtmp":
            case "rtmps":
            case "mms":
            case "icy":
            case "icyx":
            case "icecast":
            case "ftp":
            case "udp":
            case "rtp":
                return true;
            default:
                return false;
        }
    }

    /** Resolve the playable URL (CASU containers → cache file) and open. */
    private void openCurrent(long startMs) {
        MediaItem item = current();
        if (item == null) return;
        releaseVisualizer();
        prepared = false;
        pendingSeekMs = startMs > 0 ? startMs : -1;
        pendingRate = rate;
        String source = item.url;
        String kind = item.kind == null ? "" : item.kind;
        String providerSource = item.sourceUrl != null ? item.sourceUrl : source;
        android.net.Uri providerUri = android.net.Uri.parse(providerSource);
        String host = providerUri.getHost();
        boolean youtube = host != null && (host.equals("youtu.be") || host.equals("youtube.com") || host.endsWith(".youtube.com"));
        String videoId = youtube ? YouTubeClient.extractVideoId(providerSource) : null;
        if (videoId != null) {
            stopInternal(false);
            final long seq = ++openSeq;
            new Thread(() -> {
                try {
                    String resolved = YouTubeClient.resolveMediaUrl(videoId);
                    main.post(() -> {
                        if (seq != openSeq || current() != item) return;
                        openSource(resolved, true);
                    });
                } catch (Exception error) {
                    main.post(() -> {
                        if (seq == openSeq && current() == item) fireError("YouTube: " + error.getMessage());
                    });
                }
            }, "mpcasu-youtube-resolve").start();
            return;
        }
        if ("casu".equals(kind) || "mp5".equals(kind)
                || source.toLowerCase().endsWith(".casu")
                || source.toLowerCase().endsWith(".mp5")) {
            final String resolved = CasuBridge.extractToCache(source,
                    context.getCacheDir().getAbsolutePath());
            if (resolved == null || resolved.startsWith("ERROR")) {
                fireError("CASU-Container konnte nicht geöffnet werden"
                        + (resolved != null ? ": " + resolved.substring(5) : ""));
                return;
            }
            source = resolved;
        }
        // The parser already knows that playlist entries are streams.  Keep
        // that semantic information: malformed/legacy URL spelling must never
        // silently demote a radio stream to Android's local MediaPlayer.
        openSource(source, "stream".equals(kind));
    }

    private void openSource(String source) {
        openSource(source, false);
    }

    private void openSource(String source, boolean forceNetwork) {
        final String normalized = PlaylistIO.normalizePlayableLocation(source);
        final long seq = ++openSeq;
        boolean network = forceNetwork || isNetworkSource(normalized);
        usingVlc = network;
        if (network) {
            // libVLC resolves playlists/chains and decodes every stream type
            // itself — the same engine the Linux/Windows builds use. No side
            // resolution step is needed.
            main.post(() -> {
                if (seq != openSeq) return;
                openVlc(normalized);
            });
        } else {
            openResolved(normalized);
        }
    }

    // ------------------------------------------------------------------ libVLC backend

    private void ensureLibVlc() {
        if (libvlc == null) {
            List<String> args = new ArrayList<>();
            args.add("--no-video-title-show");
            args.add("--avcodec-hw=none");
            libvlc = new LibVLC(context, args);
        }
    }

    private void openVlc(String source) {
        releaseVlc();
        try {
            ensureLibVlc();
            vlc = new org.videolan.libvlc.MediaPlayer(libvlc);
            vlc.setEventListener(event -> main.post(() -> onVlcEvent(event)));
            ensureVoutAttached();
            Media media = new Media(libvlc, android.net.Uri.parse(source));
            media.setHWDecoderEnabled(false, false);
            media.addOption(":network-caching=1800");
            media.addOption(":live-caching=1800");
            media.addOption(":http-reconnect");
            media.addOption(":http-user-agent=MPCASU/7.0.0 (Android; libVLC)");
            vlc.setMedia(media);
            requestFocus();
            vlc.play();
            fireItemChanged();
            setPlaying(false, false);
        } catch (Exception e) {
            Log.w(TAG, "vlc open failed", e);
            fireError(userError(e));
        }
    }

    private static int clampVolume(int v) {
        if (v < 0) return 0;
        if (v > 100) return 100;
        return v;
    }

    private void ensureVoutAttached() {
        if (vlc == null) return;
        try {
            IVLCVout vout = vlc.getVLCVout();
            if (surface != null) {
                // Bind the Surface from the UI (TextureView) so video renders.
                vout.setVideoSurface(surface, null);
                vout.attachViews();
            } else {
                vout.attachViews();
            }
        } catch (Exception e) {
            Log.i(TAG, "vout attach skipped: " + e.getMessage());
        }
    }

    private void onVlcEvent(org.videolan.libvlc.MediaPlayer.Event event) {
        switch (event.type) {
            case org.videolan.libvlc.MediaPlayer.Event.Playing:
                Log.i(TAG, "vlc event: PLAYING");
                consecutiveFailures = 0;
                prepared = true;
                if (pendingRate > 0 && pendingRate != 1.0f) applyRate();
                if (pendingSeekMs > 0) {
                    long dur = duration();
                    if (pendingSeekMs < Math.max(dur - 500, pendingSeekMs + 500)) {
                        try { vlc.setTime(pendingSeekMs); } catch (Exception ignored) {}
                    }
                }
                pendingSeekMs = -1;
                setPlaying(true, false);
                fireTracksReady();
                if (videoWidth() > 0 && videoHeight() > 0) {
                    fireVideoSizeChanged(videoWidth(), videoHeight());
                }
                break;
            case org.videolan.libvlc.MediaPlayer.Event.Paused:
                setPlaying(false, false);
                break;
            case org.videolan.libvlc.MediaPlayer.Event.EndReached:
                if ("one".equals(repeat) && current() != null) {
                    try { vlc.setTime(0); vlc.play(); setPlaying(true, false); }
                    catch (Exception ignored) {}
                    return;
                }
                for (Listener l : new ArrayList<>(listeners)) l.onEnded(index);
                nextInternal(true);
                break;
            case org.videolan.libvlc.MediaPlayer.Event.EncounteredError:
                Log.w(TAG, "vlc error event");
                setPlaying(false, false);
                handlePlaybackFailure("Stream nicht erreichbar (stream-error)");
                break;
            case org.videolan.libvlc.MediaPlayer.Event.Vout:
                main.post(() -> {
                    int vw = videoWidth(), vh = videoHeight();
                    if (vw > 0 && vh > 0) fireVideoSizeChanged(vw, vh);
                });
                break;
            default:
                break;
        }
    }

    /** Public gain control (0..1), mirrors the old MediaPlayer volume API. */
    public void setGain(float gain) {
        try {
            if (usingVlc && vlc != null) {
                vlc.setVolume(clampVolume((int) (gain * 100)));
            } else if (player != null) {
                float g = Math.max(0f, Math.min(1f, gain));
                player.setVolume(g, g);
            }
        } catch (Exception e) {
            Log.i(TAG, "gain unavailable: " + e.getMessage());
        }
    }

    // ------------------------------------------------------------------ MediaPlayer backend

    private void openResolved(String source) {
        releaseVlc();
        if (player != null) {
            try { player.reset(); } catch (Exception ignored) {}
        } else {
            player = createPlayer();
        }
        try {
            requestFocus();
            if (source.startsWith("content://")) {
                player.setDataSource(context, android.net.Uri.parse(source));
            } else if (source.startsWith("/")) {
                player.setDataSource(source);
            } else {
                player.setDataSource(source);
            }
            player.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build());
            player.prepareAsync();
            fireItemChanged();
            setPlaying(false, false);
        } catch (Exception e) {
            Log.w(TAG, "open failed", e);
            fireError(userError(e));
        }
    }

    private MediaPlayer createPlayer() {
        MediaPlayer mp = new MediaPlayer();
        mp.setOnPreparedListener(this);
        mp.setOnCompletionListener(this);
        mp.setOnErrorListener(this);
        mp.setOnInfoListener(this);
        mp.setOnSeekCompleteListener(this);
        mp.setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                .build());
        if (surface != null) {
            try { mp.setSurface(surface); } catch (Exception ignored) {}
        }
        return mp;
    }

    /** Attach a video surface, kept referenced so every player instance is bound. */
    public void setSurface(android.view.Surface newSurface) {
        this.surface = newSurface;
        if (usingVlc && vlc != null) {
            try {
                IVLCVout vout = vlc.getVLCVout();
                if (newSurface != null) {
                    vout.setVideoSurface(newSurface, null);
                    vout.attachViews();
                } else {
                    vout.detachViews();
                }
            } catch (Exception ignored) {}
        } else if (player != null) {
            try { player.setSurface(newSurface); } catch (Exception ignored) {}
        }
    }

    public int videoWidth() {
        try {
            if (usingVlc) {
                if (vlc == null) return 0;
                org.videolan.libvlc.interfaces.IMedia.VideoTrack t = vlc.getCurrentVideoTrack();
                if (t != null && t.width > 0) return t.width;
                return 0;
            }
            return player != null && prepared ? player.getVideoWidth() : 0;
        } catch (Exception e) { return 0; }
    }

    public int videoHeight() {
        try {
            if (usingVlc) {
                if (vlc == null) return 0;
                org.videolan.libvlc.interfaces.IMedia.VideoTrack t = vlc.getCurrentVideoTrack();
                if (t != null && t.height > 0) return t.height;
                return 0;
            }
            return player != null && prepared ? player.getVideoHeight() : 0;
        } catch (Exception e) { return 0; }
    }

    /** Retained for source compatibility: returns the active MediaPlayer or null. */
    public MediaPlayer player() {
        return usingVlc ? null : player;
    }

    // ------------------------------------------------------------------ MediaPlayer events

    @Override public void onPrepared(MediaPlayer mp) {
        consecutiveFailures = 0;
        prepared = true;
        if (surface != null) {
            try { mp.setSurface(surface); } catch (Exception ignored) {}
        }
        if (pendingRate > 0 && pendingRate != 1.0f) applyRatePlayer();
        long dur = duration();
        if (pendingSeekMs > 0 && pendingSeekMs < Math.max(dur - 500, pendingSeekMs + 500)) {
            try { mp.seekTo((int) pendingSeekMs); } catch (Exception ignored) {}
        }
        pendingSeekMs = -1;
        requestFocus();
        try {
            mp.start();
            applyRatePlayer();
            setPlaying(true, false);
        } catch (Exception e) {
            fireError(userError(e));
        }
        fireTracksReady();
        int vw = videoWidth(), vh = videoHeight();
        if (vw > 0 && vh > 0) fireVideoSizeChanged(vw, vh);
    }

    @Override public void onCompletion(MediaPlayer mp) {
        if ("one".equals(repeat) && current() != null) {
            try { mp.seekTo(0); mp.start(); setPlaying(true, false); } catch (Exception ignored) {}
            return;
        }
        for (Listener l : new ArrayList<>(listeners)) l.onEnded(index);
        nextInternal(true);
    }

    @Override public boolean onError(MediaPlayer mp, int what, int extra) {
        Log.w(TAG, "player error what=" + what + " extra=" + extra);
        setPlaying(false, false);
        String message;
        if (what == MediaPlayer.MEDIA_ERROR_UNSUPPORTED || extra == -1010) {
            message = "Format wird von diesem Gerät nicht unterstützt (codec-unsupported)";
        } else if (what == MediaPlayer.MEDIA_ERROR_TIMED_OUT) {
            message = "Zeitüberschreitung der Quelle (timeout)";
        } else if (what == MediaPlayer.MEDIA_ERROR_SERVER_DIED) {
            message = "Medien-Dienst wurde beendet (playback-failed)";
        } else {
            message = "Lokale Mediendatei konnte nicht geöffnet werden (local-media-error)";
        }
        handlePlaybackFailure(message);
        return true;
    }

    /** A playlist should not die on its first stale or regional stream URL. */
    private void handlePlaybackFailure(String message) {
        fireError(message);
        consecutiveFailures++;
        if (items.size() <= 1 || consecutiveFailures >= items.size()) return;
        final int failedIndex = index;
        main.postDelayed(() -> {
            if (!playing && index == failedIndex) nextInternal(true);
        }, 350);
    }

    @Override public boolean onInfo(MediaPlayer mp, int what, int extra) {
        if (what == 3) {
            if (surface != null) {
                try { mp.setSurface(surface); } catch (Exception ignored) {}
            }
            int vw = videoWidth(), vh = videoHeight();
            if (vw > 0 && vh > 0) fireVideoSizeChanged(vw, vh);
        }
        int vw = videoWidth(), vh = videoHeight();
        if (vw > 0 && vh > 0) fireVideoSizeChanged(vw, vh);
        return false;
    }

    @Override public void onSeekComplete(MediaPlayer mp) {
        // no-op: position polling drives the UI
    }

    private void nextInternal(boolean automatic) {
        int count = items.size();
        if (count == 0) return;
        int target;
        if (shuffle && count > 1) {
            target = random.nextInt(count - 1);
            if (target >= index) target++;
        } else {
            target = index + 1;
        }
        if (target >= count) {
            if ("all".equals(repeat)) target = 0;
            else {
                setPlaying(false, false);
                persist();
                return;
            }
        }
        playIndex(target);
    }

    private void setPlaying(boolean now, boolean byUser) {
        playing = now;
        if (now) pausedByUser = false;
        else if (byUser) pausedByUser = true;
        fireStateChanged();
    }

    // ------------------------------------------------------------------ focus

    private void requestFocus() {
        if (hasFocus || audio == null) return;
        try {
            if (focusRequest == null) {
                focusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                        .setAudioAttributes(new AudioAttributes.Builder()
                                .setUsage(AudioAttributes.USAGE_MEDIA)
                                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                                .build())
                        .setOnAudioFocusChangeListener(this)
                        .build();
            }
            hasFocus = audio.requestAudioFocus(focusRequest) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
        } catch (Exception ignored) {}
    }

    private void abandonFocus() {
        if (focusRequest != null && audio != null) {
            try { audio.abandonAudioFocusRequest(focusRequest); } catch (Exception ignored) {}
        }
        hasFocus = false;
    }

    @Override public void onAudioFocusChange(int change) {
        if (change == AudioManager.AUDIOFOCUS_LOSS) {
            pause();
        } else if (change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) {
            if (playing) pause();
        }
    }

    // ------------------------------------------------------------------ errors

    private static String userError(Exception e) {
        String msg = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        if (msg.contains("Unable to resolve") || msg.contains("No address")) return "Netzwerk nicht erreichbar (network-offline)";
        if (msg.contains("timeout") || msg.contains("Timed out")) return "Zeitüberschreitung (timeout)";
        if (msg.contains("Permission") || msg.contains("denied")) return "Zugriff verweigert (permission-denied)";
        if (msg.contains("FileNotFound") || msg.contains("open failed")) return "Datei nicht gefunden (file-missing)";
        return "Quelle nicht abspielbar: " + msg;
    }

    // ------------------------------------------------------------------ persistence

    public void persist() {
        store.save(items, index, position(), playing, shuffle, repeat);
    }

    private void restore() {
        // Product decision (user): the QUEUE STARTS EMPTY on a fresh app start.
    }

    public QueueStore.Saved savedState() {
        return store.load();
    }

    public void shutdown() {
        main.removeCallbacks(abTicker);
        releaseVisualizer();
        abandonFocus();
        persist();
        stopInternal(false);
    }
}

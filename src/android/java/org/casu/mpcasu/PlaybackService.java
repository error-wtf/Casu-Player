// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

/** Foreground playback service. Owns the PlayerEngine for the whole app
 *  lifetime: the queue survives Activity death and app restarts here.
 *  Framework-only (no androidx): MediaSession + Notification.MediaStyle. */
public final class PlaybackService extends Service {

    public static final String CHANNEL_ID = "mpcasu-playback";
    public static final int NOTIFICATION_ID = 42;

    public static final String ACTION_PREV = "org.casu.mpcasu.PREV";
    public static final String ACTION_TOGGLE = "org.casu.mpcasu.TOGGLE";
    public static final String ACTION_NEXT = "org.casu.mpcasu.NEXT";
    public static final String ACTION_STOP = "org.casu.mpcasu.STOP";
    public static final String ACTION_SEEK = "org.casu.mpcasu.SEEK";

    private static PlaybackService instance;

    private PlayerEngine engine;
    private MediaSession session;
    private final Handler main = new Handler(Looper.getMainLooper());
    private boolean polling;

    /** The engine is process-global: Activity + Service + Widget share it. */
    public static PlayerEngine engine() {
        return instance != null ? instance.engine : null;
    }

    public static MediaSession session() {
        return instance != null ? instance.session : null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        engine = new PlayerEngine(this);
        session = new MediaSession(this, "MPCASU");
        session.setCallback(new SessionCallbacks());
        session.setActive(true);
        engine.addListener(engineListener);
        startPolling();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Every startForegroundService() MUST reach startForeground() within
        // the ANR window — even when playback is still preparing or the
        // command was STOP. promoteForeground() is idempotent, so simply
        // always take the foreground first, then handle the command.
        promoteForeground();
        String action = intent != null ? intent.getAction() : null;
        if (action != null && engine != null) {
            switch (action) {
                case ACTION_PREV: engine.previous(); break;
                case ACTION_NEXT: engine.next(); break;
                case ACTION_TOGGLE: engine.playPause(); break;
                case ACTION_STOP:
                    engine.stop();
                    stopForeground(STOP_FOREGROUND_REMOVE);
                    stopSelf();
                    return START_NOT_STICKY;
                case ACTION_SEEK: {
                    long ms = intent.getLongExtra("positionMs", -1);
                    if (ms >= 0) engine.seekTo(ms);
                    break;
                }
                default: break;
            }
        }
        updateNotification();
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        if (engine != null) {
            engine.removeListener(engineListener);
            engine.shutdown();
        }
        engine = null;
        if (session != null) {
            session.release();
            session = null;
        }
        instance = null;
        super.onDestroy();
    }

    // ------------------------------------------------------------------ engine events

    private final PlayerEngine.Listener engineListener = new PlayerEngine.Listener() {
        @Override public void onStateChanged(boolean nowPlaying) {
            main.post(() -> {
                if (nowPlaying) promoteForeground();
                else updateNotification();
                pushSessionState();
                McasuWidgetProvider.pushState(getApplicationContext(), currentTitle(), nowPlaying);
            });
        }

        @Override public void onItemChanged(MediaItem item, int index) {
            main.post(() -> {
                pushSessionState();
                updateNotification();
                McasuWidgetProvider.pushState(getApplicationContext(), currentTitle(),
                        engine != null && engine.isPlaying());
            });
        }

        @Override public void onPosition(long positionMs, long durationMs) { }

        @Override public void onEnded(int finishedIndex) { }

        @Override public void onError(String userMessage) {
            main.post(() -> updateNotification());
        }

        @Override public void onQueueChanged() {
            main.post(() -> pushSessionState());
        }

        @Override public void onTracksReady() { }

        @Override public void onVideoSizeChanged(int width, int height) { }
    };

    private String currentTitle() {
        if (engine == null || engine.current() == null) return "";
        return engine.current().title != null ? engine.current().title : "";
    }

    private void promoteForeground() {
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, buildNotification(),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(NOTIFICATION_ID, buildNotification());
        }
    }

    private void startPolling() {
        if (polling) return;
        polling = true;
        final int[] persistCounter = {0};
        main.postDelayed(new Runnable() {
            @Override public void run() {
                if (engine == null) return;
                engine.pollPosition();
                pushSessionState();
                // Periodically persist the position so a force-stop (which
                // skips onPause) still resumes where the user was — the old
                // APK's queue/position-reset defect class.
                if (engine.isPlaying() && ++persistCounter[0] % 10 == 0) {
                    engine.persist();
                }
                main.postDelayed(this, 200);
            }
        }, 200);
    }

    // ------------------------------------------------------------------ session

    private final class SessionCallbacks extends MediaSession.Callback {
        @Override public void onPlay() {
            if (engine == null) return;
            if (engine.current() == null && !engine.items().isEmpty()) engine.playIndex(0);
            else engine.playPause();
        }

        @Override public void onPause() {
            if (engine != null) engine.pause();
        }

        @Override public void onSkipToNext() {
            if (engine != null) engine.next();
        }

        @Override public void onSkipToPrevious() {
            if (engine != null) engine.previous();
        }

        @Override public void onStop() {
            if (engine != null) engine.stop();
        }

        @Override public void onSeekTo(long pos) {
            if (engine != null) engine.seekTo(pos);
        }
    }

    private void pushSessionState() {
        if (session == null || engine == null) return;
        MediaItem item = engine.current();
        long position = engine.position();
        long duration = engine.duration();
        String title = item != null && item.title != null && !item.title.isEmpty()
                ? item.title : "MPCASU";
        String artist = item != null && item.artist != null && !item.artist.isEmpty()
                ? item.artist : "MPCASU";

        session.setMetadata(new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, title)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, artist)
                .putLong(MediaMetadata.METADATA_KEY_DURATION, Math.max(0, duration))
                .build());

        long actions = PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE
                | PlaybackState.ACTION_PLAY_PAUSE | PlaybackState.ACTION_SKIP_TO_NEXT
                | PlaybackState.ACTION_SKIP_TO_PREVIOUS | PlaybackState.ACTION_STOP
                | PlaybackState.ACTION_SEEK_TO;
        int state = engine.isPlaying()
                ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED;
        session.setPlaybackState(new PlaybackState.Builder()
                .setActions(actions)
                .setState(state, Math.max(0, position), engine.isPlaying() ? 1.0f : 0f)
                .build());
    }

    // ------------------------------------------------------------------ notification

    private void updateNotification() {
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) manager.notify(NOTIFICATION_ID, buildNotification());
    }

    private Notification buildNotification() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID,
                    "MPCASU Wiedergabe", NotificationManager.IMPORTANCE_LOW);
            channel.setShowBadge(false);
            NotificationManager manager =
                    (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager != null) manager.createNotificationChannel(channel);
        }
        MediaItem item = engine != null ? engine.current() : null;
        String title = item != null && item.title != null && !item.title.isEmpty()
                ? item.title : "MPCASU";
        boolean playingNow = engine != null && engine.isPlaying();

        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        builder.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setContentText(item != null && item.artist != null && !item.artist.isEmpty()
                        ? item.artist : "MPCASU Media Player")
                .setContentIntent(mainActivityPendingIntent())
                .setOngoing(playingNow)
                .setOnlyAlertOnce(true)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .addAction(new Notification.Action.Builder(
                        null, "⏮", servicePendingIntent(ACTION_PREV, 2)).build())
                .addAction(new Notification.Action.Builder(
                        null, playingNow ? "❚❚" : "▶",
                        servicePendingIntent(ACTION_TOGGLE, 3)).build())
                .addAction(new Notification.Action.Builder(
                        null, "⏭", servicePendingIntent(ACTION_NEXT, 4)).build());
        if (session != null) {
            builder.setStyle(new Notification.MediaStyle()
                    .setMediaSession(session.getSessionToken()));
        }
        return builder.build();
    }

    private PendingIntent mainActivityPendingIntent() {
        Intent open = new Intent(this, MainActivity.class);
        return PendingIntent.getActivity(this, 1, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent servicePendingIntent(String action, int requestCode) {
        Intent intent = new Intent(this, PlaybackService.class).setAction(action);
        return PendingIntent.getService(this, requestCode, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}

// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;

/** MPCASU home-screen widget (4×1): title + play/pause state + ⏮ ▶ ⏭.
 *  Button taps arrive as broadcasts and drive the shared PlayerEngine in
 *  PlaybackService; without a running service the tap opens the app. */
public class McasuWidgetProvider extends AppWidgetProvider {

    public static final String ACTION_PREV = "org.casu.mpcasu.WIDGET_PREV";
    public static final String ACTION_PLAY = "org.casu.mpcasu.WIDGET_PLAY";
    public static final String ACTION_NEXT = "org.casu.mpcasu.WIDGET_NEXT";

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] appWidgetIds) {
        PlayerEngine engine = PlaybackService.engine();
        boolean playing = engine != null && engine.isPlaying();
        String title = engine != null && engine.current() != null ? engine.current().title : "";
        RemoteViews views = buildViews(context, title, playing);
        for (int id : appWidgetIds) manager.updateAppWidget(id, views);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (ACTION_PREV.equals(action) || ACTION_PLAY.equals(action) || ACTION_NEXT.equals(action)) {
            PlayerEngine engine = PlaybackService.engine();
            if (engine == null) {
                // Cold start via widget: boot the service with the command.
                String serviceAction = ACTION_PLAY.equals(action) ? PlaybackService.ACTION_TOGGLE
                        : ACTION_PREV.equals(action) ? PlaybackService.ACTION_PREV
                        : PlaybackService.ACTION_NEXT;
                Intent start = new Intent(context, PlaybackService.class)
                        .setAction(serviceAction)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startForegroundService(start);
                return;
            }
            if (ACTION_PREV.equals(action)) engine.previous();
            else if (ACTION_NEXT.equals(action)) engine.next();
            else engine.playPause();
            return;
        }
        super.onReceive(context, intent);
    }

    /** Push title/playing state into every placed widget instance. */
    public static void pushState(Context context, String title, boolean playing) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        if (manager == null) return;
        ComponentName who = new ComponentName(context, McasuWidgetProvider.class);
        int[] ids = manager.getAppWidgetIds(who);
        if (ids == null || ids.length == 0) return;
        RemoteViews views = buildViews(context, title, playing);
        manager.updateAppWidget(who, views);
    }

    private static RemoteViews buildViews(Context context, String title, boolean playing) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_mpcasu);
        boolean idle = title == null || title.isEmpty();
        views.setTextViewText(R.id.widget_title, idle ? "MPCASU" : title);
        views.setTextViewText(R.id.widget_state, idle ? "Bereit"
                : (playing ? "Wiedergabe" : "Pausiert"));
        views.setTextViewText(R.id.widget_play, playing ? "❚❚" : "▶");

        Intent prev = new Intent(context, McasuWidgetProvider.class).setAction(ACTION_PREV);
        Intent play = new Intent(context, McasuWidgetProvider.class).setAction(ACTION_PLAY);
        Intent next = new Intent(context, McasuWidgetProvider.class).setAction(ACTION_NEXT);
        views.setOnClickPendingIntent(R.id.widget_prev, pending(context, prev, 11));
        views.setOnClickPendingIntent(R.id.widget_play, pending(context, play, 12));
        views.setOnClickPendingIntent(R.id.widget_next, pending(context, next, 13));

        Intent open = new Intent(context, MainActivity.class);
        views.setOnClickPendingIntent(R.id.widget_root, pending(context, open, 14));
        return views;
    }

    private static PendingIntent pending(Context context, Intent intent, int requestCode) {
        return PendingIntent.getBroadcast(context, requestCode, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}

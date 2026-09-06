// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.Manifest;
import org.json.JSONObject;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.media.MediaMetadataRetriever;
import android.media.audiofx.Visualizer;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.OpenableColumns;
import android.text.Editable;
import android.text.TextWatcher;
import android.util.Log;
import android.view.Gravity;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.ArrayAdapter;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** MPCASU Android — native rewrite of the full Linux Qt player.
 *  Symbol-driven UI, 5 bottom tabs, everything touch-first. */
public class MainActivity extends Activity implements PlayerEngine.Listener {

    // palette (MPCASU red/black)
    private static final int BG = Color.parseColor("#0b0d10");
    private static final int SURFACE = Color.parseColor("#12151a");
    private static final int ACCENT = Color.parseColor("#ff1e2d");
    private static final int TEXT = Color.parseColor("#f2f4f7");
    private static final int MUTED = Color.parseColor("#9aa3ad");
    private static final int BORDER = Color.parseColor("#262b31");

    private static final int TAB_PLAY = 0, TAB_QUEUE = 1, TAB_LIBRARY = 2,
            TAB_WEB = 3, TAB_SETTINGS = 4;

    private static final String[] PROVIDER_NAMES = {"SPOTIFY", "HEARTHIS", "TIDAL", "NETFLIX", "BROWSE"};
    private static final String[] PROVIDER_URLS = {
            "https://open.spotify.com/", "https://hearthis.at/", "https://tidal.com/",
            "https://www.netflix.com/", "https://www.google.com/"};
    private static final int[] PROVIDER_COLORS = {
            Color.parseColor("#1DB954"),  // Spotify green
            Color.parseColor("#FF6B35"),  // HearThis orange
            Color.parseColor("#00FFFF"),  // Tidal cyan
            Color.parseColor("#E50914"),  // Netflix red
            Color.parseColor("#4285F4")}; // Browse blue

    private FrameLayout root;
    private FrameLayout content;
    private LinearLayout bottomNav;
    private final TextView[] navTabs = new TextView[5];
    private int activeTab = TAB_PLAY;

    // now playing
    private FrameLayout stage;
    private TextureView videoView;
    private WaveView waveView;
    private ImageView coverView;
    private TextView titleView;
    private TextView artistView;
    private TextView timeNow;
    private TextView timeTotal;
    private SeekBar seekBar;
    private Button playBtn;
    private Button shuffleBtn;
    private Button repeatBtn;
    private Button abBtn;
    private Button rateBtn;
    private Button recordBtn;
    private SeekBar volumeBar;
    private boolean draggingSeek;
    private boolean videoActive;

    // queue
    private ListView queueList;
    private QueueAdapter queueAdapter;
    private TextView queueSummary;
    private EditText queueSearch;

    // library
    private ListView libraryList;
    private LibraryAdapter libraryAdapter;
    private EditText librarySearch;
    private String libraryMode = "all";
    private String libraryGroupSelection;
    private List<String> libraryGroups = new ArrayList<>();
    private List<Library.Track> libraryTracks = new ArrayList<>();

    // web
    private LinearLayout providerGrid;

    // settings
    private SeekBar settingsVolume;
    private android.widget.CheckBox resumeBox;
    private android.widget.CheckBox consentBox;
    private TextView aboutBox;

    // engine + helpers
    private PlayerEngine engine;
    private Library library;
    private SubtitleLoader subtitles;
    private android.os.Handler ui;
    private Settings settings;
    private boolean recording;
    private Visualizer visualizer;

    // recording: StreamRecorder (MediaExtractor/MediaMuxer), SAF folder URI
    private StreamRecorder recorder;
    private String recordFormat = "mp4";
    private String recordFolderUri;   // SAF tree uri (or null → app dir)
    private String recordFolderName = "MPCASU (Standard)";
    private String recordSplitMode = "continuous";
    private int recordSplitMinutes = 10;
    private MediaItem pendingRecordItem;
    private String recordingItemUri;
    private String recordingTagSignature;
    private int recordingPart;
    private TextView recFolderLabel;  // folder label inside the open dialog

    // queue multi-select
    private final Set<Integer> multiSelected = new HashSet<>();
    private boolean multiSelectMode = false;

    // library multi-select
    private final Set<Integer> libMultiSelected = new HashSet<>();
    private boolean libMultiSelectMode = false;

    // saved playlists management
    private LinearLayout savedPlaylistsContainer;

    // persisted settings (JSON)
    public static final class Settings {
        public int volume = 100;
        public float rate = 1.0f;
        public boolean visualizer = true;
        public boolean resume = true;
        public boolean consent = false;
        public String subtitlePath = null;
        public String recordFormat = "mp4";
        public String recordFolder = null;
        public String recordSplitMode = "continuous";
        public int recordSplitMinutes = 10;

        public static Settings load(android.content.Context context) {
            Settings out = new Settings();
            try (java.io.FileInputStream in = new java.io.FileInputStream(
                    new java.io.File(context.getFilesDir(), "settings.json"))) {
                byte[] buf = new byte[in.available()];
                int read = in.read(buf);
                JSONObject o = new JSONObject(new String(buf, 0, Math.max(0, read)));
                out.volume = o.optInt("volume", 100);
                out.rate = (float) o.optDouble("rate", 1.0);
                out.visualizer = o.optBoolean("visualizer", true);
                out.resume = o.optBoolean("resume", true);
                out.consent = o.optBoolean("consent", false);
                out.subtitlePath = o.optString("subtitlePath", null);
                if (out.subtitlePath != null && out.subtitlePath.isEmpty()) out.subtitlePath = null;
                out.recordFormat = o.optString("recordFormat", "mp4");
                if (out.recordFormat.isEmpty()) out.recordFormat = "mp4";
                if (!StreamRecorder.formatSupported(out.recordFormat)) {
                    out.recordFormat = "mp4";
                }
                out.recordFolder = o.optString("recordFolder", null);
                if (out.recordFolder != null && out.recordFolder.isEmpty()) out.recordFolder = null;
                out.recordSplitMode = o.optString("recordSplitMode", "continuous");
                if (!java.util.Arrays.asList("continuous", "time", "track", "tags")
                        .contains(out.recordSplitMode)) out.recordSplitMode = "continuous";
                out.recordSplitMinutes = Math.max(1, Math.min(1440,
                        o.optInt("recordSplitMinutes", 10)));
            } catch (Exception ignored) {
            }
            return out;
        }

        public void save(android.content.Context context) {
            try {
                JSONObject o = new JSONObject();
                o.put("volume", volume);
                o.put("rate", rate);
                o.put("visualizer", visualizer);
                o.put("resume", resume);
                o.put("consent", consent);
                o.put("subtitlePath", subtitlePath == null ? "" : subtitlePath);
                o.put("recordFormat", recordFormat);
                o.put("recordFolder", recordFolder == null ? "" : recordFolder);
                o.put("recordSplitMode", recordSplitMode);
                o.put("recordSplitMinutes", recordSplitMinutes);
                try (java.io.FileOutputStream out = new java.io.FileOutputStream(
                        new java.io.File(context.getFilesDir(), "settings.json"))) {
                    out.write(o.toString().getBytes());
                }
            } catch (Exception ignored) {
            }
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ui = new android.os.Handler(getMainLooper());
        settings = Settings.load(this);
        recordFormat = settings.recordFormat;
        recordFolderUri = settings.recordFolder;
        recordSplitMode = settings.recordSplitMode;
        recordSplitMinutes = settings.recordSplitMinutes;
        // restore the persisted SAF permission (may be gone after reboot)
        if (recordFolderUri != null) {
            try {
                Uri tree = Uri.parse(recordFolderUri);
                androidx.documentfile.provider.DocumentFile dir =
                        androidx.documentfile.provider.DocumentFile.fromTreeUri(this, tree);
                if (dir == null || !dir.canWrite()) {
                    recordFolderUri = null;
                } else {
                    recordFolderName = dir.getName() == null ? "Ordner" : dir.getName();
                }
            } catch (Exception e) {
                recordFolderUri = null;
            }
        }

        // BUG 4+7 FIX: On cold start, delete stale queue.json so the queue
        // starts EMPTY. Library content belongs in the LIBRARY tab, not
        // preloaded into the queue from a previous session.
        clearStaleQueue();

        library = new Library(this);

        ensureEngine();
        requestPermissions();

        buildUi();
        setContentView(root);

        handleIntent(getIntent());
    }

    private void clearStaleQueue() {
        try {
            java.io.File qf = new java.io.File(getFilesDir(), "queue.json");
            if (qf.exists()) qf.delete();
        } catch (Exception ignored) {}
    }

    private void ensureEngine() {
        // Cold start: boot the service; its onCreate creates THE engine.
        // The engine appears asynchronously on the main thread — intents and
        // resume logic wait for it (withEngine) instead of using a transient
        // player that the service would never see (the old split-engine bug).
        if (engine == null) engine = PlaybackService.engine();
        if (engine == null) {
            Intent start = new Intent(this, PlaybackService.class);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(start);
            else startService(start);
            ui.postDelayed(() -> ensureEngine(), 50);
            return;
        }
        engine.addListener(this);
        onEngineReady();
    }

    private Runnable pendingOpen;
    private boolean engineReady;

    /** Runs the action once the service-owned engine exists. */
    private void withEngine(Runnable action) {
        if (engine != null && engineReady) {
            action.run();
            return;
        }
        pendingOpen = action;
    }

    private void onEngineReady() {
        engineReady = true;
        if (pendingOpen != null) {
            Runnable action = pendingOpen;
            pendingOpen = null;
            action.run();
            return;
        }
        maybeResume();
    }

    /** Resume setting: continue the last item at its saved position. */
    private void maybeResume() {
        if (engine == null || !settings.resume || engine.isPlaying()
                || engine.isPausedByUser() || engine.index() < 0
                || engine.position() > 0) {
            return;
        }
        QueueStore.Saved saved = engine.savedState();
        if (saved != null && saved.positionMs > 0) {
            engine.playIndex(engine.index(), saved.positionMs);
        }
    }

    private void requestPermissions() {
        if (Build.VERSION.SDK_INT >= 33) {
            List<String> needed = new ArrayList<>();
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                needed.add(Manifest.permission.POST_NOTIFICATIONS);
            }
            if (checkSelfPermission(Manifest.permission.READ_MEDIA_AUDIO)
                    != PackageManager.PERMISSION_GRANTED) {
                needed.add(Manifest.permission.READ_MEDIA_AUDIO);
            }
            if (checkSelfPermission(Manifest.permission.READ_MEDIA_VIDEO)
                    != PackageManager.PERMISSION_GRANTED) {
                needed.add(Manifest.permission.READ_MEDIA_VIDEO);
            }
            if (!needed.isEmpty()) requestPermissions(needed.toArray(new String[0]), 1);
        } else if (Build.VERSION.SDK_INT >= 23) {
            if (checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                    != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.READ_EXTERNAL_STORAGE}, 1);
            }
        }
    }

    // ================================================================== UI BUILD

    private void buildUi() {
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        content = new FrameLayout(this);
        root.addView(content, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        content.addView(buildPlayView());
        content.addView(buildQueueView());
        content.addView(buildLibraryView());
        content.addView(buildWebView());
        content.addView(buildSettingsView());

        bottomNav = buildBottomNav();
        root.addView(bottomNav, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(64), Gravity.BOTTOM));

        showTab(TAB_PLAY);
    }

    private LinearLayout buildBottomNav() {
        LinearLayout nav = new LinearLayout(this);
        nav.setOrientation(LinearLayout.HORIZONTAL);
        nav.setBackgroundColor(Color.parseColor("#0e1014"));
        nav.setGravity(Gravity.CENTER);
        String[] symbols = {"▶", "☰", "▣", "∿", "⚙"};
        String[] labels = {"PLAY", "QUEUE", "LIBRARY", "WEB", "SETUP"};
        for (int i = 0; i < 5; i++) {
            LinearLayout tab = new LinearLayout(this);
            tab.setOrientation(LinearLayout.VERTICAL);
            tab.setGravity(Gravity.CENTER);
            TextView icon = new TextView(this);
            icon.setText(symbols[i]);
            icon.setTextSize(20);
            icon.setGravity(Gravity.CENTER);
            TextView label = new TextView(this);
            label.setText(labels[i]);
            label.setTextSize(9);
            label.setGravity(Gravity.CENTER);
            tab.addView(icon);
            tab.addView(label);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.MATCH_PARENT, 1f);
            tab.setLayoutParams(params);
            final int tabIndex = i;
            tab.setOnClickListener(v -> showTab(tabIndex));
            nav.addView(tab);
            navTabs[i] = icon;
        }
        return nav;
    }

    private void showTab(int tab) {
        activeTab = tab;
        for (int i = 0; i < content.getChildCount(); i++) {
            content.getChildAt(i).setVisibility(i == tab ? View.VISIBLE : View.GONE);
        }
        for (int i = 0; i < navTabs.length; i++) {
            navTabs[i].setTextColor(i == tab ? ACCENT : MUTED);
        }
        if (tab == TAB_QUEUE) refreshQueueUi();
        if (tab == TAB_LIBRARY) refreshLibrary();
    }

    // ---------------------------------------------------------------- PLAY view

    private View buildPlayView() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(12), dp(12), dp(12), dp(76));

        stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.parseColor("#080a0d"));
        LinearLayout.LayoutParams stageParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        stageParams.bottomMargin = dp(10);
        page.addView(stage, stageParams);

        videoView = new TextureView(this);
        stage.addView(videoView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        videoView.setSurfaceTextureListener(new android.view.TextureView.SurfaceTextureListener() {
            @Override public void onSurfaceTextureAvailable(android.graphics.SurfaceTexture surface,
                                                            int width, int height) {
                if (engine != null) engine.setSurface(new Surface(surface));
            }
            @Override public void onSurfaceTextureSizeChanged(android.graphics.SurfaceTexture surface,
                                                              int width, int height) { }
            @Override public boolean onSurfaceTextureDestroyed(android.graphics.SurfaceTexture surface) {
                if (engine != null) engine.setSurface(null);
                return true;
            }
            @Override public void onSurfaceTextureUpdated(android.graphics.SurfaceTexture surface) { }
        });
        videoView.setVisibility(View.GONE);

        waveView = new WaveView(this);
        stage.addView(waveView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        coverView = new ImageView(this);
        coverView.setScaleType(ImageView.ScaleType.FIT_CENTER);
        coverView.setVisibility(View.GONE);
        stage.addView(coverView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        TextView badge = new TextView(this);
        badge.setText("MPCASU");
        badge.setTextColor(ACCENT);
        badge.setTextSize(11);
        badge.setTypeface(null, Typeface.BOLD);
        badge.setPadding(dp(10), dp(8), dp(10), dp(8));
        stage.addView(badge, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.START));

        // title + artist
        LinearLayout meta = new LinearLayout(this);
        meta.setOrientation(LinearLayout.VERTICAL);
        titleView = new TextView(this);
        titleView.setTextColor(TEXT);
        titleView.setTextSize(17);
        titleView.setTypeface(null, Typeface.BOLD);
        titleView.setSingleLine(true);
        titleView.setEllipsize(android.text.TextUtils.TruncateAt.MARQUEE);
        titleView.setSelected(true);
        artistView = new TextView(this);
        artistView.setTextColor(MUTED);
        artistView.setTextSize(12);
        meta.addView(titleView);
        meta.addView(artistView);
        page.addView(meta);

        // seek row
        seekBar = new SeekBar(this);
        seekBar.getProgressDrawable().setColorFilter(ACCENT, android.graphics.PorterDuff.Mode.SRC_IN);
        seekBar.getThumb().setColorFilter(ACCENT, android.graphics.PorterDuff.Mode.SRC_IN);
        page.addView(seekBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(30)));
        seekBar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar bar, int value, boolean fromUser) {
                if (fromUser) updateTimeLabels(value, bar.getMax());
            }
            @Override public void onStartTrackingTouch(SeekBar bar) { draggingSeek = true; }
            @Override public void onStopTrackingTouch(SeekBar bar) {
                draggingSeek = false;
                if (engine != null && bar.getMax() > 0) {
                    engine.seekTo((long) ((double) value(bar) / bar.getMax() * engine.duration()));
                }
            }
            private int value(SeekBar bar) { return bar.getProgress(); }
        });

        LinearLayout times = new LinearLayout(this);
        times.setOrientation(LinearLayout.HORIZONTAL);
        timeNow = new TextView(this);
        timeNow.setTextColor(MUTED);
        timeNow.setTextSize(11);
        timeTotal = new TextView(this);
        timeTotal.setTextColor(MUTED);
        timeTotal.setTextSize(11);
        timeTotal.setGravity(Gravity.END);
        times.addView(timeNow, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        times.addView(timeTotal, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        page.addView(times);

        // transport row
        LinearLayout transport = new LinearLayout(this);
        transport.setOrientation(LinearLayout.HORIZONTAL);
        transport.setGravity(Gravity.CENTER);
        transport.setPadding(0, dp(6), 0, dp(2));
        Button prev = transportButton("⏮", 22, TEXT);
        prev.setOnClickListener(v -> { if (engine != null) engine.previous(); });
        playBtn = transportButton("▶", 30, ACCENT);
        playBtn.setBackground(circleBackground());
        playBtn.setOnClickListener(v -> { if (engine != null) engine.playPause(); });
        Button next = transportButton("⏭", 22, TEXT);
        next.setOnClickListener(v -> { if (engine != null) engine.next(); });
        LinearLayout.LayoutParams playParams = new LinearLayout.LayoutParams(dp(76), dp(76));
        playParams.setMargins(dp(18), 0, dp(18), 0);
        playBtn.setLayoutParams(playParams);
        transport.addView(prev);
        transport.addView(playBtn);
        transport.addView(next);
        page.addView(transport);

        // secondary row (compact: shuffle/repeat/A-B/snapshot/rate)
        LinearLayout secondary = new LinearLayout(this);
        secondary.setOrientation(LinearLayout.HORIZONTAL);
        secondary.setGravity(Gravity.CENTER);
        shuffleBtn = smallButton("⤨");
        shuffleBtn.setOnClickListener(v -> {
            if (engine != null) {
                engine.setShuffle(!engine.shuffle());
                toast(engine.shuffle() ? "Shuffle an" : "Shuffle aus");
                refreshQueueUi();
            }
        });
        repeatBtn = smallButton("↻");
        repeatBtn.setOnClickListener(v -> {
            if (engine != null) {
                engine.cycleRepeat();
                toast("Repeat: " + engine.repeat());
                refreshQueueUi();
            }
        });
        abBtn = smallButton("A–B");
        abBtn.setOnClickListener(v -> {
            if (engine != null) toast(engine.cycleAbLoop());
        });
        Button snapshotBtn = smallButton("▧");
        snapshotBtn.setOnClickListener(v -> saveSnapshot());
        rateBtn = smallButton("1×");
        rateBtn.setOnClickListener(v -> {
            if (engine != null) {
                engine.cycleRate();
                rateBtn.setText(rateLabel(engine.rate()));
                toast("Rate " + rateLabel(engine.rate()));
            }
        });
        secondary.addView(shuffleBtn);
        secondary.addView(repeatBtn);
        secondary.addView(abBtn);
        secondary.addView(snapshotBtn);
        secondary.addView(rateBtn);
        page.addView(secondary);

        // record row — own, prominent, always visible
        LinearLayout recordRow = new LinearLayout(this);
        recordRow.setOrientation(LinearLayout.HORIZONTAL);
        recordRow.setGravity(Gravity.CENTER);
        recordRow.setPadding(0, dp(4), 0, 0);
        recordBtn = smallButton("● AUFNAHME");
        recordBtn.setTextSize(12);
        recordBtn.setTextColor(TEXT);
        recordBtn.setOnClickListener(v -> toggleRecording());
        recordRow.addView(recordBtn);
        page.addView(recordRow);

        // volume row
        LinearLayout volumeRow = new LinearLayout(this);
        volumeRow.setOrientation(LinearLayout.HORIZONTAL);
        volumeRow.setGravity(Gravity.CENTER_VERTICAL);
        volumeRow.setPadding(dp(8), 0, dp(8), 0);
        TextView volIcon = new TextView(this);
        volIcon.setText("♪");
        volIcon.setTextColor(MUTED);
        volumeBar = new SeekBar(this);
        volumeBar.setMax(100);
        volumeBar.setProgress(settings.volume);
        volumeBar.getProgressDrawable().setColorFilter(ACCENT, android.graphics.PorterDuff.Mode.SRC_IN);
        volumeBar.getThumb().setColorFilter(ACCENT, android.graphics.PorterDuff.Mode.SRC_IN);
        volumeBar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar bar, int value, boolean fromUser) {
                if (fromUser) {
                    settings.volume = value;
                    applyVolume();
                }
            }
            @Override public void onStartTrackingTouch(SeekBar bar) { }
            @Override public void onStopTrackingTouch(SeekBar bar) { settings.save(MainActivity.this); }
        });
        volumeRow.addView(volIcon);
        volumeRow.addView(volumeBar, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        page.addView(volumeRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // subtitle overlay lives on the stage
        TextView subtitleView = new TextView(this);
        subtitleView.setTextColor(TEXT);
        subtitleView.setTextSize(15);
        subtitleView.setGravity(Gravity.CENTER);
        subtitleView.setPadding(dp(16), 0, dp(16), dp(12));
        subtitleView.setId(View.generateViewId());
        subtitleView.setTag("subtitle");
        stage.addView(subtitleView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM));

        return page;
    }

    private android.graphics.drawable.GradientDrawable circleBackground() {
        android.graphics.drawable.GradientDrawable drawable =
                new android.graphics.drawable.GradientDrawable();
        drawable.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        drawable.setColor(Color.parseColor("#1c0d10"));
        drawable.setStroke(dp(2), ACCENT);
        return drawable;
    }

    private Button transportButton(String symbol, int sizeSp, int color) {
        Button button = new Button(this);
        button.setText(symbol);
        button.setTextColor(color);
        button.setTextSize(sizeSp);
        button.setBackgroundColor(Color.TRANSPARENT);
        button.setPadding(0, 0, 0, 0);
        button.setMinWidth(dp(56));
        button.setMinHeight(dp(56));
        return button;
    }

    private Button smallButton(String symbol) {
        Button button = new Button(this);
        button.setText(symbol);
        button.setTextColor(TEXT);
        button.setTextSize(14);
        button.setBackgroundColor(Color.parseColor("#161a20"));
        button.setPadding(dp(10), 0, dp(10), 0);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(40));
        params.setMargins(dp(4), 0, dp(4), 0);
        button.setLayoutParams(params);
        return button;
    }

    private static String rateLabel(float rate) {
        if (rate == (long) rate) return String.format(Locale.US, "%d×", (long) rate);
        return String.format(Locale.US, "%g×", rate);
    }

    // ---------------------------------------------------------------- QUEUE view

    private View buildQueueView() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(10), dp(10), dp(10), dp(76));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        Button add = smallButton("＋");
        add.setOnClickListener(v -> openFilePicker());
        Button addUrl = smallButton("∿");
        addUrl.setOnClickListener(v -> showAddUrlDialog());
        Button save = smallButton("⤓");
        save.setOnClickListener(v -> showSavePlaylistDialog());
        Button load = smallButton("⤒");
        load.setOnClickListener(v -> openPlaylistPicker());
        Button clear = smallButton("⌫");
        clear.setOnClickListener(v -> confirmClearQueue());
        header.addView(add);
        header.addView(addUrl);
        header.addView(load);
        header.addView(save);
        header.addView(clear);
        queueSummary = new TextView(this);
        queueSummary.setTextColor(MUTED);
        queueSummary.setTextSize(12);
        queueSummary.setGravity(Gravity.END);
        header.addView(queueSummary, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        page.addView(header);

        // multi-select action bar (hidden unless multi-select active)
        LinearLayout multiBar = new LinearLayout(this);
        multiBar.setOrientation(LinearLayout.HORIZONTAL);
        multiBar.setGravity(Gravity.CENTER_VERTICAL);
        multiBar.setBackgroundColor(Color.parseColor("#1a1014"));
        multiBar.setPadding(dp(8), dp(6), dp(8), dp(6));
        multiBar.setVisibility(View.GONE);
        multiBar.setTag("multi-bar");
        TextView multiCount = new TextView(this);
        multiCount.setTextColor(TEXT);
        multiCount.setTextSize(12);
        multiCount.setText("0 gewählt");
        multiCount.setTag("multi-count");
        multiBar.addView(multiCount, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button selectAllBtn = smallButton("Alle");
        selectAllBtn.setOnClickListener(v -> {
            multiSelected.clear();
            List<MediaItem> items = engine.items();
            for (int i = 0; i < items.size(); i++) multiSelected.add(i);
            refreshQueueUi();
        });
        multiBar.addView(selectAllBtn);
        Button deselectBtn = smallButton("Keine");
        deselectBtn.setOnClickListener(v -> {
            multiSelected.clear();
            refreshQueueUi();
        });
        multiBar.addView(deselectBtn);
        Button deleteBtn = smallButton("✕ Löschen");
        deleteBtn.setTextColor(ACCENT);
        deleteBtn.setOnClickListener(v -> {
            if (multiSelected.isEmpty()) return;
            new AlertDialog.Builder(this)
                    .setTitle(multiSelected.size() + " Einträge löschen?")
                    .setPositiveButton("Löschen", (d, w) -> {
                        List<Integer> sorted = new ArrayList<>(multiSelected);
                        Collections.sort(sorted, Collections.reverseOrder());
                        for (int idx : sorted) engine.removeAt(idx);
                        multiSelected.clear();
                        multiSelectMode = false;
                        refreshQueueUi();
                    })
                    .setNegativeButton("Abbrechen", null)
                    .show();
        });
        multiBar.addView(deleteBtn);
        Button exitMultiBtn = smallButton("✕");
        exitMultiBtn.setOnClickListener(v -> {
            multiSelected.clear();
            multiSelectMode = false;
            refreshQueueUi();
        });
        multiBar.addView(exitMultiBtn);
        page.addView(multiBar);

        queueSearch = new EditText(this);
        queueSearch.setHint("Queue durchsuchen…");
        queueSearch.setTextColor(TEXT);
        queueSearch.setHintTextColor(MUTED);
        queueSearch.setTextSize(13);
        queueSearch.setBackground(boxBackground());
        queueSearch.setPadding(dp(12), dp(8), dp(12), dp(8));
        queueSearch.setSingleLine(true);
        queueSearch.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) { }
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) { }
            @Override public void afterTextChanged(Editable s) { refreshQueueUi(); }
        });
        LinearLayout.LayoutParams searchParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        searchParams.topMargin = dp(8);
        searchParams.bottomMargin = dp(8);
        page.addView(queueSearch, searchParams);

        queueList = new ListView(this);
        queueList.setBackgroundColor(SURFACE);
        queueAdapter = new QueueAdapter();
        queueList.setAdapter(queueAdapter);
        queueList.setOnItemClickListener((parent, view, position, id) -> toggleQueueRow(position));
        queueList.setOnItemLongClickListener((parent, view, position, id) -> {
            if (!multiSelectMode) {
                multiSelectMode = true;
            }
            List<Integer> visible = visibleQueueIndexes();
            if (position < visible.size()) {
                int srcIdx = visible.get(position);
                multiSelected.add(srcIdx);
            }
            refreshQueueUi();
            return true;
        });
        page.addView(queueList, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout footer = new LinearLayout(this);
        footer.setOrientation(LinearLayout.HORIZONTAL);
        footer.setGravity(Gravity.CENTER);
        Button up = smallButton("↑");
        up.setOnClickListener(v -> moveSelected(-1));
        Button down = smallButton("↓");
        down.setOnClickListener(v -> moveSelected(1));
        Button rename = smallButton("✎");
        rename.setOnClickListener(v -> renameSelected());
        Button mergeBtn = smallButton("⊕");
        mergeBtn.setOnClickListener(v -> showMergeQueueDialog());
        footer.addView(up);
        footer.addView(down);
        footer.addView(rename);
        footer.addView(mergeBtn);
        page.addView(footer);
        return page;
    }

    private void moveSelected(int delta) {
        int position = queueAdapter.selected;
        List<Integer> visible = visibleQueueIndexes();
        int mapped = position >= 0 && position < visible.size() ? visible.get(position) : -1;
        if (mapped < 0) return;
        QueueGroups.Row row = queueRows().get(position);
        if (row.header) {
            int count = row.end - row.index;
            if (delta < 0) {
                if (row.index == 0) return;
                int target = row.index - 1;
                String group = engine.items().get(target).playlist;
                while (target > 0 && group != null && !group.isEmpty() && group.equals(engine.items().get(target - 1).playlist)) target--;
                for (int i = 0; i < count; i++) engine.move(row.index + i, target + i);
            } else {
                if (row.end >= engine.items().size()) return;
                int target = row.end;
                String group = engine.items().get(target).playlist;
                while (target + 1 < engine.items().size() && group != null && !group.isEmpty() && group.equals(engine.items().get(target + 1).playlist)) target++;
                for (int i = count - 1; i >= 0; i--) engine.move(row.index + i, target - (count - 1 - i));
            }
        } else {
            int target = mapped + delta;
            if (target < 0 || target >= engine.items().size()) return;
            engine.move(mapped, target);
        }
        queueAdapter.selected = -1;
        refreshQueueUi();
    }

    private void renameSelected() {
        int mapped = selectedQueueIndex();
        if (mapped < 0) {
            toast("Kein Eintrag gewählt");
            return;
        }
        MediaItem item = engine.items().get(mapped);
        EditText input = new EditText(this);
        QueueGroups.Row selectedRow = queueRows().get(queueAdapter.selected);
        input.setText(selectedRow.header ? item.playlist : item.title);
        input.setTextColor(TEXT);
        new AlertDialog.Builder(this)
                .setTitle("Umbenennen")
                .setView(input)
                .setPositiveButton("OK", (dialog, which) -> {
                    if (selectedRow.header) {
                        String label = input.getText().toString().trim();
                        if (label.isEmpty()) return;
                        for (int i = selectedRow.index; i < selectedRow.end; i++) engine.items().get(i).playlist = label;
                        engine.rename(mapped, item.title);
                    } else engine.rename(mapped, input.getText().toString());
                    refreshQueueUi();
                })
                .setNegativeButton("Abbrechen", null)
                .show();
    }

    private int selectedQueueIndex() {
        List<Integer> visible = visibleQueueIndexes();
        int position = queueAdapter.selected;
        return position >= 0 && position < visible.size() ? visible.get(position) : -1;
    }

    private final java.util.Set<String> expandedQueueGroups = new java.util.HashSet<>();

    private List<QueueGroups.Row> queueRows() {
        String query = queueSearch != null ? queueSearch.getText().toString().trim() : "";
        return QueueGroups.rows(engine.items(), expandedQueueGroups, query);
    }

    private List<Integer> visibleQueueIndexes() {
        List<Integer> out = new ArrayList<>();
        if (engine != null) for (QueueGroups.Row row : queueRows()) out.add(row.index);
        return out;
    }

    private void toggleQueueRow(int position) {
        List<QueueGroups.Row> rows = queueRows();
        if (position < 0 || position >= rows.size()) return;
        QueueGroups.Row row = rows.get(position);
        queueAdapter.selected = position;
        if (multiSelectMode) {
            boolean remove = multiSelected.contains(row.index);
            for (int i = row.index; i < row.end; i++) {
                if (remove) multiSelected.remove(i); else multiSelected.add(i);
            }
        } else if (row.header) {
            String group = engine.items().get(row.index).playlist;
            if (!expandedQueueGroups.remove(group)) expandedQueueGroups.add(group);
        } else {
            engine.playIndex(row.index);
        }
        refreshQueueUi();
    }

    private void refreshQueueUi() {
        if (queueAdapter == null) return;
        queueAdapter.reload();
        List<MediaItem> items = engine.items();
        queueSummary.setText(engine.items().size() + " Einträge"
                + (engine.shuffle() ? " · ⤨" : "")
                + ("one".equals(engine.repeat()) ? " · ↻1" : "all".equals(engine.repeat()) ? " · ↻∞" : ""));
        // multi-select bar
        LinearLayout multiBar = content.findViewWithTag("multi-bar");
        TextView multiCount = multiBar != null ? multiBar.findViewWithTag("multi-count") : null;
        if (multiSelectMode && !multiSelected.isEmpty()) {
            if (multiBar != null) multiBar.setVisibility(View.VISIBLE);
            if (multiCount != null) multiCount.setText(multiSelected.size() + " gewählt");
        } else if (!multiSelectMode) {
            if (multiBar != null) multiBar.setVisibility(View.GONE);
            multiSelected.clear();
        }
    }

    private void refreshLibMultiBar() {
        LinearLayout bar = content.findViewWithTag("lib-multi-bar");
        TextView count = bar != null ? bar.findViewWithTag("lib-multi-count") : null;
        if (libMultiSelectMode && !libMultiSelected.isEmpty()) {
            if (bar != null) bar.setVisibility(View.VISIBLE);
            if (count != null) count.setText(libMultiSelected.size() + " gewählt");
        } else {
            if (bar != null) bar.setVisibility(View.GONE);
            libMultiSelected.clear();
            libMultiSelectMode = false;
        }
    }

    private final class QueueAdapter extends BaseAdapter {
        private final List<MediaItem> visible = new ArrayList<>();
        private final List<Integer> sourceIndexes = new ArrayList<>();
        int selected = -1;

        void reload() {
            visible.clear();
            sourceIndexes.clear();
            sourceIndexes.addAll(visibleQueueIndexes());
            for (int index : sourceIndexes) visible.add(engine.items().get(index));
            notifyDataSetChanged();
        }

        @Override public int getCount() { return visible.size(); }
        @Override public Object getItem(int position) { return visible.get(position); }
        @Override public long getItemId(int position) { return position; }

        @Override public View getView(int position, View convertView, ViewGroup parent) {
            LinearLayout row = convertView instanceof LinearLayout ? (LinearLayout) convertView : createQueueRow();
            MediaItem item = visible.get(position);
            int sourceIndex = sourceIndexes.get(position);
            TextView title = row.findViewWithTag("qtitle");
            TextView badge = row.findViewWithTag("qbadge");
            TextView check = row.findViewWithTag("qcheck");
            QueueGroups.Row groupRow = queueRows().get(position);
            title.setText(groupRow.header ? item.playlist + " · " + (groupRow.end - groupRow.index) + " Videos"
                    : item.title + (item.artist != null && !item.artist.isEmpty() ? "\n" + item.artist : ""));
            badge.setText(groupRow.header ? (expandedQueueGroups.contains(item.playlist) ? "▼" : "▶") : item.badge);
            row.setPadding(dp(groupRow.header || item.playlist == null ? 12 : 28), dp(10), dp(12), dp(10));
            Button removeButton = row.findViewWithTag("qremove");
            removeButton.setOnClickListener(v -> {
                List<Integer> indexes = new ArrayList<>();
                for (int i = groupRow.index; i < groupRow.end; i++) indexes.add(i);
                engine.removeAll(indexes);
                refreshQueueUi();
            });
            boolean active = sourceIndex == engine.index();
            boolean selected = multiSelected.contains(sourceIndex);
            if (multiSelectMode) {
                check.setVisibility(View.VISIBLE);
                check.setText(selected ? "◉" : "○");
                check.setTextColor(selected ? ACCENT : MUTED);
            } else {
                check.setVisibility(View.GONE);
            }
            row.setBackgroundColor(selected ? Color.parseColor("#2a1018")
                    : active ? Color.parseColor("#2a1114") : SURFACE);
            title.setTextColor(active ? ACCENT : TEXT);
            return row;
        }

        private LinearLayout createQueueRow() {
            LinearLayout row = new LinearLayout(MainActivity.this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(12), dp(10), dp(12), dp(10));
            TextView check = new TextView(MainActivity.this);
            check.setTag("qcheck");
            check.setTextColor(MUTED);
            check.setTextSize(16);
            check.setVisibility(View.GONE);
            check.setPadding(0, 0, dp(6), 0);
            row.addView(check, new LinearLayout.LayoutParams(dp(24), dp(24)));
            TextView badge = new TextView(MainActivity.this);
            badge.setTag("qbadge");
            badge.setTextColor(ACCENT);
            badge.setTextSize(10);
            badge.setTypeface(null, Typeface.BOLD);
            badge.setGravity(Gravity.CENTER);
            badge.setBackground(boxBackground());
            row.addView(badge, new LinearLayout.LayoutParams(dp(52), dp(24)));
            TextView title = new TextView(MainActivity.this);
            title.setTag("qtitle");
            title.setTextColor(TEXT);
            title.setTextSize(13);
            title.setPadding(dp(12), 0, dp(8), 0);
            row.addView(title, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            Button remove = new Button(MainActivity.this);
            remove.setText("×");
            remove.setTextColor(MUTED);
            remove.setTextSize(14);
            remove.setBackgroundColor(Color.TRANSPARENT);
            remove.setPadding(dp(8), 0, dp(8), 0);
            remove.setOnClickListener(v -> {
                Object tag = v.getTag();
                if (tag != null) engine.removeAt((int) tag);
            });
            remove.setTag("qremove");
            row.addView(remove, new LinearLayout.LayoutParams(dp(40), dp(40)));
            row.setOnClickListener(v -> toggleQueueRow(queueList.getPositionForView(v)));
            row.setOnLongClickListener(v -> {
                int position = queueList.getPositionForView(v);
                if (!multiSelectMode) multiSelectMode = true;
                if (position >= 0 && position < sourceIndexes.size()) {
                    multiSelected.add(sourceIndexes.get(position));
                }
                refreshQueueUi();
                return true;
            });
            return row;
        }
    }

    // ---------------------------------------------------------------- LIBRARY view

    private View buildLibraryView() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(10), dp(10), dp(10), dp(76));

        librarySearch = new EditText(this);
        librarySearch.setHint("Bibliothek durchsuchen…");
        librarySearch.setTextColor(TEXT);
        librarySearch.setHintTextColor(MUTED);
        librarySearch.setTextSize(13);
        librarySearch.setBackground(boxBackground());
        librarySearch.setPadding(dp(12), dp(8), dp(12), dp(8));
        librarySearch.setSingleLine(true);
        librarySearch.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) { }
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) { }
            @Override public void afterTextChanged(Editable s) { refreshLibrary(); }
        });
        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setOrientation(LinearLayout.HORIZONTAL);
        searchRow.setGravity(Gravity.CENTER_VERTICAL);
        searchRow.addView(librarySearch, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button libRefresh = smallButton("⟳");
        libRefresh.setOnClickListener(v -> {
            if (library != null) library.rescan();
            refreshLibrary();
            toast("Bibliothek aktualisiert");
        });
        searchRow.addView(libRefresh);
        page.addView(searchRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        String[] modes = {"all", "artists", "albums", "genres", "favorites", "playlists"};
        String[] labels = {"ALLE", "ARTISTS", "ALBUMS", "GENRES", "★", "≡"};
        for (int i = 0; i < modes.length; i++) {
            Button chip = new Button(this);
            chip.setText(labels[i]);
            chip.setTextSize(10);
            chip.setTextColor(TEXT);
            chip.setBackgroundColor(Color.parseColor("#161a20"));
            chip.setPadding(dp(10), 0, dp(10), 0);
            String mode = modes[i];
            chip.setOnClickListener(v -> {
                libraryMode = mode;
                libraryGroupSelection = null;
                refreshLibrary();
            });
            chips.addView(chip);
        }
        page.addView(chips, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // library multi-select action bar
        LinearLayout libMultiBar = new LinearLayout(this);
        libMultiBar.setOrientation(LinearLayout.HORIZONTAL);
        libMultiBar.setGravity(Gravity.CENTER_VERTICAL);
        libMultiBar.setBackgroundColor(Color.parseColor("#1a1014"));
        libMultiBar.setPadding(dp(8), dp(6), dp(8), dp(6));
        libMultiBar.setVisibility(View.GONE);
        libMultiBar.setTag("lib-multi-bar");
        TextView libMultiCount = new TextView(this);
        libMultiCount.setTextColor(TEXT);
        libMultiCount.setTextSize(12);
        libMultiCount.setText("0 gewählt");
        libMultiCount.setTag("lib-multi-count");
        libMultiBar.addView(libMultiCount, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button libSelectAll = smallButton("Alle");
        libSelectAll.setOnClickListener(v -> {
            libMultiSelected.clear();
            for (int i = 0; i < libraryTracks.size(); i++) libMultiSelected.add(i);
            libraryAdapter.notifyDataSetChanged();
            refreshLibMultiBar();
        });
        libMultiBar.addView(libSelectAll);
        Button libDeselect = smallButton("Keine");
        libDeselect.setOnClickListener(v -> {
            libMultiSelected.clear();
            libraryAdapter.notifyDataSetChanged();
            refreshLibMultiBar();
        });
        libMultiBar.addView(libDeselect);
        Button libAddQueue = smallButton("▶ Queue");
        libAddQueue.setOnClickListener(v -> {
            if (libMultiSelected.isEmpty()) return;
            List<Integer> sorted = new ArrayList<>(libMultiSelected);
            Collections.sort(sorted);
            for (int idx : sorted) {
                if (idx < libraryTracks.size()) {
                    Library.Track track = libraryTracks.get(idx);
                    MediaItem item = track.toItem();
                    item.playlist = "LIBRARY";
                    engine.openExternal(item, true, engine.items().size());
                }
            }
            toast("▶ " + sorted.size() + " Tracks zur Queue hinzugefügt");
            libMultiSelected.clear();
            libMultiSelectMode = false;
            libraryAdapter.notifyDataSetChanged();
            refreshLibMultiBar();
        });
        libMultiBar.addView(libAddQueue);
        Button libFavToggle = smallButton("★");
        libFavToggle.setOnClickListener(v -> {
            if (libMultiSelected.isEmpty()) return;
            boolean anyFav = false;
            for (int idx : libMultiSelected) {
                if (idx < libraryTracks.size()) {
                    if (library.isFavorite(libraryTracks.get(idx).uri)) { anyFav = true; break; }
                }
            }
            for (int idx : libMultiSelected) {
                if (idx < libraryTracks.size()) {
                    boolean isFav = library.isFavorite(libraryTracks.get(idx).uri);
                    if (anyFav && isFav) library.toggleFavorite(libraryTracks.get(idx).uri);
                    else if (!anyFav && !isFav) library.toggleFavorite(libraryTracks.get(idx).uri);
                }
            }
            toast(anyFav ? "★ Favoriten entfernt" : "★ Favoriten hinzugefügt");
            refreshLibrary();
        });
        libMultiBar.addView(libFavToggle);
        Button libExitMulti = smallButton("✕");
        libExitMulti.setOnClickListener(v -> {
            libMultiSelected.clear();
            libMultiSelectMode = false;
            libraryAdapter.notifyDataSetChanged();
            refreshLibMultiBar();
        });
        libMultiBar.addView(libExitMulti);
        page.addView(libMultiBar);

        libraryList = new ListView(this);
        libraryList.setBackgroundColor(SURFACE);
        libraryAdapter = new LibraryAdapter();
        libraryList.setAdapter(libraryAdapter);
        libraryList.setOnItemClickListener((parent, view, position, id) -> {
            if (libMultiSelectMode) {
                if (libMultiSelected.contains(position)) libMultiSelected.remove(position);
                else libMultiSelected.add(position);
                libraryAdapter.notifyDataSetChanged();
                refreshLibMultiBar();
                return;
            }
            if (position < libraryTracks.size()) {
                Library.Track track = libraryTracks.get(position);
                if (isLibraryGroupingMode() && libraryGroupSelection == null
                        && position < libraryGroups.size()) {
                    libraryGroupSelection = libraryGroups.get(position);
                    refreshLibrary();
                    return;
                }
                if ("playlists".equals(libraryMode) && playlistFiles.containsKey(track.title.replaceFirst("^≡ ", ""))) {
                    openPlaylistFile(playlistFiles.get(track.title.replaceFirst("^≡ ", "")));
                    return;
                }
                MediaItem item = track.toItem();
                item.playlist = "LIBRARY";
                engine.openExternal(item, true, 0);
                toast("▶ " + item.title);
                showTab(TAB_PLAY);
            }
        });
        libraryList.setOnItemLongClickListener((parent, view, position, id) -> {
            if (position < libraryTracks.size()) {
                libMultiSelectMode = true;
                libMultiSelected.clear();
                libMultiSelected.add(position);
                libraryAdapter.notifyDataSetChanged();
                refreshLibMultiBar();
                return true;
            }
            return false;
        });
        page.addView(libraryList, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        return page;
    }

    private void refreshLibrary() {
        ui.post(() -> {
            if (library == null) return;
            if ("playlists".equals(libraryMode)) {
                scanPlaylists();
                return;
            }
            String query = librarySearch != null ? librarySearch.getText().toString().trim() : "";
            if (isLibraryGroupingMode()) {
                List<Library.Track> allAudio = library.query("", true, false);
                if (libraryGroupSelection == null) {
                    libraryGroups = Library.groups(allAudio, libraryMode, query);
                    List<Library.Track> groupRows = new ArrayList<>();
                    long id = -1;
                    for (String group : libraryGroups) {
                        int count = Library.tracksInGroup(allAudio, libraryMode, group).size();
                        groupRows.add(new Library.Track(id--, "library-group://" + libraryMode,
                                group, count + (count == 1 ? " track" : " tracks"),
                                libraryMode.substring(0, libraryMode.length() - 1), "", 0, false));
                    }
                    libraryTracks = groupRows;
                } else {
                    libraryGroups.clear();
                    libraryTracks = Library.tracksInGroup(allAudio, libraryMode, libraryGroupSelection);
                }
                if (libraryAdapter != null) libraryAdapter.notifyDataSetChanged();
                return;
            }
            libraryGroups.clear();
            boolean includeAudio = !"video-only".equals(libraryMode);
            boolean includeVideo = !"artists".equals(libraryMode) && !"albums".equals(libraryMode);
            List<Library.Track> tracks = library.query(query, includeAudio, includeVideo);
            if ("favorites".equals(libraryMode)) {
                tracks = library.filterFavorites(tracks);
            }
            libraryTracks = tracks;
            if (libraryAdapter != null) libraryAdapter.notifyDataSetChanged();
        });
    }

    private boolean isLibraryGroupingMode() {
        return "artists".equals(libraryMode) || "albums".equals(libraryMode)
                || "genres".equals(libraryMode);
    }

    private final java.util.Map<String, String> playlistFiles = new java.util.LinkedHashMap<>();

    private void scanPlaylists() {
        playlistFiles.clear();
        String[] exts = {".m3u", ".m3u8", ".pls", ".xspf"};
        java.util.Set<String> found = new java.util.LinkedHashSet<>();
        java.io.File extDir = android.os.Environment.getExternalStorageDirectory();
        scanPlaylistsInDir(extDir, exts, found);
        java.io.File dlDir = getExternalFilesDir(null);
        if (dlDir != null) scanPlaylistsInDir(dlDir, exts, found);
        java.io.File dlDir2 = android.os.Environment.getExternalStoragePublicDirectory(
                android.os.Environment.DIRECTORY_DOWNLOADS);
        if (dlDir2 != null) scanPlaylistsInDir(dlDir2, exts, found);
        for (String path : found) {
            java.io.File f = new java.io.File(path);
            playlistFiles.put(f.getName().replaceFirst("\\.[^.]+$", ""), path);
        }
        libraryTracks.clear();
        int idx = 0;
        for (java.util.Map.Entry<String, String> e : playlistFiles.entrySet()) {
            String parentName = new java.io.File(e.getValue()).getParentFile() != null
                    ? new java.io.File(e.getValue()).getParentFile().getName() : "";
            libraryTracks.add(new Library.Track(idx++, e.getValue(), "≡ " + e.getKey(),
                    parentName, "Playlist", "", 0, false));
        }
        if (libraryAdapter != null) libraryAdapter.notifyDataSetChanged();
    }

    private void scanPlaylistsInDir(java.io.File dir, String[] exts, java.util.Set<String> found) {
        if (dir == null || !dir.isDirectory()) return;
        try {
            java.io.File[] files = dir.listFiles();
            if (files == null) return;
            for (java.io.File f : files) {
                if (f.isDirectory() && !f.getName().startsWith(".")) {
                    scanPlaylistsInDir(f, exts, found);
                } else if (f.isFile()) {
                    String name = f.getName().toLowerCase(java.util.Locale.ROOT);
                    for (String ext : exts) {
                        if (name.endsWith(ext)) {
                            found.add(f.getAbsolutePath());
                            break;
                        }
                    }
                }
            }
        } catch (Exception ignored) {}
    }

    private void openPlaylistFile(String path) {
        java.io.File file = new java.io.File(path);
        if (!file.isFile()) {
            toast("Playlist nicht gefunden: " + path);
            return;
        }
        loadPlaylist(Uri.fromFile(file));
    }

    private final class LibraryAdapter extends BaseAdapter {
        @Override public int getCount() { return libraryTracks.size(); }
        @Override public Object getItem(int position) { return libraryTracks.get(position); }
        @Override public long getItemId(int position) { return position; }

        @Override public View getView(int position, View convertView, ViewGroup parent) {
            LinearLayout row = convertView instanceof LinearLayout ? (LinearLayout) convertView : null;
            if (row == null) {
                row = new LinearLayout(MainActivity.this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                row.setPadding(dp(12), dp(8), dp(12), dp(8));
                row.setGravity(Gravity.CENTER_VERTICAL);
                TextView check = new TextView(MainActivity.this);
                check.setTag("lcheck");
                check.setTextColor(ACCENT);
                check.setTextSize(14);
                check.setPadding(0, 0, dp(8), 0);
                check.setVisibility(android.view.View.GONE);
                row.addView(check, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT));
                LinearLayout textCol = new LinearLayout(MainActivity.this);
                textCol.setOrientation(LinearLayout.VERTICAL);
                textCol.setLayoutParams(new LinearLayout.LayoutParams(0,
                        LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
                TextView title = new TextView(MainActivity.this);
                title.setTag("ltitle");
                title.setTextColor(TEXT);
                title.setTextSize(13);
                title.setMaxLines(2);
                title.setEllipsize(android.text.TextUtils.TruncateAt.END);
                TextView meta = new TextView(MainActivity.this);
                meta.setTag("lmeta");
                meta.setTextColor(MUTED);
                meta.setTextSize(11);
                textCol.addView(title);
                textCol.addView(meta);
                row.addView(textCol);
            }
            Library.Track track = libraryTracks.get(position);
            TextView check = row.findViewWithTag("lcheck");
            TextView title = row.findViewWithTag("ltitle");
            TextView meta = row.findViewWithTag("lmeta");
            if (libMultiSelectMode) {
                check.setVisibility(android.view.View.VISIBLE);
                check.setText(libMultiSelected.contains(position) ? "◉" : "○");
            } else {
                check.setVisibility(android.view.View.GONE);
            }
            title.setText((library.isFavorite(track.uri) ? "★ " : "") + track.title);
            String details = join(" · ", track.artist, track.album, track.genre);
            meta.setText(details.isEmpty() ? (track.video ? "VIDEO" : "AUDIO") : details);
            row.setBackgroundColor(libMultiSelected.contains(position) && libMultiSelectMode
                    ? Color.parseColor("#2a1018") : Color.TRANSPARENT);
            return row;
        }
    }

    private static String join(String separator, String... parts) {
        StringBuilder sb = new StringBuilder();
        for (String part : parts) {
            if (part == null || part.isEmpty()) continue;
            if (sb.length() > 0) sb.append(separator);
            sb.append(part);
        }
        return sb.toString();
    }

    // ---------------------------------------------------------------- WEB view

    private View buildWebView() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(10), dp(10), dp(10), dp(76));

        TextView heading = new TextView(this);
        heading.setText("WEB & STREAMS");
        heading.setTextColor(ACCENT);
        heading.setTextSize(13);
        heading.setTypeface(null, Typeface.BOLD);
        page.addView(heading);

        providerGrid = new LinearLayout(this);
        providerGrid.setOrientation(LinearLayout.VERTICAL);
        for (int i = 0; i < PROVIDER_NAMES.length; i += 3) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            for (int j = i; j < Math.min(i + 3, PROVIDER_NAMES.length); j++) {
                LinearLayout card = new LinearLayout(this);
                card.setOrientation(LinearLayout.VERTICAL);
                card.setGravity(Gravity.CENTER);
                card.setBackground(boxBackground());
                card.setPadding(dp(8), dp(14), dp(8), dp(14));
                ImageView icon = new ImageView(this);
                Bitmap iconBmp = ProviderIcons.get(PROVIDER_NAMES[j]);
                if (iconBmp != null) {
                    icon.setImageBitmap(iconBmp);
                    icon.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
                } else {
                    icon.setImageBitmap(drawFallbackIcon(PROVIDER_NAMES[j],
                            PROVIDER_COLORS[j]));
                    icon.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
                }
                icon.setLayoutParams(new LinearLayout.LayoutParams(dp(56), dp(56)));
                TextView label = new TextView(this);
                label.setText(PROVIDER_NAMES[j]);
                label.setTextColor(PROVIDER_COLORS[j]);
                label.setTextSize(10);
                label.setGravity(Gravity.CENTER);
                label.setTypeface(null, Typeface.BOLD);
                card.addView(icon);
                card.addView(label);
                final String name = PROVIDER_NAMES[j];
                final String url = PROVIDER_URLS[j];
                card.setOnClickListener(v -> openProvider(name, url));
                row.addView(card, new LinearLayout.LayoutParams(0,
                        ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            }
            providerGrid.addView(row);
        }
        page.addView(providerGrid, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView ytHeading = new TextView(this);
        ytHeading.setText("YOUTUBE");
        ytHeading.setTextColor(ACCENT);
        ytHeading.setTextSize(13);
        ytHeading.setTypeface(null, Typeface.BOLD);
        LinearLayout.LayoutParams ytParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ytParams.topMargin = dp(14);
        page.addView(ytHeading, ytParams);

        // inline consent banner (shown when consent not yet given)
        LinearLayout consentBanner = new LinearLayout(this);
        consentBanner.setOrientation(LinearLayout.VERTICAL);
        consentBanner.setBackground(boxBackground());
        consentBanner.setPadding(dp(12), dp(10), dp(12), dp(10));
        consentBanner.setTag("yt-consent-banner");
        consentBanner.setVisibility(settings.consent ? View.GONE : View.VISIBLE);
        TextView consentInfo = new TextView(this);
        consentInfo.setText("YouTube-Suche nutzt die öffentliche Innertube-API.\n"
                + "Nur für private Nutzung. Bestätigen:");
        consentInfo.setTextColor(MUTED);
        consentInfo.setTextSize(11);
        consentBanner.addView(consentInfo);
        android.widget.CheckBox consentInline = new android.widget.CheckBox(this);
        consentInline.setText("YouTube aktivieren (nur privat)");
        consentInline.setTextColor(TEXT);
        consentInline.setTextSize(12);
        consentInline.setChecked(settings.consent);
        consentInline.setOnCheckedChangeListener((b, checked) -> {
            settings.consent = checked;
            settings.save(this);
            consentBanner.setVisibility(checked ? View.GONE : View.VISIBLE);
        });
        consentBanner.addView(consentInline);
        page.addView(consentBanner);

        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setOrientation(LinearLayout.HORIZONTAL);
        EditText ytQuery = new EditText(this);
        ytQuery.setHint("Suchbegriff oder YouTube-URL…");
        ytQuery.setTextColor(TEXT);
        ytQuery.setHintTextColor(MUTED);
        ytQuery.setTextSize(13);
        ytQuery.setBackground(boxBackground());
        ytQuery.setPadding(dp(12), dp(10), dp(12), dp(10));
        ytQuery.setSingleLine(true);
        ytQuery.setImeOptions(android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH);
        ytQuery.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH
                    || (event != null && event.getKeyCode() == android.view.KeyEvent.KEYCODE_ENTER
                    && event.getAction() == android.view.KeyEvent.ACTION_DOWN)) {
                runYouTubeSearch(ytQuery.getText().toString());
                return true;
            }
            return false;
        });
        ytQuery.setId(View.generateViewId());
        searchRow.addView(ytQuery, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button go = smallButton("▶");
        go.setOnClickListener(v -> runYouTubeSearch(ytQuery.getText().toString()));
        searchRow.addView(go);
        page.addView(searchRow);

        LinearLayout results = new LinearLayout(this);
        results.setOrientation(LinearLayout.VERTICAL);
        results.setId(View.generateViewId());
        results.setTag("yt-results");
        page.addView(results, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        return page;
    }

    private void runYouTubeSearch(String query) {
        if (query == null || query.trim().isEmpty()) return;
        if (!settings.consent) {
            toast("YouTube zuerst aktivieren (Inline-Checkbox oben)");
            LinearLayout consentBanner = content.findViewWithTag("yt-consent-banner");
            if (consentBanner != null) consentBanner.setVisibility(View.VISIBLE);
            return;
        }
        LinearLayout results = content.findViewWithTag("yt-results");
        results.removeAllViews();
        TextView status = new TextView(this);
        status.setTextColor(MUTED);
        status.setText("Suche…");
        results.addView(status);
        final String term = query.trim();
        new Thread(() -> {
            String finalError = null;
            List<YouTubeClient.Video> found = null;
            try {
                String id = YouTubeClient.extractVideoId(term);
                if (id != null && (term.contains("youtu") || term.length() == 11)) {
                    String mediaUrl = YouTubeClient.resolveMediaUrl(term);
                    MediaItem item = new MediaItem(mediaUrl, "YouTube " + id,
                            "youtube", "YT");
                    ui.post(() -> {
                        engine.openExternal(item, true, 0);
                        toast("▶ YouTube");
                        showTab(TAB_PLAY);
                    });
                    return;
                }
                found = YouTubeClient.search(term, 20);
            } catch (Exception e) {
                finalError = e.getMessage();
            }
            final List<YouTubeClient.Video> finalFound = found;
            final String error = finalError;
            ui.post(() -> {
                results.removeAllViews();
                if (error != null) {
                    TextView failed = new TextView(MainActivity.this);
                    failed.setTextColor(ACCENT);
                    failed.setText("Suche fehlgeschlagen: " + error);
                    results.addView(failed);
                    return;
                }
                if (finalFound == null || finalFound.isEmpty()) {
                    TextView empty = new TextView(MainActivity.this);
                    empty.setTextColor(MUTED);
                    empty.setText("Keine Ergebnisse");
                    results.addView(empty);
                    return;
                }
                for (YouTubeClient.Video video : finalFound) {
                    results.addView(youTubeResultRow(video));
                }
            });
        }).start();
    }

    private View youTubeResultRow(YouTubeClient.Video video) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(10), dp(8), dp(10), dp(8));
        row.setBackground(boxBackground());

        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rowParams.topMargin = dp(4);
        rowParams.bottomMargin = dp(4);
        row.setLayoutParams(rowParams);

        // thumbnail
        ImageView thumb = new ImageView(this);
        thumb.setScaleType(ImageView.ScaleType.CENTER_CROP);
        thumb.setBackgroundColor(Color.parseColor("#1a1d22"));
        LinearLayout.LayoutParams thumbParams = new LinearLayout.LayoutParams(dp(80), dp(56));
        thumbParams.setMarginEnd(dp(10));
        thumb.setLayoutParams(thumbParams);
        row.addView(thumb);

        // load thumbnail in background
        if (video.thumbnail != null && !video.thumbnail.isEmpty()) {
            final String thumbUrl = video.thumbnail;
            new Thread(() -> {
                try {
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection)
                            new URL(thumbUrl).openConnection();
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.setRequestProperty("User-Agent",
                            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36");
                    InputStream is = conn.getInputStream();
                    Bitmap bmp = android.graphics.BitmapFactory.decodeStream(is);
                    is.close();
                    if (bmp != null) {
                        ui.post(() -> thumb.setImageBitmap(bmp));
                    }
                } catch (Exception ignored) {}
            }).start();
        }

        // text column
        LinearLayout textCol = new LinearLayout(this);
        textCol.setOrientation(LinearLayout.VERTICAL);
        textCol.setLayoutParams(new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView title = new TextView(this);
        title.setTextColor(TEXT);
        title.setTextSize(13);
        title.setMaxLines(2);
        title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        title.setText(video.title);
        textCol.addView(title);

        TextView meta = new TextView(this);
        meta.setTextColor(MUTED);
        meta.setTextSize(11);
        String channel = video.channel == null ? "YouTube" : video.channel;
        String duration = video.durationSeconds > 0
                ? String.format(Locale.US, "%d:%02d",
                        video.durationSeconds / 60, video.durationSeconds % 60)
                : "";
        meta.setText(channel + (duration.isEmpty() ? "" : " · " + duration));
        textCol.addView(meta);

        row.addView(textCol);

        // play indicator
        TextView playArrow = new TextView(this);
        playArrow.setText("▶");
        playArrow.setTextColor(ACCENT);
        playArrow.setTextSize(14);
        playArrow.setPadding(dp(8), 0, 0, 0);
        row.addView(playArrow);

        row.setOnClickListener(v -> {
            toast("Lade… " + video.title);
            new Thread(() -> {
                try {
                    String mediaUrl = YouTubeClient.resolveMediaUrl(video.id);
                    MediaItem item = new MediaItem(mediaUrl,
                            video.title, video.durationSeconds > 0 ? "video" : "stream", "YT");
                    ui.post(() -> {
                        engine.openExternal(item, true, 0);
                        toast("▶ " + video.title);
                        showTab(TAB_PLAY);
                    });
                } catch (Exception e) {
                    ui.post(() -> toast("YouTube: " + e.getMessage()));
                }
            }).start();
        });
        return row;
    }

    private void openProvider(String name, String url) {
        Intent intent = new Intent(this, ProviderActivity.class);
        intent.putExtra("name", name);
        intent.putExtra("url", url);
        startActivity(intent);
    }

    // ---------------------------------------------------------------- SETTINGS view

    private View buildSettingsView() {
        android.widget.ScrollView scroll = new android.widget.ScrollView(this);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(16), dp(16), dp(16), dp(90));
        scroll.addView(page);

        page.addView(sectionLabel("PLAYBACK"));
        settingsVolume = new SeekBar(this);
        settingsVolume.setMax(100);
        settingsVolume.setProgress(settings.volume);
        settingsVolume.getProgressDrawable().setColorFilter(ACCENT, android.graphics.PorterDuff.Mode.SRC_IN);
        settingsVolume.getThumb().setColorFilter(ACCENT, android.graphics.PorterDuff.Mode.SRC_IN);
        settingsVolume.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar bar, int value, boolean fromUser) {
                if (fromUser) {
                    settings.volume = value;
                    applyVolume();
                    if (volumeBar != null) volumeBar.setProgress(value);
                }
            }
            @Override public void onStartTrackingTouch(SeekBar bar) { }
            @Override public void onStopTrackingTouch(SeekBar bar) { settings.save(MainActivity.this); }
        });
        page.addView(settingsVolume);

        resumeBox = new android.widget.CheckBox(this);
        resumeBox.setText("Wiedergabe beim Start fortsetzen");
        resumeBox.setTextColor(TEXT);
        resumeBox.setChecked(settings.resume);
        resumeBox.setOnCheckedChangeListener((button, checked) -> {
            settings.resume = checked;
            settings.save(this);
        });
        page.addView(resumeBox);

        page.addView(sectionLabel("VISUALIZER"));
        android.widget.CheckBox vizBox = new android.widget.CheckBox(this);
        vizBox.setText("Wellenform-Visualizer (Oszilloskop)");
        vizBox.setTextColor(TEXT);
        vizBox.setChecked(settings.visualizer);
        vizBox.setOnCheckedChangeListener((button, checked) -> {
            settings.visualizer = checked;
            settings.save(this);
            waveView.setVisibility(checked ? View.VISIBLE : View.GONE);
            if (!checked) {
                if (visualizer != null) {
                    try { visualizer.setEnabled(false); visualizer.release(); } catch (Exception ignored) {}
                    visualizer = null;
                }
            } else {
                attachVisualizer();
            }
        });
        page.addView(vizBox);

        page.addView(sectionLabel("LEGAL"));
        consentBox = new android.widget.CheckBox(this);
        consentBox.setText("YouTube/yt-dlp nur für private Nutzung aktivieren");
        consentBox.setTextColor(TEXT);
        consentBox.setChecked(settings.consent);
        consentBox.setOnCheckedChangeListener((button, checked) -> {
            settings.consent = checked;
            settings.save(this);
        });
        page.addView(consentBox);

        page.addView(sectionLabel("LIBRARY"));
        Button refreshLib = new Button(this);
        refreshLib.setText("Bibliothek aktualisieren");
        refreshLib.setTextColor(TEXT);
        refreshLib.setBackgroundColor(Color.parseColor("#161a20"));
        refreshLib.setOnClickListener(v -> {
            if (library != null) library.rescan();
            refreshLibrary();
            toast("Bibliothek aktualisiert");
        });
        page.addView(refreshLib);

        Button scanDir = new Button(this);
        scanDir.setText("Ordner scannen…");
        scanDir.setTextColor(TEXT);
        scanDir.setBackgroundColor(Color.parseColor("#161a20"));
        scanDir.setOnClickListener(v -> openFilePicker());
        page.addView(scanDir);

        Button managePlaylists = new Button(this);
        managePlaylists.setText("Playlists verwalten");
        managePlaylists.setTextColor(TEXT);
        managePlaylists.setBackgroundColor(Color.parseColor("#161a20"));
        managePlaylists.setOnClickListener(v -> showManagePlaylistsDialog());
        page.addView(managePlaylists);

        page.addView(sectionLabel("RECORDING"));
        final TextView recInfo = new TextView(this);
        recInfo.setTextColor(MUTED);
        recInfo.setTextSize(12);
        recInfo.setText(recordingInfoText());
        page.addView(recInfo);
        Button recSettings = new Button(this);
        recSettings.setText("Aufnahme-Einstellungen");
        recSettings.setTextColor(TEXT);
        recSettings.setBackgroundColor(Color.parseColor("#161a20"));
        recSettings.setOnClickListener(v -> {
            MediaItem item = engine != null ? engine.current() : null;
            if (item != null && item.url != null && item.url.startsWith("http")) {
                showRecordingSetupDialog(item);
                // keep the settings view in sync after the dialog closes
                recInfo.postDelayed(() -> recInfo.setText(recordingInfoText()), 800);
            } else {
                toast("Erst einen Stream abspielen (YouTube, Radio, …)");
            }
        });
        page.addView(recSettings);

        Button recOpen = new Button(this);
        recOpen.setText("Aufnahmen-Ordner öffnen (Dateien)");
        recOpen.setTextColor(TEXT);
        recOpen.setBackgroundColor(Color.parseColor("#161a20"));
        recOpen.setOnClickListener(v -> {
            File dir = new File(getExternalFilesDir(Environment.DIRECTORY_MUSIC),
                    "MPCASU");
            if (!dir.exists()) dir.mkdirs();
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(Uri.fromFile(dir), "resource/folder");
            try {
                startActivity(intent);
            } catch (Exception e) {
                toast("Ordner: " + dir.getAbsolutePath());
            }
        });
        page.addView(recOpen);

        page.addView(sectionLabel("ACTIONS"));
        Button loadSubtitle = new Button(this);
        loadSubtitle.setText("Untertitel laden (SRT/VTT)");
        loadSubtitle.setTextColor(TEXT);
        loadSubtitle.setBackgroundColor(Color.parseColor("#161a20"));
        loadSubtitle.setOnClickListener(v -> openSubtitlePicker());
        page.addView(loadSubtitle);

        Button info = new Button(this);
        info.setText("Media-Info");
        info.setTextColor(TEXT);
        info.setBackgroundColor(Color.parseColor("#161a20"));
        info.setOnClickListener(v -> showMediaInfo());
        page.addView(info);

        page.addView(sectionLabel("ABOUT"));
        aboutBox = new TextView(this);
        aboutBox.setTextColor(MUTED);
        aboutBox.setTextSize(12);
        aboutBox.setText("MPCASU 5.0 — Native Android\nMedia Player für CASU & Legacy-Medien\n"
                + "In-Process Playback · Kein externer Player\n\n"
                + "Design inspiriert von VLC und Webamp — unabhängiger Original-Code.\n"
                + "Anti-Capitalist License 1.4 · Lino Casu");
        page.addView(aboutBox);
        return scroll;
    }

    private TextView sectionLabel(String text) {
        TextView label = new TextView(this);
        label.setText(text);
        label.setTextColor(ACCENT);
        label.setTextSize(11);
        label.setTypeface(null, Typeface.BOLD);
        label.setPadding(0, dp(16), 0, dp(6));
        return label;
    }

    private android.graphics.drawable.GradientDrawable boxBackground() {
        android.graphics.drawable.GradientDrawable drawable =
                new android.graphics.drawable.GradientDrawable();
        drawable.setColor(Color.parseColor("#12151a"));
        drawable.setCornerRadius(dp(8));
        drawable.setStroke(1, BORDER);
        return drawable;
    }

    // ================================================================== ENGINE EVENTS

    @Override public void onStateChanged(boolean playing) {
        ui.post(() -> {
            playBtn.setText(playing ? "❚❚" : "▶");
            if (recordBtn != null) recordBtn.setTextColor(recording ? ACCENT : TEXT);
        });
    }

    @Override public void onItemChanged(MediaItem item, int index) {
        ui.post(() -> {
            String nextUri = item == null ? "" : item.url;
            String nextTags = item == null ? "" : (String.valueOf(item.title) + "\n"
                    + String.valueOf(item.artist) + "\n" + String.valueOf(item.badge));
            boolean trackBoundary = "track".equals(recordSplitMode)
                    && !nextUri.equals(recordingItemUri);
            boolean tagBoundary = "tags".equals(recordSplitMode)
                    && !nextTags.equals(recordingTagSignature);
            if (recording && recorder != null && (trackBoundary || tagBoundary)) {
                pendingRecordItem = item;
                recorder.stop();
            }
            titleView.setText(item != null && item.title != null ? item.title : "MPCASU");
            artistView.setText(item != null && item.badge != null ? item.badge : "");
            updateStageFor(item);
            loadCover(item);
            loadSubtitleFor(item);
            refreshQueueUi();
        });
    }

    @Override public void onPosition(long positionMs, long durationMs) {
        ui.post(() -> {
            if (draggingSeek) return;
            if (durationMs > 0) {
                seekBar.setMax((int) durationMs);
                seekBar.setProgress((int) positionMs);
            }
            updateTimeLabels(positionMs, durationMs);
        });
    }

    @Override public void onEnded(int finishedIndex) { }

    @Override public void onError(String userMessage) {
        ui.post(() -> toast(userMessage));
    }

    @Override public void onQueueChanged() {
        ui.post(this::refreshQueueUi);
    }

    @Override public void onTracksReady() {
        ui.post(() -> {
            boolean video = engine.videoWidth() > 0 && engine.videoHeight() > 0;
            videoActive = video;
            updateStageFor(engine.current());
            attachVisualizer();
            applyVolume();
        });
    }

    @Override public void onVideoSizeChanged(int width, int height) {
        ui.post(() -> {
            if (width > 0 && height > 0) {
                videoActive = true;
                updateStageFor(engine.current());
            }
        });
    }

    private void updateTimeLabels(long positionMs, long durationMs) {
        timeNow.setText(formatTime(positionMs));
        timeTotal.setText(formatTime(durationMs));
    }

    private static String formatTime(long ms) {
        long seconds = Math.max(0, ms) / 1000;
        return String.format(Locale.US, "%d:%02d", seconds / 60, seconds % 60);
    }

    // ================================================================== STAGE

    private void updateStageFor(MediaItem item) {
        boolean video = item != null && (item.isVideo() || (videoActive && engine.videoWidth() > 0));
        videoView.setVisibility(video ? View.VISIBLE : View.GONE);
        waveView.setVisibility(!video && settings.visualizer ? View.VISIBLE : View.GONE);
        coverView.setVisibility(!video && coverView.getVisibility() == View.VISIBLE
                && !settings.visualizer ? View.VISIBLE : View.GONE);
    }

    private void attachVisualizer() {
        if (engine == null || !settings.visualizer || videoActive) return;
        if (visualizer != null) {
            try { visualizer.release(); } catch (Exception ignored) {}
            visualizer = null;
        }
        visualizer = engine.attachVisualizer(7000, new Visualizer.OnDataCaptureListener() {
            @Override public void onWaveFormDataCapture(Visualizer vis, byte[] waveform, int samplingRate) {
                waveView.setWaveform(waveform);
            }
            @Override public void onFftDataCapture(Visualizer vis, byte[] fft, int samplingRate) { }
        });
    }

    private void loadCover(MediaItem item) {
        if (item == null || item.isVideo()) {
            coverView.setVisibility(View.GONE);
            return;
        }
        new Thread(() -> {
            Bitmap bitmap = null;
            try {
                MediaMetadataRetriever retriever = new MediaMetadataRetriever();
                try {
                    if (item.url.startsWith("content://")) {
                        retriever.setDataSource(this, Uri.parse(item.url));
                    } else if (item.url.startsWith("/")) {
                        retriever.setDataSource(item.url);
                    } else {
                        retriever.setDataSource(item.url, new java.util.HashMap<>());
                    }
                    byte[] art = retriever.getEmbeddedPicture();
                    if (art != null) {
                        bitmap = android.graphics.BitmapFactory.decodeByteArray(art, 0, art.length);
                    }
                } finally {
                    retriever.release();
                }
            } catch (Exception ignored) {
            }
            Bitmap finalBitmap = bitmap;
            ui.post(() -> {
                if (finalBitmap != null) {
                    coverView.setImageBitmap(finalBitmap);
                    if (!settings.visualizer) coverView.setVisibility(View.VISIBLE);
                } else {
                    coverView.setVisibility(View.GONE);
                }
            });
        }).start();
    }

    private void loadSubtitleFor(MediaItem item) {
        subtitles = null;
        TextView subtitleView = content.findViewWithTag("subtitle");
        if (subtitleView != null) subtitleView.setText("");
        if (item == null || item.subtitle == null || item.subtitle.isEmpty()) return;
        new Thread(() -> {
            try {
                SubtitleLoader loaded = SubtitleLoader.load(item.subtitle);
                subtitles = loaded;
                ui.post(() -> toast("Untertitel geladen · " + loaded.count() + " cues"));
            } catch (Exception e) {
                ui.post(() -> toast("Untertitel konnte nicht geladen werden"));
            }
        }).start();
    }

    // ================================================================== ACTIONS

    private void applyVolume() {
        // Volume maps linearly from settings.volume (0..100) to the engine's
        // gain control, which routes to MediaPlayer (local) or libVLC (streams).
        if (engine != null) {
            float gain = Math.max(0f, Math.min(1f, settings.volume / 100f));
            engine.setGain(gain);
        }
    }

    private void saveSnapshot() {
        MediaItem item = engine != null ? engine.current() : null;
        if (item == null) {
            toast("Keine Wiedergabe aktiv");
            return;
        }
        new Thread(() -> {
            try {
                MediaMetadataRetriever retriever = new MediaMetadataRetriever();
                try {
                    if (item.url.startsWith("content://")) {
                        retriever.setDataSource(this, Uri.parse(item.url));
                    } else if (item.url.startsWith("/")) {
                        retriever.setDataSource(item.url);
                    } else {
                        retriever.setDataSource(item.url, new java.util.HashMap<>());
                    }
                    Bitmap frame = retriever.getFrameAtTime(engine.position() * 1000,
                            MediaMetadataRetriever.OPTION_CLOSEST);
                    if (frame == null) {
                        ui.post(() -> toast("Kein Video-Frame verfügbar"));
                        return;
                    }
                    File dir = new File(getExternalFilesDir(Environment.DIRECTORY_PICTURES),
                            "MPCASU");
                    if (!dir.exists()) dir.mkdirs();
                    File out = new File(dir, "snapshot-"
                            + new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
                            .format(new Date()) + ".png");
                    try (FileOutputStream stream = new FileOutputStream(out)) {
                        frame.compress(Bitmap.CompressFormat.PNG, 90, stream);
                    }
                    ui.post(() -> toast("Snapshot gespeichert · " + out.getName()));
                } finally {
                    retriever.release();
                }
            } catch (Exception e) {
                ui.post(() -> toast("Snapshot fehlgeschlagen: " + e.getMessage()));
            }
        }).start();
    }

    private String recordingInfoText() {
        return "Format: " + recordFormat.toUpperCase(Locale.ROOT)
                + "\nOrdner: " + recordFolderName;
    }

    private void toggleRecording() {
        if (recording && recorder != null) {
            toast("Aufnahme wird abgeschlossen…");
            recorder.stop();
            return;
        }
        MediaItem item = engine != null ? engine.current() : null;
        if (item == null || item.url == null) {
            toast("Erst etwas abspielen (YouTube, Radio, Datei, …)");
            return;
        }
        String recordingSource = PlaylistIO.normalizePlayableLocation(item.url);
        boolean localFile = item.isLocalFile();
        boolean stream = "stream".equals(item.kind)
                || recordingSource.startsWith("http://")
                || recordingSource.startsWith("https://");
        if (!localFile && !stream) {
            toast("Diese Quelle kann nicht aufgenommen werden");
            return;
        }
        showRecordingSetupDialog(item);
    }

    private void showRecordingSetupDialog(MediaItem item) {
        final boolean srcIsVideo = item.isVideo();
        final String[] formats = {StreamRecorder.FMT_MP4, StreamRecorder.FMT_M4A,
                StreamRecorder.FMT_COPY};
        final String[] labels = {"MP4 — Video + Audio", "M4A/AAC — nur Audio",
                "Original — Stream-Kopie (Radio/TS)"};
        int checked = 0;
        for (int i = 0; i < formats.length; i++) {
            if (formats[i].equals(recordFormat)) { checked = i; break; }
        }

        LinearLayout dialog = new LinearLayout(this);
        dialog.setOrientation(LinearLayout.VERTICAL);
        dialog.setPadding(dp(20), dp(10), dp(20), dp(4));

        TextView formatLabel = new TextView(this);
        formatLabel.setText("Aufnahme-Format:");
        formatLabel.setTextColor(TEXT);
        formatLabel.setTextSize(13);
        formatLabel.setTypeface(null, Typeface.BOLD);
        dialog.addView(formatLabel);

        final int[] selectedFormat = {checked};
        android.widget.RadioGroup formatGroup = new android.widget.RadioGroup(this);
        formatGroup.setOrientation(android.widget.RadioGroup.VERTICAL);
        for (int i = 0; i < formats.length; i++) {
            android.widget.RadioButton rb = new android.widget.RadioButton(this);
            rb.setText(labels[i]);
            rb.setTextColor(TEXT);
            rb.setTextSize(12);
            rb.setChecked(formats[i].equals(recordFormat));
            final String fmt = formats[i];
            rb.setOnCheckedChangeListener((b, isChecked) -> {
                if (isChecked) selectedFormat[0] = java.util.Arrays.asList(formats).indexOf(fmt);
            });
            formatGroup.addView(rb);
        }
        dialog.addView(formatGroup);

        TextView splitLabel = new TextView(this);
        splitLabel.setText("Aufnahme aufteilen:");
        splitLabel.setTextColor(TEXT);
        splitLabel.setTypeface(null, Typeface.BOLD);
        splitLabel.setPadding(0, dp(12), 0, 0);
        dialog.addView(splitLabel);
        Spinner splitSpinner = new Spinner(this);
        String[] splitLabels = {"Eine Datei", "Nach Zeit", "Bei Trackwechsel",
                "Bei Titel-/Tagwechsel"};
        String[] splitValues = {"continuous", "time", "track", "tags"};
        ArrayAdapter<String> splitAdapter = darkSpinnerAdapter(splitLabels);
        splitSpinner.setAdapter(splitAdapter);
        int splitIndex = java.util.Arrays.asList(splitValues).indexOf(recordSplitMode);
        splitSpinner.setSelection(Math.max(0, splitIndex));
        dialog.addView(splitSpinner);

        EditText minutesInput = new EditText(this);
        minutesInput.setHint("Minuten (nur bei Nach Zeit)");
        minutesInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        minutesInput.setText(String.valueOf(recordSplitMinutes));
        minutesInput.setTextColor(TEXT);
        dialog.addView(minutesInput);

        // ---- folder picker row (SAF) ----
        TextView folderLabel = new TextView(this);
        folderLabel.setText("Zielordner:");
        folderLabel.setTextColor(TEXT);
        folderLabel.setTextSize(13);
        folderLabel.setTypeface(null, Typeface.BOLD);
        folderLabel.setPadding(0, dp(14), 0, 0);
        dialog.addView(folderLabel);

        LinearLayout folderRow = new LinearLayout(this);
        folderRow.setOrientation(LinearLayout.HORIZONTAL);
        folderRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView folderName = new TextView(this);
        folderName.setText(recordFolderName);
        folderName.setTextColor(MUTED);
        folderName.setTextSize(12);
        folderName.setSingleLine(true);
        folderName.setEllipsize(android.text.TextUtils.TruncateAt.MIDDLE);
        recFolderLabel = folderName;
        folderRow.addView(folderName, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button pickFolder = smallButton("Ordner wählen…");
        pickFolder.setTextSize(11);
        pickFolder.setOnClickListener(v -> {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
            try {
                startActivityForResult(intent, REQUEST_PICK_RECORD_FOLDER);
            } catch (Exception e) {
                toast("Kein Ordner-Dialog verfügbar");
            }
        });
        folderRow.addView(pickFolder);
        dialog.addView(folderRow);

        Button resetFolder = new Button(this);
        resetFolder.setText("Zurücksetzen auf Standardordner");
        resetFolder.setTextColor(MUTED);
        resetFolder.setTextSize(11);
        resetFolder.setBackgroundColor(Color.TRANSPARENT);
        resetFolder.setPadding(0, 0, 0, 0);
        resetFolder.setOnClickListener(v -> {
            recordFolderUri = null;
            recordFolderName = "MPCASU (Standard)";
            settings.recordFolder = null;
            settings.save(MainActivity.this);
            if (recFolderLabel != null) recFolderLabel.setText(recordFolderName);
        });
        dialog.addView(resetFolder);

        new AlertDialog.Builder(this)
                .setTitle("Aufnahme starten")
                .setView(dialog)
                .setPositiveButton("● Aufnahme", (d, w) -> {
                    recordFormat = formats[selectedFormat[0]];
                    settings.recordFormat = recordFormat;
                    recordSplitMode = splitValues[splitSpinner.getSelectedItemPosition()];
                    try { recordSplitMinutes = Math.max(1,
                            Integer.parseInt(minutesInput.getText().toString())); }
                    catch (NumberFormatException ignored) { recordSplitMinutes = 10; }
                    settings.recordSplitMode = recordSplitMode;
                    settings.recordSplitMinutes = recordSplitMinutes;
                    settings.save(MainActivity.this);
                    recordingPart = 0;
                    startRecording(item);
                })
                .setNegativeButton("Abbrechen", null)
                .show();
    }

    /** Keep both the collapsed field and platform popup inside the MPCASU dark contract. */
    private ArrayAdapter<String> darkSpinnerAdapter(String[] labels) {
        return new ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, labels) {
            private View style(View row, boolean selected) {
                TextView text = (TextView) row;
                text.setTextColor(TEXT);
                text.setBackgroundColor(selected ? Color.parseColor("#3a1015") : Color.parseColor("#080a0c"));
                text.setPadding(dp(12), dp(10), dp(12), dp(10));
                return text;
            }

            @Override public View getView(int position, View convertView, ViewGroup parent) {
                return style(super.getView(position, convertView, parent), false);
            }

            @Override public View getDropDownView(int position, View convertView, ViewGroup parent) {
                return style(super.getDropDownView(position, convertView, parent),
                        position == splitSpinnerPosition(parent));
            }

            private int splitSpinnerPosition(ViewGroup parent) {
                return parent instanceof ListView ? ((ListView) parent).getCheckedItemPosition() : -1;
            }
        };
    }

    private void startRecording(MediaItem item) {
        // Recording always lands in the app's Music/MPCASU dir (no storage
        // permission needed). libVLC writes the transcode output there.
        File dir = new File(getExternalFilesDir(Environment.DIRECTORY_MUSIC),
                "MPCASU");
        if (!dir.exists()) dir.mkdirs();
        String ext = StreamRecorder.extensionFor(recordFormat, item.url);
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date());
        String part = "continuous".equals(recordSplitMode) ? ""
                : String.format(Locale.US, "-part%03d", ++recordingPart);
        File target = new File(dir, "rec-" + stamp + part + "." + ext);
        recordingItemUri = item.url;
        recordingTagSignature = String.valueOf(item.title) + "\n"
                + String.valueOf(item.artist) + "\n" + String.valueOf(item.badge);

        final String format = recordFormat;
        recorder = new StreamRecorder(this,
                PlaylistIO.normalizePlayableLocation(item.url), target, format,
                new StreamRecorder.Listener() {
                    private String fileName = "rec";

                    @Override public void onStarted(String info) {
                        ui.post(() -> toast(info));
                    }

                    @Override public void onProgress(long seconds, long bytes) {
                        ui.post(() -> {
                            if (recordBtn != null) {
                                recordBtn.setText("● " + seconds + "s · "
                                        + (bytes / 1024) + " KB");
                            }
                        });
                    }

                    @Override public void onFinished(String name, long bytes,
                                                     String error) {
                        ui.post(() -> {
                            recording = false;
                            if (recordBtn != null) {
                                recordBtn.setText("● AUFNAHME");
                                recordBtn.setTextColor(TEXT);
                            }
                            if (error != null) {
                                toast("Aufnahme fehlgeschlagen: " + error);
                            } else {
                                toast("Aufnahme gespeichert · " + name + " · "
                                        + (bytes / 1024) + " KB");
                            }
                            MediaItem restart = pendingRecordItem;
                            pendingRecordItem = null;
                            if (restart != null && error == null) startRecording(restart);
                        });
                    }
                });
        recording = true;
        recordBtn.setTextColor(ACCENT);
        recordBtn.setText("● starte…");
        toast("Aufnahme läuft · Format " + recordFormat.toUpperCase(Locale.ROOT)
                + " · Ziel: " + recordFolderName);
        recorder.start();
        if ("time".equals(recordSplitMode)) {
            ui.postDelayed(() -> {
                if (recording && recorder != null && "time".equals(recordSplitMode)) {
                    pendingRecordItem = engine != null ? engine.current() : item;
                    recorder.stop();
                }
            }, recordSplitMinutes * 60_000L);
        }
    }

    private void showMediaInfo() {
        MediaItem item = engine != null ? engine.current() : null;
        StringBuilder info = new StringBuilder();
        if (item == null) {
            info.append("Keine Wiedergabe aktiv");
        } else {
            info.append("Titel: ").append(item.title).append('\n');
            info.append("Badge: ").append(item.badge).append('\n');
            info.append("Quelle: ").append(item.url).append('\n');
            info.append("Position: ").append(formatTime(engine.position()))
                .append(" / ").append(formatTime(engine.duration())).append('\n');
            info.append("Video: ").append(engine.videoWidth()).append("×")
                .append(engine.videoHeight()).append('\n');
            if (item.url.toLowerCase().endsWith(".casu")) {
                String verify = CasuBridge.verifyCasunat2(item.url);
                info.append("CASU: ").append(verify.startsWith("ERROR")
                        ? verify : "Manifest verifiziert ✓");
            }
        }
        new AlertDialog.Builder(this)
                .setTitle("Media-Info")
                .setMessage(info.toString())
                .setPositiveButton("OK", null)
                .show();
    }

    private void confirmClearQueue() {
        new AlertDialog.Builder(this)
                .setTitle("Queue leeren?")
                .setPositiveButton("Leeren", (dialog, which) -> engine.clear())
                .setNegativeButton("Abbrechen", null)
                .show();
    }

    private void showAddUrlDialog() {
        EditText input = new EditText(this);
        input.setHint("http(s)://, rtsp:// …");
        input.setTextColor(TEXT);
        new AlertDialog.Builder(this)
                .setTitle("Netzwerk-Stream hinzufügen")
                .setView(input)
                .setPositiveButton("Hinzufügen", (dialog, which) -> {
                    String url = input.getText().toString().trim();
                    if (url.isEmpty()) return;
                    if (url.contains("youtu")) {
                        expandYouTubeAdd(url);
                        return;
                    }
                    MediaItem item = new MediaItem(url, null, "stream", null);
                    engine.openExternal(item, true, 0);
                })
                .setNegativeButton("Abbrechen", null)
                .show();
    }

    /** Expand a free-form YouTube field (several videos and/or full playlists,
     *  separated by commas/semicolons/line breaks) into individual queue items
     *  and start playing the first one. Linux/Windows parity. */
    private void expandYouTubeAdd(String url) {
        new Thread(() -> {
            final List<MediaItem> toAdd = new ArrayList<>();
            final boolean[] failed = {false};
            final String[] errorMsg = {null};
            final List<String> tokens = new ArrayList<>();
            for (String t : url.split("[\n,;]+")) {
                String s = t.trim();
                if (!s.isEmpty()) tokens.add(s);
            }
            try {
                for (String token : tokens) {
                    String playlistId = YouTubeClient.extractPlaylistId(token);
                    if (playlistId != null) {
                        List<YouTubeClient.Video> videos = YouTubeClient.fetchPlaylist(playlistId);
                        int n = 0;
                        for (YouTubeClient.Video v : videos) {
                            if (n >= 100) break;
                            try {
                                String mediaUrl = "https://www.youtube.com/watch?v=" + v.id;
                                MediaItem item = new MediaItem(mediaUrl,
                                        v.title != null && !v.title.isEmpty() ? v.title : "YouTube " + v.id,
                                        "youtube", "YT");
                                item.playlist = "YouTube " + playlistId;
                                item.sourceUrl = "https://www.youtube.com/watch?v=" + v.id;
                                toAdd.add(item);
                            } catch (Exception ignored) {
                            }
                            n++;
                        }
                    } else {
                        String id = YouTubeClient.extractVideoId(token);
                        if (id == null) continue;
                        String mediaUrl = "https://www.youtube.com/watch?v=" + id;
                        MediaItem item = new MediaItem(mediaUrl, "YouTube " + id, "youtube", "YT");
                        item.sourceUrl = "https://www.youtube.com/watch?v=" + id;
                        toAdd.add(item);
                    }
                }
            } catch (Exception e) {
                failed[0] = true;
                errorMsg[0] = e.getMessage();
            }
            final List<MediaItem> added = toAdd;
            final boolean failedFinal = failed[0];
            final String errorFinal = errorMsg[0];
            ui.post(() -> {
                if (failedFinal) {
                    toast("YouTube: " + errorFinal);
                    return;
                }
                if (added.isEmpty()) {
                    toast("Keine YouTube-Videos erkannt");
                    return;
                }
                MediaItem first = added.get(0);
                engine.addAll(added);
                engine.openExternal(first, false, 0);
                toast(added.size() + " Video(s) zur Queue hinzugefügt · ▶");
                showTab(TAB_PLAY);
            });
        }).start();
    }

    private void showSavePlaylistDialog() {
        EditText name = new EditText(this);
        name.setHint("Playlist-Name");
        name.setTextColor(TEXT);
        String[] formats = {"m3u", "m3u8", "pls", "xspf", "jspf", "json"};
        new AlertDialog.Builder(this)
                .setTitle("Queue speichern als")
                .setView(name)
                .setItems(formats, (dialog, which) -> {
                    String base = name.getText().toString().trim();
                    if (base.isEmpty()) base = "playlist";
                    File dir = new File(getExternalFilesDir(Environment.DIRECTORY_MUSIC), "MPCASU");
                    if (!dir.exists()) dir.mkdirs();
                    base = new File(base).getName().replaceAll("(?i)\\.(m3u8?|pls|xspf|jspf|json)$", "");
                    File target = new File(dir, base + "." + formats[which]);
                    try {
                        String text;
                        if (formats[which].startsWith("m3u")) text = PlaylistIO.writeM3u(base, engine.items());
                        else if ("pls".equals(formats[which])) text = PlaylistIO.writePls(engine.items());
                        else if ("xspf".equals(formats[which])) text = PlaylistIO.writeXspf(base, engine.items());
                        else if ("jspf".equals(formats[which])) text = PlaylistIO.writeJspf(base, engine.items());
                        else text = PlaylistIO.writeCasuJson(base, engine.items());
                        PlaylistIO.writeText(target.getAbsolutePath(), text);
                        toast("Gespeichert · " + target.getName());
                    } catch (Exception e) {
                        toast("Speichern fehlgeschlagen: " + e.getMessage());
                    }
                })
                .setNegativeButton("Abbrechen", null)
                .show();
    }

    private void showMergeQueueDialog() {
        File dir = new File(getExternalFilesDir(Environment.DIRECTORY_MUSIC), "MPCASU");
        if (!dir.exists()) dir.mkdirs();
        File[] files = dir.listFiles((d, name) ->
                name.endsWith(".m3u") || name.endsWith(".m3u8") || name.endsWith(".pls")
                || name.endsWith(".xspf") || name.endsWith(".jspf") || name.endsWith(".json")
                || name.endsWith(".asx") || name.endsWith(".wpl"));
        if (files == null || files.length == 0) {
            toast("Keine gespeicherten Playlists gefunden");
            return;
        }
        String[] names = new String[files.length + 1];
        for (int i = 0; i < files.length; i++) names[i] = files[i].getName();
        names[files.length] = "URL hinzufügen…";
        new AlertDialog.Builder(this)
                .setTitle("Queue mergen / erweitern")
                .setItems(names, (dialog, which) -> {
                    if (which < files.length) {
                        loadPlaylist(new Uri.Builder().path(files[which].getAbsolutePath()).build());
                        toast("Merging… " + files[which].getName());
                    } else {
                        showAddUrlDialog();
                    }
                })
                .setNegativeButton("Abbrechen", null)
                .show();
    }

    private void showManagePlaylistsDialog() {
        File dir = new File(getExternalFilesDir(Environment.DIRECTORY_MUSIC), "MPCASU");
        if (!dir.exists()) dir.mkdirs();
        File[] files = dir.listFiles((d, name) ->
                name.endsWith(".m3u") || name.endsWith(".m3u8") || name.endsWith(".pls")
                || name.endsWith(".xspf") || name.endsWith(".jspf") || name.endsWith(".json")
                || name.endsWith(".asx") || name.endsWith(".wpl"));
        if (files == null || files.length == 0) {
            toast("Keine gespeicherten Playlists");
            return;
        }
        String[] names = new String[files.length];
        for (int i = 0; i < files.length; i++) names[i] = files[i].getName();
        new AlertDialog.Builder(this)
                .setTitle("Playlists verwalten")
                .setItems(names, (dialog, which) -> {
                    final File file = files[which];
                    new AlertDialog.Builder(this)
                            .setTitle(file.getName())
                            .setItems(new String[]{"Abspielen", "In Queue mergen", "Löschen"},
                                    (d2, which2) -> {
                                        if (which2 == 0) {
                                            loadPlaylist(Uri.fromFile(file));
                                        } else if (which2 == 1) {
                                            loadPlaylist(Uri.fromFile(file));
                                            toast("Gemerged: " + file.getName());
                                        } else {
                                            new AlertDialog.Builder(this)
                                                    .setTitle(file.getName() + " löschen?")
                                                    .setPositiveButton("Löschen", (d3, w3) -> {
                                                        if (file.delete()) {
                                                            toast("Gelöscht: " + file.getName());
                                                        } else {
                                                            toast("Fehler beim Löschen");
                                                        }
                                                    })
                                                    .setNegativeButton("Abbrechen", null)
                                                    .show();
                                        }
                                    })
                            .setNegativeButton("Abbrechen", null)
                            .show();
                })
                .setNegativeButton("Abbrechen", null)
                .show();
    }

    // ================================================================== FILE PICKING

    private static final int REQUEST_OPEN_MEDIA = 21;
    private static final int REQUEST_OPEN_PLAYLIST = 22;
    private static final int REQUEST_OPEN_SUBTITLE = 23;
    private static final int REQUEST_PICK_RECORD_FOLDER = 24;

    private void openFilePicker() {
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        try {
            startActivityForResult(Intent.createChooser(intent, "Medien öffnen"),
                    REQUEST_OPEN_MEDIA);
        } catch (Exception e) {
            toast("Kein Datei-Dialog verfügbar");
        }
    }

    private void openPlaylistPicker() {
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        try {
            startActivityForResult(Intent.createChooser(intent, "Playlist öffnen"),
                    REQUEST_OPEN_PLAYLIST);
        } catch (Exception e) {
            toast("Kein Datei-Dialog verfügbar");
        }
    }

    private void openSubtitlePicker() {
        MediaItem item = engine != null ? engine.current() : null;
        if (item == null) {
            toast("Erst Medien öffnen");
            return;
        }
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        try {
            startActivityForResult(Intent.createChooser(intent, "Untertitel öffnen"),
                    REQUEST_OPEN_SUBTITLE);
        } catch (Exception e) {
            toast("Kein Datei-Dialog verfügbar");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        // SAF folder picker delivers a tree:// uri (no clip data path above).
        if (requestCode == REQUEST_PICK_RECORD_FOLDER) {
            if (resultCode != RESULT_OK || data == null || data.getData() == null) {
                return;
            }
            Uri treeUri = data.getData();
            try {
                getContentResolver().takePersistableUriPermission(treeUri,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                                | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                recordFolderUri = treeUri.toString();
                settings.recordFolder = recordFolderUri;
                settings.save(this);
                androidx.documentfile.provider.DocumentFile dir =
                        androidx.documentfile.provider.DocumentFile.fromTreeUri(
                                this, treeUri);
                recordFolderName = dir != null && dir.getName() != null
                        ? dir.getName() : "Ordner";
                toast("Aufnahme-Zielordner: " + recordFolderName);
                // update the folder label of the still-open setup dialog
                if (recFolderLabel != null) recFolderLabel.setText(recordFolderName);
            } catch (Exception e) {
                toast("Ordner konnte nicht übernommen werden");
            }
            return;
        }
        if (resultCode != RESULT_OK || data == null) return;
        List<Uri> uris = new ArrayList<>();
        if (data.getData() != null) uris.add(data.getData());
        if (data.getClipData() != null) {
            android.content.ClipDescription description = data.getClipData().getDescription();
            for (int i = 0; i < data.getClipData().getItemCount(); i++) {
                Uri uri = data.getClipData().getItemAt(i).getUri();
                if (uri != null) uris.add(uri);
            }
        }
        if (uris.isEmpty()) return;
        if (requestCode == REQUEST_OPEN_MEDIA) {
            List<MediaItem> items = new ArrayList<>();
            for (Uri uri : uris) {
                String kind = guessKind(uri);
                if ("playlist".equals(kind)) {
                    // The generic media picker may return playlists too. They
                    // must be expanded just like the dedicated playlist
                    // picker, never handed to the local MediaPlayer as a file.
                    loadPlaylist(uri);
                    continue;
                }
                items.add(new MediaItem(uri.toString(), null, kind, null));
            }
            boolean wasEmpty = engine.items().isEmpty();
            engine.addAll(items);
            if (wasEmpty && !items.isEmpty()) {
                engine.playIndex(engine.items().size() - items.size());
            }
            if (!items.isEmpty()) toast(items.size() + " zur Queue hinzugefügt");
        } else if (requestCode == REQUEST_OPEN_PLAYLIST) {
            Uri uri = uris.get(0);
            loadPlaylist(uri);
        } else if (requestCode == REQUEST_OPEN_SUBTITLE) {
            MediaItem item = engine.current();
            if (item != null) {
                item.subtitle = uris.get(0).toString();
                loadSubtitleFor(item);
                engine.persist();
            }
        }
    }

    private String guessKind(Uri uri) {
        StringBuilder identity = new StringBuilder(uri.toString());
        try {
            String mime = getContentResolver().getType(uri);
            if (mime != null) identity.append(' ').append(mime);
        } catch (Exception ignored) {}
        if ("content".equalsIgnoreCase(uri.getScheme())) {
            try (android.database.Cursor cursor = getContentResolver().query(uri,
                    new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    int column = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                    if (column >= 0) identity.append(' ').append(cursor.getString(column));
                }
            } catch (Exception ignored) {}
        }
        String value = identity.toString().toLowerCase(Locale.ROOT);
        if (value.endsWith(".casu")) return "casu";
        if (value.endsWith(".mp5")) return "mp5";
        if (value.contains(".m3u") || value.contains(".pls")
                || value.contains(".xspf") || value.contains(".jspf")
                || value.contains(".asx") || value.contains(".wpl")
                || value.contains("application/vnd.apple.mpegurl")
                || value.contains("application/x-mpegurl")
                || value.contains("audio/x-mpegurl")) return "playlist";
        if (value.contains("video") || value.endsWith(".mp4") || value.endsWith(".mkv")
                || value.endsWith(".webm") || value.endsWith(".mov") || value.endsWith(".m4v")) {
            return "video";
        }
        return "audio";
    }

    private void loadPlaylist(Uri uri) {
        new Thread(() -> {
            try {
                PlaylistIO.Playlist playlist = PlaylistIO.load(uri.toString(),
                        location -> PlaylistIO.fetchText(this, location));
                List<MediaItem> items = new ArrayList<>();
                for (PlaylistIO.Entry entry : playlist.items) {
                    if (entry.url == null || entry.url.isEmpty()) continue;
                    MediaItem item = new MediaItem(entry.url, entry.title, "stream", null);
                    item.playlist = playlist.name;
                    items.add(item);
                }
                ui.post(() -> {
                    if (items.isEmpty()) {
                        toast("Playlist enthält keine abspielbaren Einträge");
                        return;
                    }
                    int firstAdded = engine.items().size();
                    engine.addAll(items);
                    engine.playIndex(firstAdded);
                    toast(items.size() + " Einträge · " + playlist.name);
                    refreshQueueUi();
                    showTab(TAB_PLAY);
                });
            } catch (Exception e) {
                ui.post(() -> toast("Playlist fehlgeschlagen: " + e.getMessage()));
            }
        }).start();
    }

    /** Route every external Android entry point like the Linux player:
     *  expand playlist containers first; only actual media reaches the engine. */
    private void openIncomingUri(Uri uri) {
        String kind = guessKind(uri);
        if ("playlist".equals(kind)) {
            loadPlaylist(uri);
            return;
        }
        engine.openExternal(new MediaItem(uri.toString(), null, kind, null), true, 0);
    }

    // ================================================================== INTENTS

    private void handleIntent(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (Intent.ACTION_VIEW.equals(action) && intent.getData() != null) {
            Uri uri = intent.getData();
            withEngine(() -> openIncomingUri(uri));
        } else if (Intent.ACTION_SEND.equals(action)) {
            Uri uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (uri != null) {
                withEngine(() -> openIncomingUri(uri));
            } else {
                String text = intent.getStringExtra(Intent.EXTRA_TEXT);
                if (text != null && text.contains("youtu")) {
                    showAddUrlDialog();
                }
            }
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    // ================================================================== lifecycle

    @Override
    protected void onResume() {
        super.onResume();
        if (engine == null) {
            // Service engine not up yet: ensureEngine's retry loop will call
            // back through onEngineReady; nothing to render from a null queue.
            ensureEngine();
            return;
        }
        onStateChanged(engine.isPlaying());
        onItemChanged(engine.current(), engine.index());
        refreshQueueUi();
        // Resume playback on app start (setting).
        maybeResume();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (engine != null) engine.persist();
    }

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }

    private Bitmap drawFallbackIcon(String name, int color) {
        int size = dp(56);
        Bitmap bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        Paint bg = new Paint(Paint.ANTI_ALIAS_FLAG);
        bg.setColor(Color.parseColor("#12151a"));
        c.drawCircle(size/2f, size/2f, size/2f, bg);
        Paint fg = new Paint(Paint.ANTI_ALIAS_FLAG);
        fg.setColor(color);
        fg.setTextSize(size * 0.45f);
        fg.setTextAlign(Paint.Align.CENTER);
        fg.setTypeface(Typeface.DEFAULT_BOLD);
        String letter = name != null && !name.isEmpty() ? name.substring(0, 1) : "?";
        c.drawText(letter, size/2f, size * 0.62f, fg);
        return bmp;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density);
    }
}

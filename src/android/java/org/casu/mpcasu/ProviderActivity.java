// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Message;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

/** Embedded tabbed browser for the provider pages (Spotify/Tidal/HearThis/
 *  Netflix/Browse) — the Android twin of the Linux WEB PLAYERS sidebar.
 *
 *  Real browsing: logins survive (persistent cookies, DOM storage, popup
 *  windows become new tabs so OAuth flows stay INSIDE the app), every link
 *  keeps loading in a tab, downloads go to the system DownloadManager. */
public final class ProviderActivity extends Activity {

    private static final int MAX_TABS = 12;
    private static final int REQUEST_FILE_CHOOSER = 41;

    private static final int BG = Color.parseColor("#0b0d10");
    private static final int BAR = Color.parseColor("#12151a");
    private static final int ACCENT = Color.parseColor("#ff1e2d");
    private static final int TEXT = Color.parseColor("#f2f4f7");
    private static final int MUTED = Color.parseColor("#9aa3ad");
    private static final int CHIP = Color.parseColor("#161a20");
    private static final int CHIP_ACTIVE = Color.parseColor("#2a1114");
    private static final int BORDER = Color.parseColor("#262b31");

    private final List<WebView> tabs = new ArrayList<>();
    private final List<String> tabTitles = new ArrayList<>();
    private FrameLayout tabHost;
    private LinearLayout tabStrip;
    private HorizontalScrollView tabStripScroll;
    private EditText urlInput;
    private WebView active;
    private String startUrl;
    private String providerName;
    private ValueCallback<Uri[]> fileCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        providerName = getIntent().getStringExtra("name");
        String url = getIntent().getStringExtra("url");
        if (providerName == null) providerName = "WEB";
        if (url == null || url.isEmpty()) url = "https://www.google.com/";
        startUrl = url;

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);

        root.addView(buildToolbar());
        tabStripScroll = new HorizontalScrollView(this);
        tabStripScroll.setHorizontalScrollBarEnabled(false);
        tabStrip = new LinearLayout(this);
        tabStrip.setOrientation(LinearLayout.HORIZONTAL);
        tabStrip.setPadding(dp(8), dp(4), dp(8), dp(4));
        tabStripScroll.addView(tabStrip);
        root.addView(tabStripScroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        tabHost = new FrameLayout(this);
        tabHost.setBackgroundColor(Color.parseColor("#080a0d"));
        root.addView(tabHost, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);

        newTab(startUrl);
    }

    // ------------------------------------------------------------------ toolbar

    private LinearLayout buildToolbar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(BAR);
        bar.setPadding(dp(8), dp(8), dp(8), dp(8));

        TextView back = navButton("◀");
        back.setOnClickListener(v -> { if (active != null && active.canGoBack()) active.goBack(); });
        TextView fwd = navButton("▶");
        fwd.setOnClickListener(v -> { if (active != null && active.canGoForward()) active.goForward(); });
        TextView reload = navButton("⟳");
        reload.setOnClickListener(v -> { if (active != null) active.reload(); });

        urlInput = new EditText(this);
        urlInput.setTextColor(TEXT);
        urlInput.setHintTextColor(MUTED);
        urlInput.setTextSize(13);
        urlInput.setHint(providerName + " — Adresse");
        urlInput.setBackground(null);
        urlInput.setSingleLine(true);
        urlInput.setImeOptions(EditorInfo.IME_ACTION_GO);
        urlInput.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO
                    || (event != null && event.getAction() == KeyEvent.ACTION_DOWN
                    && event.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
                loadFromUrlBar();
                return true;
            }
            return false;
        });
        urlInput.setOnFocusChangeListener((v, hasFocus) -> {
            if (hasFocus) urlInput.selectAll();
        });

        TextView newTabBtn = navButton("＋");
        newTabBtn.setOnClickListener(v -> newTab(startUrl));
        TextView close = navButton("✕");
        close.setOnClickListener(v -> finish());

        bar.addView(back);
        bar.addView(fwd);
        bar.addView(reload);
        bar.addView(urlInput, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        bar.addView(newTabBtn);
        bar.addView(close);
        return bar;
    }

    private TextView navButton(String symbol) {
        TextView view = new TextView(this);
        view.setText(symbol);
        view.setTextColor(TEXT);
        view.setTextSize(15);
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(10), dp(8), dp(10), dp(8));
        return view;
    }

    private void loadFromUrlBar() {
        if (active == null) return;
        String input = urlInput.getText().toString().trim();
        if (input.isEmpty()) return;
        if (!input.startsWith("http://") && !input.startsWith("https://")
                && !input.startsWith("file://")) {
            boolean looksLikeDomain = input.matches(
                    "^[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}(/.*)?$");
            input = looksLikeDomain ? "https://" + input
                    : "https://www.google.com/search?q=" + Uri.encode(input);
        }
        active.loadUrl(input);
        urlInput.clearFocus();
    }

    // ------------------------------------------------------------------ tabs

    private int activeIndex() {
        return tabs.indexOf(active);
    }

    private WebView newTab(String url) {
        if (tabs.size() >= MAX_TABS) {
            toast("Maximal " + MAX_TABS + " Tabs");
            return active;
        }
        WebView view = buildWebView();
        tabs.add(view);
        tabTitles.add(providerName);
        attachTab(view);
        if (url != null && !url.isEmpty()) view.loadUrl(url);
        renderTabStrip();
        return view;
    }

    private void attachTab(WebView view) {
        if (active != null) tabHost.removeView(active);
        active = view;
        tabHost.removeAllViews();
        tabHost.addView(view, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        renderTabStrip();
        syncUrlBar();
    }

    private void switchTab(int index) {
        if (index < 0 || index >= tabs.size() || tabs.get(index) == active) return;
        attachTab(tabs.get(index));
    }

    private void closeTab(int index) {
        if (index < 0 || index >= tabs.size()) return;
        WebView closed = tabs.remove(index);
        tabTitles.remove(index);
        boolean wasActive = closed == active;
        tabHost.removeView(closed);
        closed.destroy();
        if (tabs.isEmpty()) {
            finish();
            return;
        }
        if (wasActive) {
            int next = Math.min(index, tabs.size() - 1);
            attachTab(tabs.get(next));
        } else {
            renderTabStrip();
        }
    }

    private void renderTabStrip() {
        tabStrip.removeAllViews();
        for (int i = 0; i < tabs.size(); i++) {
            final int index = i;
            LinearLayout chip = new LinearLayout(this);
            chip.setOrientation(LinearLayout.HORIZONTAL);
            chip.setGravity(Gravity.CENTER_VERTICAL);
            boolean isActive = i == activeIndex();
            android.graphics.drawable.GradientDrawable bg =
                    new android.graphics.drawable.GradientDrawable();
            bg.setColor(isActive ? CHIP_ACTIVE : CHIP);
            bg.setCornerRadius(dp(10));
            bg.setStroke(1, isActive ? ACCENT : BORDER);
            chip.setBackground(bg);
            chip.setPadding(dp(10), dp(4), dp(4), dp(4));
            LinearLayout.LayoutParams chipParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            chipParams.setMargins(dp(4), 0, dp(4), 0);

            TextView title = new TextView(this);
            String label = tabTitles.get(i);
            if (label == null || label.isEmpty()) label = providerName;
            if (label.length() > 18) label = label.substring(0, 17) + "…";
            title.setText((i + 1) + "· " + label);
            title.setTextColor(isActive ? ACCENT : TEXT);
            title.setTextSize(12);
            title.setSingleLine(true);
            chip.addView(title, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            chip.setOnClickListener(v -> switchTab(index));

            TextView closeBtn = new TextView(this);
            closeBtn.setText("✕");
            closeBtn.setTextColor(MUTED);
            closeBtn.setTextSize(12);
            closeBtn.setPadding(dp(8), dp(2), dp(6), dp(2));
            closeBtn.setOnClickListener(v -> closeTab(index));
            chip.addView(closeBtn);

            tabStrip.addView(chip, chipParams);
        }
        // scroll the active chip into view
        tabHost.post(() -> {
            View chipAt = tabStrip.getChildAt(activeIndex());
            if (chipAt != null) tabStripScroll.smoothScrollTo(
                    Math.max(0, chipAt.getLeft() - dp(40)), 0);
        });
    }

    private void syncUrlBar() {
        if (active == null) return;
        String url = active.getUrl();
        urlInput.setText(url == null ? "" : url);
        if (url != null && !url.equals("about:blank")) urlInput.setSelection(0);
    }

    // ------------------------------------------------------------------ webview

    private WebView buildWebView() {
        WebView view = new WebView(this);
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        // Login/Weiterverlinkung: window.open + target=_blank landen in einem
        // neuen TAB (onCreateWindow) statt im externen Browser zu verschwinden.
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setAllowFileAccess(true);

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(view, true);

        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme() == null ? "" : uri.getScheme();
                if (scheme.equals("http") || scheme.equals("https")
                        || scheme.equals("about") || scheme.equals("data")
                        || scheme.equals("blob") || scheme.equals("file")) {
                    return false;  // stay INSIDE the tab (login redirects!)
                }
                // foreign schemes (mailto:, tel:, intent:, spotify:) → system
                try {
                    Intent intent = Intent.parseUri(uri.toString(),
                            Intent.URI_INTENT_SCHEME);
                    intent.addCategory(Intent.CATEGORY_BROWSABLE);
                    intent.setComponent(null);
                    startActivity(intent);
                } catch (Exception e) {
                    toast("Link wird nicht unterstützt");
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                if (v == active) {
                    urlInput.setText(url == null ? "" : url);
                }
            }
        });

        view.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onCreateWindow(WebView v, boolean isDialog,
                                          boolean isUserGesture, Message resultMsg) {
                // OAuth/popups: neuer TAB im selben Browser (Login geht nicht
                // verloren, nichts springt nach draußen).
                if (tabs.size() >= MAX_TABS) return false;
                WebView newView = buildWebView();
                tabs.add(newView);
                tabTitles.add("Lädt…");
                attachTab(newView);
                renderTabStrip();
                WebView.WebViewTransport transport =
                        (WebView.WebViewTransport) resultMsg.obj;
                transport.setWebView(newView);
                resultMsg.sendToTarget();
                return true;
            }

            @Override
            public void onReceivedTitle(WebView v, String title) {
                int index = tabs.indexOf(v);
                if (index >= 0 && title != null && !title.isEmpty()) {
                    tabTitles.set(index, title);
                    if (v == active) renderTabStrip();
                }
            }

            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                // Media-Rechte (Kamera/Mikro) bestätigen — z. B. für Web-Player,
                // die Device-IDs anfordern. Alles erlauben ist hier OK: der
                // User steuert die Seiten selbst an.
                runOnUiThread(() -> request.grant(request.getResources()));
            }

            @Override
            public void onGeolocationPermissionsShowPrompt(String origin,
                    GeolocationPermissions.Callback callback) {
                callback.invoke(origin, true, false);
            }

            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb,
                                             FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                try {
                    Intent intent = params.createIntent();
                    startActivityForResult(intent, REQUEST_FILE_CHOOSER);
                    return true;
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
            }
        });

        view.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent,
                                        String contentDisposition,
                                        String mimeType, long size) {
                try {
                    DownloadManager.Request request =
                            new DownloadManager.Request(Uri.parse(url));
                    request.setNotificationVisibility(
                            DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                    String name = "download";
                    if (contentDisposition != null) {
                        for (String part : contentDisposition.split(";")) {
                            String trimmed = part.trim();
                            if (trimmed.startsWith("filename=")) {
                                name = trimmed.substring(9).replace("\"", "");
                                break;
                            }
                        }
                    }
                    request.setDestinationInExternalPublicDir(
                            android.os.Environment.DIRECTORY_DOWNLOADS, name);
                    String cookie = CookieManager.getInstance().getCookie(url);
                    if (cookie != null) request.addRequestHeader("cookie", cookie);
                    request.addRequestHeader("User-Agent", userAgent);
                    DownloadManager manager = (DownloadManager)
                            getSystemService(Context.DOWNLOAD_SERVICE);
                    manager.enqueue(request);
                    toast("Download gestartet · " + name);
                } catch (Exception e) {
                    toast("Download fehlgeschlagen");
                }
            }
        });

        return view;
    }

    // ------------------------------------------------------------------ events

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQUEST_FILE_CHOOSER && fileCallback != null) {
            Uri[] results = null;
            if (resultCode == RESULT_OK && data != null) {
                if (data.getData() != null) results = new Uri[]{data.getData()};
                else if (data.getClipData() != null) {
                    List<Uri> uris = new ArrayList<>();
                    for (int i = 0; i < data.getClipData().getItemCount(); i++) {
                        uris.add(data.getClipData().getItemAt(i).getUri());
                    }
                    results = uris.toArray(new Uri[0]);
                }
            }
            fileCallback.onReceiveValue(results);
            fileCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (active != null && active.canGoBack()) {
            active.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (active != null) active.onPause();
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (active != null) active.onResume();
    }

    @Override
    protected void onDestroy() {
        for (WebView view : new ArrayList<>(tabs)) {
            tabHost.removeView(view);
            view.destroy();
        }
        tabs.clear();
        super.onDestroy();
    }

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density);
    }
}

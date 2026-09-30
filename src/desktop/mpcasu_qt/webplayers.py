# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Tabbed embedded web-player views (Spotify/Hearthis/Tidal/Netflix).

Each provider gets its own tab with an embedded Chromium (QtWebEngine) view, a
URL/search field and the official web player loaded through it. Direct URLs and
searches open in the matching tab; the user logs in with their normal account.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from .browser_runtime import configure_widevine
WIDEVINE_PATH = configure_widevine()

try:
    from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings, qWebEngineChromiumVersion)
    from PySide6.QtWebEngineWidgets import QWebEngineView
    _HAVE_WEBENGINE = True
except ImportError:
    QWebEnginePage = QWebEngineProfile = QWebEngineView = None
    _HAVE_WEBENGINE = False

from casu.webproviders import WEB_PLAYERS, web_player_url, _PROVIDER_DOMAINS as _PROVIDER_HOSTS

BROWSE_URL = "https://duckduckgo.com/"


def _persistent_profile(parent) -> object | None:
    """A persistent QtWebEngine profile so logins/cookies survive restarts."""
    if not _HAVE_WEBENGINE:
        return None
    config = Path(os.environ.get("XDG_CONFIG_HOME",
                                 str(Path.home() / ".config"))) / "mpcasu"
    storage = config / "webengine"
    storage.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile("mpcasu", parent)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    profile.setPersistentStoragePath(str(storage))
    profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
    # Keep Chromium's real engine/platform versions, without Qt's application
    # token, which some sites mistake for an unsupported mobile/embed client.
    profile.setHttpUserAgent(re.sub(r"\sQtWebEngine/[\d.]+", "", profile.httpUserAgent()))
    # Keep the User-Agent Client Hints consistent with that UA. The default
    # hints advertise brand "Chromium" (no "Google Chrome") while the stripped
    # UA says "Chrome/…" — exactly the header/JS identity mismatch that
    # anti-bot systems (DataDome, Google) score. Mirror the UA brand instead:
    try:
        hints = profile.clientHints()
        chromium_version = qWebEngineChromiumVersion()
        full = f"{chromium_version.major}.{chromium_version.minor}.{chromium_version.build}.{chromium_version.patch}" if hasattr(chromium_version, "major") else str(chromium_version)
        hints.setFullVersion(full)
        hints.setFullVersionList({
            "Not:A-Brand": "24",
            "Chromium": full,
            "Google Chrome": full,
        })
    except Exception:
        pass  # hints API unavailable on this Qt — UA-only fallback stays
    return profile


if _HAVE_WEBENGINE:
    class ProviderPage(QWebEnginePage):
        def __init__(self, profile, parent, owner):
            super().__init__(profile, parent)
            self.owner = owner
            self.last_load_ok = True   # did the last navigation finish cleanly?
            self._debug = bool(os.environ.get("MPCASU_WEB_DEBUG"))
            import time as _time
            self._t0 = _time.time()
            self.settings().setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
            self.loadFinished.connect(self._track_load)
            self.fullScreenRequested.connect(self._fullscreen)
            if self._debug:
                import time as _time
                def _log_started():
                    print(f"[NAV+{ _time.time()-self._t0:.1f}s] loadStarted", flush=True)
                def _log_url(u):
                    print(f"[NAV+{_time.time()-self._t0:.1f}s] urlChanged {u.toString()[:100]}", flush=True)
                def _log_finished(ok):
                    print(f"[NAV+{_time.time()-self._t0:.1f}s] loadFinished ok={ok}", flush=True)
                def _log_render(status):
                    print(f"[NAV+{_time.time()-self._t0:.1f}s] renderProcessTerminated status={status}", flush=True)
                def _log_console(level, msg, line, sid):
                    if level >= 2:  # warn+error only
                        print(f"[JS+{_time.time()-self._t0:.1f}s] lvl={level} {msg[:150]}", flush=True)
                self.loadStarted.connect(_log_started)
                self.urlChanged.connect(_log_url)
                self.loadFinished.connect(_log_finished)
                self.renderProcessTerminated.connect(_log_render)

        def javaScriptConsoleMessage(self, level, message, line_number, source_id):
            if getattr(self, "_debug", False) and level >= 2:
                import time as _time
                print(f"[JS+{_time.time()-self._t0:.1f}s] lvl={level} {message[:150]}", flush=True)
            super().javaScriptConsoleMessage(level, message, line_number, source_id)

        def _track_load(self, ok):
            self.last_load_ok = bool(ok)

        def createWindow(self, window_type):
            if getattr(self, "_debug", False):
                import time as _time
                print(f"[NAV+{_time.time()-self._t0:.1f}s] createWindow type={window_type}", flush=True)
            return self.owner._popup_page()

        def _fullscreen(self, request):
            request.accept()
            window = self.owner.window()
            if request.toggleOn():
                window.showFullScreen()
            else:
                window.showNormal()


class WebPlayerTabs(QWidget):
    """Tab widget with one embedded web player per provider."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WebPlayers")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._drm_status = QLabel(
            "DRM-Komponente erkannt. Die Verfügbarkeit wird vom Anbieter geprüft."
            if WIDEVINE_PATH else
            "Für geschützte Inhalte fehlt Widevine. Einen Browser mit Widevine installieren "
            "oder CASU_WIDEVINE_PATH konfigurieren, danach CASU neu starten.")
        self._drm_status.setWordWrap(True)
        layout.addWidget(self._drm_status)
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._views: dict[str, QWebEngineView] = {}
        self._entries: dict[str, QLineEdit] = {}
        self._profile = _persistent_profile(self)
        for key, spec in WEB_PLAYERS.items():
            page = QWidget()
            page.setStyleSheet("background: transparent;")
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(6, 6, 6, 6)
            page_layout.setSpacing(6)
            entry = QLineEdit()
            entry.setObjectName("IconButton")
            entry.setPlaceholderText(f"{spec['label']} URL oder Suchbegriff…")
            entry.returnPressed.connect(lambda k=key: self._submit(k))
            page_layout.addWidget(entry)
            if _HAVE_WEBENGINE:
                view = QWebEngineView()
                if self._profile is not None:
                    view.setPage(ProviderPage(self._profile, view, self))
                page_layout.addWidget(view)
            else:
                view = None
            self._entries[key] = entry
            self._views[key] = view
            self._tabs.addTab(page, spec["label"])
        # Browse tab: a general browser (QtWebEngine loads any site directly).
        browse_page = QWidget()
        browse_page.setStyleSheet("background: transparent;")
        browse_layout = QVBoxLayout(browse_page)
        browse_layout.setContentsMargins(6, 6, 6, 6)
        browse_layout.setSpacing(6)
        browse_entry = QLineEdit()
        browse_entry.setObjectName("IconButton")
        browse_entry.setPlaceholderText("Browse — URL oder DuckDuckGo-Suche…")
        browse_entry.returnPressed.connect(self._submit_browse)
        browse_layout.addWidget(browse_entry)
        self._entries["browse"] = browse_entry
        self._views["browse"] = QWebEngineView() if _HAVE_WEBENGINE else None
        if self._views["browse"] is not None and self._profile is not None:
            self._views["browse"].setPage(ProviderPage(self._profile, self._views["browse"], self))
            browse_layout.addWidget(self._views["browse"])
        self._browse_index = self._tabs.addTab(browse_page, "BROWSE")
        layout.addWidget(self._tabs)
        self._tabs.tabBarClicked.connect(self._tab_clicked)

    def _tab_clicked(self, index):
        keys = list(WEB_PLAYERS)
        if 0 <= index < len(keys):
            key = keys[index]
            view = self._views.get(key)
            if view is not None and view.url().isEmpty():
                self.open(key)

    def _popup_page(self):
        view = QWebEngineView(self._tabs)
        page = ProviderPage(self._profile, view, self)
        view.setPage(page)
        index = self._tabs.addTab(view, "Anmeldung")
        self._tabs.setCurrentIndex(index)
        view.titleChanged.connect(lambda title, v=view: self._tabs.setTabText(
            self._tabs.indexOf(v), title[:30] or "Anmeldung"))
        return page

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    def _submit(self, key: str):
        text = self._entries[key].text().strip()
        if not text:
            return
        is_url = "://" in text and "." in text
        self.open(key, query=("" if is_url else text), url=(text if is_url else ""))

    def _submit_browse(self):
        text = self._entries["browse"].text().strip()
        if not text:
            return
        if "://" in text and "." in text:
            target = text
        else:
            target = "https://duckduckgo.com/?q=" + text.replace(" ", "+")
        view = self._views.get("browse")
        if view is not None:
            view.load(QUrl(target))

    def open(self, provider: str, *, query: str = "", url: str = ""):
        """Load a provider's web player at a search query or direct URL."""
        keys = list(WEB_PLAYERS)
        if provider == "browse":
            self._tabs.setCurrentIndex(self._browse_index)
            view = self._views.get("browse")
            if view is not None:
                target = url or (BROWSE_URL if not query
                                 else "https://duckduckgo.com/?q=" + query.replace(" ", "+"))
                view.load(QUrl(target))
            return
        if provider not in self._views:
            provider = "hearthis"
        self._tabs.setCurrentIndex(keys.index(provider))
        if query:
            self._entries[provider].setText(query)
        target = web_player_url(provider, query=query, url=url)
        view = self._views[provider]
        if view is None:
            return
        if os.environ.get("MPCASU_WEB_DEBUG"):
            import time as _t
            print(f"[WEB] open provider={provider} query={query!r} url={url!r} "
                  f"t={_t.time():.0f}", flush=True)
        # Load-once lifecycle: a provider view that already runs its web app is
        # only SHOWN again — never reloaded. Every reload restarts the whole SPA
        # (all its API requests), which rate-limits accounts ("too many
        # requests") and re-triggers anti-bot verdicts. A reload happens only
        # for an empty/errored view or an explicitly different target.
        current = view.url().toString() if hasattr(view, "url") else ""
        page = view.page() if hasattr(view, "page") else None
        load_was_ok = getattr(page, "last_load_ok", True) if page is not None else True
        already_home = (not query and not url) and load_was_ok and bool(current) and (
            current.rstrip("/") == target.rstrip("/")
            or current.startswith("https://" + _PROVIDER_HOSTS.get(provider, "")))
        if already_home:
            return
        if page is not None and not load_was_ok:
            # The view sits on an error body (e.g. an HTTP 429/5xx response kept
            # the URL unchanged). Show the tab again and refresh it once.
            page.reload()
            return
        view.load(QUrl(target))

    def play_video(self, url: str, title: str = "") -> bool:
        """Stream a direct media URL in an embedded <video> element (yt-dlp).

        Mirrors the web-casu player: the resolved googlevideo URL is played by
        the browser engine, which handles the HTTP session YouTube requires
        (plain HTTP clients such as libVLC get HTTP 403).
        """
        view = self._views.get("browse")
        if view is None:
            return False
        safe = url.replace("&", "&amp;").replace("'", "&#39;")
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>html,body{margin:0;height:100%;background:#000}"
            "video{width:100vw;height:100vh;background:#000;outline:none}</style>"
            "</head><body><video src='__URL__' autoplay controls playsinline "
            "style='width:100vw;height:100vh'></video></body></html>"
        ).replace("__URL__", safe)
        view.setHtml(html, QUrl("https://www.youtube.com/"))
        parent = view.parentWidget()
        idx = self._tabs.indexOf(parent)
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)
        return True

    def focus_entry(self, provider: str):
        if provider in self._entries:
            self._entries[provider].setFocus()
            self._entries[provider].selectAll()

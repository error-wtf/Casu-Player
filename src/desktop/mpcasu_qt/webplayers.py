# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Tabbed embedded web-player views (Spotify/Hearthis/Tidal/Netflix).

DRM providers open in a maintained system browser. Non-DRM sites retain
embedded views. Provider tabs, direct URLs and embedded navigation share
the same routing policy.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    _HAVE_WEBENGINE = True
except ImportError:
    QWebEnginePage = QWebEngineProfile = QWebEngineView = None
    _HAVE_WEBENGINE = False

from casu.webproviders import (EXTERNAL_PROVIDERS, WEB_PLAYERS, open_web_player,
                               provider_for_url, web_player_url)

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
    return profile


if _HAVE_WEBENGINE:
    class RoutedPage(QWebEnginePage):
        """Hand off DRM navigation, including links and redirects from Browse."""
        def __init__(self, profile, parent, launch):
            super().__init__(profile, parent)
            self._launch = launch
            self.newWindowRequested.connect(self._new_window)

        def _new_window(self, request):
            url = request.requestedUrl().toString()
            if url:
                self._launch(provider_for_url(url) or "browse", url)

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            provider = provider_for_url(url.toString())
            if is_main_frame and provider in EXTERNAL_PROVIDERS:
                self._launch(provider, url.toString())
                return False
            return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class WebPlayerTabs(QWidget):
    """Tab widget with one embedded web player per provider."""

    browser_launched = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WebPlayers")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._views: dict[str, QWebEngineView] = {}
        self._entries: dict[str, QLineEdit] = {}
        self._targets = {}
        self._messages = {}
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
            if key in EXTERNAL_PROVIDERS:
                view = None
                message = QLabel("Wiedergabe im Browser mit DRM-Unterstützung. "
                                 "Dort mit deinem Account anmelden.")
                message.setWordWrap(True)
                self._messages[key] = message
                page_layout.addWidget(message)
                button = QPushButton("Im Browser öffnen")
                button.clicked.connect(lambda checked=False, k=key: self.open(
                    k, url=self._targets.get(k, "")))
                page_layout.addWidget(button)
                page_layout.addStretch()
            elif _HAVE_WEBENGINE:
                view = QWebEngineView()
                if self._profile is not None:
                    view.setPage(RoutedPage(self._profile, view, self._launch_external))
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
            self._views["browse"].setPage(RoutedPage(self._profile, self._views["browse"], self._launch_external))
            browse_layout.addWidget(self._views["browse"])
        self._tabs.addTab(browse_page, "BROWSE")
        layout.addWidget(self._tabs)
        self._tabs.tabBarClicked.connect(self._tab_clicked)

    def _tab_clicked(self, index):
        keys = list(WEB_PLAYERS)
        if 0 <= index < len(keys) and keys[index] in EXTERNAL_PROVIDERS:
            key = keys[index]
            self.open(key, url=self._targets.get(key, ""))

    def _launch_external(self, provider, target):
        self._targets[provider] = target
        ok = open_web_player(provider, url=target)
        message = self._messages.get(provider)
        if message is not None:
            message.setText(
                "Browser gestartet. Dort anmelden und geschützte Inhalte/DRM aktivieren."
                if ok else
                "Browser konnte nicht gestartet werden. Installiere einen aktuellen "
                "Google Chrome, Microsoft Edge oder Firefox (macOS: Safari).")
        self.browser_launched.emit(provider, ok)
        return ok

    def _load_target(self, key, target):
        provider = provider_for_url(target)
        if provider in EXTERNAL_PROVIDERS:
            return self._launch_external(provider, target)
        view = self._views.get(key)
        if view is not None:
            view.load(QUrl(target))
            return True
        return False

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
        self._load_target("browse", target)

    def open(self, provider: str, *, query: str = "", url: str = ""):
        """Load a provider's web player at a search query or direct URL."""
        keys = list(WEB_PLAYERS)
        if provider == "browse":
            self._tabs.setCurrentIndex(self._tabs.count() - 1)
            target = url or (BROWSE_URL if not query
                             else "https://duckduckgo.com/?q=" + query.replace(" ", "+"))
            return self._load_target("browse", target)
        if provider not in self._views:
            provider = "spotify"
        self._tabs.setCurrentIndex(keys.index(provider))
        if query:
            self._entries[provider].setText(query)
        target = web_player_url(provider, query=query, url=url)
        self._entries[provider].setText(url or query)
        self._targets[provider] = target
        if provider in EXTERNAL_PROVIDERS:
            return self._launch_external(provider, target)
        return self._load_target(provider, target)

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

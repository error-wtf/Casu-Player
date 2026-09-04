# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Tabbed embedded web-player views (Spotify/Hearthis/Tidal/Netflix).

Each provider gets its own tab with an embedded Chromium (QtWebEngine) view, a
URL/search field and the official web player loaded through it. Direct URLs and
searches open in the matching tab; the user logs in with their normal account.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtWidgets import QLineEdit, QTabWidget, QVBoxLayout, QWidget

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    _HAVE_WEBENGINE = True
except ImportError:
    QWebEnginePage = QWebEngineProfile = QWebEngineView = None
    _HAVE_WEBENGINE = False

from casu.webproviders import WEB_PLAYERS, spotify_embed_url, web_player_url

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


class WebPlayerTabs(QWidget):
    """Tab widget with one embedded web player per provider."""

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
                    view.setPage(QWebEnginePage(self._profile, view))
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
            self._views["browse"].setPage(QWebEnginePage(self._profile, self._views["browse"]))
            browse_layout.addWidget(self._views["browse"])
        self._tabs.addTab(browse_page, "BROWSE")
        layout.addWidget(self._tabs)

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    def _submit(self, key: str):
        text = self._entries[key].text().strip()
        if not text:
            return
        is_url = "://" in text and "." in text
        if key == "spotify" and is_url:
            text = spotify_embed_url(text)
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
            self._tabs.setCurrentIndex(self._tabs.count() - 1)
            view = self._views.get("browse")
            if view is not None:
                target = url or (BROWSE_URL if not query
                                 else "https://duckduckgo.com/?q=" + query.replace(" ", "+"))
                view.load(QUrl(target))
            return
        if provider not in self._views:
            provider = "spotify"
        self._tabs.setCurrentIndex(keys.index(provider))
        if query:
            self._entries[provider].setText(query)
        target = web_player_url(provider, query=query, url=url)
        view = self._views[provider]
        if view is not None:
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

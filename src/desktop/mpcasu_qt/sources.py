# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# Copyright (C) 2026 Lino Casu
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the CASU Anti-Capitalist License 1.4.
# ---------------------------------------------------------------------
"""SourcesView: in-window YouTube/Spotify search and network stream entry.

Extracted from main_window.py in the v7.8 modularization pass. Owns the
consent gate, search entry, result list and status line; playback routing
stays in MainWindow via the queueRequested signal.
"""

from __future__ import annotations

import threading
import urllib.parse
from dataclasses import replace

from mpcasu_qt.theme import PALETTE
from mpcasu_qt.threads import _ThreadBridge

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from casu.locations import is_youtube_url
from casu.spotify import SpotifyError, expand_spotify, search_spotify


class SourcesView(QFrame):
    """In-window view for YouTube/Spotify search and network stream URLs.

    Replaces modal dialogs: consent gate, search entry, yt-dlp result list
    and status line all live inside the main window.
    """

    MODES = {
        "youtube": {
            "title": "YOUTUBE",
            "hint": "YouTube URL or search term — e.g. https://www.youtube.com/watch?v=…",
            "search": True,
            "web": False,
        },
        "url": {
            "title": "NETWORK STREAM",
            "hint": "HTTP(S), HLS, RTSP, RTP, UDP, FTP or SMB URL",
            "search": False,
            "web": False,
        },
    }

    sourceActivated = Signal(object)
    # Emitted with a flat list of SearchResult-style objects (individual
    # YouTube videos, expanded from playlists and/or several pasted URLs) that
    # the main window drops straight into the queue.
    queueItemsRequested = Signal(object)
    consentAccepted = Signal()
    closeRequested = Signal()
    webPlayerRequested = Signal(str, str, str)  # provider, query, url

    def __init__(self, settings_store, parent=None):
        super().__init__(parent)
        self.setObjectName("SourcesView")
        self._settings_store = settings_store
        self._mode = "youtube"
        self._results: list = []
        self._searching = False
        self._thumb_jobs = []
        self._bridge = _ThreadBridge()
        self._bridge.resultReady.connect(self._present_results)
        self._bridge.errorReady.connect(self._present_error)
        self._queue_bridge = _ThreadBridge()
        self._queue_bridge.resultReady.connect(self._present_queue_items)
        self._queue_bridge.errorReady.connect(self._present_error)
        self._thumb_bridge = _ThreadBridge()
        self._thumb_bridge.resultReady.connect(self._apply_thumb)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(10)

        self._consent_frame = QFrame()
        self._consent_frame.setObjectName("Panel")
        cf_layout = QVBoxLayout(self._consent_frame)
        cf_layout.setContentsMargins(14, 12, 14, 12)
        cf_layout.setSpacing(8)
        notice = QLabel(
            "Legal notice — YouTube search/playback uses yt-dlp (GNU GPL); "
            "Spotify uses spotDL: Spotify metadata matched on YouTube "
            "(metadata → match → YouTube audio source).\n"
            "Stream URLs are resolved temporarily and never stored or "
            "redistributed. Personal use only.")
        notice.setObjectName("NowPlayingMeta")
        notice.setWordWrap(True)
        cf_layout.addWidget(notice)
        accept_btn = QPushButton("Accept and enable yt-dlp features")
        accept_btn.setObjectName("NavItem")
        accept_btn.setStyleSheet(
            f"background-color: {PALETTE.accent}; color: {PALETTE.text_on_accent}; font-weight: 600;")
        accept_btn.clicked.connect(self._accept_consent)
        cf_layout.addWidget(accept_btn, 0, Qt.AlignLeft)
        layout.addWidget(self._consent_frame)

        entry_row = QHBoxLayout()
        self._entry = QLineEdit()
        self._entry.setFixedHeight(34)
        self._entry.returnPressed.connect(self._open_typed)
        entry_row.addWidget(self._entry, 1)
        self._youtube_search_type = QComboBox()
        self._youtube_search_type.setObjectName("IconButton")
        self._youtube_search_type.addItem("Videos", "videos")
        self._youtube_search_type.addItem("Playlists", "playlists")
        entry_row.addWidget(self._youtube_search_type)
        self._go_btn = QPushButton("Play / search")
        self._go_btn.setObjectName("NavItem")
        self._go_btn.setStyleSheet(
            f"background-color: {PALETTE.accent}; color: {PALETTE.text_on_accent}; font-weight: 600;")
        self._go_btn.clicked.connect(self._open_typed)
        entry_row.addWidget(self._go_btn)
        layout.addLayout(entry_row)

        self._list = QListWidget()
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._list.itemDoubleClicked.connect(
            lambda item: self._play_row(self._list.row(item)))
        layout.addWidget(self._list, 1)

        self._status = QLabel("Search uses yt-dlp (GNU GPL) · personal use only")
        self._status.setObjectName("NowPlayingMeta")
        self._status.setStyleSheet(f"color: {PALETTE.text_faint};")
        layout.addWidget(self._status)

    def set_mode(self, mode: str):
        if mode not in self.MODES:
            mode = "youtube"
        self._mode = mode
        spec = self.MODES[mode]
        self._entry.setPlaceholderText(spec["hint"])
        self._entry.clear()
        self._list.clear()
        self._results = []
        self._searching = False
        self._go_btn.setText("Play / search" if spec["search"] else "Play")
        self._youtube_search_type.setVisible(mode == "youtube")
        # The yt-dlp consent gate only applies to YouTube search.
        self._consent_frame.setVisible(
            mode == "youtube" and not self._consent_given())
        if spec["search"]:
            self._status.setText("Search uses yt-dlp (GNU GPL) · personal use only")
        else:
            self._status.setText("Opens directly in the internal libVLC backend — no external player")
        self._entry.setFocus()

    def _consent_given(self) -> bool:
        try:
            return bool(self._settings_store.load().ytdlp_consent)
        except (OSError, ValueError, TypeError):
            return False

    def _accept_consent(self):
        try:
            settings = self._settings_store.load()
            self._settings_store.save(replace(settings, ytdlp_consent=True))
        except (OSError, ValueError, TypeError):
            pass
        self._consent_frame.setVisible(False)
        self.consentAccepted.emit()

    def _open_typed(self):
        text = self._entry.text().strip()
        if not text:
            return
        # A free-form YouTube field: several videos and/or complete playlists
        # separated by commas/line breaks expand straight into the queue so
        # shuffle/repeat act per-video (Windows/Linux parity).
        if self._is_expandable_youtube(text):
            self._expand_youtube_input(text)
            return
        is_url = text.startswith(("http://", "https://", "rtsp://", "rtmp://",
                                  "udp://", "rtp://", "ftp://", "smb://"))
        if not is_url and self.MODES[self._mode]["search"]:
            self._run_search(text)
            return
        self.sourceActivated.emit(text)

    def _is_expandable_youtube(self, text: str) -> bool:
        from casu.search import split_youtube_input, youtube_playlist_id
        tokens = split_youtube_input(text)
        if not tokens:
            return False
        youtube = [t for t in tokens if is_youtube_url(t)]
        if not youtube:
            return False
        # A single plain video URL keeps the existing one-shot path; anything
        # with several entries (comma/line separated) or a playlist link goes
        # through the queue expansion.
        return len(youtube) > 1 or any(youtube_playlist_id(t) for t in youtube)

    def _expand_youtube_input(self, text: str):
        if self._searching:
            return
        self._searching = True
        self._list.clear()
        self._results = []
        self._status.setText("Expanding YouTube into the queue…")

        def worker():
            from casu.search import SearchError
            from casu.youtube_groups import expand_queue_input
            try:
                found = expand_queue_input(text)
            except SearchError as exc:
                self._queue_bridge.errorReady.emit(str(exc))
            else:
                self._queue_bridge.resultReady.emit(found)
        threading.Thread(target=worker, daemon=True).start()

    def _present_queue_items(self, found):
        self._searching = False
        self.queueItemsRequested.emit(list(found))
        self._status.setText(
            f"{len(found)} video(s)/playlist(s) added to the queue")

    def _expand_spotify_url(self, url: str):
        if self._searching:
            return
        self._searching = True
        self._list.clear()
        self._results = []
        self._status.setText("Expanding Spotify playlist via spotDL…")

        def worker():
            from casu.search import SearchResult
            try:
                found = [SearchResult(
                    title=r.title, url=r.url, duration=r.duration,
                    uploader=r.artist or "Spotify", source="spotify")
                    for r in expand_spotify(url)]
            except SpotifyError as exc:
                self._bridge.errorReady.emit(str(exc))
            else:
                self._bridge.resultReady.emit(found)
        threading.Thread(target=worker, daemon=True).start()

    def _expand_youtube_playlist(self, url: str, title: str = ""):
        if self._searching:
            return
        self._searching = True
        self._list.clear()
        self._results = []
        self._status.setText("Expanding YouTube playlist…")

        def worker():
            from casu.search import SearchError
            from casu.youtube_groups import expand_queue_input
            try:
                found = expand_queue_input(url, title=title)
            except SearchError as exc:
                self._queue_bridge.errorReady.emit(str(exc))
            else:
                self._queue_bridge.resultReady.emit(found)
        threading.Thread(target=worker, daemon=True).start()

    def _fetch_spotify_handoff(self, url: str):
        self._open_web_player("spotify", url=url)

    def _run_search(self, query: str):
        if self._searching:
            return
        self._searching = True
        self._list.clear()
        self._results = []
        if self._mode == "spotify":
            self._status.setText("Searching Spotify via spotDL (open.spotify.com)…")
        else:
            self._status.setText("Searching YouTube via yt-dlp…")
        mode = self._mode
        youtube_search_type = str(self._youtube_search_type.currentData() or "videos")

        def worker():
            try:
                from casu.search import (SearchResult, search_youtube,
                                         search_youtube_playlists)
                if mode == "spotify":
                    found = [SearchResult(title=r.title, url=r.url,
                                          duration=r.duration,
                                          uploader=r.artist or "Spotify",
                                          source="spotify")
                             for r in search_spotify(query, limit=12)]
                else:
                    found = (search_youtube_playlists(query, limit=25)
                             if youtube_search_type == "playlists"
                             else search_youtube(query, limit=25))
            except Exception as exc:  # noqa: BLE001 - surface any engine failure
                self._bridge.errorReady.emit(str(exc))
            else:
                self._bridge.resultReady.emit(found)
        threading.Thread(target=worker, daemon=True).start()

    def _present_results(self, found):
        self._searching = False
        self._results = list(found)
        self._list.clear()
        self._list.setIconSize(QSize(120, 68))
        self._thumb_jobs = []
        for row, item in enumerate(self._results):
            duration = (f"{int(item.duration // 60)}:{int(item.duration % 60):02d}"
                        if item.duration else "live")
            tag = ("PLAYLIST" if item.source == "youtube_playlist" else
                   ("YT" if item.source != "handoff" else "FIND"))
            uploader = item.uploader or "unknown"
            title = item.title
            if len(title) > 70:
                title = title[:67] + "…"
            entry = QListWidgetItem(
                f"  {title}\n  {tag} · {uploader}  ·  {duration}  ▶")
            entry.setSizeHint(QSize(0, 76))
            self._list.addItem(entry)
            self._thumb_jobs.append((row, item))
        if self._thumb_jobs:
            self._load_thumbnails(list(self._thumb_jobs))
        self._status.setText(f"{len(self._results)} results — double-click or press Enter to play")

    def _load_thumbnails(self, jobs):
        bridge = self._thumb_bridge

        def worker():
            import urllib.request
            for row, item in jobs:
                url = str(item.thumbnail or "")
                if not url.startswith("http"):
                    continue
                try:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": "MPCASU/1.0"})
                    data = urllib.request.urlopen(
                        request, timeout=10).read(1024 * 1024)
                    image = QImage()
                    if image.loadFromData(data):
                        bridge.resultReady.emit(("thumb", row, image.copy()))
                except (OSError, ValueError):
                    continue
        threading.Thread(target=worker, daemon=True).start()

    def _apply_thumb(self, payload):
        if not payload or payload[0] != "thumb":
            return
        _, row, image = payload
        item = self._list.item(row)
        if item is None:
            return
        pixmap = QPixmap.fromImage(image).scaled(
            88, 50, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        item.setIcon(QIcon(pixmap))

    def _present_error(self, detail):
        self._searching = False
        self._status.setText(f"Search failed: {detail}")

    def _play_row(self, row: int):
        if 0 <= row < len(self._results):
            item = self._results[row]
            if item.source == "youtube_playlist":
                self._expand_youtube_playlist(item.url, item.title)
                return
            if item.source == "handoff":
                if not self._consent_given():
                    self._status.setText("Accept the yt-dlp legal notice above to enable the YouTube handoff")
                    return
                self._status.setText(f"Handoff to YouTube provider: {item.title}")
                self._run_search(item.title)
                return
            self.sourceActivated.emit(item)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return and self._list.hasFocus():
            self._play_row(self._list.currentRow())
            return
        if event.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)


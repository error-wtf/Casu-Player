# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# Copyright (C) 2026 Lino Casu
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the CASU Anti-Capitalist License 1.4.
# ---------------------------------------------------------------------
"""Queue widgets: QueueTree (drag-reorder list) and PlaylistPane (tabbed
queue/playlist editor).

Extracted from main_window.py in the v7.8 modularization pass. The pane
edits queue/playlist data and emits change signals; the authoritative
QueueModel stays in MainWindow.
"""

from __future__ import annotations

import threading
from pathlib import Path

from mpcasu_qt.theme import METRICS, PALETTE
from mpcasu_qt.threads import _ThreadBridge

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from casu.playlist import (
    PlaylistError,
    detect_entry_type,
    detect_media_type,
    load_playlist_file,
    playlist_names,
)
from casu.tags import metadata_for
from casu.thumbnail import thumbnail_for

# Tree items carry their QUEUE MODEL index in this role. Under an active
# view filter (Local files/Streams/…) the tree shows only a subset, so tree
# rows and model indices diverge; every consumer must map through this role.
_MODEL_INDEX_ROLE = Qt.UserRole + 2


class QueueTree(QTreeWidget):
    """Queue list with drag-reorder, Delete removal and a context menu."""

    orderChanged = Signal(list)
    removePressed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(2)
        self.setColumnWidth(0, METRICS.playlist_width - 110)
        self.setColumnWidth(1, 104)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {PALETTE.surface_alt};
                border: 0; outline: 0; font-size: 12px;
            }}
            QTreeWidget::item {{
                background: transparent;
                border-bottom: 1px solid {PALETTE.border};
                padding: 7px 6px; color: {PALETTE.text};
            }}
            QTreeWidget::item:hover {{ background-color: #171b20; }}
            QTreeWidget::item:selected {{
                background-color: {PALETTE.accent_wash};
                color: {PALETTE.accent};
            }}
            QTreeWidget::branch {{ background: transparent; }}
            QScrollBar:vertical {{ background: {PALETTE.surface}; width: 10px; }}
            QScrollBar::handle:vertical {{ background: {PALETTE.border_strong}; border-radius: 5px; }}
        """)

    def dropEvent(self, event):
        super().dropEvent(event)
        order = []
        for index in range(self.topLevelItemCount()):
            order.append(self.topLevelItem(index).data(0, Qt.UserRole))
        self.orderChanged.emit(order)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            # Model indices, not tree rows: under a view filter the tree
            # shows a subset and tree rows would delete the wrong entries.
            indexes = set()
            for item in self.selectedItems():
                if item.parent() is not None:
                    continue
                value = item.data(0, _MODEL_INDEX_ROLE)
                if value is None:
                    continue
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue
                if index >= 0:
                    indexes.add(index)
            rows = sorted(indexes, reverse=True)
            if rows:
                self.removePressed.emit(rows)
                return
        super().keyPressEvent(event)


class PlaylistPane(QFrame):
    """Right-side playlist drawer with expandable playlists."""

    playRequested = Signal(int)
    removeRequested = Signal(list)
    # moveRequested: (delta, selected model rows) — moving a multi-selection
    # (Ctrl/Shift) moves all selected rows together.
    moveRequested = Signal(int, list)
    orderChanged = Signal(list)
    childPlayRequested = Signal(str)
    saveRequested = Signal()
    loadRequested = Signal()
    addRequested = Signal()
    urlRequested = Signal()
    renameRequested = Signal(int)
    favoriteRequested = Signal(list)
    # mergeRequested: emit the selected top-level rows (media/URLs) so the
    # main window can offer to merge/append them into a playlist.
    mergeRequested = Signal(list)
    # Playlist children taken out of their playlist file ("remove from
    # playlist" / "move to playlist").
    childRemoveRequested = Signal(list)
    childMoveRequested = Signal(list)
    # v7.8.1: playlist editor shortcuts (previously Player-only dead UI).
    newPlaylistRequested = Signal()
    addCurrentToPlaylistRequested = Signal()

    # All row-based signals (playRequested/removeRequested/moveRequested/
    # favoriteRequested/renameRequested) emit MODEL indices, never tree rows:
    # under an active view filter the tree shows a subset, so tree rows would
    # address (and delete/move/favorite) the WRONG queue entries.

    PLAYLIST_SUFFIXES = {".cue", ".m3u", ".m3u8", ".pls", ".json", ".wpl", ".xspf",
                         ".jspf", ".asx", ".wmx", ".wvx", ".rmp", ".ram"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlaylistPane")
        self.setFixedWidth(METRICS.playlist_width)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("TopBar")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 14, 12, 8)
        title = QLabel("PLAYLIST")
        title.setObjectName("NowPlayingTitle")
        title.setStyleSheet("font-size: 14px; background: transparent;")
        header_layout.addWidget(title)
        sub = QLabel("Queue · expandable · drag to reorder")
        sub.setObjectName("NowPlayingMeta")
        header_layout.addWidget(sub)
        self._view_combo = QComboBox()
        self._view_combo.setObjectName("IconButton")
        for label, key in [("All items", "all"), ("Local files", "files"),
                           ("Streams / IPTV", "streams"), ("Playlists", "playlists"),
                           ("CASU", "casu"), ("YouTube", "youtube"),
                           ("Spotify", "spotify")]:
            self._view_combo.addItem(label, key)
        self._view_combo.currentIndexChanged.connect(lambda *_: self._apply_view_filter())
        header_layout.addWidget(self._view_combo)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        choose_btn = QPushButton("Choose files")
        choose_btn.setObjectName("PrimaryButton")
        choose_btn.setToolTip("Add media files to the queue (Ctrl+O)")
        choose_btn.clicked.connect(lambda: self.addRequested.emit())
        actions.addWidget(choose_btn, 1)
        url_btn = QPushButton("Add URL")
        url_btn.setObjectName("IconButton")
        url_btn.setToolTip("Add a network stream URL (Ctrl+L)")
        url_btn.clicked.connect(lambda: self.urlRequested.emit())
        actions.addWidget(url_btn)
        header_layout.addLayout(actions)
        playlist_actions = QHBoxLayout()
        playlist_actions.setSpacing(6)
        new_playlist_btn = QPushButton("New playlist")
        new_playlist_btn.setObjectName("IconButton")
        new_playlist_btn.setToolTip("Create a new playlist file")
        new_playlist_btn.clicked.connect(lambda: self.newPlaylistRequested.emit())
        playlist_actions.addWidget(new_playlist_btn)
        add_current_btn = QPushButton("Add current media")
        add_current_btn.setObjectName("IconButton")
        add_current_btn.setToolTip("Append the currently playing media to a playlist")
        add_current_btn.clicked.connect(
            lambda: self.addCurrentToPlaylistRequested.emit())
        playlist_actions.addWidget(add_current_btn)
        header_layout.addLayout(playlist_actions)
        layout.addWidget(header)

        self.tree = QueueTree(self)
        self._collapsed: set = set()
        self._all_paths: list = []
        self._display_titles: dict = {}
        self._tag_titles: dict = {}
        self._search = ""
        self._thumb_bridge = _ThreadBridge()
        self._thumb_bridge.resultReady.connect(self._apply_thumb)
        self._thumb_dir = Path.home() / ".cache" / "mpcasu" / "thumbnails"
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemExpanded.connect(self._on_expanded)
        self.tree.itemCollapsed.connect(self._on_collapsed)
        self.tree.orderChanged.connect(lambda order: self.orderChanged.emit(order))
        self.tree.removePressed.connect(lambda rows: self.removeRequested.emit(rows))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tree.setIconSize(QSize(METRICS.thumbnail_width, METRICS.thumbnail_height))
        layout.addWidget(self.tree, 1)

        controls = QFrame()
        controls.setObjectName("TopBar")
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(10, 8, 10, 8)
        up_btn = QPushButton("↑")
        up_btn.setObjectName("IconButton")
        up_btn.setFixedWidth(30)
        up_btn.setToolTip("Move selection up")
        up_btn.clicked.connect(lambda: self.moveRequested.emit(-1, self.selected_rows()))
        cl.addWidget(up_btn)
        down_btn = QPushButton("↓")
        down_btn.setObjectName("IconButton")
        down_btn.setFixedWidth(30)
        down_btn.setToolTip("Move selection down")
        down_btn.clicked.connect(lambda: self.moveRequested.emit(1, self.selected_rows()))
        cl.addWidget(down_btn)
        remove_btn = QPushButton("×")
        remove_btn.setObjectName("IconButton")
        remove_btn.setFixedWidth(30)
        remove_btn.setToolTip("Remove selected entries (Del)")
        remove_btn.clicked.connect(self._remove_selection)
        cl.addWidget(remove_btn)
        rename_btn = QPushButton("✎")
        rename_btn.setObjectName("IconButton")
        rename_btn.setFixedWidth(30)
        rename_btn.setToolTip("Rename the selected queue entry")
        rename_btn.clicked.connect(lambda: self.renameRequested.emit(self.selected_row()))
        cl.addWidget(rename_btn)
        cl.addStretch()
        load_btn = QPushButton("Load")
        load_btn.setObjectName("IconButton")
        load_btn.setFixedWidth(46)
        load_btn.setToolTip("Load M3U/PLS/JSON playlist")
        load_btn.clicked.connect(lambda: self.loadRequested.emit())
        cl.addWidget(load_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("IconButton")
        save_btn.setFixedWidth(46)
        save_btn.setToolTip("Save queue as M3U/PLS/JSON playlist")
        save_btn.clicked.connect(lambda: self.saveRequested.emit())
        cl.addWidget(save_btn)
        layout.addWidget(controls)

        self.empty_label = QLabel("No media queued\nAdd files or drop a playlist here")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setObjectName("NowPlayingMeta")
        self.empty_label.setStyleSheet(f"color: {PALETTE.text_faint}; padding: 20px; background: transparent;")
        layout.addWidget(self.empty_label)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 4, 12, 12)
        self.shuffle_btn = QPushButton("Shuffle off")
        self.shuffle_btn.setObjectName("IconButton")
        self.shuffle_btn.setCheckable(True)
        footer_layout.addWidget(self.shuffle_btn)
        self.repeat_btn = QPushButton("Repeat off")
        self.repeat_btn.setObjectName("IconButton")
        footer_layout.addWidget(self.repeat_btn)
        footer_layout.addStretch()
        layout.addWidget(footer)

    # --- public API used by MainWindow ---

    def select_row(self, row: int):
        """Select the tree item for MODEL index ``row`` (no-op when the view
        filter currently hides it)."""
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if self._model_index_of(item) == row:
                self.tree.setCurrentItem(item)
                return

    def select_child(self, playlist_path, child_path):
        """Highlight a specific child of an expandable playlist group."""
        playlist_path = str(playlist_path)
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            if str(top.data(0, Qt.UserRole) or "") != playlist_path:
                continue
            if not top.isExpanded():
                top.setExpanded(True)
                self._expand_playlist_item(top)
            wanted = str(child_path)
            for c in range(top.childCount()):
                child = top.child(c)
                if str(child.data(0, Qt.UserRole) or "") == wanted:
                    self.tree.setCurrentItem(child)
                    return
            self.tree.setCurrentItem(top)
            return

    def selected_row(self) -> int:
        """MODEL index of the first selected top-level item, or -1."""
        items = self.tree.selectedItems()
        for item in items:
            index = self._model_index_of(item)
            if index >= 0:
                return index
        return -1

    def selected_rows(self) -> list:
        """Sorted MODEL indices of the current (multi-)selection."""
        return sorted({self._model_index_of(item)
                       for item in self.tree.selectedItems()
                       if self._model_index_of(item) >= 0})

    def _model_index_of(self, item) -> int:
        """Model (queue) index stored on the tree item, or -1.

        Qt.UserRole+2 is set in populate(); a missing value can only mean a
        stale/foreign item, which maps to no queue row. NOTE: 0 is a valid
        index — compare against None, not falsiness."""
        if item is None:
            return -1
        value = item.data(0, _MODEL_INDEX_ROLE)
        if value is None:
            return -1
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    def selected_child(self) -> str | None:
        """Path/URL of the selected child of an expanded playlist group."""
        for item in self.tree.selectedItems():
            if item.parent() is not None and item.data(0, Qt.UserRole):
                return str(item.data(0, Qt.UserRole))
        return None

    def selected_playlist_path(self) -> Path | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        if item.parent() is not None:
            item = item.parent()
        value = str(item.data(0, Qt.UserRole) or "")
        return Path(value) if self._is_playlist(value) else None

    def _remove_selection(self):
        child = self.selected_child()
        if child is not None:
            self.childRemoveRequested.emit([child])
            return
        rows = self.selected_rows()
        if rows:
            self.removeRequested.emit(rows)

    def item_for_model_index(self, index: int):
        """Tree item currently representing MODEL index ``index``, or None."""
        for row in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(row)
            if self._model_index_of(item) == int(index):
                return item
        return None

    def select_rows(self, indexes: list):
        """Re-apply a multi-selection after a queue re-render."""
        want = {str(self._all_paths[i]) for i in indexes
                if 0 <= i < len(self._all_paths)}
        if not want:
            return
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if str(item.data(0, Qt.UserRole)) in want:
                item.setSelected(True)
        self.tree.blockSignals(False)

    def populate(self, paths: list, selected: int = -1):
        self._all_paths = list(paths)
        view = str(self._view_combo.currentData() or "all")
        visible = [(index, path) for index, path in enumerate(self._all_paths)
                   if self._matches(path, view)]
        # Drag-reorder reorders the WHOLE queue; under a view filter the tree
        # only shows a subset and a drop could not express a valid full order
        # (orderChanged would silently no-op on the count mismatch or, worse,
        # misorder the queue). Reordering stays available in the "All items"
        # view; search hiding (items stay in the tree) is unaffected.
        filtered = len(visible) != len(self._all_paths)
        self.tree.setDragDropMode(
            QAbstractItemView.NoDragDrop if filtered
            else QAbstractItemView.InternalMove)
        self.tree.blockSignals(True)
        self.tree.clear()
        for _index, path in visible:
            item = QTreeWidgetItem([self._label_for(path)])
            item.setData(0, Qt.UserRole, str(path))
            item.setData(0, _MODEL_INDEX_ROLE, _index)
            item.setToolTip(0, str(path))
            item.setText(1, self._badge_for(path))
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setForeground(1, QBrush(QColor(PALETTE.text_faint)))
            item.setFont(1, QFont(item.font(0).family(), max(8, item.font(0).pointSize() - 1)))
            item.setIcon(0, QIcon(self._thumb_for(path)))
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled
                          | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.tree.addTopLevelItem(item)
            if self._is_playlist(path):
                placeholder = QTreeWidgetItem(["…"])
                placeholder.setFlags(Qt.NoItemFlags)
                item.addChild(placeholder)
                item.setExpanded(str(path) not in self._collapsed)
        self.tree.blockSignals(False)
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.isExpanded() and self._is_playlist(item.data(0, Qt.UserRole) or ""):
                self._expand_playlist_item(item)
                item.setExpanded(True)
        if 0 <= selected < len(self._all_paths):
            want = str(self._all_paths[selected])
            for index in range(self.tree.topLevelItemCount()):
                if str(self.tree.topLevelItem(index).data(0, Qt.UserRole)) == want:
                    self.tree.setCurrentItem(self.tree.topLevelItem(index))
                    break
        if self._search:
            self._apply_search()
        self._request_thumbnails()
        self.empty_label.setVisible(len(self._all_paths) == 0)

    def set_search(self, text: str):
        self._search = (text or "").strip().lower()
        self._apply_search()

    def set_view(self, key: str):
        index = self._view_combo.findData(key)
        if index >= 0:
            self._view_combo.setCurrentIndex(index)
        self._apply_view_filter()

    def _apply_search(self):
        query = self._search
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            label = item.text(0).lower()
            child_hits = 0
            if query and self._is_playlist(item.data(0, Qt.UserRole) or ""):
                if not item.isExpanded():
                    item.setExpanded(True)
                for c in range(item.childCount()):
                    child = item.child(c)
                    hit = bool(query) and query in child.text(0).lower()
                    child.setHidden(bool(query) and not hit)
                    child_hits += 1 if hit else 0
            item.setHidden(bool(query) and query not in label and child_hits == 0)

    def _request_thumbnails(self):
        jobs = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            path = str(item.data(0, Qt.UserRole) or "")
            if not path or path.startswith(("http://", "https://", "rtsp://", "rtmp://")):
                continue
            if Path(path).suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".avi"}:
                continue
            jobs.append(path)
        if not jobs:
            return
        bridge = self._thumb_bridge
        cache = str(self._thumb_dir)

        def worker():
            from casu.thumbnail import thumbnail_for
            for path in jobs:
                try:
                    thumb = thumbnail_for(path, cache)
                except Exception:  # noqa: BLE001 - thumbnails are optional
                    thumb = None
                if thumb is not None:
                    bridge.resultReady.emit((path, str(thumb)))
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _apply_thumb(self, payload):
        path, thumb = payload
        pix = QPixmap(thumb)
        if pix.isNull():
            return
        scaled = pix.scaled(METRICS.thumbnail_width, METRICS.thumbnail_height,
                            Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if str(item.data(0, Qt.UserRole) or "") == path:
                item.setIcon(0, QIcon(scaled))

    def _apply_view_filter(self):
        current = self.tree.currentItem()
        sel = -1
        if current is not None and current.parent() is None:
            want = str(current.data(0, Qt.UserRole))
            if want in [str(p) for p in self._all_paths]:
                sel = [str(p) for p in self._all_paths].index(want)
        self.populate(self._all_paths, sel)

    def _matches(self, path, view: str) -> bool:
        s = str(path)
        low = s.lower()
        is_url = low.startswith(("http://", "https://", "rtsp://", "rtmp://"))
        if view == "all":
            return True
        if view == "playlists":
            return self._is_playlist(path)
        if view == "files":
            return not is_url and not self._is_playlist(path)
        if view == "casu":
            return low.endswith((".casu", ".mp5"))
        if view == "youtube":
            return "youtube.com" in low or "youtu.be" in low
        if view == "spotify":
            return "spotify.com" in low
        if view == "streams":
            return is_url and not self._is_playlist(path)
        return True

    def clear(self):
        self.tree.clear()
        self.empty_label.setVisible(True)

    # --- internals ---

    def _thumb_for(self, path) -> QPixmap:
        """Web-style 54x38 thumbnail: red/dark gradient + format glyph."""
        pixmap = QPixmap(METRICS.thumbnail_width, METRICS.thumbnail_height)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        gradient = QLinearGradient(0, 0, METRICS.thumbnail_width, METRICS.thumbnail_height)
        gradient.setColorAt(0.0, QColor("#391119"))
        gradient.setColorAt(1.0, QColor("#080b0f"))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, METRICS.thumbnail_width, METRICS.thumbnail_height, 5, 5)
        glyph = self._badge_for(path)
        short = {"MP4": "▶", "MP3": "♪", "CASU": "◈", "MP5": "◉", "PLAYLIST": "≡",
                 "STREAM": "∿", "YT": "▶", "RTSP": "∿", "RTMP": "∿", "HLS": "∿"}.get(glyph, "•")
        painter.setPen(QPen(QColor(PALETTE.text), 15))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, short)
        painter.end()
        return pixmap

    @staticmethod
    def _is_playlist(path) -> bool:
        # Remote URLs (even with a playlist-like suffix, e.g. stream.m3u8)
        # are stream entries, never playlist groups.
        try:
            text = str(path)
            if text.startswith(("http://", "https://", "rtsp://", "rtmp://",
                                "udp://", "rtp://", "ftp://", "smb://")):
                return False
            return Path(text).suffix.lower() in PlaylistPane.PLAYLIST_SUFFIXES
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _badge_for(path) -> str:
        text = str(path)
        if text.startswith(("http://", "https://", "rtsp://", "rtmp://")):
            try:
                etype = detect_entry_type(text)
            except (ValueError, TypeError):
                etype = "http-stream"
            return {"youtube": "YT", "http-stream": "STREAM",
                    "rtsp-stream": "RTSP", "rtmp-stream": "RTMP"}.get(
                etype, "STREAM")
        try:
            return detect_media_type(path)
        except (OSError, ValueError, TypeError):
            return "MEDIA"

    def _label_for(self, path) -> str:
        text = str(path)
        display = self._display_titles.get(text, "")
        if display:
            return display
        if text.startswith(("http://", "https://", "rtsp://", "rtmp://",
                            "udp://", "rtp://", "spotify:", "ytdl:")):
            return text
        cached = self._tag_titles.get(text)
        if cached is None:
            cached = self._tag_titles[text] = self._read_tag_title(Path(text))
        return cached or Path(text).name

    @staticmethod
    def _read_tag_title(path) -> str:
        """Return "title — artist" from media tags, else an empty string."""
        try:
            from casu.tags import metadata_for
            tags = metadata_for(path)
            title = str(tags.get("title") or "").strip()
            artist = str(tags.get("artist") or "").strip()
            if title:
                return f"{title} — {artist}" if artist else title
        except Exception:  # noqa: BLE001 - tags are best effort
            return ""
        return ""

    @staticmethod
    def _child_badge(entry) -> str:
        text = str(entry)
        try:
            etype = detect_entry_type(text)
        except (ValueError, TypeError):
            etype = "local-file"
        return {"local-file": detect_media_type(text) if Path(text).suffix else "FILE",
                "casu": "CASU", "mp5": "MP5", "playlist": "PL",
                "http-stream": "STREAM", "youtube": "YT",
                "rtsp-stream": "RTSP", "rtmp-stream": "RTMP"}.get(etype, "MEDIA")

    @staticmethod
    def _child_label(entry, display: str = "") -> str:
        text = str(entry)
        name = display or (Path(text).name if not text.startswith(("http://", "https://", "rtsp://")) else text)
        return name

    def _on_clear(self):
        self.removeRequested.emit(self.selected_rows())

    def _on_double_click(self, item, _column):
        if item.parent() is None and self._is_playlist(item.data(0, Qt.UserRole) or ""):
            item.setExpanded(not item.isExpanded())
            return
        if item.parent() is None:
            index = self._model_index_of(item)
            if index >= 0:
                self.playRequested.emit(index)
                return
        parent = item.parent()
        if parent is not None and item.data(0, Qt.UserRole):
            self.childPlayRequested.emit(str(item.data(0, Qt.UserRole)))

    def _on_item_clicked(self, item, _column):
        if item.parent() is None and self._is_playlist(item.data(0, Qt.UserRole) or ""):
            item.setExpanded(not item.isExpanded())
            return

    def _on_expanded(self, item):
        self._collapsed.discard(item.data(0, Qt.UserRole))
        self._expand_playlist_item(item)

    def _on_collapsed(self, item):
        self._collapsed.add(item.data(0, Qt.UserRole))

    def _expand_playlist_item(self, item):
        if item.childCount() and item.child(0).data(0, Qt.UserRole):
            return
        source = str(item.data(0, Qt.UserRole))
        while item.childCount():
            item.removeChild(item.child(0))
        try:
            loaded = load_playlist_file(source)
        except (PlaylistError, OSError, ValueError) as exc:
            error_item = QTreeWidgetItem([f"Could not expand: {exc}"])
            error_item.setFlags(Qt.NoItemFlags)
            item.addChild(error_item)
            return
        entries = list(loaded.items)
        if not entries:
            empty_item = QTreeWidgetItem(["(empty playlist)"])
            empty_item.setFlags(Qt.NoItemFlags)
            item.addChild(empty_item)
            return
        names = playlist_names(source)
        for entry in entries:
            display = names.get(str(entry), "")
            child = QTreeWidgetItem([self._child_label(entry, display)])
            child.setData(0, Qt.UserRole, str(entry))
            if display:
                child.setData(0, Qt.UserRole + 1, display)
            child.setText(1, self._child_badge(entry))
            child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            child.setForeground(1, QBrush(QColor(PALETTE.text_faint)))
            child.setFont(1, QFont(child.font(0).family(), max(8, child.font(0).pointSize() - 1)))
            child.setToolTip(0, str(entry))
            child.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.addChild(child)

    def name_for(self, url: str) -> str:
        """Display name for a queued stream URL (from playlist EXTINF names)."""
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for c in range(parent.childCount()):
                child = parent.child(c)
                if str(child.data(0, Qt.UserRole)) == str(url):
                    return str(child.data(0, Qt.UserRole + 1) or "").strip()
        return ""

    def refresh_group(self, playlist_path):
        """Re-read the children of a playlist group from its (possibly
        rewritten) file, keeping the current expanded/collapsed state."""
        playlist_path = str(playlist_path)
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            if str(top.data(0, Qt.UserRole) or "") != playlist_path:
                continue
            expanded = top.isExpanded()
            while top.childCount():
                top.removeChild(top.child(0))
            self._expand_playlist_item(top)
            top.setExpanded(expanded)
            return

    def _context_menu(self, position):
        item = self.tree.itemAt(position)
        menu = QMenu(self)
        if item is None:
            menu.addAction("Clear queue", lambda: self.removeRequested.emit([]))
            menu.exec(self.tree.viewport().mapToGlobal(position))
            return
        selected = self.tree.selectedItems()
        top_rows = sorted({self._model_index_of(sel)
                           for sel in selected
                           if sel.parent() is None
                           and self._model_index_of(sel) >= 0})
        row = self._model_index_of(item) if item.parent() is None else -1
        # If the right-clicked item is not part of the current multi-selection,
        # collapse the action set to that single item.
        if row >= 0 and row not in top_rows:
            top_rows = [row]
        if row >= 0:
            count = len(top_rows)
            label = f"Play" if count <= 1 else f"Play ({count} items)"
            menu.addAction(label, lambda: self.playRequested.emit(top_rows[0]))
            if count == 1:
                single = item
                if single.childCount() or self._is_playlist(str(single.data(0, Qt.UserRole))):
                    if single.isExpanded():
                        menu.addAction("Collapse", single.setCollapsed)
                    else:
                        menu.addAction("Expand", single.setExpanded)
            menu.addSeparator()
            menu.addAction("Move up", lambda: self.moveRequested.emit(-1, list(top_rows)))
            menu.addAction("Move down", lambda: self.moveRequested.emit(1, list(top_rows)))
            remove_label = "Remove" if count <= 1 else f"Remove ({count} items)"
            menu.addAction(remove_label, lambda: self.removeRequested.emit(list(top_rows)))
            menu.addSeparator()
            fav_label = "Toggle ★ Favorite" if count <= 1 else f"Toggle ★ ({count} items)"
            menu.addAction(fav_label, lambda: self.favoriteRequested.emit(list(top_rows)))
        else:
            parent = item.parent()
            if parent is not None and item.data(0, Qt.UserRole):
                menu.addAction("Play", lambda: self.childPlayRequested.emit(
                    str(item.data(0, Qt.UserRole))))
                # Playlist children (the media inside a playlist) can also be
                # merged/added to any playlist, same as top-level rows.
                child_rows = [item] if parent is None else [
                    parent.child(c) for c in range(parent.childCount())
                    if parent.child(c).isSelected()
                    and parent.child(c).data(0, Qt.UserRole)]
                if not child_rows or item not in child_rows:
                    child_rows = [item]
                data = [str(c.data(0, Qt.UserRole)) for c in child_rows]
                label = "Save to playlist…" if len(data) == 1 else \
                        f"Save {len(data)} items to playlist…"
                menu.addAction(label, lambda: self.mergeRequested.emit(data))
                move_label = "Move to playlist…" if len(data) == 1 else \
                             f"Move {len(data)} items to playlist…"
                menu.addAction(move_label, lambda: self.childMoveRequested.emit(data))
                remove_label = "Remove from playlist" if len(data) == 1 else \
                               f"Remove {len(data)} items from playlist"
                menu.addAction(remove_label, lambda: self.childRemoveRequested.emit(data))
        menu.exec(self.tree.viewport().mapToGlobal(position))



# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""MPCASU Qt main window — full-featured media player UI."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QObject, Property, QPropertyAnimation, QRect, QRectF, QPointF, Qt, QTimer,
    Signal, Slot, QSize,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap,
    QTextDocument, QImage, QLinearGradient, QRadialGradient, QBrush, QGuiApplication,
    QPainterPath, QPolygonF,
)

from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QStackedWidget, QStatusBar, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QDoubleSpinBox, QGridLayout, QSplitter, QTabBar,
)

from casu.core import CasuError, ffprobe, resolve_casu_source
from casu.locations import (
    LocationResolutionError, is_youtube_url, resolve_media_location,
)
from casu.schema import validate_manifest
from casu.scheduler import CasuScheduler
from casu.library import MediaLibrary, PlaybackPreferences
from casu.media import TrackKind
from casu.playlist import (
    PlaylistError, PlaylistModel, detect_entry_type, detect_media_type,
    load_playlist_file, playlist_names, save_playlist_file,
)
from casu.settings import SettingsStore
from casu.spotify import (SpotifyError, expand_spotify, fetch_spotify_metadata,
                          is_spotify_url, open_spotify_web, resolve_spotify_url,
                          search_spotify, spotify_kind, youtube_handoff_query)
from casu.thumbnail import thumbnail_for
from casu.waveform import decode_all_pcm, window_wave
from casu.recording import MediaRecorder, RecordingError

from casu.native import NativeCasuError, read_native
from casu.native_v2 import ChunkType, NativeV2Error, read_native_v2

from mpcasu_backend import (
    BackendError, CasuBackend, LibVLCBackend, PlaybackState,
    display_media_source,
)
from mpcasu_native_backend import NativeCasuBackend, PulseAudioSink
from mpcasu_playback import PlaybackController

from mpcasu_qt.theme import PALETTE, METRICS, apply_dark_combo_popup, format_duration, stylesheet
from mpcasu_qt.videoframe import QtVideoSurfaceSink, VideoSurface
from mpcasu_qt.youtube_proxy import YouTubeMediaProxy, YouTubeProxyError

MEDIA_EXTENSIONS = {".mp4", ".mp3", ".mkv", ".m4v", ".mov", ".flac", ".wav", ".ogg", ".webm", ".m4a", ".aac", ".opus", ".aiff", ".alac", ".casu"}

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".aiff", ".alac"}


class ChapterTimeline(QSlider):
    """Seek slider with chapter markers painted on top."""

    chaptersChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setObjectName("Timeline")
        self.setRange(0, 1000)
        self._chapters: list = []
        self._active_chapter = -1
        self._dragging = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

    def set_chapters(self, chapters, active=-1):
        self._chapters = list(chapters)
        self._active_chapter = int(active)
        self.update()

    def clear_chapters(self):
        self._chapters = []
        self._active_chapter = -1
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._chapters:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            groove_rect = self.style().subControlRect(
                self.style().CC_Slider, self.style().SC_SliderGroove, self
            ) if hasattr(self.style(), 'CC_Slider') else self.rect()
            if groove_rect.isNull() or groove_rect.width() <= 0:
                return
            width = max(1, groove_rect.width() - 16)
            offset = groove_rect.x() + 8
            for chapter in self._chapters:
                try:
                    identifier = int(chapter.identifier)
                    start = float(chapter.start_seconds) if hasattr(chapter, 'start_seconds') else 0.0
                    title = str(chapter.title) if hasattr(chapter, 'title') else ""
                except (AttributeError, ValueError, TypeError):
                    continue
                duration = max(1.0, float(self.maximum()))
                x = offset + int((start / duration) * width) if duration > 0 else offset
                x = max(offset, min(offset + width, x))
                color = QColor(PALETTE.accent) if identifier == self._active_chapter else QColor(PALETTE.text_faint)
                painter.setPen(QPen(color, 1.5))
                painter.setBrush(color)
                painter.drawRect(x - 2, groove_rect.y() + 2, 4, groove_rect.height() - 4)
        finally:
            painter.end()


class NowPlayingBar(QFrame):
    """Top bar: fixed NOW PLAYING heading plus the current media metadata."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(62)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)

        meta_box = QVBoxLayout()
        meta_box.setContentsMargins(0, 0, 0, 0)
        meta_box.setSpacing(1)
        self.title_label = QLabel("NOW PLAYING")
        self.title_label.setObjectName("BreadcrumbLabel")
        meta_box.addWidget(self.title_label)
        self.media_title_label = QLabel("")
        self.media_title_label.setObjectName("NowPlayingMeta")
        meta_box.addWidget(self.media_title_label)
        layout.addLayout(meta_box)

        layout.addStretch()

        self.diagnostics_label = QLabel("CASU · LEGACY SAFE")
        self.diagnostics_label.setObjectName("NowPlayingMeta")
        layout.addWidget(self.diagnostics_label)

    def set_now_playing(self, text: str):
        """Show the media title without replacing the fixed NOW PLAYING heading."""
        if text:
            self.media_title_label.setText(str(text))
            self.media_title_label.show()
        else:
            self.media_title_label.setText("")
            self.media_title_label.hide()

    def set_diagnostics_text(self, text: str):
        self.diagnostics_label.setText(text)


def _nav_icon(name: str, color: QColor, active: QColor, size: int = 18) -> QIcon:
    """Font-independent sidebar nav icons drawn with QPainter."""
    icon = QIcon()
    for state, tint in ((QIcon.Off, color), (QIcon.On, active)):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(tint, 1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        r = QRectF(2.5, 2.5, size - 5.0, size - 5.0)
        c = r.center()
        w, h = r.width(), r.height()
        if name == "NOW PLAYING":
            p.setBrush(tint)
            p.drawPolygon(QPolygonF([QPointF(r.left(), r.top()),
                                     QPointF(r.right(), c.y()),
                                     QPointF(r.left(), r.bottom())]))
        elif name == "LIBRARY":
            p.drawRect(r.adjusted(0, 0, 0, 0))
            p.drawRect(r.adjusted(w * 0.22, h * 0.22, -w * 0.22, -h * 0.22))
        elif name == "WEB & STREAMS":
            p.drawEllipse(c, w * 0.48, h * 0.48)
            p.drawLine(QPointF(r.left(), c.y()), QPointF(r.right(), c.y()))
            p.drawEllipse(QPointF(c.x(), c.y()), w * 0.48, h * 0.2)
        elif name == "PLAYLISTS":
            for frac in (0.3, 0.5, 0.7):
                y = r.top() + h * frac
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        elif name == "IPTV / EPG":
            gap = 2.5
            cw2 = (w - gap * 3) / 2
            ch2 = (h - gap * 3) / 2
            for row in range(2):
                for col in range(2):
                    x = r.left() + gap + col * (cw2 + gap)
                    y = r.top() + gap + row * (ch2 + gap)
                    p.fillRect(int(x), int(y), int(cw2), int(ch2), tint)
        elif name == "YOUTUBE":
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
            p.setBrush(tint)
            p.drawPolygon(QPolygonF([QPointF(c.x() - 3, c.y() - 5),
                                     QPointF(c.x() + 6, c.y()),
                                     QPointF(c.x() - 3, c.y() + 5)]))
        elif name == "SPOTIFY":
            for frac in (0.2, 0.38, 0.56):
                r2 = r.adjusted(int(w * frac), int(h * frac),
                                -int(w * frac), -int(h * frac))
                p.drawArc(r2, -45 * 16, 90 * 16)
        elif name == "CASU FILES":
            half = min(w, h) * 0.35
            p.setBrush(tint)
            p.drawPolygon(QPolygonF([QPointF(c.x(), c.y() - half),
                                     QPointF(c.x() + half, c.y()),
                                     QPointF(c.x(), c.y() + half),
                                     QPointF(c.x() - half, c.y())]))
            half2 = half * 0.5
            pm2 = QPixmap(size, size); pm2.fill(Qt.transparent)
        elif name == "HEARTHIS":
            p.drawLine(QPointF(r.left(), r.bottom()),
                       QPointF(r.right(), r.top()))
            p.drawLine(QPointF(r.right() - 5, r.top()),
                       QPointF(r.right(), r.top()))
            p.drawLine(QPointF(r.right(), r.top()),
                       QPointF(r.right(), r.top() + 6))
        elif name == "TIDAL":
            for y_off in (-3, 3):
                pts = []
                for i in range(20):
                    frac = i / 19.0
                    x = r.left() + w * frac
                    y = c.y() + y_off + math.sin(frac * math.pi * 2) * h * 0.22
                    pts.append(QPointF(x, y))
                p.drawPolyline(QPolygonF(pts))
        elif name == "NETFLIX":
            top_l = QPointF(r.left() + 2, r.top())
            top_r = QPointF(r.right() - 2, r.top())
            bot_l = QPointF(r.left() + 2, r.bottom())
            bot_r = QPointF(r.right() - 2, r.bottom())
            p.drawLine(top_l, bot_l)
            p.drawLine(top_l, bot_r)
            p.drawLine(top_r, bot_r)
        elif name == "BROWSE":
            p.drawEllipse(c, w * 0.46, h * 0.46)
            p.drawLine(QPointF(c.x(), r.top()), QPointF(c.x(), r.bottom()))
            p.drawLine(QPointF(r.left(), c.y()), QPointF(r.right(), c.y()))
        elif name == "OPTIONS":
            p.drawEllipse(c, w * 0.18, h * 0.18)
            for i in range(8):
                angle = i * math.pi / 4.0
                x1 = c.x() + math.cos(angle) * w * 0.30
                y1 = c.y() + math.sin(angle) * h * 0.30
                x2 = c.x() + math.cos(angle) * w * 0.44
                y2 = c.y() + math.sin(angle) * h * 0.44
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        elif name == "ABOUT":
            p.drawEllipse(c, w * 0.44, h * 0.44)
            p.setBrush(tint)
            p.drawEllipse(QPointF(c.x(), c.y() - h * 0.14), 1.5, 1.5)
            p.drawLine(QPointF(c.x(), c.y() - h * 0.02),
                       QPointF(c.x(), c.y() + h * 0.22))
        else:
            p.drawText(r, Qt.AlignCenter, name[0])
        p.end()
        icon.addPixmap(pm, QIcon.Normal, state)
    return icon


class Sidebar(QFrame):
    """Left navigation sidebar."""

    navRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(METRICS.sidebar_width)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        logo_path = Path(__file__).resolve().parent.parent / "assets" / "mpcasu_player_logo_cropped.png"
        self._logo_label = None
        if logo_path.is_file():
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                scaled = pixmap.scaledToWidth(150, Qt.SmoothTransformation)
                header = QWidget()
                header.setStyleSheet("background: transparent;")
                # The native window crops the very top edge of the client
                # area, so keep the logo clearly below it.
                header.setFixedHeight(scaled.height() + 68)
                header_layout = QVBoxLayout(header)
                header_layout.setContentsMargins(16, 60, 16, 0)
                header_layout.setSpacing(0)
                logo = QLabel()
                logo.setStyleSheet("background: transparent;")
                logo.setPixmap(scaled)
                logo.setFixedSize(scaled.size())
                header_layout.addWidget(logo, 0, Qt.AlignLeft | Qt.AlignTop)
                layout.addWidget(header)
                self._logo_label = logo
        layout.addSpacing(14)

        nav_items = [
            ("LIBRARY", ["NOW PLAYING", "LIBRARY", "WEB & STREAMS",
                         "PLAYLISTS", "IPTV / EPG"]),
            ("SEARCH", ["YOUTUBE"]),
            ("CASU", ["CASU FILES"]),
            ("WEB PLAYERS", ["SPOTIFY", "HEARTHIS", "TIDAL", "NETFLIX", "BROWSE"]),
            ("SYSTEM", ["OPTIONS", "ABOUT"]),
        ]
        self.NAV_ICONS = {name: _nav_icon(name, QColor("#8a93a0"), QColor("#ff1e2d"))
                          for items in nav_items for name in items[1]}
        self._nav_buttons: list[QPushButton] = []
        self._rail_hidden: list = []
        if self._logo_label is not None:
            self._rail_hidden.append(self._logo_label)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for section_title, items in nav_items:
            section = QLabel(section_title)
            section.setObjectName("SidebarSection")
            layout.addWidget(section)
            self._rail_hidden.append(section)
            for item in items:
                btn = QPushButton(item)
                btn.setObjectName("NavItem")
                btn.setCheckable(True)
                btn.setIcon(self.NAV_ICONS[item])
                btn.setIconSize(QSize(18, 18))
                btn.setProperty("nav_name", item)
                btn.setToolTip(item)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, name=item: self.navRequested.emit(name))
                self._nav_group.addButton(btn)
                self._nav_buttons.append(btn)
                layout.addWidget(btn)

        layout.addStretch()

        version = QLabel("MPCASU 7.0.0")
        version.setObjectName("NowPlayingMeta")
        version.setContentsMargins(16, 8, 16, 8)
        version.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        layout.addWidget(version)
        self._rail_hidden.append(version)
        self._rail = False

    def set_rail(self, on: bool):
        if on == self._rail:
            return
        self._rail = on
        self.setFixedWidth(70 if on else METRICS.sidebar_width)
        for widget in self._rail_hidden:
            widget.setVisible(not on)
        for btn in self._nav_buttons:
            btn.setText("" if on else str(btn.property("nav_name")))

    def select(self, name: str):
        for btn in self._nav_buttons:
            if btn.property("nav_name") == name:
                btn.setChecked(True)
                return

    def set_active(self, entry: str):
        for btn in self._nav_buttons:
            btn.setChecked(btn.property("nav_name") == entry)


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
            rows = sorted({self.indexOfTopLevelItem(item)
                           for item in self.selectedItems()
                           if self.indexOfTopLevelItem(item) >= 0}, reverse=True)
            if rows:
                self.removePressed.emit(rows)
                return
        super().keyPressEvent(event)


class PlaylistPane(QFrame):
    """Right-side playlist drawer with expandable playlists."""

    playRequested = Signal(int)
    removeRequested = Signal(list)
    # moveRequested: (delta, selected top-level rows) — moving a multi-
    # selection (Ctrl/Shift) moves all selected rows together.
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
    # childRemoveRequested/childMoveRequested: playlist children taken out of
    # their playlist file ("remove from playlist" / "move to playlist").
    childRemoveRequested = Signal(list)
    childMoveRequested = Signal(list)

    PLAYLIST_SUFFIXES = {".m3u", ".m3u8", ".pls", ".json", ".wpl", ".xspf",
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
        remove_btn.clicked.connect(lambda: self.removeRequested.emit(self.selected_rows()))
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
        if 0 <= row < self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(row))

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
        items = self.tree.selectedItems()
        for item in items:
            row = self.tree.indexOfTopLevelItem(item)
            if row >= 0:
                return row
        return -1

    def selected_rows(self) -> list:
        """Sorted top-level rows of the current (multi-)selection."""
        return sorted({self.tree.indexOfTopLevelItem(item)
                       for item in self.tree.selectedItems()
                       if self.tree.indexOfTopLevelItem(item) >= 0})

    def selected_child(self) -> str | None:
        """Path/URL of the selected child of an expanded playlist group."""
        for item in self.tree.selectedItems():
            if item.parent() is not None and item.data(0, Qt.UserRole):
                return str(item.data(0, Qt.UserRole))
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
        self.tree.blockSignals(True)
        self.tree.clear()
        for _index, path in visible:
            item = QTreeWidgetItem([self._label_for(path)])
            item.setData(0, Qt.UserRole, str(path))
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
        rows = sorted({self.tree.indexOfTopLevelItem(item)
                       for item in self.tree.selectedItems()
                       if self.tree.indexOfTopLevelItem(item) >= 0}, reverse=True)
        self.removeRequested.emit(rows)

    def _on_double_click(self, item, _column):
        if item.parent() is None and self._is_playlist(item.data(0, Qt.UserRole) or ""):
            item.setExpanded(not item.isExpanded())
            return
        row = self.tree.indexOfTopLevelItem(item)
        if row >= 0:
            self.playRequested.emit(row)
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
        top_rows = sorted({self.tree.indexOfTopLevelItem(sel)
                           for sel in selected
                           if self.tree.indexOfTopLevelItem(sel) >= 0})
        row = self.tree.indexOfTopLevelItem(item)
        # If the right-clicked item is not part of the current multi-selection,
        # collapse the action set to that single item.
        if row >= 0 and row not in top_rows:
            top_rows = [row]
        if row >= 0:
            count = len(top_rows)
            label = f"Play" if count <= 1 else f"Play ({count} items)"
            menu.addAction(label, lambda: self.playRequested.emit(top_rows[0]))
            if count == 1:
                single = self.tree.topLevelItem(row)
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


class VisualizerWidget(QWidget):
    """Lightweight, UI-thread-rendered waveform visualizer for desktop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()
        self._small = False
        self._cover = None
        self._cover_scaled = None
        self._cover_scaled_size = (0, 0)
        self._mode = "waveform"
        self._wave: list[float] = []
        self._wave_buffer: list[float] = []
        self._live = None
        self._overview: tuple = ()
        self._bg_cache = None
        self._bg_cache_size = (0, 0)

    def configure(self, mode, wave, bands, duration, overview=()):
        self._mode = "off" if mode == "off" else "waveform"
        self._duration = max(0.0, float(duration or 0.0))
        self._assign_wave(wave)
        self._overview = tuple(overview or ())
        if self._small:
            self.setVisible(False)
        else:
            self.setVisible(mode != "off" or self._cover is not None)
        self.update()

    def _assign_wave(self, wave):
        """Reuse a small display buffer and lightly smooth adjacent frames."""
        values = wave or ()
        if len(self._wave_buffer) != len(values):
            self._wave_buffer = [float(value) for value in values]
        else:
            for index, value in enumerate(values):
                self._wave_buffer[index] = (
                    0.65 * self._wave_buffer[index] + 0.35 * float(value)
                )
        self._wave = self._wave_buffer

    def set_mode(self, mode):
        self._mode = mode
        if self._small:
            self.setVisible(False)
        else:
            self.setVisible(mode != "off" or self._cover is not None)
        self.update()

    def set_position(self, position):
        self._position = float(position or 0.0)

    def set_cover(self, pixmap):
        self._cover = pixmap
        self._cover_scaled = None
        self._cover_scaled_size = (0, 0)
        if pixmap is not None and not self._small:
            self.setVisible(True)
        self.update()

    def _paint_cover_art(self, painter, cover, w, h):
        size = int(min(w, h) * 0.44)
        size = max(40, min(size, 480))
        if (self._cover_scaled is None or self._cover_scaled_size != (size, size)
                or self._cover is not cover):
            self._cover_scaled = cover.scaled(size, size, Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation)
            self._cover_scaled_size = (size, size)
        pix = self._cover_scaled
        px = (w - pix.width()) // 2
        py = (h - pix.height()) // 2
        radius = 10.0
        rect = QRectF(px, py, pix.width(), pix.height())
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(path)
        painter.drawPixmap(px, py, pix)
        painter.restore()

    def set_live(self, wave):
        self._live = tuple(wave or ())
        self._assign_wave(self._live)
        if self._small:
            self.setVisible(False)
        elif self._live or self._cover:
            self.setVisible(True)
        self.update()

    def clear_live(self):
        self._live = None
        self.update()

    def set_small(self, small: bool):
        self._small = bool(small)
        if small:
            self.setVisible(False)
        self.update()

    def _paint_wave_line(self, painter, wave, w, h):
        wave = list(wave or ())
        if len(wave) < 8 or h <= 0:
            return
        count = len(wave)
        step = w / count
        poly = QPolygonF()
        for i, value in enumerate(wave):
            poly.append(QPointF(i * step,
                                h * 0.75 + max(-1.0, min(1.0, value)) * h * 0.5))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 30, 45, 0x88), 2.0))
        painter.drawPolyline(poly)
        painter.restore()

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        if self._small:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return
        # Cache the radial-gradient background so the 30 Hz repaint path does
        # not re-rasterise it every frame (big CPU saving).
        if self._bg_cache is None or self._bg_cache_size != (w, h):
            bg = QRadialGradient(w / 2.0, h / 2.0, max(w, h) * 0.7)
            bg.setColorAt(0.0, QColor("#1a0e12"))
            bg.setColorAt(0.7, QColor("#050608"))
            cached = QPixmap(w, h)
            cached.fill(Qt.transparent)
            cp = QPainter(cached)
            cp.fillRect(QRectF(0, 0, w, h), QBrush(bg))
            cp.end()
            self._bg_cache = cached
            self._bg_cache_size = (w, h)
        painter.drawPixmap(0, 0, self._bg_cache)
        if self._cover is not None and not self._cover.isNull():
            self._paint_cover_art(painter, self._cover, w, h)
        if self._mode != "off":
            if self._wave:
                self._paint_wave_line(painter, self._wave, w, h)
        painter.end()

    def _paint_cover_art(self, painter, cover, w, h):
        size = int(min(w, h) * 0.44)
        size = max(40, min(size, 480))
        if (self._cover_scaled is None or self._cover_scaled_size != (size, size)
                or self._cover is not cover):
            self._cover_scaled = cover.scaled(size, size, Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation)
            self._cover_scaled_size = (size, size)
        pix = self._cover_scaled
        px = (w - pix.width()) // 2
        py = (h - pix.height()) // 2
        radius = 10.0
        rect = QRectF(px, py, pix.width(), pix.height())
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(path)
        painter.drawPixmap(px, py, pix)
        painter.restore()


class SeekSliderWithChapters(QWidget):
    """Custom seek bar with chapter markers."""

    seekRequested = Signal(float)
    seekStarted = Signal()
    seekFinished = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Timeline")
        self.setFixedHeight(28)
        self.setMouseTracking(True)
        self._position = 0.0
        self._duration = 0.0
        self._chapters: list = []
        self._active_chapter = -1
        self._dragging = False
        self._hover_x = -1

    def set_position(self, pos: float):
        self._position = max(0.0, pos)
        self.update()

    def set_duration(self, dur: float):
        self._duration = max(1.0, dur)
        self.update()

    def set_chapters(self, chapters, active=-1):
        self._chapters = list(chapters)
        self._active_chapter = int(active)
        self.update()

    def clear_chapters(self):
        self._chapters = []
        self._active_chapter = -1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            groove_y = h // 2 - 2
            groove_h = 4

            painter.fillRect(0, groove_y, w, groove_h, QColor(PALETTE.border_strong))
            if self._duration > 0:
                fill_w = int((self._position / self._duration) * w)
                painter.fillRect(0, groove_y, fill_w, groove_h, QColor(PALETTE.accent))

            handle_x = int((self._position / self._duration) * w) if self._duration > 0 else 0
            handle_x = max(0, min(w - 1, handle_x))
            painter.setBrush(QColor(PALETTE.accent))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(handle_x - 6, groove_y - 4, 12, 12)

            for chapter in self._chapters:
                try:
                    cid = int(chapter.identifier)
                    start = float(chapter.start_seconds)
                except (AttributeError, ValueError, TypeError):
                    continue
                x = int((start / self._duration) * w) if self._duration > 0 else 0
                x = max(0, min(w - 1, x))
                color = QColor(PALETTE.accent) if cid == self._active_chapter else QColor(PALETTE.text_faint)
                painter.setPen(QPen(color, 2))
                painter.drawLine(x, groove_y - 4, x, groove_y + groove_h + 4)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._duration > 0:
            self._dragging = True
            pos = max(0.0, min(self._duration, (event.position().x() / self.width()) * self._duration))
            self.seekStarted.emit()
            self.seekRequested.emit(pos)

    def mouseMoveEvent(self, event):
        if self._dragging and self._duration > 0:
            pos = max(0.0, min(self._duration, (event.position().x() / self.width()) * self._duration))
            self.seekRequested.emit(pos)
        self._hover_x = int(event.position().x())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            if self._duration > 0:
                pos = max(0.0, min(self._duration, (event.position().x() / self.width()) * self._duration))
                self.seekFinished.emit(pos)

    def is_dragging(self) -> bool:
        return self._dragging


class DiagnosticsBar(QFrame):
    """Diagnostic info cards row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self._labels: dict[str, QLabel] = {}
        for title, default in [
            ("SEGMENTED PLAYBACK", "unavailable"),
            ("LIVE GUIDE", "no EPG loaded"),
            ("INTEGRITY MODE", "unavailable"),
            ("CASU SUPPORT", "Legacy backend"),
        ]:
            card = QFrame()
            card.setObjectName("Panel")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 8, 12, 8)
            cl.setSpacing(3)
            t = QLabel(title)
            t.setObjectName("PanelTitle")
            cl.addWidget(t)
            v = QLabel(default)
            v.setObjectName("PanelValue")
            cl.addWidget(v)
            layout.addWidget(card)
            self._labels[title] = v

    def set_values(self, *, support=None, integrity=None, segmented=None, guide=None):
        mapping = {
            "SEGMENTED PLAYBACK": segmented,
            "LIVE GUIDE": guide,
            "INTEGRITY MODE": integrity,
            "CASU SUPPORT": support,
        }
        for key, value in mapping.items():
            if value is not None and key in self._labels:
                self._labels[key].setText(value)


class LibraryPage(QFrame):
    """In-window media library: search + artist/album/genre navigation."""

    addRequested = Signal(list)
    refreshRequested = Signal()
    backRequested = Signal()

    MODES = {"all": "All Tracks", "artists": "Artists", "albums": "Albums",
             "genres": "Genres", "favorites": "Favorites", "playlists": "Playlists"}

    def __init__(self, media_library, thumbnail_dir, settings_store=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self._media_library = media_library
        self._thumbnail_dir = thumbnail_dir
        self._settings_store = settings_store
        self._tracks: list[Path] = []
        self._splitter = None
        self._playlist_files: dict[str, Path] = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self._search_entry = QLineEdit()
        self._search_entry.setObjectName("IconButton")
        self._search_entry.setPlaceholderText(
            "Search library · title, artist, album, genre…")
        self._search_entry.textChanged.connect(lambda _text: self._refresh())
        top.addWidget(self._search_entry, 1)

        self._mode_combo = QTabBar()
        self._mode_combo.setObjectName("LibraryTabs")
        for value, label in self.MODES.items():
            index = self._mode_combo.addTab(label)
            self._mode_combo.setTabData(index, value)
        self._mode_combo.currentChanged.connect(lambda _i: self._refresh())
        top.addWidget(self._mode_combo)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("IconButton")
        refresh_btn.clicked.connect(self._on_refresh)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self._splitter = split
        self._groups_list = QListWidget()
        self._groups_list.setObjectName("QueueTree")
        self._groups_list.currentItemChanged.connect(self._on_group_selected)
        split.addWidget(self._groups_list)

        self._tracks_list = QListWidget()
        self._tracks_list.setObjectName("QueueTree")
        self._tracks_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tracks_list.itemDoubleClicked.connect(lambda _item: self._add_selected())
        self._tracks_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tracks_list.customContextMenuRequested.connect(
            self._library_track_context_menu)
        split.addWidget(self._tracks_list)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        split.setSizes([260, 720])
        layout.addWidget(split, 1)

        bottom = QHBoxLayout()
        self._count_label = QLabel("")
        self._count_label.setObjectName("NowPlayingMeta")
        bottom.addWidget(self._count_label)
        bottom.addStretch()
        add_btn = QPushButton("Add to queue")
        add_btn.setObjectName("PrimaryButton")
        add_btn.clicked.connect(self._add_selected)
        bottom.addWidget(add_btn)
        layout.addLayout(bottom)

        if self._settings_store is not None:
            folder_section = QLabel("WATCHED FOLDERS")
            folder_section.setObjectName("SidebarSection")
            layout.addWidget(folder_section)
            self._folders_widget = QListWidget()
            self._folders_widget.setObjectName("QueueTree")
            self._folders_widget.setMaximumHeight(110)
            layout.addWidget(self._folders_widget)
            folder_row = QHBoxLayout()
            add_folder_btn = QPushButton("Add folder…")
            add_folder_btn.setObjectName("IconButton")
            add_folder_btn.clicked.connect(self._add_folder)
            folder_row.addWidget(add_folder_btn)
            remove_folder_btn = QPushButton("Remove selected")
            remove_folder_btn.setObjectName("IconButton")
            remove_folder_btn.clicked.connect(self._remove_folder)
            folder_row.addWidget(remove_folder_btn)
            scan_btn = QPushButton("Scan now")
            scan_btn.setObjectName("IconButton")
            scan_btn.clicked.connect(self._scan_folders)
            folder_row.addWidget(scan_btn)
            folder_row.addStretch()
            layout.addLayout(folder_row)
            self._load_folders()

    # --- data ---

    def _query(self) -> str:
        return str(self._search_entry.text()).strip().casefold()

    def _mode(self) -> str:
        return str(self._mode_combo.tabData(self._mode_combo.currentIndex()) or "all")

    def _key(self) -> str:
        return {"artists": "artist", "albums": "album", "genres": "genre"}[self._mode()]

    def _filtered(self, items, query: str):
        if not query:
            return items
        out = []
        for item in items:
            meta = item.metadata or {}
            hay = " ".join(str(meta.get(k) or "") for k in
                           ("title", "artist", "album_artist", "album", "genre"))
            hay += " " + item.path.name
            if query in hay.casefold():
                out.append(item)
        return out

    @staticmethod
    def _track_sort(item):
        meta = item.metadata or {}
        track = re.sub(r"\D", "", str(meta.get("track") or ""))
        return (str(meta.get("album") or "").casefold(),
                int(track or 0),
                str(meta.get("title") or "").casefold())

    @staticmethod
    def _row_text(item):
        meta = item.metadata or {}
        title = str(meta.get("title") or item.path.stem)
        details = " · ".join(str(meta.get(k) or "").strip() for k in
                             ("artist", "album", "genre") if str(meta.get(k) or "").strip())
        if details:
            return f"{title}\n{details}"
        return title

    def _append_track(self, item):
        row = QListWidgetItem(self._row_text(item))
        marker = "★ " if item.favorite else ""
        row.setText(f"{marker}{self._row_text(item)}")
        duration = float((item.metadata or {}).get("duration") or 0.0)
        if duration > 0:
            minutes, seconds = divmod(int(duration), 60)
            row.setToolTip(f"{minutes}:{seconds:02d}\n{item.path}")
        else:
            row.setToolTip(str(item.path))
        font = row.font()
        font.setBold(True)
        row.setFont(font)
        self._tracks_list.addItem(row)
        self._tracks.append(item.path)

    # --- UI flow ---

    def _refresh(self):
        query = self._query()
        mode = self._mode()
        self._tracks.clear()
        self._tracks_list.clear()
        use_groups = mode in ("artists", "albums", "genres", "playlists")
        self._groups_list.setVisible(use_groups)

        if mode == "all":
            self._groups_list.clear()
            items = self._filtered(self._media_library.items(), query)
            for item in sorted(items, key=self._track_sort):
                self._append_track(item)
        elif mode == "favorites":
            self._groups_list.clear()
            self._show_favorites()
            self._count_label.setText(f"{len(self._tracks)} tracks")
            return
        elif mode == "playlists":
            self._scan_playlist_files()
            self._count_label.setText(f"{len(self._tracks)} tracks")
            return
        else:
            self._groups_list.setEnabled(True)
            self._rebuild_groups(mode, query)
            if self._groups_list.count() > 0:
                self._groups_list.setCurrentRow(0)
            else:
                self._count_label.setText("No groups found")
        self._count_label.setText(f"{len(self._tracks)} tracks")

    def _rebuild_groups(self, mode, query):
        self._groups_list.blockSignals(True)
        self._groups_list.clear()
        if mode == "favorites":
            self._groups_list.blockSignals(False)
            return
        key = self._key()
        values = [v for v in self._media_library.field_values(key)
                  if not query or query in v.casefold()]
        has_unknown = any(not str((item.metadata or {}).get(key) or "").strip()
                          for item in self._media_library.items())
        unknown_label = {"artists": "Unknown Artist", "albums": "Unknown Album",
                         "genres": "Unknown Genre"}.get(mode, "Unknown")
        if has_unknown and (not query or query in unknown_label.casefold()):
            values.append("")
        if mode == "favorites":
            favorites = {str(i.path) for i in self._media_library.items(favorites_only=True)}
            values = [v for v in values
                      if any(str(i.path) in favorites and
                             str((i.metadata or {}).get(key) or "").casefold() == v.casefold()
                             for i in self._media_library.items())]
        if not values:
            values = []
        for value in values:
            row = QListWidgetItem(value if value else unknown_label)
            row.setData(Qt.UserRole, value)
            self._groups_list.addItem(row)
        self._groups_list.blockSignals(False)

    def _on_group_selected(self, current):
        if current is None:
            self._tracks_list.clear()
            self._tracks.clear()
            return
        mode = self._mode()
        if mode == "favorites":
            self._show_favorites()
            return
        if mode == "playlists":
            self._on_playlist_group_selected(current)
            return
        value = str(current.data(Qt.UserRole) or "")
        key = self._key()
        query = self._query()
        if not value:
            items = [i for i in self._media_library.items()
                     if not str((i.metadata or {}).get(key) or "").strip()]
        else:
            items = self._media_library.by_field(key, value)
        self._tracks.clear()
        self._tracks_list.clear()
        for item in sorted(self._filtered(items, query), key=self._track_sort):
            self._append_track(item)
        self._count_label.setText(f"{len(self._tracks)} tracks")

    def _show_favorites(self):
        favorites = self._media_library.items(favorites_only=True)
        query = self._query()
        self._tracks.clear()
        self._tracks_list.clear()
        for item in sorted(self._filtered(favorites, query), key=self._track_sort):
            self._append_track(item)
        self._count_label.setText(f"{len(self._tracks)} tracks")

    def _scan_playlist_files(self):
        self._groups_list.blockSignals(True)
        self._groups_list.clear()
        self._playlist_files.clear()
        playlist_exts = {".m3u", ".m3u8", ".pls", ".xspf", ".cue"}
        folders = []
        if self._settings_store is not None:
            try:
                settings = self._settings_store.load()
                folders = list(settings.watched_folders)
            except Exception:
                pass
        if not folders:
            folders = [str(Path.home())]
        for folder in folders:
            fpath = Path(folder)
            if not fpath.is_dir():
                continue
            try:
                for p in sorted(fpath.rglob("*")):
                    try:
                        if p.suffix.lower() in playlist_exts and p.is_file():
                            name = p.stem
                            self._playlist_files[name] = p
                            self._groups_list.addItem(name)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue
        self._groups_list.blockSignals(False)
        self._groups_list.setEnabled(True)
        if self._groups_list.count() > 0:
            self._groups_list.setCurrentRow(0)
            self._on_playlist_group_selected(self._groups_list.item(0))
        else:
            self._count_label.setText("No playlist files found")

    def _on_playlist_group_selected(self, current):
        if current is None:
            return
        name = current.text()
        pl_path = self._playlist_files.get(name)
        if pl_path is None or not pl_path.is_file():
            return
        self._tracks.clear()
        self._tracks_list.clear()
        entries = self._parse_playlist_file(pl_path)
        lib_items = self._media_library.items()
        lib_by_path = {str(it.path): it for it in lib_items}
        for entry_path in entries:
            resolved = str(Path(entry_path).expanduser().resolve())
            lib_item = lib_items[0] if lib_items else None
            for it in lib_items:
                if str(it.path) == resolved:
                    lib_item = it
                    break
            if lib_item is None:
                from casu.library import LibraryItem as LI
                lib_item = LI(Path(resolved), 0, 0, False, 0.0, None, {})
            self._tracks.append(Path(resolved))
            self._append_track(lib_item)
        self._count_label.setText(f"{len(self._tracks)} tracks")

    @staticmethod
    def _parse_playlist_file(path: Path) -> list[str]:
        ext = path.suffix.lower()
        entries: list[str] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return entries
        if ext in (".m3u", ".m3u8"):
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    entries.append(line)
        elif ext == ".pls":
            for line in text.splitlines():
                line = line.strip()
                if line.lower().startswith("file"):
                    eq = line.find("=")
                    if eq >= 0:
                        entries.append(line[eq + 1:].strip().strip('"'))
        elif ext == ".xspf":
            import re
            for m in re.finditer(r"<location>(.*?)</location>", text, re.IGNORECASE):
                entries.append(m.group(1).strip())
        elif ext == ".cue":
            for line in text.splitlines():
                line = line.strip()
                if line.upper().startswith("FILE "):
                    parts = line.split(None, 1)
                    if len(parts) >= 2:
                        fname = parts[1].rsplit(None, 1)[0].strip('"')
                        entries.append(fname)
        return entries

    def _add_selected(self, *_args):
        selected = self._tracks_list.selectedItems()
        if not selected:
            item = self._tracks_list.currentItem()
            if item is not None:
                selected = [item]
        paths = []
        for item in selected:
            row = self._tracks_list.row(item)
            if 0 <= row < len(self._tracks):
                paths.append(self._tracks[row])
        if paths:
            self.addRequested.emit(paths)

    def _library_track_context_menu(self, position):
        item = self._tracks_list.itemAt(position)
        if item is None:
            return
        selected_items = self._tracks_list.selectedItems()
        if not selected_items:
            selected_items = [item]
        paths = []
        for sel in selected_items:
            r = self._tracks_list.row(sel)
            if 0 <= r < len(self._tracks):
                paths.append(self._tracks[r])
        if not paths:
            return
        menu = QMenu(self)
        if len(paths) == 1:
            meta_item = None
            for mi in self._media_library.items():
                if mi.path == paths[0]:
                    meta_item = mi
                    break
            is_fav = bool(meta_item.favorite) if meta_item else False
            fav_text = "Remove ★" if is_fav else "Add ★ Favorite"
            menu.addAction(fav_text, lambda: self._toggle_favorite(paths[0], self._tracks_list.row(item)))
        else:
            any_fav = any(
                bool(self._media_library.get(p).favorite)
                for p in paths if self._media_library.get(p))
            fav_text = "Remove ★" if any_fav else "Add ★ Favorite"
            menu.addAction(f"{fav_text} ({len(paths)})", lambda: self._toggle_favorite_multi(paths, not any_fav))
        menu.addSeparator()
        add_text = "Add to queue" if len(paths) == 1 else f"Add to queue ({len(paths)})"
        menu.addAction(add_text, lambda: self.addRequested.emit(paths))
        menu.exec(self._tracks_list.viewport().mapToGlobal(position))

    def _toggle_favorite(self, path, row):
        item = self._media_library.get(path)
        current = bool(item.favorite) if item else False
        self._media_library.set_favorite(path, not current)
        self._refresh()

    def _toggle_favorite_multi(self, paths, state):
        for p in paths:
            self._media_library.set_favorite(p, state)
        self._refresh()

    def _on_refresh(self):
        self.refreshRequested.emit()
        self._refresh()

    # --- watched folders ---

    def _load_folders(self):
        self._folders_widget.blockSignals(True)
        self._folders_widget.clear()
        settings = self._settings_store.load()
        for folder in settings.watched_folders:
            self._folders_widget.addItem(str(folder))
        self._folders_widget.blockSignals(False)

    def _add_folder(self):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "Add library folder")
        if not folder:
            return
        settings = self._settings_store.load()
        folders = list(settings.watched_folders)
        if folder in folders:
            return
        folders.append(folder)
        self._settings_store.save(replace(settings, watched_folders=folders))
        self._load_folders()
        self.refreshRequested.emit()

    def _remove_folder(self):
        row = self._folders_widget.currentRow()
        if row < 0:
            return
        folder = self._folders_widget.item(row).text()
        settings = self._settings_store.load()
        folders = [f for f in settings.watched_folders if f != folder]
        self._settings_store.save(replace(settings, watched_folders=folders))
        self._load_folders()
        self.refreshRequested.emit()

    def _scan_folders(self):
        self.refreshRequested.emit()


class OptionsPage(QFrame):
    """In-window options area (replaces the settings popup)."""

    applied = Signal(object)
    actionRequested = Signal(str)
    backRequested = Signal()

    def __init__(self, settings_store, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self._settings_store = settings_store
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(14)

        def section(label_text):
            label = QLabel(label_text)
            label.setObjectName("SidebarSection")
            label.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(label)

        settings = self._settings_store.load()

        section("PLAYBACK")
        row = QHBoxLayout()
        row.addWidget(QLabel("Volume"))
        self._volume_spin = QSpinBox()
        self._volume_spin.setObjectName("IconButton")
        self._volume_spin.setRange(0, 200)
        self._volume_spin.setValue(settings.volume)
        row.addWidget(self._volume_spin)
        row.addSpacing(18)
        row.addWidget(QLabel("Rate"))
        self._rate_spin = QDoubleSpinBox()
        self._rate_spin.setObjectName("IconButton")
        self._rate_spin.setRange(0.25, 4.0)
        self._rate_spin.setSingleStep(0.25)
        self._rate_spin.setValue(settings.rate)
        row.addWidget(self._rate_spin)
        row.addStretch()
        layout.addLayout(row)
        self._muted_cb = QCheckBox("Muted")
        self._muted_cb.setChecked(settings.muted)
        layout.addWidget(self._muted_cb)
        self._resume_cb = QCheckBox("Resume playback on startup")
        self._resume_cb.setChecked(settings.resume_playback)
        layout.addWidget(self._resume_cb)

        section("VISUALIZER")
        viz_row = QHBoxLayout()
        self._viz_combo = QComboBox()
        self._viz_combo.setObjectName("IconButton")
        for label, value in [("Waveform", "waveform"), ("Off", "off")]:
            self._viz_combo.addItem(label, value)
        index = self._viz_combo.findData(settings.visualizer)
        self._viz_combo.setCurrentIndex(max(0, index))
        viz_row.addWidget(self._viz_combo)
        viz_row.addStretch()
        layout.addLayout(viz_row)

        section("CACHE")
        cache_row = QHBoxLayout()
        self._cache_spin = QSpinBox()
        self._cache_spin.setObjectName("IconButton")
        self._cache_spin.setRange(64, 8192)
        self._cache_spin.setSuffix(" MiB")
        self._cache_spin.setValue(settings.cache_limit_mib)
        cache_row.addWidget(self._cache_spin)
        clear_btn = QPushButton("Clear yt-dlp temp cache")
        clear_btn.setObjectName("IconButton")
        clear_btn.clicked.connect(lambda: self.actionRequested.emit("clear-cache"))
        cache_row.addWidget(clear_btn)
        cache_row.addStretch()
        layout.addLayout(cache_row)

        section("LIBRARY FOLDERS")
        folders_hint = QLabel(
            "Folders whose audio/video files are indexed into the library "
            "(tags and file names are read for album/track/artist/genre).")
        folders_hint.setObjectName("NowPlayingMeta")
        folders_hint.setWordWrap(True)
        layout.addWidget(folders_hint)
        self._folders_list = QListWidget()
        self._folders_list.setObjectName("QueueTree")
        self._folders_list.setMinimumHeight(110)
        self._folders_list.setMaximumHeight(180)
        for folder in settings.watched_folders:
            self._folders_list.addItem(str(folder))
        layout.addWidget(self._folders_list)
        folder_row = QHBoxLayout()
        add_folder_btn = QPushButton("Add folder…")
        add_folder_btn.setObjectName("IconButton")
        add_folder_btn.clicked.connect(self._add_library_folder)
        folder_row.addWidget(add_folder_btn)
        remove_folder_btn = QPushButton("Remove selected")
        remove_folder_btn.setObjectName("IconButton")
        remove_folder_btn.clicked.connect(self._remove_library_folder)
        folder_row.addWidget(remove_folder_btn)
        scan_btn = QPushButton("Scan now")
        scan_btn.setObjectName("IconButton")
        scan_btn.clicked.connect(lambda: self.actionRequested.emit("refresh-db"))
        folder_row.addWidget(scan_btn)
        folder_row.addStretch()
        layout.addLayout(folder_row)

        section("RECORDINGS & SNAPSHOTS")
        rec_row = QHBoxLayout()
        self._recordings_entry = QLineEdit()
        self._recordings_entry.setObjectName("IconButton")
        self._recordings_entry.setPlaceholderText("Default folder for recordings and snapshots (empty = ~/Videos/MPCASU)")
        self._recordings_entry.setText(settings.recordings_dir)
        rec_row.addWidget(self._recordings_entry, 1)
        rec_btn = QPushButton("Choose folder…")
        rec_btn.setObjectName("IconButton")
        rec_btn.clicked.connect(self._pick_recordings_dir)
        rec_row.addWidget(rec_btn)
        layout.addLayout(rec_row)
        split_row = QHBoxLayout()
        split_row.addWidget(QLabel("Recording split"))
        self._split_mode_combo = QComboBox()
        self._split_mode_combo.setObjectName("IconButton")
        for label, value in (("Single recording", "continuous"),
                             ("By time", "time"),
                             ("At track changes", "track"),
                             ("At title/tag changes", "tags")):
            self._split_mode_combo.addItem(label, value)
        self._split_mode_combo.setCurrentIndex(max(
            0, self._split_mode_combo.findData(settings.record_split_mode)))
        split_row.addWidget(self._split_mode_combo)
        self._split_spin = QSpinBox()
        self._split_spin.setObjectName("IconButton")
        self._split_spin.setRange(0, 24 * 60)
        self._split_spin.setSuffix(" min")
        self._split_spin.setSpecialValueText("no splitting")
        self._split_spin.setValue(settings.record_split_minutes)
        self._split_spin.setEnabled(settings.record_split_mode == "time")
        self._split_mode_combo.currentIndexChanged.connect(
            lambda _i: self._split_spin.setEnabled(
                self._split_mode_combo.currentData() == "time"))
        split_row.addWidget(self._split_spin)
        split_row.addSpacing(12)
        split_row.addWidget(QLabel("Format"))
        self._format_combo = QComboBox()
        self._format_combo.setObjectName("IconButton")
        for fmt in ("mkv", "mp4", "ts", "webm", "ogg", "mp3", "flac", "wav"):
            self._format_combo.addItem(fmt)
        index = self._format_combo.findText(settings.record_format)
        self._format_combo.setCurrentIndex(max(0, index))
        split_row.addWidget(self._format_combo)
        split_row.addStretch()
        layout.addLayout(split_row)

        section("LEGAL")
        self._consent_cb = QCheckBox("I understand that YouTube uses yt-dlp and Spotify uses spotDL (personal use only)")
        self._consent_cb.setChecked(settings.ytdlp_consent)
        layout.addWidget(self._consent_cb)

        section("PROVIDERS")
        providers = QLabel(self._provider_status())
        providers.setObjectName("NowPlayingMeta")
        providers.setWordWrap(True)
        providers.setTextFormat(Qt.PlainText)
        layout.addWidget(providers)

        apply_row = QHBoxLayout()
        apply_row.addStretch()
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("PrimaryButton")
        apply_btn.clicked.connect(self._apply)
        apply_row.addWidget(apply_btn)
        layout.addLayout(apply_row)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

    def _apply(self):
        settings = self._settings_store.load()
        updated = replace(settings,
                          volume=self._volume_spin.value(),
                          muted=self._muted_cb.isChecked(),
                          rate=self._rate_spin.value(),
                          ytdlp_consent=self._consent_cb.isChecked(),
                          visualizer=str(self._viz_combo.currentData()),
                          resume_playback=self._resume_cb.isChecked(),
                          cache_limit_mib=self._cache_spin.value(),
                          recordings_dir=self._recordings_entry.text().strip(),
                          record_split_minutes=self._split_spin.value(),
                          record_split_mode=str(self._split_mode_combo.currentData()),
                          record_format=str(self._format_combo.currentText()),
                          watched_folders=self._library_folders())
        self._settings_store.save(updated)
        self.applied.emit(updated)

    def _pick_recordings_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Recordings & snapshots folder")
        if folder:
            self._recordings_entry.setText(folder)

    def _library_folders(self) -> list[str]:
        return [self._folders_list.item(i).text().strip()
                for i in range(self._folders_list.count())
                if self._folders_list.item(i).text().strip()]

    def _add_library_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add library folder")
        if not folder:
            return
        folders = self._library_folders()
        if folder in folders:
            return
        self._folders_list.addItem(folder)
        self._folders_list.setCurrentRow(self._folders_list.count() - 1)

    def _remove_library_folder(self):
        row = self._folders_list.currentRow()
        if row >= 0:
            self._folders_list.takeItem(row)

    @staticmethod
    def _provider_status() -> str:
        import shutil
        from glob import glob
        vlc = bool(shutil.which("vlc")) or bool(glob("/usr/lib/*/libvlc.so*"))
        lines = [
            f"libVLC (legacy playback): {'✓' if vlc else '✗ missing'}",
            f"FFmpeg (convert/analysis): {'✓' if shutil.which('ffmpeg') else '✗ missing'}",
            f"yt-dlp (YouTube provider): {'✓' if shutil.which('yt-dlp') else '✗ missing'}",
        ]
        from casu.spotify import spotdl_binary
        if spotdl_binary():
            lines.append("spotDL (Spotify provider): ✓")
        else:
            lines.append("spotDL (Spotify provider): ✗ not installed — "
                         "python3 -m venv /opt/casu-spotdl && "
                         "/opt/casu-spotdl/bin/pip install spotdl")
        lines.append(f"Deno (optional spotDL helper): {'✓' if shutil.which('deno') else '– optional'}")
        return "\n".join(lines)

    def reload(self):
        settings = self._settings_store.load()
        self._volume_spin.setValue(settings.volume)
        self._muted_cb.setChecked(settings.muted)
        self._rate_spin.setValue(settings.rate)
        self._resume_cb.setChecked(settings.resume_playback)
        self._consent_cb.setChecked(settings.ytdlp_consent)
        self._cache_spin.setValue(settings.cache_limit_mib)
        self._recordings_entry.setText(settings.recordings_dir)
        self._split_spin.setValue(settings.record_split_minutes)
        self._split_mode_combo.setCurrentIndex(max(
            0, self._split_mode_combo.findData(settings.record_split_mode)))
        index = self._format_combo.findText(settings.record_format)
        self._format_combo.setCurrentIndex(max(0, index))
        index = self._viz_combo.findData(settings.visualizer)
        self._viz_combo.setCurrentIndex(max(0, index))
        self._folders_list.blockSignals(True)
        self._folders_list.clear()
        for folder in settings.watched_folders:
            self._folders_list.addItem(str(folder))
        self._folders_list.blockSignals(False)


class EpgPage(QFrame):
    """In-window Live TV / EPG guide (M3U + XMLTV), web-style channel cards."""

    channelActivated = Signal(object)
    backRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self._catalog = None
        self._guide = None
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(10)

        source_row = QHBoxLayout()
        self._source_entry = QLineEdit()
        self._source_entry.setPlaceholderText("M3U / XMLTV path or http(s) URL…")
        source_row.addWidget(self._source_entry, 1)
        load_file_btn = QPushButton("Load file")
        load_file_btn.setObjectName("IconButton")
        load_file_btn.clicked.connect(self._load_file)
        source_row.addWidget(load_file_btn)
        load_url_btn = QPushButton("Load URL")
        load_url_btn.setObjectName("IconButton")
        load_url_btn.clicked.connect(lambda: self._load_source(self._source_entry.text().strip()))
        source_row.addWidget(load_url_btn)
        outer.addLayout(source_row)

        self._status = QLabel("Load an Extended-M3U playlist (and optional XMLTV guide) to browse channels.")
        self._status.setObjectName("NowPlayingMeta")
        outer.addWidget(self._status)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._grid_host = QWidget()
        self._grid_host.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(8)
        self._scroll.setWidget(self._grid_host)
        outer.addWidget(self._scroll, 1)

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load playlist / guide", str(Path.home()),
            "Playlists & guides (*.m3u *.m3u8 *.pls *.xspf *.wpl *.asx *.xml *.xmltv);;All files (*)")
        if path:
            self._load_source(path)

    def _load_source(self, source: str):
        if not source:
            return
        try:
            if source.endswith((".xml", ".xmltv")):
                from casu.epg import load_xmltv, fetch_xmltv
                self._guide = fetch_xmltv(source) if source.startswith(("http://", "https://")) else load_xmltv(source)
                self._status.setText(f"Guide loaded: {len(self._guide.entries) if hasattr(self._guide, 'entries') else ''} programmes")
                self._sync_host_epg()
                self._render()
                return
            from casu.epg import load_m3u, fetch_m3u
            self._catalog = fetch_m3u(source) if source.startswith(("http://", "https://")) else load_m3u(source)
            self._status.setText(f"{len(self._catalog.channels)} channels loaded")
            self._sync_host_epg()
            self._render()
        except Exception as exc:  # noqa: BLE001 - show any loader failure inline
            self._status.setText(f"Load failed: {exc}")

    def _sync_host_epg(self):
        host = self.parent()
        if host is None or not hasattr(host, "_epg_catalog"):
            return
        host._epg_catalog = self._catalog
        host._epg_guide = self._guide
        host._diagnostics_bar.set_values(guide=host._epg_now_next())

    def _now_next(self, channel):
        if self._guide is None:
            return ""
        try:
            programmes = self._guide.for_channel(getattr(channel, "tvg_id", "") or channel.name)
        except Exception:  # noqa: BLE001
            return ""
        current = next((p for p in programmes if p.current), None) if programmes else None
        if current is not None:
            return f"{current.title}"
        return ""

    def _render(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if self._catalog is None:
            return
        for index, channel in enumerate(self._catalog.channels):
            card = QFrame()
            card.setObjectName("EpgChannel")
            card.setCursor(Qt.PointingHandCursor)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            name = QLabel(channel.name)
            name.setObjectName("NowPlayingTitle")
            name.setStyleSheet("font-size: 13px;")
            name.setWordWrap(True)
            cl.addWidget(name)
            now = self._now_next(channel)
            meta = QLabel(now or (getattr(channel, "group", "") or ""))
            meta.setObjectName("NowPlayingMeta")
            meta.setWordWrap(True)
            cl.addWidget(meta)
            card.mousePressEvent = lambda event, ch=channel: self.channelActivated.emit(ch)
            self._grid.addWidget(card, index // 3, index % 3)


class AboutPage(QFrame):
    """In-window about view (no popup)."""

    backRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setAlignment(Qt.AlignCenter)

        brand = QLabel("MPCASU")
        brand.setObjectName("BrandName")
        brand.setAlignment(Qt.AlignCenter)
        layout.addWidget(brand)
        sub = QLabel("PLAYER")
        sub.setObjectName("BrandSub")
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub)
        layout.addSpacing(12)
        info = QLabel("Version 7.0.0\nMedia Player for CASU & Legacy Media\nIn-process playback · No external player")
        info.setObjectName("NowPlayingMeta")
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)
        layout.addSpacing(12)
        note = QLabel("Design inspired by VLC and Webamp — independent original code.\nAnti-Capitalist License 1.4 · Lino Casu")
        note.setObjectName("NowPlayingMeta")
        note.setAlignment(Qt.AlignCenter)
        note.setWordWrap(True)
        layout.addWidget(note)
class _ThreadBridge(QObject):
    """Marshals worker-thread results onto the Qt event loop (no popups)."""

    resultReady = Signal(object)
    errorReady = Signal(object)



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
            from casu.search import SearchError, expand_youtube_input
            try:
                found = expand_youtube_input(text)
            except SearchError as exc:
                self._queue_bridge.errorReady.emit(str(exc))
            else:
                self._queue_bridge.resultReady.emit(found)
        threading.Thread(target=worker, daemon=True).start()

    def _present_queue_items(self, found):
        self._searching = False
        self.queueItemsRequested.emit(list(found))
        self._status.setText(
            f"{len(found)} video(s) added to the queue — playing now")

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

    def _expand_youtube_playlist(self, url: str):
        if self._searching:
            return
        self._searching = True
        self._list.clear()
        self._results = []
        self._status.setText("Expanding YouTube playlist…")

        def worker():
            from casu.search import SearchError, search_youtube_playlist
            try:
                found = search_youtube_playlist(url)
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
                self._expand_youtube_playlist(item.url)
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


class MainWindow(QMainWindow):
    """MPCASU Qt main window — full media player."""

    def __init__(self, initial: list[Path] | None = None):
        super().__init__()
        self.setWindowTitle("MPCASU Media Player")
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(min(980, avail.width()), min(620, avail.height()))
        self.resize(min(1360, avail.width() - 24), min(820, avail.height() - 24))
        self.move(avail.x() + max(0, (avail.width() - self.width()) // 2),
                  avail.y() + max(0, (avail.height() - self.height()) // 2))
        self.setAcceptDrops(True)
        self.setStyleSheet(stylesheet())

        self.backend: LibVLCBackend | NativeCasuBackend | None = None
        self.last_playback_error = ""
        self._native_sink: QtVideoSurfaceSink | None = None
        self.controller = PlaybackController()
        self.current: Path | None = None
        self.duration = 0.0
        self._paused = False
        self._dragging = False
        self._advancing = False
        self._end_handled = False
        self._started_at = 0.0
        # Logical playback sequence over the queue: playlist groups stay in
        # the model (they are never dissolved); playback walks this flattened
        # list instead. Rebuilt lazily, invalidated on every queue mutation.
        self._play_seq: list[str] | None = None
        self._start_offset = 0.0
        self._visual_phase = 0.0
        self._visual_state = "idle"
        self._visual_segments: list[dict] = []
        self._visual_video_segments: list[dict] = []
        self._visual_audio_segments: list[dict] = []
        self._scheduler = None
        self._volume = 100
        self._muted = False
        self._rate = 1.0
        self._audio_delay_ms = 0.0
        self._subtitle_delay_ms = 0.0
        self._resume_source: str | None = None
        self._resume_position = 0.0
        self._fullscreen = False
        self._layout_mode = "wide"
        self._shuffle = False
        self._repeat_mode = "off"
        self._recorder = None
        self._recording_finishing = False
        self._ab_a: float | None = None
        self._ab_b: float | None = None
        self._network_source: str | None = None
        self._temp_media: Path | None = None
        self._epg_catalog = None
        self._epg_guide = None
        self._sidebar_rail = False
        self._playlist_auto_hidden = False
        self._queue_drawer = False
        self._audio_stage = False
        self._viz_pcm = None
        self._viz_rate = 0
        self._viz_generation = 0
        self._viz_overview = ()
        self._viz_mode = "waveform"
        self._viz_timer = QTimer(self)
        self._viz_timer.setInterval(33)  # bounded ~30 FPS UI update
        self._viz_timer.timeout.connect(self._tick_visualizer)

        config_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "mpcasu"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._session_file = config_dir / "session.json"

        self.settings_store = SettingsStore(config_dir / "settings.json")
        effective = self.settings_store.load()
        self._volume = effective.volume
        self._muted = effective.muted
        self._rate = effective.rate
        self._audio_device = effective.audio_device
        self._watched_folders = list(effective.watched_folders)
        self._shuffle = bool(effective.shuffle)
        self._repeat_mode = str(effective.repeat_mode)
        self._viz_mode = str(effective.visualizer)
        self._cover_dir = config_dir / "covers"
        self._cover_dir.mkdir(parents=True, exist_ok=True)
        self.media_library = MediaLibrary(config_dir / "library.sqlite3")
        self._thumbnail_dir = config_dir / "thumbnails"
        self._thumbnail_dir.mkdir(parents=True, exist_ok=True)
        self.playlist_model = PlaylistModel()

        self._resolve_generation = 0
        self._resolve_bridge = _ThreadBridge()
        self._resolve_bridge.resultReady.connect(self._on_resolve_ready)
        self._resolve_bridge.errorReady.connect(self._on_resolve_failed)
        self._title_bridge = _ThreadBridge()
        self._title_bridge.resultReady.connect(self._apply_queue_title)

        self._build_ui()
        for combo in self.findChildren(QComboBox):
            apply_dark_combo_popup(combo)
        self._restore_session()
        self._setup_shortcuts()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(200)
        self._mpris_notifier = _register_mpris(self)

        if initial:
            self.add_files(initial)
            QTimer.singleShot(300, self.play_selected)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._now_playing_bar = NowPlayingBar()
        self._now_playing_bar.hide()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._body_layout = body

        self._sidebar = Sidebar()
        self._sidebar.navRequested.connect(self._navigate)
        body.addWidget(self._sidebar)

        player_page = QWidget()
        self._player_page = player_page
        center_column = QVBoxLayout(player_page)
        center_column.setContentsMargins(0, 0, 0, 0)
        center_column.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("TopBar")
        topbar.setFixedHeight(METRICS.topbar_height)
        tb_layout = QHBoxLayout(topbar)
        tb_layout.setContentsMargins(10, 0, 10, 0)
        self._back_btn = QPushButton("‹")
        self._back_btn.setObjectName("IconButton")
        self._back_btn.setFixedSize(40, 40)
        self._back_btn.setToolTip("Back to Now Playing")
        self._back_btn.clicked.connect(self._show_player_page)
        tb_layout.addWidget(self._back_btn)
        self._topbar_title = QLabel("NOW PLAYING")
        self._topbar_title.setObjectName("NowPlayingTitle")
        tb_layout.addWidget(self._topbar_title)
        tb_layout.addStretch()
        self._queue_filter = QLineEdit()
        self._queue_filter.setPlaceholderText("Search queue…")
        self._queue_filter.setFixedWidth(220)
        self._queue_filter.setFixedHeight(34)
        self._queue_filter.textChanged.connect(self._filter_queue)
        tb_layout.addWidget(self._queue_filter)
        self._nav_toggle = QPushButton("☰")
        self._nav_toggle.setObjectName("IconButton")
        self._nav_toggle.setFixedSize(40, 40)
        self._nav_toggle.setToolTip("Toggle navigation")
        self._nav_toggle.clicked.connect(
            lambda: self._sidebar.setVisible(not self._sidebar.isVisible()))
        tb_layout.addWidget(self._nav_toggle)
        self._queue_toggle = QPushButton("☷")
        self._queue_toggle.setObjectName("IconButton")
        self._queue_toggle.setFixedSize(40, 40)
        self._queue_toggle.setToolTip("Toggle playlist panel")
        self._queue_toggle.clicked.connect(self._toggle_queue_pane)
        tb_layout.addWidget(self._queue_toggle)
        self._topbar = topbar
        center_column.addWidget(topbar)

        self._video_surface = VideoSurface()
        self._video_surface.doubleClicked.connect(self.toggle_fullscreen)
        center_column.addWidget(self._video_surface, 1)

        self._yt_stream = YouTubeMediaProxy()
        self._badges_label = QLabel(self._player_page)
        self._badges_label.setStyleSheet(
            "background-color: #090b0ddd; border: 1px solid #383d43; color: #f4f5f7;"
            " font-size: 11px; font-weight: 800; padding: 5px 8px;")
        self._badges_label.hide()
        self._caption_label = QLabel(self._player_page)
        self._caption_label.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 transparent,"
            " stop:1 #050607e8); color: #f4f5f7; font-size: 14px; font-weight: 700;"
            " padding: 40px 18px 12px 18px; border: none;")
        self._caption_label.hide()
        self._empty_hint = QFrame(self._player_page)
        self._empty_hint.setStyleSheet(
            "background: qradialgradient(cx:0.5, cy:0.5, radius:0.9, "
            "stop:0 #291014, stop:1 #0b0d10); border: none;")
        eh_layout = QVBoxLayout(self._empty_hint)
        eh_layout.setContentsMargins(24, 24, 24, 24)
        eh_layout.setSpacing(6)
        eh_layout.addStretch()
        icon_label = QLabel()
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "web_casu_icon.png"
        if icon_path.is_file():
            pix = QPixmap(str(icon_path))
            if not pix.isNull():
                icon_label.setPixmap(pix.scaledToWidth(72, Qt.SmoothTransformation))
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("background: transparent;")
        eh_layout.addWidget(icon_label)
        eh_title = QLabel("Drop media here")
        eh_title.setObjectName("NowPlayingTitle")
        eh_title.setStyleSheet("background: transparent; font-size: 18px;")
        eh_title.setAlignment(Qt.AlignCenter)
        eh_layout.addWidget(eh_title)
        eh_meta = QLabel("Audio, video, CASU, playlists and streams — "
                         "“Choose files” in the playlist panel, or drag & drop")
        eh_meta.setObjectName("NowPlayingMeta")
        eh_meta.setStyleSheet("background: transparent;")
        eh_meta.setAlignment(Qt.AlignCenter)
        eh_meta.setWordWrap(True)
        eh_layout.addWidget(eh_meta)
        eh_layout.addStretch()

        self._visualizer = VisualizerWidget(self._player_page)
        self._viz_bridge = _ThreadBridge()
        self._scan_bridge = _ThreadBridge()
        self._scan_bridge.resultReady.connect(self._scan_done)
        self._viz_bridge.resultReady.connect(self._apply_viz)

        self._toast_label = QLabel(self._player_page)
        self._toast_label.setObjectName("Toast")
        self._toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast_label.hide)

        self._drop_overlay = QLabel("DROP TO PLAY / ADD TO QUEUE", self._player_page)
        self._drop_overlay.setAlignment(Qt.AlignCenter)
        self._drop_overlay.setStyleSheet(
            "background: #07090bcc; border: 2px solid #ff1e2d; border-radius: 10px;"
            " color: #ff1e2d; font-size: 16px; font-weight: 800;")
        self._drop_overlay.hide()

        self._fs_overlay = QFrame(self._player_page)
        self._fs_overlay.setStyleSheet(
            "background: #07090bdd; border: 1px solid #252a30; border-radius: 8px;")
        fsl = QHBoxLayout(self._fs_overlay)
        fsl.setContentsMargins(10, 6, 10, 6)
        fsl.setSpacing(6)
        self._fs_play_btn = QPushButton("▶")
        self._fs_play_btn.setObjectName("TransportButton")
        self._fs_play_btn.clicked.connect(self.toggle_playback)
        fsl.addWidget(self._fs_play_btn)
        self._fs_time = QLabel("00:00 / 00:00")
        self._fs_time.setObjectName("TimeLabel")
        fsl.addWidget(self._fs_time)
        fsl.addStretch()
        self._fs_vol = QSlider(Qt.Horizontal)
        self._fs_vol.setObjectName("VolumeSlider")
        self._fs_vol.setRange(0, 200)
        self._fs_vol.setValue(self._volume)
        self._fs_vol.setFixedWidth(90)
        self._fs_vol.valueChanged.connect(self._on_volume_slider)
        fsl.addWidget(self._fs_vol)
        self._fs_exit_btn = QPushButton("□")
        self._fs_exit_btn.setObjectName("IconButton")
        self._fs_exit_btn.clicked.connect(self.toggle_fullscreen)
        fsl.addWidget(self._fs_exit_btn)
        self._fs_overlay.hide()
        self._fs_hide_timer = QTimer(self)
        self._fs_hide_timer.setSingleShot(True)
        self._fs_hide_timer.setInterval(2500)
        self._fs_hide_timer.timeout.connect(self._fs_overlay.hide)

        transport_container = QFrame()
        self._transport_container = transport_container
        transport_container.setObjectName("Panel")
        tc_layout = QVBoxLayout(transport_container)
        tc_layout.setContentsMargins(14, 6, 14, 6)
        tc_layout.setSpacing(4)

        self._seek_slider = SeekSliderWithChapters()
        self._seek_slider.seekRequested.connect(self._on_seek_preview)
        self._seek_slider.seekStarted.connect(self._on_seek_start)
        self._seek_slider.seekFinished.connect(self._on_seek_finish)
        tc_layout.addWidget(self._seek_slider)

        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 4)
        self._time_current = QLabel("00:00")
        self._time_current.setObjectName("TimeLabel")
        time_row.addWidget(self._time_current)
        time_row.addStretch()
        self._time_total = QLabel("00:00")
        self._time_total.setObjectName("TimeLabel")
        time_row.addWidget(self._time_total)
        tc_layout.addLayout(time_row)

        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._prev_btn = QPushButton("«")
        self._prev_btn.setObjectName("TransportButton")
        self._prev_btn.clicked.connect(self.play_previous)
        self._prev_btn.setToolTip("Previous track")
        controls.addWidget(self._prev_btn)

        self._play_btn = QPushButton("▶")
        self._play_btn.setObjectName("PlayButton")
        self._play_btn.setFixedSize(METRICS.play_button, METRICS.play_button)
        self._play_btn.clicked.connect(self.toggle_playback)
        self._play_btn.setToolTip("Play / Pause")
        controls.addWidget(self._play_btn)

        self._next_btn = QPushButton("»")
        self._next_btn.setObjectName("TransportButton")
        self._next_btn.clicked.connect(self.play_next)
        self._next_btn.setToolTip("Next track")
        controls.addWidget(self._next_btn)

        self._shuffle_btn = QPushButton("⤨")
        self._shuffle_btn.setObjectName("TransportButton")
        self._shuffle_btn.setCheckable(True)
        self._shuffle_btn.setChecked(self._shuffle)
        self._shuffle_btn.toggled.connect(self._toggle_shuffle)
        self._shuffle_btn.setToolTip("Shuffle")
        controls.addWidget(self._shuffle_btn)

        self._repeat_btn = QPushButton("↻")
        self._repeat_btn.setObjectName("TransportButton")
        self._repeat_btn.clicked.connect(self._cycle_repeat)
        self._repeat_btn.setToolTip("Repeat off / all / one")
        self._repeat_btn.setText("↻" if self._repeat_mode == "off" else
                                 ("↻1" if self._repeat_mode == "one" else "↻∞"))
        self._repeat_btn.setProperty("on", "true" if self._repeat_mode != "off" else "false")
        controls.addWidget(self._repeat_btn)

        self._ab_btn = QPushButton("A–B")
        self._ab_btn.setObjectName("IconButton")
        self._ab_btn.clicked.connect(self.cycle_ab_loop)
        self._ab_btn.setToolTip("A/B loop")
        controls.addWidget(self._ab_btn)

        self._snapshot_btn = QPushButton("▧")
        self._snapshot_btn.setObjectName("IconButton")
        self._snapshot_btn.clicked.connect(self.save_snapshot)
        self._snapshot_btn.setToolTip("Save current video frame")
        controls.addWidget(self._snapshot_btn)

        self._rate_btn = QPushButton(f"{self._rate:g}×")
        self._rate_btn.setObjectName("IconButton")
        self._rate_btn.clicked.connect(self.cycle_rate)
        self._rate_btn.setToolTip("Playback speed")
        controls.addWidget(self._rate_btn)

        self._viz_btn = QPushButton("〰")
        self._viz_btn.setObjectName("IconButton")
        self._viz_btn.clicked.connect(self.toggle_visualizer)
        self._viz_btn.setToolTip("Visualizer on/off")
        controls.addWidget(self._viz_btn)

        self._record_btn = QPushButton("●")
        self._record_btn.setObjectName("IconButton")
        self._record_btn.clicked.connect(self.toggle_recording)
        self._record_btn.setToolTip("Record stream / source")
        controls.addWidget(self._record_btn)

        controls.addStretch()

        volume_layout = QHBoxLayout()
        volume_layout.setSpacing(4)
        self._mute_btn = QPushButton("♪")
        self._mute_btn.setObjectName("IconButton")
        self._mute_btn.setFixedSize(32, 32)
        self._mute_btn.clicked.connect(self.toggle_mute)
        self._mute_btn.setToolTip("Mute / Unmute")
        volume_layout.addWidget(self._mute_btn)

        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setObjectName("VolumeSlider")
        self._volume_slider.setRange(0, 200)
        self._volume_slider.setValue(self._volume)
        self._volume_slider.setFixedWidth(100)
        self._volume_slider.valueChanged.connect(self._on_volume_slider)
        self._volume_slider.setToolTip("Volume")
        volume_layout.addWidget(self._volume_slider)
        controls.addLayout(volume_layout)

        self._fullscreen_btn = QPushButton("□")
        self._fullscreen_btn.setObjectName("IconButton")
        self._fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self._fullscreen_btn.setToolTip("Fullscreen (F)")
        controls.addWidget(self._fullscreen_btn)

        self._more_btn = QPushButton("⋯")
        self._more_btn.setObjectName("IconButton")
        self._more_btn.setCheckable(True)
        self._more_btn.setToolTip("More controls")
        controls.addWidget(self._more_btn)

        tc_layout.addLayout(controls)

        self._more_panel = QFrame()
        self._more_panel.setObjectName("Panel")
        secondary = QHBoxLayout(self._more_panel)
        secondary.setSpacing(3)

        self._stop_btn = QPushButton("■")
        self._stop_btn.setObjectName("TransportButton")
        self._stop_btn.clicked.connect(self.stop)
        self._stop_btn.setToolTip("Stop")
        secondary.addWidget(self._stop_btn)

        self._seek_back_btn = QPushButton("‹")
        self._seek_back_btn.setObjectName("TransportButton")
        self._seek_back_btn.clicked.connect(lambda: self.seek_by(-10))
        self._seek_back_btn.setToolTip("Rewind 10s")
        secondary.addWidget(self._seek_back_btn)

        self._seek_fwd_btn = QPushButton("›")
        self._seek_fwd_btn.setObjectName("TransportButton")
        self._seek_fwd_btn.clicked.connect(lambda: self.seek_by(10))
        self._seek_fwd_btn.setToolTip("Forward 10s")
        secondary.addWidget(self._seek_fwd_btn)

        self._audio_track_menu = QPushButton("Audio")
        self._audio_track_menu.setObjectName("IconButton")
        self._audio_track_menu.setMenu(QMenu(self))
        self._audio_track_menu.menu().aboutToShow.connect(lambda: self._refresh_track_menu(TrackKind.AUDIO))
        self._audio_track_menu.setToolTip("Audio track")
        secondary.addWidget(self._audio_track_menu)

        self._video_track_menu = QPushButton("Video")
        self._video_track_menu.setObjectName("IconButton")
        self._video_track_menu.setMenu(QMenu(self))
        self._video_track_menu.menu().aboutToShow.connect(lambda: self._refresh_track_menu(TrackKind.VIDEO))
        self._video_track_menu.setToolTip("Video track")
        secondary.addWidget(self._video_track_menu)

        self._subtitle_track_menu = QPushButton("Subtitles")
        self._subtitle_track_menu.setObjectName("IconButton")
        self._subtitle_track_menu.setMenu(QMenu(self))
        self._subtitle_track_menu.menu().aboutToShow.connect(lambda: self._refresh_track_menu(TrackKind.SUBTITLE))
        self._subtitle_track_menu.setToolTip("Subtitle track")
        secondary.addWidget(self._subtitle_track_menu)

        self._audio_device_menu = QPushButton("Output")
        self._audio_device_menu.setObjectName("IconButton")
        self._audio_device_menu.setMenu(QMenu(self))
        self._audio_device_menu.menu().aboutToShow.connect(self._refresh_audio_devices)
        self._audio_device_menu.setToolTip("Audio output device")
        secondary.addWidget(self._audio_device_menu)

        self._chapter_menu = QPushButton("Chapters")
        self._chapter_menu.setObjectName("IconButton")
        self._chapter_menu.setMenu(QMenu(self))
        self._chapter_menu.menu().aboutToShow.connect(self._refresh_chapters)
        self._chapter_menu.setToolTip("Chapters")
        secondary.addWidget(self._chapter_menu)

        sync_menu_btn = QPushButton("Sync")
        sync_menu_btn.setObjectName("IconButton")
        sync_menu = QMenu(self)
        sync_menu.addAction("Audio delay…", self.set_audio_delay_dialog)
        sync_menu.addAction("Subtitle delay…", self.set_subtitle_delay_dialog)
        sync_menu_btn.setMenu(sync_menu)
        sync_menu_btn.setToolTip("Audio / subtitle sync")
        secondary.addWidget(sync_menu_btn)

        load_sub_btn = QPushButton("Load subtitle")
        load_sub_btn.setObjectName("IconButton")
        load_sub_btn.clicked.connect(self.load_external_subtitle)
        secondary.addWidget(load_sub_btn)

        frame_btn = QPushButton("Frame")
        frame_btn.setObjectName("IconButton")
        frame_btn.clicked.connect(self.next_frame)
        secondary.addWidget(frame_btn)

        info_btn = QPushButton("Info")
        info_btn.setObjectName("IconButton")
        info_btn.clicked.connect(self.show_media_info)
        secondary.addWidget(info_btn)

        rec_settings_btn = QPushButton("Rec-Settings")
        rec_settings_btn.setObjectName("IconButton")
        rec_settings_btn.setToolTip("Recording: Speicherort, Format, Splitting")
        rec_settings_btn.clicked.connect(self._show_record_settings_dialog)
        secondary.addWidget(rec_settings_btn)

        self._more_panel.hide()
        self._more_btn.toggled.connect(self._more_panel.setVisible)
        tc_layout.addWidget(self._more_panel)

        center_column.addWidget(transport_container)

        self._diagnostics_bar = DiagnosticsBar()
        center_column.addWidget(self._diagnostics_bar)

        self._sources_view = SourcesView(self.settings_store)
        self._sources_view.sourceActivated.connect(self._on_source_activated)
        self._sources_view.queueItemsRequested.connect(self._on_queue_items_requested)
        self._sources_view.closeRequested.connect(self._show_player_page)
        self._sources_view.webPlayerRequested.connect(self._open_web_player)
        self._sources_view.consentAccepted.connect(
            lambda: self.status("yt-dlp consent saved — YouTube/Spotify enabled"))

        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(player_page)
        self._center_stack.addWidget(self._sources_view)
        from mpcasu_qt.webplayers import WebPlayerTabs
        self._web_player_tabs = WebPlayerTabs()
        self._center_stack.addWidget(self._web_player_tabs)
        self._pages: list = []
        self._library_page = LibraryPage(self.media_library, self._thumbnail_dir,
                                         self.settings_store, self)
        self._library_page.addRequested.connect(lambda paths: self.add_files(paths))
        self._library_page.refreshRequested.connect(self.refresh_watched_folders)
        self._library_page.backRequested.connect(self._show_player_page)
        self._options_page = OptionsPage(self.settings_store, self)
        self._options_page.applied.connect(self._apply_settings)
        self._options_page.actionRequested.connect(self._options_action)
        self._options_page.backRequested.connect(self._show_player_page)
        self._epg_page = EpgPage(self)
        self._epg_page.channelActivated.connect(self._on_epg_channel)
        self._epg_page.backRequested.connect(self._show_player_page)
        self._about_page = AboutPage(self)
        self._about_page.backRequested.connect(self._show_player_page)
        body.addWidget(self._center_stack, 1)

        self._playlist_pane = PlaylistPane()
        self._playlist_pane.addRequested.connect(self.add_dialog)
        self._playlist_pane.urlRequested.connect(lambda: self.show_sources("url"))
        self._playlist_pane.renameRequested.connect(self._rename_queue_row)
        self._playlist_pane.playRequested.connect(self._play_playlist_row)
        self._playlist_pane.removeRequested.connect(self._on_playlist_remove)
        self._playlist_pane.moveRequested.connect(self._on_playlist_move)
        self._playlist_pane.favoriteRequested.connect(self._on_queue_favorite)
        self._playlist_pane.orderChanged.connect(self._apply_queue_order)
        self._playlist_pane.childPlayRequested.connect(self._on_queue_child_play)
        self._playlist_pane.childRemoveRequested.connect(self._on_child_remove_from_playlist)
        self._playlist_pane.childMoveRequested.connect(self._on_child_move_to_playlist)
        self._playlist_pane.saveRequested.connect(self.save_playlist)
        self._playlist_pane.loadRequested.connect(self.load_playlist)
        self._random = random.SystemRandom()
        self._playlist_pane.shuffle_btn.toggled.connect(self._toggle_shuffle)
        self._playlist_pane.repeat_btn.clicked.connect(self._cycle_repeat)
        # Apply the persisted shuffle/repeat state to both control bars.
        self._playlist_pane.shuffle_btn.setChecked(self._shuffle)
        self._playlist_pane.shuffle_btn.setText("Shuffle on" if self._shuffle else "Shuffle off")
        self._playlist_pane.repeat_btn.setText(f"Repeat {self._repeat_mode}")
        if hasattr(self, "_repeat_btn"):
            self._repeat_btn.setText("↻" if self._repeat_mode == "off" else
                                     ("↻1" if self._repeat_mode == "one" else "↻∞"))
            self._repeat_btn.setProperty("on", "true" if self._repeat_mode != "off" else "false")
        self._playlist_pane.repeat_btn.clicked.connect(self._cycle_repeat)
        body.addWidget(self._playlist_pane)

        main_layout.addLayout(body)

        status_bar = QStatusBar()
        status_bar.setObjectName("StatusBar")
        self._status_left = QLabel("MPCASU 7.0.0")
        self._status_left.setObjectName("StatusText")
        self._status_left.setStyleSheet(f"color: {PALETTE.text_muted};")
        status_bar.addWidget(self._status_left)
        self._status_center = QLabel("Optimized for performance and integrity")
        self._status_center.setObjectName("StatusText")
        self._status_center.setStyleSheet(f"color: {PALETTE.text_faint};")
        status_bar.addWidget(self._status_center)
        self._status_right = QLabel("CPU/RAM telemetry unavailable")
        self._status_right.setObjectName("StatusText")
        self._status_right.setStyleSheet(f"color: {PALETTE.text_faint};")
        status_bar.addPermanentWidget(self._status_right)
        self.setStatusBar(status_bar)

    def _setup_shortcuts(self):
        space = QAction("Play/Pause", self)
        space.setShortcut(QKeySequence(Qt.Key_Space))
        space.triggered.connect(self.toggle_playback)
        self.addAction(space)

        ctrl_o = QAction("Open file", self)
        ctrl_o.setShortcut(QKeySequence("Ctrl+O"))
        ctrl_o.triggered.connect(self.add_dialog)
        self.addAction(ctrl_o)

        ctrl_l = QAction("Open URL", self)
        ctrl_l.setShortcut(QKeySequence("Ctrl+L"))
        ctrl_l.triggered.connect(self.open_url_dialog)
        self.addAction(ctrl_l)

        ctrl_i = QAction("Media info", self)
        ctrl_i.setShortcut(QKeySequence("Ctrl+I"))
        ctrl_i.triggered.connect(self.show_media_info)
        self.addAction(ctrl_i)

        left = QAction("Seek back", self)
        left.setShortcut(QKeySequence(Qt.Key_Left))
        left.triggered.connect(lambda: self.seek_by(-10))
        self.addAction(left)

        right = QAction("Seek forward", self)
        right.setShortcut(QKeySequence(Qt.Key_Right))
        right.triggered.connect(lambda: self.seek_by(10))
        self.addAction(right)

        up = QAction("Volume up", self)
        up.setShortcut(QKeySequence(Qt.Key_Up))
        up.triggered.connect(lambda: self.change_volume(5))
        self.addAction(up)

        down = QAction("Volume down", self)
        down.setShortcut(QKeySequence(Qt.Key_Down))
        down.triggered.connect(lambda: self.change_volume(-5))
        self.addAction(down)

        f_action = QAction("Fullscreen", self)
        f_action.setShortcut(QKeySequence(Qt.Key_F))
        f_action.triggered.connect(self.toggle_fullscreen)
        self.addAction(f_action)

        m_action = QAction("Mute", self)
        m_action.setShortcut(QKeySequence(Qt.Key_M))
        m_action.triggered.connect(self.toggle_mute)
        self.addAction(m_action)

        s_action = QAction("Stop", self)
        s_action.setShortcut(QKeySequence(Qt.Key_S))
        s_action.triggered.connect(self.stop)
        self.addAction(s_action)

        esc = QAction("Exit fullscreen", self)
        esc.setShortcut(QKeySequence(Qt.Key_Escape))
        esc.triggered.connect(self._exit_fullscreen)
        self.addAction(esc)

    def _navigate(self, name: str):
        if name == "NOW PLAYING":
            self._show_player_page()
            self._sidebar.set_active("NOW PLAYING")
            return
        if name == "LIBRARY":
            self._library_page._refresh()
            self._show_page(self._library_page, "LIBRARY")
            self._sidebar.set_active("LIBRARY")
            return
        if name == "WEB & STREAMS":
            self.show_sources("url")
            return
        if name == "PLAYLISTS":
            self._show_player_page()
            self._playlist_pane.setVisible(True)
            self._playlist_pane.set_view("playlists")
            self._sidebar.set_active("PLAYLISTS")
            return
        if name == "IPTV / EPG":
            self._show_page(self._epg_page, "IPTV / EPG")
            self._sidebar.set_active("IPTV / EPG")
            return
        if name == "YOUTUBE":
            self.show_sources("youtube")
            return
        if name == "SPOTIFY":
            self._open_web_player("spotify")
            self._sidebar.set_active("SPOTIFY")
            return
        if name in ("HEARTHIS", "TIDAL", "NETFLIX", "BROWSE"):
            self._open_web_player(name.lower())
            self._sidebar.set_active(name)
            return
        if name == "CASU FILES":
            self._show_player_page()
            self._playlist_pane.setVisible(True)
            self._playlist_pane.set_view("casu")
            self._sidebar.set_active("CASU FILES")
            return
        if name == "OPTIONS":
            self._options_page.reload()
            self._show_page(self._options_page, "OPTIONS")
            self._sidebar.set_active("OPTIONS")
            return
        if name == "ABOUT":
            self._show_page(self._about_page, "ABOUT")
            self._sidebar.set_active("ABOUT")
            return
        self._show_player_page()

    def status(self, text: str):
        if hasattr(self, "_status_center"):
            self._status_center.setText(str(text))
        if hasattr(self, "_status_label"):
            self._status_label.setText(str(text))

    # --- Playback control ---

    def toggle_playback(self):
        if not self.backend:
            self.play_selected()
        else:
            self.pause()

    def pause(self):
        if self.backend and self.backend.state() not in {PlaybackState.EMPTY, PlaybackState.STOPPED, PlaybackState.ENDED}:
            if self._paused:
                self.controller.pause_or_resume()
                self._paused = False
                self.status("Playing — source timing is preserved")
                self._play_btn.setText("▶")
            else:
                self._sync_position()
                self.controller.pause_or_resume()
                self._paused = True
                self.status("Paused — source timing is preserved")
                self._play_btn.setText("| |")

    def stop(self, *, stop_youtube: bool = True):
        if stop_youtube:
            self._stop_yt_transport()
        self._stop_stream_viz()
        self._viz_timer.stop()
        self._viz_pcm = None
        self._viz_rate = 0
        self._viz_overview = ()
        self._visualizer.set_cover(None)
        if self._temp_media is not None:
            try:
                self._temp_media.unlink(missing_ok=True)
            except OSError:
                pass
            self._temp_media = None
        self._viz_generation += 1
        self._audio_stage = False
        self._reposition_overlays()
        if self.backend:
            self._persist_media_preferences()
            self.controller.stop()
            self.controller.close()
        self.backend = None
        self._seek_slider.clear_chapters()
        self._paused = False
        self._play_btn.setText("▶")
        self._diagnostics_bar.set_values(
            support="Legacy backend", integrity="unavailable",
            segmented="unavailable",
        )
        self.status("Stopped")
        self._video_surface.set_video_active(False)
        self._video_surface.clear()

    def seek_by(self, seconds: float):
        pos = max(0.0, min(self.duration, self._seek_slider._position + seconds))
        self._seek_slider.set_position(pos)
        self._do_seek(pos)

    def _on_seek_preview(self, pos: float):
        if not self._dragging:
            self._seek_slider.set_position(pos)
            self._update_time_labels(pos)
            if self._mpris_notifier is not None:
                self._mpris_notifier.seeked(pos)

    def _on_seek_start(self):
        self._dragging = True

    def _on_seek_finish(self, pos: float):
        self._dragging = False
        self._do_seek(pos)

    def _do_seek(self, pos: float):
        if not self.backend or pos < 0:
            return
        try:
            self.backend.seek(pos)
            if not self._paused:
                self.backend.play()
            self._seek_slider.set_position(pos)
            self._update_time_labels(pos)
        except (BackendError, CasuError, OSError) as exc:
            self.last_playback_error = str(exc)
            self.status(f"Cannot seek — {exc}")

    def change_volume(self, delta: int):
        self._volume = max(0, min(200, self._volume + delta))
        self._volume_slider.setValue(self._volume)
        if self.backend:
            try:
                self._volume = self.backend.set_volume(self._volume)
            except BackendError as exc:
                self.status(str(exc))
                return
        self.status(f"Volume {self._volume}%")

    def _on_volume_slider(self, value: int):
        self._volume = value
        if self.backend:
            try:
                self._volume = self.backend.set_volume(value)
            except BackendError:
                pass
        self.status(f"Volume {self._volume}%")

    def toggle_mute(self):
        self._muted = not self._muted
        if self.backend:
            try:
                self.backend.set_mute(self._muted)
            except BackendError as exc:
                self.status(str(exc))
                return
        self.status("Muted" if self._muted else f"Volume {self._volume}%")
        self._mute_btn.setText("×" if self._muted else "♪")

    def cycle_rate(self):
        rates = (0.5, 1.0, 1.25, 1.5, 2.0)
        next_rate = rates[(rates.index(self._rate) + 1) % len(rates)] if self._rate in rates else 1.0
        if not self.backend:
            self._rate = next_rate
            self.status(f"Playback rate {self._rate:g}× (applies on next media)")
            return
        try:
            self._rate = self.backend.set_rate(next_rate)
            self._rate_btn.setText(f"{self._rate:g}×")
            self.status(f"Playback rate {self._rate:g}×")
        except BackendError as exc:
            self.status(f"Playback rate unavailable: {exc}")

    def play_selected(self, path: Path | None = None):
        if path is not None:
            selected = Path(path)
        else:
            # A selected child of an expanded playlist group plays through the
            # same resolution path as every playlist action.
            child = self._playlist_pane.selected_child()
            if child is not None:
                self._on_queue_child_play(child)
                return
            selected = self.selected_path()
        if selected is None:
            self.status("Add a media file first.")
            return
        text = str(selected)
        if "://" in text or text.startswith(("spotify:", "ytdl:")):
            self._play_network_source(text)
            return
        path = selected
        # A playlist file (a .m3u/.pls/… row in the queue) is not playable
        # itself: the main Play button must start the FULL playlist, exactly
        # like right-click -> Play does.
        if path.is_file() and path.suffix.lower() in PlaylistPane.PLAYLIST_SUFFIXES:
            self._play_playlist_full(path)
            return
        if not Path(text).is_file():
            self.status("Add a media file first.")
            return
        self._recording_source_boundary(str(path))
        self.stop()
        self._show_player_page()
        self._stop_stream_viz()
        self._end_handled = False
        self.current = path
        self._network_source = None
        display_title = self._display_title(path)
        self._now_playing_bar.set_now_playing(display_title)
        self._audio_stage = path.suffix.lower() in AUDIO_EXTENSIONS
        if self._audio_stage:
            self._set_caption(display_title, path)
        else:
            # Videos: libVLC owns the native VideoSurface exclusively, so no
            # Qt caption/badge/empty-hint overlays over the picture.
            self._caption_label.hide()
            self._badges_label.hide()
            self._empty_hint.hide()
        selected_index = self.playlist_model.index_of(path)
        if selected_index is not None:
            self._playlist_pane.populate(list(self.playlist_model.items), selected_index)

        sidecar = path if path.suffix.lower() == ".casu" else path.with_suffix(path.suffix + ".casu")
        self._load_visual_state(sidecar if sidecar.exists() else path)
        self._load_visualizer(path)
        self._probe_stage(path)

        if path.suffix.lower() == ".casu":
            magic = b""
            try:
                magic = path.read_bytes()[:8]
                native = magic in {b"CASUNAT1", b"CASUNAT2"}
            except OSError:
                native = False
            self._diagnostics_bar.set_values(
                support=("CASUNAT2 native key-state/tile/PCM" if magic == b"CASUNAT2" else
                         "CASUNAT1 compatibility + libVLC" if native else
                         "CASUNAT1 container + libVLC"),
                integrity="verified source manifest" if not self._visual_state.startswith("invalid") else "failed manifest validation",
                segmented=f"{len(self._visual_segments)} segments" if self._visual_segments else "no segment data",
            )
        elif path.suffix.lower() == ".mp5":
            self._diagnostics_bar.set_values(
                support="MP5 enhanced container + libVLC",
                integrity="SHA-256 verified attachment",
                segmented=f"{len(self._visual_segments)} segments" if self._visual_segments else "no segment data",
            )
        elif sidecar.exists():
            self._diagnostics_bar.set_values(
                support="CASUNAT1 + CASUNAT2",
                integrity="CASUNAT1 envelope verified on load",
                segmented=f"{len(self._visual_segments)} segments" if self._visual_segments else "no segment data",
            )
        else:
            self._diagnostics_bar.set_values(
                support="Legacy backend", integrity="unavailable", segmented="unavailable",
            )
        self._diagnostics_bar.set_values(guide=self._epg_now_next())

        try:
            source = self._source_for(path)
        except CasuError as exc:
            self.toast(f"{path.name}: {exc} — if this is an old or invalid .casu file, "
                       "re-convert it with the converter (CASUNAT2 recommended)")
            self.status("Cannot play — the CASU manifest failed validation")
            return

        state = ("CASU manifest selected" if path.suffix.lower() == ".casu"
                 else ("MP5 container selected" if path.suffix.lower() == ".mp5"
                       else ("CASU envelope found" if sidecar.exists() else "legacy media — no CASU envelope")))
        self.status(f"{path.name} · {state}")

        is_casu_container = path.suffix.lower() in {".casu", ".mp5"}
        try:
            if path.suffix.lower() == ".casu" and NativeCasuBackend.supports(path):
                audio_sink = None
                if PulseAudioSink.probe():
                    try:
                        audio_sink = PulseAudioSink()
                    except BackendError:
                        audio_sink = None
                self._native_sink = QtVideoSurfaceSink(self._video_surface)
                self.backend = NativeCasuBackend(self._native_sink, audio_sink)
            else:
                # The packaged smoke must exercise the same real macOS output
                # modules as users. Dummy output can leave VLC's media clock
                # parked at zero on macOS even after successful decoding.
                runtime_options = ()
                self.backend = (CasuBackend(self._video_surface.handle)
                                if is_casu_container
                                else LibVLCBackend(self._video_surface.handle,
                                                   runtime_options=runtime_options))
            self.backend.on_event = self._backend_event
            if is_casu_container:
                self.backend.open_casu(path)
            else:
                self.backend.open(source)
            self.controller.attach(self.backend, path)
            if isinstance(self.backend, NativeCasuBackend):
                self._apply_media_preferences()
            self.controller.play()
            self._apply_playback_rate()
            self._apply_backend_settings()
            if os.environ.get("MPCASU_PACKAGED_PLAYBACK_SMOKE"):
                self.backend.set_volume(0)
            self.duration = self.backend.duration()
            self._seek_slider.set_duration(self.duration)
            self._draw_chapter_markers()
            if (self._resume_source and str(path) == self._resume_source
                    and 5.0 < self._resume_position < max(5.0, self.duration - 5.0)):
                self.controller.seek(self._resume_position)
                self._seek_slider.set_position(self._resume_position)
                self.status(f"Resumed {path.name} at {self._resume_position:.1f} s")
            else:
                self._resume_position = 0.0
            capabilities = self.backend.capabilities()
            self.status(f"{path.name} · {state} · {capabilities.get('version', 'libVLC')}")
            self._video_surface.set_video_active(not self._audio_stage)
            if isinstance(self.backend, LibVLCBackend):
                QTimer.singleShot(500, self._apply_media_preferences)
                QTimer.singleShot(1500, self._check_playback_start)
        except (BackendError, CasuError, OSError) as exc:
            self.controller.close()
            self.backend = None
            self.status(f"Cannot play — {exc}")
            self.toast(f"{path.name}: {exc}")
            self.toast(f"Could not start internal playback: {exc}")
            return
        self._paused = False
        self._play_btn.setText("| |")

    def _toggle_shuffle(self, checked: bool) -> None:
        self._shuffle = checked
        settings = self.settings_store.load()
        self.settings_store.save(replace(settings, shuffle=checked))
        self._playlist_pane.shuffle_btn.setText("Shuffle on" if checked else "Shuffle off")
        self._playlist_pane.shuffle_btn.setChecked(checked)
        if hasattr(self, "_shuffle_btn"):
            self._shuffle_btn.setProperty("on", "true" if checked else "false")
            self._shuffle_btn.style().unpolish(self._shuffle_btn)
            self._shuffle_btn.style().polish(self._shuffle_btn)
            self._shuffle_btn.setChecked(checked)
        self.status(f"Shuffle {'on' if checked else 'off'}")

    def _cycle_repeat(self) -> None:
        values = ("off", "all", "one")
        self._repeat_mode = values[(values.index(self._repeat_mode) + 1) % len(values)]
        settings = self.settings_store.load()
        self.settings_store.save(replace(settings, repeat_mode=self._repeat_mode))
        self._playlist_pane.repeat_btn.setText(f"Repeat {self._repeat_mode}")
        if hasattr(self, "_repeat_btn"):
            self._repeat_btn.setText("↻" if self._repeat_mode == "off" else
                                     ("↻1" if self._repeat_mode == "one" else "↻∞"))
            self._repeat_btn.setProperty("on", "true" if self._repeat_mode != "off" else "false")
            self._repeat_btn.style().unpolish(self._repeat_btn)
            self._repeat_btn.style().polish(self._repeat_btn)
        self.status(f"Repeat mode: {self._repeat_mode}")

    def play_next(self, automatic: bool = False):
        count = len(self.playlist_model)
        if automatic and self._repeat_mode == "one" and self.current and self.backend:
            # Replay the current track: reset the end guard, seek to 0 AND
            # resume playback (seek alone on an ended media stays silent).
            self._end_handled = False
            try:
                self.backend.play()
                self.backend.seek(0.0)
            except (BackendError, CasuError) as exc:
                self.status(f"Repeat failed — {exc}")
            self._paused = False
            self._play_btn.setText("| |")
            return
        if not count:
            if automatic and self.current and self.backend:
                # A single track outside the queue: loop it for every repeat
                # mode (and shuffle has nothing else to pick from).
                self._end_handled = False
                try:
                    self.backend.play()
                    self.backend.seek(0.0)
                except (BackendError, CasuError) as exc:
                    self.status(f"Loop failed — {exc}")
                self._paused = False
                self._play_btn.setText("| |")
                return
            self.status("Playlist is empty")
            return

        # The queue is the single source of truth: playlist groups stay in
        # the model (they are never dissolved into their entries), so the
        # playback order is a logical walk through the flattened queue —
        # UI expand state never matters, and the playlists stay visible.
        seq = self._ensure_play_seq()
        count = len(seq)
        if not count:
            self.status("Playlist is empty")
            return

        current_text = str(self.current) if self.current else None
        index = -1
        if current_text is not None:
            try:
                index = seq.index(current_text)
            except ValueError:
                index = -1
        if index < 0:
            # Current entry is not part of the logical sequence (queue was
            # edited or a stream is playing): continue from the selected
            # row/child (or the beginning).
            index = self._row_to_seq(self._selected_playlist_row())
            if index is None:
                child = self._playlist_pane.selected_child()
                if child is not None:
                    try:
                        index = seq.index(str(child))
                    except ValueError:
                        index = -1
            if index is None or index < 0:
                index = 0
        if self._shuffle and count > 1:
            choices = [value for value in range(count) if value != index]
            target = self._random.choice(choices)
        else:
            target = index + 1
        if target >= count and self._repeat_mode == "all":
            target = 0
        if target >= count:
            self.status("End of playlist")
            return
        self._play_playlist_entry(seq[target])

    def play_previous(self):
        seq = self._ensure_play_seq()
        count = len(seq)
        if not count:
            self.status("Playlist is empty")
            return

        current_text = str(self.current) if self.current else None
        index = -1
        if current_text is not None:
            try:
                index = seq.index(current_text)
            except ValueError:
                index = -1
        if index < 0:
            index = self._row_to_seq(self._selected_playlist_row())
            if index is None:
                child = self._playlist_pane.selected_child()
                if child is not None:
                    try:
                        index = seq.index(str(child))
                    except ValueError:
                        index = -1
            if index is None or index < 0:
                index = 0
        target = index - 1
        if target < 0 and self._repeat_mode == "all":
            target = count - 1
        if target < 0:
            self.status("Beginning of playlist")
            return
        self._play_playlist_entry(seq[target])

    def _current_playlist_context(self):
        """If the current item is a child of a playlist group, return
        (playlist_path, entries, child_index); otherwise None."""
        if self.current is None:
            return None
        current = str(self.current)
        pane = self._playlist_pane
        for index in range(pane.tree.topLevelItemCount()):
            top = pane.tree.topLevelItem(index)
            playlist_path = str(top.data(0, Qt.UserRole) or "")
            if not playlist_path or playlist_path.startswith(("http://", "https://", "rtsp://")):
                continue
            if Path(playlist_path).suffix.lower() not in PlaylistPane.PLAYLIST_SUFFIXES:
                continue
            if not top.isExpanded():
                continue
            for c in range(top.childCount()):
                child = top.child(c)
                if str(child.data(0, Qt.UserRole) or "") == current:
                    entries = self._playlist_entries(Path(playlist_path))
                    for i, entry in enumerate(entries):
                        if str(entry) == current:
                            return (Path(playlist_path), entries, i)
        return None

    def _play_entry(self, playlist: Path, entry):
        """Play one entry of a playlist group, keeping the group highlighted."""
        self._playlist_pane.select_child(playlist, entry)
        if isinstance(entry, str) and entry.startswith(("http://", "https://",
                                                        "rtsp://", "rtmp://",
                                                        "udp://", "rtp://",
                                                        "ftp://", "smb://")):
            self._resolve_and_open_external_source(entry)
            return
        path = Path(entry)
        if not path.is_file():
            self.toast(f"Local file not found: {path.name}")
            return
        self.play_selected(path)

    def _selected_playlist_row(self) -> int:
        return self._playlist_pane.selected_row()

    def selected_path(self) -> Path | None:
        selected = self._selected_playlist_row()
        if selected < 0:
            if self.current:
                return self.current
            if len(self.playlist_model):
                return self.playlist_model.item(0)
            return None
        try:
            return self.playlist_model.item(selected)
        except PlaylistError:
            return None

    # --- Track menus ---

    def _refresh_track_menu(self, kind: TrackKind):
        menu_map = {
            TrackKind.AUDIO: self._audio_track_menu.menu(),
            TrackKind.VIDEO: self._video_track_menu.menu(),
            TrackKind.SUBTITLE: self._subtitle_track_menu.menu(),
        }
        menu = menu_map[kind]
        menu.clear()
        if not self.backend:
            menu.addAction("No active media").setEnabled(False)
            return
        descriptors = self.backend.track_descriptors(kind)
        getters = {
            TrackKind.AUDIO: self.backend.audio_track,
            TrackKind.VIDEO: self.backend.video_track,
            TrackKind.SUBTITLE: self.backend.subtitle_track,
        }
        current = getters[kind]()
        if kind is TrackKind.SUBTITLE:
            act = menu.addAction("Off")
            act.setCheckable(True)
            act.setChecked(current == -1)
            act.triggered.connect(lambda checked=False, k=kind, v=-1: self._select_track(k, v))
        if not descriptors:
            menu.addAction("No tracks reported").setEnabled(False)
        for item in descriptors:
            details = [item.label]
            if item.language and item.language not in item.label:
                details.append(item.language)
            if item.codec and item.codec not in item.label:
                details.append(item.codec)
            label = " · ".join(details)
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current == item.identifier)
            act.triggered.connect(lambda checked=False, k=kind, v=item.identifier: self._select_track(k, v))

    def _select_track(self, kind: TrackKind, identifier: int):
        if not self.backend:
            return
        setters = {
            TrackKind.AUDIO: self.backend.set_audio_track,
            TrackKind.VIDEO: self.backend.set_video_track,
            TrackKind.SUBTITLE: self.backend.set_subtitle_track,
        }
        try:
            setters[kind](identifier)
            self._persist_media_preferences()
            self.status(f"{kind.value.title()} track selected: {identifier}")
        except BackendError as exc:
            self.status(str(exc))

    # --- Audio devices ---

    def _refresh_audio_devices(self):
        menu = self._audio_device_menu.menu()
        menu.clear()
        if not self.backend:
            menu.addAction("No active media").setEnabled(False)
            return
        devices = self.backend.audio_devices()
        if not devices:
            menu.addAction("Runtime reported no devices").setEnabled(False)
            return
        for device in devices:
            act = menu.addAction(device.label)
            act.triggered.connect(lambda checked=False, did=device.identifier: self._select_audio_device(did))

    def _select_audio_device(self, identifier: str):
        if not self.backend:
            return
        try:
            self.backend.set_audio_device(identifier)
            self._audio_device = identifier
            self.status(f"Audio output selected: {identifier}")
        except BackendError as exc:
            self.status(str(exc))

    # --- Chapters ---

    def _refresh_chapters(self):
        menu = self._chapter_menu.menu()
        menu.clear()
        if not self.backend:
            menu.addAction("No active media").setEnabled(False)
            return
        chapters = self.backend.chapter_descriptors()
        if not chapters:
            menu.addAction("No chapters reported").setEnabled(False)
            return
        for chapter in chapters:
            minutes, seconds = divmod(max(0, int(chapter.start_seconds)), 60)
            act = menu.addAction(f"{minutes:02d}:{seconds:02d} · {chapter.title}")
            act.triggered.connect(lambda checked=False, cid=chapter.identifier: self._select_chapter(cid))

    def _select_chapter(self, identifier: int):
        if not self.backend:
            return
        try:
            self.backend.set_chapter(identifier)
            self.status(f"Chapter selected: {identifier + 1}")
            self._seek_slider.set_position(self.backend.position())
            self._draw_chapter_markers()
        except BackendError as exc:
            self.status(str(exc))

    def _draw_chapter_markers(self, chapters=None):
        if chapters is None:
            if not self.backend:
                self._seek_slider.clear_chapters()
                return
            try:
                chapters = self.backend.chapter_descriptors()
            except BackendError:
                self._seek_slider.clear_chapters()
                return
        try:
            active = self.backend.chapter() if self.backend else -1
        except BackendError:
            active = -1
        self._seek_slider.set_chapters(chapters, active)

    # --- Subtitle ---

    def load_external_subtitle(self):
        if not self.backend or not self.current:
            self.status("Open local media before loading an external subtitle")
            return
        from PySide6.QtWidgets import QFileDialog
        subtitle, _ = QFileDialog.getOpenFileName(
            self, "Load subtitle",
            filter="Subtitle files (*.srt *.ass *.ssa *.vtt *.sub);;All files (*.*)"
        )
        if not subtitle:
            return
        try:
            position = self.backend.position()
            paused = self._paused
            self.backend.add_external_subtitle(Path(subtitle))
            self.duration = self.backend.duration()
            self._seek_slider.set_duration(self.duration)
            self._draw_chapter_markers()
            self.backend.seek(position)
            if not paused:
                self.backend.play()
            self.status(f"External subtitle loaded · {Path(subtitle).name}")
        except (BackendError, OSError) as exc:
            self.status(f"Could not load subtitle: {exc}")

    # --- Frame step ---

    def next_frame(self):
        if not self.backend:
            self.status("No active media backend")
            return
        try:
            self.backend.next_frame()
            self._paused = True
            self._play_btn.setText("▶")
            self.status("Advanced one decoded frame")
        except BackendError as exc:
            self.status(str(exc))

    # --- Fullscreen ---

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if getattr(self, "_saved_geometry", None):
                self.setGeometry(self._saved_geometry)
            self._exit_fs_ui()
        else:
            self._saved_geometry = self.geometry()
            self._enter_fs_ui()
            self.showFullScreen()
        self._fullscreen = self.isFullScreen()

    def _enter_fs_ui(self):
        self._fs_saved = {
            "sidebar": self._sidebar.isVisible(),
            "playlist": self._playlist_pane.isVisible(),
            "topbar": self._topbar.isVisible(),
            "transport": self._transport_container.isVisible(),
            "diag": self._diagnostics_bar.isVisible(),
        }
        self._sidebar.hide()
        self._playlist_pane.hide()
        self._topbar.hide()
        self._transport_container.hide()
        self._diagnostics_bar.hide()
        self.statusBar().hide()
        self._fs_overlay.setGeometry(self._player_page.rect())
        self._fs_overlay.show()
        self._fs_hide_timer.start()

    def _exit_fs_ui(self):
        self._fs_hide_timer.stop()
        self._fs_overlay.hide()
        saved = getattr(self, "_fs_saved", None) or {}
        self._sidebar.setVisible(saved.get("sidebar", True))
        self._playlist_pane.setVisible(saved.get("playlist", True))
        self._topbar.setVisible(saved.get("topbar", True))
        self._transport_container.setVisible(saved.get("transport", True))
        self._diagnostics_bar.setVisible(saved.get("diag", True))
        self.statusBar().show()

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        self._exit_fs_ui()
        self._fullscreen = False

    def mouseMoveEvent(self, event):
        if self.isFullScreen():
            self._fs_overlay.show()
            self._fs_hide_timer.start()
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self._center_stack.currentWidget() is not None and \
                    self._center_stack.currentIndex() != 0:
                self._show_player_page()
                return
            if self.isFullScreen():
                self.toggle_fullscreen()
                return
        super().keyPressEvent(event)

    # --- Playlist management ---

    def add_files(self, paths: list[Path | str]):
        # A media file that is already available as a child of a playlist in
        # the queue must not be added a second time as a separate top-level
        # row. Resolve every (new) playlist's children first and treat those
        # paths as "already covered" so Choose files never double-loads.
        # URLs (streams) are queued as top-level rows like files, so they can
        # be combined with playlists and saved/merged into them. The input
        # order is preserved: playlists, files and URLs keep their relative
        # positions in the queue.
        playlists: list[Path] = []
        plain: list[Path] = []
        urls: list[str] = []
        for value in paths:
            try:
                text = str(value)
                path = Path(value)
            except (TypeError, ValueError):
                continue
            suffix = path.suffix.lower()
            if text.startswith(("http://", "https://", "rtsp://", "rtmp://",
                                "udp://", "rtp://", "ftp://", "smb://")):
                urls.append(text)
            elif path.is_file() and suffix in PlaylistPane.PLAYLIST_SUFFIXES:
                playlists.append(path.expanduser().resolve())
            elif path.is_file():
                plain.append(path.expanduser().resolve())

        covered: set[str] = set()
        for playlist in playlists:
            try:
                from casu.playlist import load_playlist_file
                loaded = load_playlist_file(playlist)
                covered.update(str(item) for item in loaded.items)
            except (PlaylistError, OSError, ValueError):
                pass

        added: list[Path] = []
        for value in paths:
            try:
                text = str(value)
                path = Path(value)
            except (TypeError, ValueError):
                continue
            if text.startswith(("http://", "https://", "rtsp://", "rtmp://",
                                "udp://", "rtp://", "ftp://", "smb://")):
                try:
                    self.playlist_model.add((text,))
                except PlaylistError as exc:
                    self.status(str(exc))
                continue
            if not path.is_file():
                continue
            resolved = path.expanduser().resolve()
            if resolved in added or str(resolved) in covered:
                continue
            try:
                if self.playlist_model.add((resolved,), existing_only=True):
                    added.append(resolved)
            except PlaylistError as exc:
                self.status(str(exc))
                break
        for path in added:
            try:
                self.media_library.upsert(path)
            except OSError:
                pass
        self._invalidate_play_seq()
        self._render_playlist()

    def add_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        dialog = QFileDialog(self, "Add media")
        dialog.setFileMode(QFileDialog.ExistingFiles)
        dialog.setNameFilter(
            "Media and streams (*);;Known media ({});;All files (*.*)".format(
                " ".join(f"*{x}" for x in sorted(MEDIA_EXTENSIONS)))
        )
        # The native/portal file dialog is a common freeze source on Wayland
        # (and some X11) sessions. Use the in-process Qt dialog so adding files
        # can never hang the player.
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        if not dialog.exec():
            return
        self.add_files([Path(p) for p in dialog.selectedFiles()])

    def open_url_dialog(self):
        self.show_sources("url")

    def show_sources(self, mode: str):
        """Switch the center area to the in-window sources view (no popup)."""
        self._sources_view.set_mode(mode)
        self._center_stack.setCurrentWidget(self._sources_view)
        self._topbar_title.setText(self._sources_view.MODES[mode]["title"])
        self._back_btn.show()

    def _open_web_player(self, provider: str, *, query: str = "", url: str = ""):
        from casu.webproviders import WEB_PLAYERS
        label = ("BROWSE" if provider == "browse"
                 else WEB_PLAYERS.get(provider, WEB_PLAYERS["spotify"])["label"])
        self._web_player_tabs.open(provider, query=query, url=url)
        self._center_stack.setCurrentWidget(self._web_player_tabs)
        self._topbar_title.setText(label)
        self._back_btn.show()
        self.status(f"{label} Web Player (eingebettet) — dort mit deinem Account einloggen")
        self.toast(f"{label} geöffnet im eingebetteten Browser")

    def _show_player_page(self):
        self._center_stack.setCurrentIndex(0)
        self._topbar_title.setText("NOW PLAYING")
        self._back_btn.hide()
        self._playlist_pane.show()
        if self._queue_drawer:
            self._playlist_pane.setVisible(True)

    def _stop_yt_transport(self):
        """Stop the YouTube loopback transport owned by the previous session.

        Only called on real stops/source switches — never right after a new
        proxy was started for the source that is about to be opened.
        """
        if getattr(self, "_yt_stream", None) is not None:
            self._yt_stream.stop()

    def _toggle_queue_pane(self):
        if self.width() < 1100:
            if self._queue_drawer:
                self._close_queue_drawer()
            else:
                self._open_queue_drawer()
            return
        if self._queue_drawer:
            self._close_queue_drawer()
        else:
            self._playlist_pane.setVisible(not self._playlist_pane.isVisible())

    def _open_queue_drawer(self):
        pane = self._playlist_pane
        if pane.parent() is self.centralWidget():
            self._body_layout.removeWidget(pane)
        pane.setParent(self.centralWidget())
        width = min(320, int(self.width() * 0.88))
        pane.setFixedWidth(width)
        pane.setGeometry(self.width() - width, 0, width, self.height())
        pane.raise_()
        pane.show()
        self._queue_drawer = True

    def _close_queue_drawer(self):
        pane = self._playlist_pane
        pane.hide()
        if pane.parent() is self.centralWidget():
            self._body_layout.addWidget(pane)
        pane.setFixedWidth(METRICS.playlist_width)
        self._queue_drawer = False
        self._body_layout.invalidate()

    def _position_queue_drawer(self):
        if not self._queue_drawer:
            return
        pane = self._playlist_pane
        width = pane.width()
        pane.setGeometry(self.width() - width, 0, width, self.height())
        pane.raise_()

    def toast(self, text: str):
        """Web-player style transient toast over the stage (no popup)."""
        self._toast_label.setText(str(text))
        self._toast_label.adjustSize()
        stage = self._video_surface
        width = min(self._toast_label.width(), max(240, stage.width() - 32))
        self._toast_label.setFixedWidth(width)
        self._toast_label.setWordWrap(True)
        self._toast_label.adjustSize()
        x = stage.x() + max(16, (stage.width() - self._toast_label.width()) // 2)
        y = stage.y() + max(8, stage.height() - self._toast_label.height() - 18)
        self._toast_label.move(x, y)
        self._toast_label.raise_()
        self._toast_label.show()
        self._toast_timer.start(2600)

    def _display_title(self, path) -> str:
        """Tag info (title — artist) if available, otherwise the file name."""
        try:
            probe = ffprobe(Path(path))
            tags = (probe.get("format", {}) or {}).get("tags") or {}
            title = str(tags.get("title") or "").strip()
            artist = str(tags.get("artist") or "").strip()
            if title:
                return f"{title} — {artist}" if artist else title
        except Exception:  # noqa: BLE001 - tag lookup is best effort
            pass
        return Path(path).name

    def _set_caption(self, text: str, path=None):
        if not text:
            self._caption_label.hide()
            self._badges_label.hide()
            self._empty_hint.show()
            return
        self._empty_hint.hide()
        display = ""
        if str(text).startswith(("http://", "https://", "rtsp://", "rtmp://")):
            display = self._playlist_pane.name_for(str(text))
        caption = display or str(text)
        if str(text).startswith(("http://", "https://")):
            epg = self._epg_now_next()
            if epg and epg != "no EPG loaded" and epg != "EPG loaded":
                caption = f"{caption}\n{epg}"
        self._caption_label.setText(caption)
        self._caption_label.show()
        badge = ""
        if path is not None:
            suffix = Path(str(path)).suffix.lower()
            badge = {"casu": "CASU", "mp5": "MP5", "mp3": "MP3", "mp4": "MP4"}.get(
                suffix.lstrip("."), suffix.lstrip(".").upper() or "MEDIA")
        else:
            badge = "STREAM"
        self._badges_label.setText(badge)
        self._badges_label.show()
        self._reposition_overlays()

    def _reposition_overlays(self):
        stage = self._video_surface
        sx, sy = stage.x(), stage.y()
        self._badges_label.move(sx + 16, sy + 16)
        self._caption_label.setGeometry(sx, sy + max(0, stage.height() - 72),
                                        stage.width(), 72)
        ew = min(480, max(200, stage.width() - 60))
        eh = min(300, max(140, stage.height() - 60))
        self._empty_hint.setGeometry((stage.width() - ew) // 2 + sx,
                                     (stage.height() - eh) // 2 + sy, ew, eh)
        if self._audio_stage:
            self._visualizer.setGeometry(sx, sy, stage.width(), stage.height())
            self._visualizer.set_small(False)
            visible = (self._visualizer._cover is not None
                       or self._viz_mode != "off")
            self._visualizer.setVisible(visible)
            # Audio mode: Qt owns the surface, caption and badges are allowed.
            self._caption_label.raise_()
            self._badges_label.raise_()
        else:
            # Videos: libVLC owns the native VideoSurface exclusively. Qt must
            # not paint caption/badges over it or the picture flickers.
            self._visualizer.hide()
            self._caption_label.hide()
            self._badges_label.hide()
        self._empty_hint.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        if width < 1200 and not self._sidebar_rail:
            self._sidebar.set_rail(True)
            self._sidebar_rail = True
        elif width >= 1250 and self._sidebar_rail:
            self._sidebar.set_rail(False)
            self._sidebar_rail = False
        if self._queue_drawer:
            if width >= 1100:
                self._close_queue_drawer()
                self._playlist_pane.setVisible(True)
            else:
                self._position_queue_drawer()
        elif width < 1000 and self._playlist_pane.isVisible() and not self._playlist_auto_hidden:
            self._playlist_pane.hide()
            self._playlist_auto_hidden = True
        elif width >= 1050 and self._playlist_auto_hidden:
            self._playlist_pane.show()
            self._playlist_auto_hidden = False
        self._reposition_overlays()

    def showEvent(self, event):
        super().showEvent(event)
        self._clamp_to_screen()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._clamp_to_screen()

    def _clamp_to_screen(self):
        if self.isFullScreen() or getattr(self, "_clamping", False):
            return
        self._clamping = True
        try:
            avail = QGuiApplication.primaryScreen().availableGeometry()
            geo = self.geometry()
            width = min(geo.width(), avail.width())
            height = min(geo.height(), avail.height())
            if (width, height) != (geo.width(), geo.height()):
                self.resize(width, height)
                geo = self.geometry()
            x = min(max(geo.x(), avail.x()), avail.x() + max(0, avail.width() - geo.width()))
            y = min(max(geo.y(), avail.y()), avail.y() + max(0, avail.height() - geo.height()))
            if (x, y) != (geo.x(), geo.y()):
                self.move(x, y)
        finally:
            self._clamping = False

    def _filter_queue(self, text: str):
        self._playlist_pane.set_search(text)

    def _rename_queue_row(self, row: int):
        if row < 0:
            return
        item = self._playlist_pane.tree.topLevelItem(row)
        if item is None:
            return
        current = item.text(0)
        entry = QLineEdit(self._playlist_pane)
        entry.setText(current)
        entry.setObjectName("IconButton")
        self._playlist_pane.tree.setItemWidget(item, 0, entry)
        entry.returnPressed.connect(lambda: self._commit_rename(item, entry))
        entry.editingFinished.connect(lambda: self._commit_rename(item, entry))
        entry.setFocus()
        entry.selectAll()

    def _commit_rename(self, item, entry):
        text = entry.text().strip()
        self._playlist_pane.tree.removeItemWidget(item, 0)
        if text:
            url = str(item.data(0, Qt.UserRole) or "")
            self._playlist_pane._display_titles[url] = text
            item.setText(0, self._playlist_pane._label_for(url))

    def _apply_settings(self, settings):
        self._volume = max(0, min(200, int(settings.volume)))
        self._muted = bool(settings.muted)
        self._rate = float(settings.rate)
        self._watched_folders = list(settings.watched_folders)
        self._viz_mode = str(settings.visualizer)
        self._record_format = str(settings.record_format)
        self._record_split_minutes = int(settings.record_split_minutes)
        self._record_split_mode = str(settings.record_split_mode)
        self._volume_slider.setValue(self._volume)
        self._apply_backend_settings()
        self._apply_playback_rate()
        self._mute_btn.setText("×" if self._muted else "♪")
        if self._viz_mode == "off":
            self._visualizer.configure("off", (), (), 0.0)
        elif self.current is not None:
            self._load_visualizer(self.current)
        self.toast("Settings saved")
        self.status("Settings updated")

    def _load_visualizer(self, path):
        mode = str(self.settings_store.load().visualizer)
        self._viz_mode = mode

        self._viz_generation += 1
        generation = self._viz_generation

        self._viz_timer.stop()
        self._viz_pcm = None
        self._viz_rate = 0
        self._viz_overview = ()
        self._visualizer.set_cover(None)

        if mode == "off" or path is None:
            self._visualizer.configure("off", (), (), 0.0)
            return

        source = Path(str(path))

        if not source.is_file():
            return

        def worker():
            pcm, rate, _channels = decode_all_pcm(source)
            overview = self._overview_peaks(pcm)
            cover = self._cover_for(source)
            self._viz_bridge.resultReady.emit(
                ("pcm", generation, mode, pcm, rate, overview)
            )
            if cover:
                self._viz_bridge.resultReady.emit(
                    ("cover", generation, cover)
                )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_viz(self, payload):
        if not payload:
            return

        if payload[0] == "pcm":
            _, generation, mode, pcm, rate, overview = payload

            if generation != self._viz_generation:
                return

            self._viz_pcm = pcm
            self._viz_rate = rate
            self._viz_overview = tuple(overview or ())
            self._viz_mode = mode

            self._visualizer.configure(
                mode, (), (), self.duration or 0.0, self._viz_overview
            )

            if pcm is not None and rate > 0:
                self._viz_timer.start()

            return

        if payload[0] == "cover":
            _, generation, cover_path = payload

            if generation != self._viz_generation:
                return

            pixmap = QPixmap(str(cover_path))
            if not pixmap.isNull():
                self._visualizer.set_cover(pixmap)
            return

        if payload[0] == "stage":
            _, generation, has_audio, has_video = payload

            if generation != self._viz_generation:
                return

            self._audio_stage = bool(has_audio) and not bool(has_video)
            self._video_surface.set_video_active(not self._audio_stage)
            self._visualizer.set_small(not self._audio_stage)
            if not self._audio_stage:
                # Videos: no visualization, stop any stream visualizer.
                self._stop_stream_viz()
                self._viz_timer.stop()
                self._viz_pcm = None
                self._viz_rate = 0
            self._reposition_overlays()
            return

        if payload[0] == "live":
            self._visualizer.set_live(payload[1])

    def _tick_visualizer(self):
        if (
            self._viz_pcm is None
            or self._viz_rate <= 0
            or not self.backend
            or self._paused
        ):
            return

        mode = self._viz_mode or "waveform"

        if mode == "off":
            return

        # Do not burn CPU recomputing FFTs and repainting when the visualizer
        # is not on screen (e.g. during video playback, in small mode, or when
        # the widget is hidden). This is the runaway-CPU / unresponsive-UI fix.
        if not self._visualizer.isVisible():
            return

        try:
            position = float(self.backend.position())
        except Exception:  # noqa: BLE001 - visualizer is optional
            return

        points = max(64, min(128, max(1, self._visualizer.width()) // 6))
        wave = window_wave(
            self._viz_pcm,
            self._viz_rate,
            position,
            window_s=2048.0 / max(1, self._viz_rate),
            points=points,
        )

        self._visualizer.configure(
            mode,
            wave,
            (),
            self.duration or 0.0,
            self._viz_overview,
        )

    @staticmethod
    def _overview_peaks(pcm, points: int = 480) -> tuple:
        """Downsample the full decoded PCM into a static waveform overview."""
        if pcm is None or getattr(pcm, "size", 0) == 0 or points < 16:
            return ()
        import numpy as np
        values = np.abs(np.asarray(pcm, dtype=np.float32))
        size = values.size
        width = max(1, math.ceil(size / points))
        groups = max(1, size // width)
        pooled = values[:groups * width].reshape(groups, width).max(axis=1)
        return tuple(float(min(1.0, float(value))) for value in pooled)

    def _cover_for(self, source) -> str | None:
        """Return a cached cover image path for a local file or stream."""
        text = str(source)
        cache = self._cover_dir
        key = hashlib.sha256(text.encode("utf-8")).hexdigest() + ".png"
        target = cache / key
        if target.is_file() and 0 < target.stat().st_size <= 4 * 1024 * 1024:
            return str(target)

        try:
            path = Path(text)
        except (TypeError, ValueError):
            path = None

        native = False
        if path is not None and path.is_file() and path.suffix.lower() in {".casu", ".mp5"}:
            try:
                with path.open("rb") as handle:
                    native = handle.read(8) == b"CASUNAT2"
            except OSError:
                native = False
            if native:
                try:
                    thumb = thumbnail_for(path, cache)
                    if thumb is not None:
                        return str(thumb)
                except Exception:  # noqa: BLE001 - cover is optional
                    return None

        # Embedded cover art / attached picture is the first video frame, so
        # local files must not be seeked past it. Live streams get a small
        # input seek to reach the first usable frame without buffering the
        # whole feed (audio-only radios simply yield no cover).
        fd, temporary = tempfile.mkstemp(prefix=".cover-", dir=cache)
        os.close(fd)
        tmp = Path(temporary)
        stream = text.startswith(("http://", "https://", "rtsp://", "rtmp://"))
        command = ["ffmpeg", "-v", "error"]
        if stream:
            command += ["-ss", "1"]
        command += [
            "-i", text,
            "-map", "0:v:0", "-frames:v", "1",
            "-vf", "scale=480:480:force_original_aspect_ratio=increase,"
                   "crop=480:480",
            "-f", "image2", "-vcodec", "png", "-y", str(tmp),
        ]
        try:
            result = subprocess.run(command, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=30,
                                    check=False)
            if result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
                return None
            os.replace(tmp, target)
            return str(target)
        except (OSError, subprocess.TimeoutExpired):
            return None
        finally:
            tmp.unlink(missing_ok=True)

    def _probe_stage(self, source):
        generation = self._viz_generation
        probe_source = str(source)

        def worker():
            has_audio, has_video = self._probe_media_streams(probe_source)
            self._viz_bridge.resultReady.emit(
                ("stage", generation, has_audio, has_video)
            )

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _probe_media_streams(source: str) -> tuple[bool, bool]:
        raw = str(source)
        try:
            path = Path(raw)
        except (TypeError, ValueError):
            return False, False
        if path.is_file() and path.suffix.lower() in {".casu", ".mp5"}:
            try:
                with path.open("rb") as handle:
                    magic = handle.read(8)
                if magic == b"CASUNAT2":
                    container = read_native_v2(path, load_payloads=False)
                    types = [str(stream.get("type", ""))
                             for stream in container.manifest.get("streams", [])]
                    return ("audio" in types), ("video" in types)
            except (OSError, ValueError, NativeV2Error):
                return False, False
        try:
            probe = ffprobe(raw)
            streams = probe.get("streams", []) if isinstance(probe, dict) else []
            has_audio = any(isinstance(s, dict) and s.get("codec_type") == "audio"
                            for s in streams)
            has_video = any(
                isinstance(s, dict) and s.get("codec_type") == "video"
                and not (isinstance(s.get("disposition"), dict)
                         and s["disposition"].get("attached_pic"))
                for s in streams)
            return has_audio, has_video
        except Exception:  # noqa: BLE001 - stage detection is best effort
            return False, False

    def _recordings_root(self) -> Path:
        folder = str(self.settings_store.load().recordings_dir or "").strip()
        root = Path(folder).expanduser() if folder else Path.home() / "Videos" / "MPCASU"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _show_record_settings_dialog(self):
        """Dialog für Aufnahme: Speicherort, Format, Splitting an/aus."""
        settings = self.settings_store.load()
        dialog = QDialog(self)
        dialog.setWindowTitle("Recording settings")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Speicherort"))
        folder_entry = QLineEdit(str(settings.recordings_dir or ""))
        folder_entry.setObjectName("IconButton")
        folder_row.addWidget(folder_entry, 1)
        folder_btn = QPushButton("…")
        folder_btn.setObjectName("IconButton")
        folder_btn.clicked.connect(
            lambda: folder_entry.setText(QFileDialog.getExistingDirectory(
                dialog, "Aufnahmenordner",
                str(Path(settings.recordings_dir).expanduser())
                if settings.recordings_dir else str(Path.home() / "Videos"))))
        folder_row.addWidget(folder_btn)
        layout.addLayout(folder_row)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Format"))
        format_combo = QComboBox()
        format_combo.setObjectName("IconButton")
        for fmt in ("mkv", "mp4", "ts", "webm", "ogg", "mp3", "flac", "wav"):
            format_combo.addItem(fmt)
        format_combo.setCurrentText(str(settings.record_format))
        apply_dark_combo_popup(format_combo)
        format_row.addWidget(format_combo)
        format_row.addStretch()
        layout.addLayout(format_row)

        split_row = QHBoxLayout()
        split_row.addWidget(QLabel("Aufteilen"))
        split_mode = QComboBox()
        for label, value in (("Eine Datei", "continuous"), ("Nach Zeit", "time"),
                             ("Bei Trackwechsel", "track"),
                             ("Bei Titel-/Tagwechsel", "tags")):
            split_mode.addItem(label, value)
        split_mode.setCurrentIndex(max(0, split_mode.findData(settings.record_split_mode)))
        apply_dark_combo_popup(split_mode)
        split_row.addWidget(split_mode)
        split_spin = QSpinBox()
        split_spin.setObjectName("IconButton")
        split_spin.setRange(1, 24 * 60)
        split_spin.setSuffix(" min")
        split_spin.setValue(max(1, int(settings.record_split_minutes)))
        split_spin.setEnabled(settings.record_split_mode == "time")
        split_mode.currentIndexChanged.connect(
            lambda _i: split_spin.setEnabled(split_mode.currentData() == "time"))
        split_row.addWidget(split_spin)
        split_row.addStretch()
        layout.addLayout(split_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return
        updated = replace(
            settings,
            recordings_dir=folder_entry.text().strip(),
            record_format=str(format_combo.currentText()),
            record_split_minutes=split_spin.value(),
            record_split_mode=str(split_mode.currentData()),
        )
        self.settings_store.save(updated)
        self._record_split_minutes = updated.record_split_minutes
        self._record_split_mode = updated.record_split_mode
        self._record_format = updated.record_format
        self.toast("Recording settings gespeichert")
        self.status("Recording: Speicherort/Format/Splitting gespeichert")

    def _recording_source(self) -> str:
        if getattr(self, "_network_source", None):
            return str(self._network_source)
        if self.current is not None and self.current.is_file():
            if self.current.suffix.lower() in {".casu", ".mp5"}:
                raise RecordingError("CASU sources are stored already — use Export instead")
            return str(self.current)
        raise RecordingError("Open a local file or network stream first")

    def toggle_recording(self) -> None:
        if self._recorder is not None:
            self._record_timer.stop()
            self._finish_recording_async()
            return
        try:
            source = self._recording_source()
        except (RecordingError, OSError) as exc:
            self.toast(str(exc))
            return
        settings = self.settings_store.load()
        self._record_format = str(settings.record_format)
        self._record_split_minutes = int(settings.record_split_minutes)
        self._record_split_mode = str(settings.record_split_mode)
        self._record_part = 1
        self._record_stem = time.strftime("%Y%m%d-%H%M%S") + "-" + (
            self.current.stem if self.current else "stream")
        if not self._start_recording_part(source):
            return
        self._record_timer = QTimer(self)
        self._record_timer.setSingleShot(True)
        self._record_timer.timeout.connect(self._rotate_recording)
        if self._record_split_mode == "time" and self._record_split_minutes > 0:
            self._record_timer.start(self._record_split_minutes * 60 * 1000)
        self._record_btn.setProperty("on", "true")
        self._record_btn.style().unpolish(self._record_btn)
        self._record_btn.style().polish(self._record_btn)

    def _record_destination(self) -> Path:
        suffix = f".{self._record_format}"
        if self._record_split_mode != "continuous":
            return self._recordings_root() / (
                f"{self._record_stem}-part{self._record_part:03d}{suffix}")
        return self._recordings_root() / f"{self._record_stem}{suffix}"

    def _start_recording_part(self, source: str) -> bool:
        destination = self._record_destination()
        try:
            recorder = MediaRecorder(source, destination)
            recorder.start()
        except (RecordingError, OSError) as exc:
            self.toast(f"Record failed: {exc}")
            return False
        self._recorder = recorder
        self.toast(f"Recording · {destination.name}"
                   + (f" · split {self._record_split_mode}"
                      if self._record_split_mode != "continuous" else ""))
        return True

    def _rotate_recording(self) -> None:
        if self._recorder is None:
            return
        try:
            source = self._recording_source()
        except (RecordingError, OSError) as exc:
            self.toast(str(exc))
            return
        self._finish_recording_async(quiet=True, restart_source=source)

    def _recording_source_boundary(self, source: str) -> None:
        if (self._recorder is not None
                and self._record_split_mode in {"track", "tags"}):
            self._finish_recording_async(quiet=True, restart_source=source)

    def _recording_tag_boundary(self) -> None:
        if self._recorder is None or self._record_split_mode != "tags":
            return
        try:
            source = self._recording_source()
        except (RecordingError, OSError):
            return
        self._finish_recording_async(quiet=True, restart_source=source)

    def _finish_recording_async(self, quiet: bool = False,
                                restart_source: str | None = None) -> None:
        recorder = self._recorder
        if recorder is None or self._recording_finishing:
            return
        self._recording_finishing = True
        self._record_btn.setEnabled(False)
        self.toast("Finalizing and verifying recording…")

        def worker():
            try:
                result, error = recorder.finish(timeout=5), None
            except (RecordingError, OSError) as exc:
                result, error = None, exc

            def present():
                self._recorder = None
                self._recording_finishing = False
                self._record_btn.setEnabled(True)
                self._record_btn.setProperty("on", "false")
                self._record_btn.style().unpolish(self._record_btn)
                self._record_btn.style().polish(self._record_btn)
                if error is None:
                    if not quiet:
                        self.toast(f"Recording saved · {Path(result).name}")
                else:
                    self.toast(f"Recording failed: {error}")
                if restart_source is not None and error is None:
                    self._record_part += 1
                    if self._start_recording_part(restart_source):
                        if (self._record_split_mode == "time"
                                and self._record_split_minutes > 0):
                            self._record_timer.start(
                                self._record_split_minutes * 60 * 1000)
            QTimer.singleShot(0, present)
        threading.Thread(target=worker, daemon=True).start()

    def cycle_ab_loop(self) -> None:
        position = self.backend.position() if self.backend else 0.0
        if self._ab_a is None:
            self._ab_a = position
            self._ab_btn.setProperty("on", "true")
            self._ab_btn.style().unpolish(self._ab_btn)
            self._ab_btn.style().polish(self._ab_btn)
            self.toast(f"A point set at {position:.1f}s")
        elif self._ab_b is None:
            if position <= self._ab_a:
                self.toast("B point must be after A point")
                return
            self._ab_b = position
            self.toast(f"A–B loop active · {self._ab_a:.1f}s – {position:.1f}s")
        else:
            self._ab_a = self._ab_b = None
            self._ab_btn.setProperty("on", "false")
            self._ab_btn.style().unpolish(self._ab_btn)
            self._ab_btn.style().polish(self._ab_btn)
            self.toast("A–B loop off")

    def save_snapshot(self) -> None:
        backend = self.backend
        if backend is None or not hasattr(backend, "take_snapshot"):
            self.toast("Snapshot needs an active libVLC video")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = self._recordings_root() / f"snapshot-{stamp}.png"
        try:
            backend.take_snapshot(destination)
            self.toast(f"Snapshot saved · {destination.name}")
        except (BackendError, OSError) as exc:
            self.toast(f"Snapshot failed: {exc}")

    def toggle_visualizer(self) -> None:
        if not self._audio_stage:
            self.toast("Visualizer is for audio only (subtitles show for video)")
            return
        settings = self.settings_store.load()
        mode = "off" if settings.visualizer != "off" else "waveform"
        self.settings_store.save(replace(settings, visualizer=mode))
        self._viz_mode = mode
        self._viz_btn.setProperty("on", "true" if mode != "off" else "false")
        self._viz_btn.style().unpolish(self._viz_btn)
        self._viz_btn.style().polish(self._viz_btn)
        if mode == "off":
            self._stop_stream_viz()
            self._viz_timer.stop()
            self._visualizer.set_mode("off")
        elif self._network_source:
            self._visualizer.set_mode(mode)
            self._start_stream_viz(self._network_source)
        elif self.current is not None:
            if self._viz_pcm is not None and self._viz_rate > 0:
                # Already-decoded PCM is kept: toggling back on is instant.
                self._visualizer.set_mode(mode)
                self._viz_timer.start()
            else:
                self._load_visualizer(self.current)
        else:
            self._visualizer.set_mode(mode)
        self.toast(f"Visualizer: {mode}")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self._drop_overlay.setGeometry(self._video_surface.geometry())
            self._drop_overlay.show()
            self._drop_overlay.raise_()
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._drop_overlay.hide()
        targets = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            targets.append(local if local else url.toString())
        targets = [t for t in targets if t]
        if targets:
            self.add_files(targets)
            event.acceptProposedAction()

    def _start_stream_viz(self, source: str):
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            return
        self._stop_stream_viz()
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "quiet", "-i", source, "-map", "0:a:0",
             "-ac", "1", "-ar", "22050", "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._stream_viz_proc = proc
        bridge = self._viz_bridge

        def reader():
            import numpy as np
            import time as _time
            buff = b""
            last_emit = 0.0
            while True:
                try:
                    data = proc.stdout.read(1024)
                except (OSError, ValueError):
                    break
                if not data:
                    break
                buff += data
                if len(buff) < 4096:
                    continue
                # Throttle to a realtime cadence (~30 Hz): ffmpeg may decode
                # a fast source faster than realtime, which would flood the
                # UI thread and make the visualization lag.
                now = _time.perf_counter()
                if now - last_emit < 0.033:
                    _time.sleep(0.004)
                    continue
                last_emit = now
                try:
                    # Wave only: bound work to 128 visible points and avoid FFT.
                    buff = buff[-4096:]
                    samples = (np.frombuffer(buff, dtype="<i2")
                               .astype(np.float32) / 32768.0)
                    width = max(1, len(samples) // 128)
                    wave = tuple(float(samples[i])
                                 for i in range(0, len(samples), width))[:128]
                    bridge.resultReady.emit(("live", wave))
                except Exception:  # noqa: BLE001 - stream viz is optional
                    continue
        import threading
        self._stream_viz_thread = threading.Thread(target=reader, daemon=True)
        self._stream_viz_thread.start()

    def _stop_stream_viz(self):
        proc = getattr(self, "_stream_viz_proc", None)
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
            self._stream_viz_proc = None
        self._visualizer.clear_live()

    def _epg_now_next(self) -> str:
        if self._epg_catalog is None and self._epg_guide is None:
            return "no EPG loaded"
        url = str(self._network_source or self.current or "")
        if self._epg_catalog is not None:
            channel = next((c for c in self._epg_catalog.channels
                            if getattr(c, "url", "") == url), None)
            if channel is not None:
                if self._epg_guide is not None:
                    try:
                        programmes = self._epg_guide.for_channel(
                            getattr(channel, "tvg_id", "") or channel.name)
                        now = next((p for p in programmes
                                    if getattr(p, "current", False)), None)
                        if now is not None:
                            return f"{channel.name} · now: {now.title}"
                    except Exception:  # noqa: BLE001 - guide lookup is best effort
                        pass
                return str(channel.name)
        return "EPG loaded"

    def _options_action(self, action: str):
        if action == "clear-cache":
            import shutil, tempfile
            cache_dir = Path(tempfile.gettempdir()) / "yt-dlp"
            if cache_dir.is_dir():
                shutil.rmtree(cache_dir, ignore_errors=True)
                self.toast(f"Cleared {cache_dir}")
            else:
                self.toast("No yt-dlp cache found")
        elif action == "refresh-db":
            self.refresh_watched_folders()

    def _on_epg_channel(self, channel):
        url = getattr(channel, "url", None) or ""
        name = getattr(channel, "name", str(channel))
        if url:
            self._resolve_and_open_external_source(url, display_label=name)

    def _on_source_activated(self, payload):
        if isinstance(payload, str):
            if is_youtube_url(payload):
                self._queue_and_play(payload)
                self._tag_queue_title(payload)
                return
            # Plain stream URLs are queued like files, so they combine with
            # playlists in one mixed queue and can be saved/merged.
            try:
                self.playlist_model.add((payload,))
                self._render_playlist()
            except Exception:  # noqa: BLE001 - queue must never block playback
                pass
            self._resolve_and_open_external_source(payload)
            return
        if getattr(payload, "source", None) == "spotify" and is_spotify_url(payload.url):
            self._open_web_player("spotify", url=payload.url)
            return
        if is_youtube_url(payload.url):
            self._queue_and_play(payload.url, label=payload.title)
            return
        self._resolve_and_open_external_source(payload.url,
                                               display_label=payload.title)

    def _on_queue_items_requested(self, found):
        """Add several individual YouTube videos (expanded from playlists
        and/or several pasted URLs) to the queue and start the first one.

        Each video becomes its own queue entry, so shuffle/repeat and the
        normal Next/Previous controls act per-video (Windows/Linux parity).
        """
        if not found:
            return
        urls: list[str] = []
        labels: dict = {}
        for item in found:
            url = str(getattr(item, "url", "") or "").strip()
            if not url:
                continue
            title = str(getattr(item, "title", "") or "").strip()
            if title and title != url:
                labels[url] = title
            urls.append(url)
        if not urls:
            return
        self._playlist_pane._display_titles.update(labels)
        try:
            self.playlist_model.add(urls)
            self._invalidate_play_seq()
            self._render_playlist()
            first_row = self.playlist_model.index_of(urls[0])
            if first_row is not None:
                self._playlist_pane.select_row(first_row)
        except Exception:  # noqa: BLE001 - queue must never block playback
            pass
        first = urls[0]
        self._show_player_page()
        self._play_youtube(first, label=labels.get(first, first))
        for url in urls:
            self._tag_queue_title(url)

    def _queue_and_play(self, url: str, *, label: str = ""):
        """Add a YouTube source to the queue (with a display tag) and play it.

        Playback streams via the shared yt-dlp resolver + loopback transport
        into the normal libVLC pipeline (no download, no browser).
        """
        if label:
            self._playlist_pane._display_titles[url] = label
        try:
            self.playlist_model.add((url,))
            self._render_playlist()
        except Exception:  # noqa: BLE001 - queue must never block playback
            pass
        self._play_youtube(url, label=label or url)

    def _play_youtube(self, url: str, *, label: str = ""):
        """Resolve YouTube with the shared web-casu resolver and stream via libVLC.

        Exactly one resolver exists (casu.locations.resolve_media_location);
        the loopback proxy is transport only. The proxy is started on the GUI
        thread AFTER the previous session is fully stopped, so playback
        cleanup can never destroy it before libVLC opens it.
        """
        self.stop()
        self._show_player_page()
        self.status("Resolving YouTube stream (yt-dlp)…")
        self._now_playing_bar.set_now_playing(label or "YouTube")
        self._yt_source_url = url
        self._resolve_generation += 1
        generation = self._resolve_generation

        def worker():
            try:
                resolved = resolve_media_location(url)
            except (LocationResolutionError, OSError, ValueError) as exc:
                self._resolve_bridge.errorReady.emit((generation, str(exc)))
                return
            self._resolve_bridge.resultReady.emit(
                (generation, ("youtube_proxy", resolved, url), label or url)
            )

        threading.Thread(target=worker, daemon=True).start()


    def _tag_queue_title(self, url: str):
        """Fetch the YouTube title in the background and update queue + NOW PLAYING."""

        def worker():
            try:
                import subprocess as _sp
                proc = _sp.run(
                    ["yt-dlp", "--no-warnings", "--no-playlist", "--skip-download",
                     "--print", "%(title)s", url],
                    capture_output=True, text=True, timeout=25)
                title = (proc.stdout or "").strip()
                if not title or proc.returncode != 0:
                    return
                self._title_bridge.resultReady.emit((url, title))
            except Exception:  # noqa: BLE001 - titles are cosmetic
                return

        threading.Thread(target=worker, daemon=True).start()

    def _apply_queue_title(self, payload):
        url, title = payload
        pane = self._playlist_pane
        pane._display_titles[url] = title
        self._render_playlist()
        if (str(getattr(self, "_network_source", "") or "") == url
                or url == getattr(self, "_yt_source_url", "")):
            self._now_playing_bar.set_now_playing(title)
            self._set_caption(title)
            self._recording_tag_boundary()

    def _resolve_spotify_playback(self, url: str, *, title: str = "",
                                  artist: str = "", display_label: str = ""):
        self._resolve_generation += 1
        generation = self._resolve_generation
        self.status("Resolving Spotify track via spotDL…")

        def worker():
            try:
                from casu.spotify import download_spotify_track
                local = download_spotify_track(title, artist)
            except (SpotifyError, OSError, ValueError) as exc:
                self._resolve_bridge.errorReady.emit((generation, str(exc)))
                return
            self._resolve_bridge.resultReady.emit(
                (generation, str(local), display_label or url))
        threading.Thread(target=worker, daemon=True).start()

    def _resolve_and_open_external_source(self, source: str, *,
                                          display_label: str | None = None):
        """Resolve web sources off the GUI thread, then hand a direct URL to libVLC."""
        from casu.webproviders import provider_for_url
        provider = provider_for_url(str(source))
        if provider:
            self._open_web_player(provider, url=str(source))
            return
        if is_youtube_url(source):
            if not self.settings_store.load().ytdlp_consent:
                self.show_sources("youtube")
                self.status("YouTube playback requires yt-dlp consent — accept in the view above")
                return
            # Same streaming path as every YouTube source: shared resolver +
            # loopback transport + libVLC. Never a download, never a browser.
            self._play_youtube(source, label=display_label or source)
            return
        self._resolve_generation += 1
        generation = self._resolve_generation
        self.status("Resolving network media…")

        def worker():
            try:
                resolved = resolve_media_location(source)
            except (LocationResolutionError, SpotifyError, OSError,
                    ValueError, CasuError) as exc:
                self._resolve_bridge.errorReady.emit((generation, str(exc)))
                return
            self._resolve_bridge.resultReady.emit(
                (generation, resolved, display_label or source))
        threading.Thread(target=worker, daemon=True).start()

    def _on_resolve_ready(self, payload):
        generation, resolved, label = payload
        if generation != self._resolve_generation:
            return
        if isinstance(resolved, tuple) and resolved and resolved[0] == "youtube_proxy":
            _, direct_url, source_url = resolved
            # Transport only: the loopback media URL goes into the normal
            # LibVLCBackend/PlaybackController pipeline. The previous session
            # (and its proxy) was already stopped in _play_youtube; the NEW
            # proxy is started here and must survive until the source stops.
            self.stop()
            try:
                media_url = self._yt_stream.start(
                    direct_url,
                    refresh=lambda u=source_url: resolve_media_location(u),
                )
            except (YouTubeProxyError, OSError, ValueError) as exc:
                self.status(f"YouTube stream unavailable: {exc}")
                self.toast(f"YouTube stream unavailable: {exc}")
                return
            self._open_external_source(
                media_url, display_label=label, youtube=True, preserve_proxy=True)
            return
        self._open_external_source(resolved, display_label=label)

    def _on_resolve_failed(self, payload):
        generation, detail = payload
        if generation != self._resolve_generation:
            return
        self.status(f"Could not resolve network source: {detail}")
        self.toast(f"Could not resolve network source: {detail}")

    def _play_network_source(self, text: str):
        from casu.webproviders import provider_for_url
        provider = provider_for_url(text)
        if provider:
            self._open_web_player(provider, url=str(text))
            return
        if is_youtube_url(text):
            label = self._playlist_pane._display_titles.get(text, "")
            self._play_youtube(text, label=label or text)
            return
        self._open_external_source(text)

    def _open_external_source(self, source: str, *, display_label: str | None = None,
                             youtube: bool = False, preserve_proxy: bool = False):
        from casu.webproviders import provider_for_url
        provider = provider_for_url(str(source))
        if provider:
            self._open_web_player(provider, url=str(source))
            return
        # Keep the queue selection on the stream's row (if it is queued) so
        # Next/Previous advance through the mixed queue in order.
        index = self.playlist_model.index_of(source)
        if index is not None:
            self._playlist_pane.select_row(index)
        self._show_player_page()
        self._recording_source_boundary(str(source))
        if preserve_proxy:
            # The loopback transport for THIS source is already running and
            # must survive the teardown of the previous session.
            self.stop(stop_youtube=False)
        else:
            self.stop()
        self._end_handled = False
        self.current = None
        visible_source = display_media_source(display_label or source)
        self._now_playing_bar.set_now_playing(visible_source)
        if youtube:
            # Videos: libVLC owns the native VideoSurface exclusively; no Qt
            # caption/badge/empty-hint overlays over the picture.
            self._caption_label.hide()
            self._badges_label.hide()
            self._empty_hint.hide()
        else:
            self._set_caption(visible_source)
        try:
            self.backend = LibVLCBackend(self._video_surface.handle)
            self.backend.on_event = self._backend_event
            self.backend.open_source(source)
            self.controller.attach(self.backend, visible_source)
            self.controller.play()
            self._apply_playback_rate()
            self._apply_backend_settings()
            self._diagnostics_bar.set_values(
                support="Legacy network backend", integrity="unavailable",
                segmented="unavailable",
            )
            self.duration = self.backend.duration()
            self._seek_slider.set_duration(self.duration)
            self._draw_chapter_markers()
            capabilities = self.backend.capabilities()
            self.status(f"Playing network source · {capabilities.get('version', 'libVLC')} · timing owned by libVLC")
            self._video_surface.set_video_active(True)
            if Path(str(source)).is_file():
                self._network_source = None
                self._temp_media = Path(str(source))
            else:
                self._network_source = source
            mode = str(self.settings_store.load().visualizer)
            self._viz_mode = mode
            self._viz_overview = ()
            self._visualizer.set_cover(None)
            self._visualizer.configure(
                mode,
                (),
                (),
                self.duration or 0.0,
            )
            if youtube or is_youtube_url(str(source)):
                # Video mode: the visualizer must not cover the video and no
                # Qt overlay may paint over the native libVLC surface.
                self._audio_stage = False
                self._visualizer.setVisible(False)
                self._reposition_overlays()
            elif mode != "off" and str(source).startswith(("http://", "https://")):
                self._start_stream_viz(source)
            if not youtube:
                # YouTube is always video; probing the loopback URL while
                # libVLC streams can misclassify the stage and flip Qt
                # overlays over the native video surface.
                self._probe_stage(source)
            generation = self._viz_generation
            cover_source = str(source)

            def cover_worker():
                cover = self._cover_for(cover_source)
                if cover:
                    self._viz_bridge.resultReady.emit(
                        ("cover", generation, cover))

            threading.Thread(target=cover_worker, daemon=True).start()
        except (BackendError, OSError) as exc:
            self.controller.close()
            self.backend = None
            self.status(f"Could not open network source: {exc}")
            self.toast(f"Could not open network source: {exc}")

    def _render_playlist(self, selected: int = -1):
        self._playlist_pane.populate(list(self.playlist_model.items), selected)

    def _play_playlist_row(self, row: int):
        self._show_player_page()
        self._playlist_pane.select_row(row)
        # If the selected row is a playlist group, "Play" must start from the
        # FIRST track of the playlist (its first child), not try to play the
        # playlist file itself as media.
        try:
            item = self.playlist_model.item(row)
        except PlaylistError:
            self.play_selected()
            return
        if not isinstance(item, str) and item.suffix.lower() in PlaylistPane.PLAYLIST_SUFFIXES:
            entries = self._playlist_entries(item)
            if not entries:
                self.toast("Playlist is empty")
                return
            first = entries[0]
            if isinstance(first, str):
                self._play_playlist_entry(first)
                return
            # Start from the FIRST track of the playlist. The file may not be
            # a top-level queue row (it is covered by the playlist group), so
            # play it directly and highlight it inside the group.
            self._playlist_pane.select_child(item, first)
            self.play_selected(first)
            return
        self.play_selected()

    def _play_playlist_full(self, playlist: Path):
        """Play the whole playlist: its group row stays in the queue, the
        playback sequence walks through all its entries (then continues with
        whatever follows in the queue). The expanded/collapsed UI state never
        matters — and the playlist never disappears from the display."""
        entries = self._playlist_entries(playlist)
        if not entries:
            return
        seq = self._ensure_play_seq()
        row = self.playlist_model.index_of(playlist)
        pos = self._row_to_seq(row) if row is not None else None
        if pos is None or pos >= len(seq):
            pos = 0
        self._play_playlist_entry(seq[pos])

    def _playback_sequence(self) -> list:
        """Logical playback order over the queue: each playlist group
        contributes its entries (in file order) at the group's position,
        every other row is itself. The queue model is NEVER modified here —
        playlists stay visible as groups."""
        seq: list[str] = []
        for idx in range(len(self.playlist_model)):
            try:
                item = self.playlist_model.item(idx)
            except PlaylistError:
                continue
            if isinstance(item, str):
                seq.append(str(item))
                continue
            if item.suffix.lower() in PlaylistPane.PLAYLIST_SUFFIXES:
                try:
                    loaded = load_playlist_file(item)
                except (PlaylistError, OSError, ValueError):
                    continue
                seq.extend(str(entry) for entry in loaded.items)
            else:
                seq.append(str(item))
        return seq

    def _ensure_play_seq(self) -> list:
        if self._play_seq is None:
            self._play_seq = self._playback_sequence()
        return self._play_seq

    def _invalidate_play_seq(self):
        self._play_seq = None

    def _row_to_seq(self, row: int) -> int | None:
        """First position in the logical playback sequence that the top-level
        queue row ``row`` contributes (a playlist group's first entry), or
        None when the row does not exist."""
        if row is None or row < 0:
            return None
        pos = 0
        for idx in range(len(self.playlist_model)):
            if idx == row:
                return pos
            try:
                item = self.playlist_model.item(idx)
            except PlaylistError:
                continue
            if isinstance(item, str):
                pos += 1
            elif item.suffix.lower() in PlaylistPane.PLAYLIST_SUFFIXES:
                try:
                    pos += len(load_playlist_file(item).items)
                except (PlaylistError, OSError, ValueError):
                    pass
            else:
                pos += 1
        return None

    def _playlist_entries(self, playlist: Path) -> list:
        try:
            from casu.playlist import load_playlist_file
            return list(load_playlist_file(playlist).items)
        except (PlaylistError, OSError, ValueError) as exc:
            self.toast(f"Could not read playlist: {exc}")
            return []

    def _containing_playlist(self, entry) -> Path | None:
        """Return the playlist group (a .m3u/.pls/… row in the queue) whose
        entries contain ``entry``, or None. Independent of the UI expand
        state: the entries are read from the playlist files directly."""
        want = str(entry)
        for idx in range(len(self.playlist_model)):
            try:
                item = self.playlist_model.item(idx)
            except PlaylistError:
                continue
            if isinstance(item, str):
                continue
            if item.suffix.lower() not in PlaylistPane.PLAYLIST_SUFFIXES:
                continue
            try:
                loaded = load_playlist_file(item)
            except (PlaylistError, OSError, ValueError):
                continue
            if any(str(e) == want for e in loaded.items):
                return item
        return None

    def _play_playlist_entry(self, entry):
        # Highlight what is playing: the row itself, or the child inside its
        # (still visible) playlist group. The queue model is never modified.
        index = self.playlist_model.index_of(entry)
        if index is not None:
            self._playlist_pane.select_row(index)
        else:
            playlist = self._containing_playlist(entry)
            if playlist is not None:
                self._playlist_pane.select_child(playlist, entry)
        # Play one playlist entry (stream or local file) using the same path
        # as clicking a playlist child.
        if isinstance(entry, str) and entry.startswith(("http://", "https://",
                                                        "rtsp://", "rtmp://",
                                                        "udp://", "rtp://",
                                                        "ftp://", "smb://")):
            self._resolve_and_open_external_source(entry)
            return
        path = Path(entry)
        if not path.is_file():
            self.toast(f"Local file not found: {path.name}")
            return
        self.play_selected(path)

    def _on_playlist_remove(self, indices):
        if not indices:
            if self.backend:
                self.stop()
            self.playlist_model.clear()
            self._invalidate_play_seq()
            self._render_playlist()
            self.current = None
            self._now_playing_bar.set_now_playing("")
            self._set_caption("")
            self.status("Playlist cleared")
            return
        items = self.playlist_model.items
        before = set(self._playlist_pane.selected_rows())
        try:
            self.playlist_model.remove(indices)
        except PlaylistError as exc:
            self.status(str(exc))
            return
        self._invalidate_play_seq()
        self._render_playlist()
        removed = {int(i) for i in indices if 0 <= i < len(items)}
        keep = {str(items[i]) for i in (before - removed)}
        if keep:
            new_indices = [i for i, path in enumerate(self.playlist_model.items)
                           if str(path) in keep]
            self._playlist_pane.select_rows(new_indices)

    def _on_queue_favorite(self, indices):
        for idx in indices:
            try:
                item = self.playlist_model.item(idx)
                if item and Path(item).is_file():
                    lib_item = self.media_library.get(str(item))
                    current = bool(lib_item.favorite) if lib_item else False
                    self.media_library.set_favorite(str(item), not current)
            except Exception:
                pass
        self.toast("★ Favorites updated")

    def _on_playlist_move(self, delta: int, indices: list):
        if not indices:
            return
        items = self.playlist_model.items
        saved = [items[i] for i in sorted({int(i) for i in indices})
                 if 0 <= i < len(items)]
        try:
            target = self.playlist_model.move_many(indices, delta)
        except PlaylistError as exc:
            self.status(str(exc))
            return
        self._invalidate_play_seq()
        self._render_playlist()
        if saved:
            want = {str(path) for path in saved}
            new_indices = [i for i, path in enumerate(self.playlist_model.items)
                           if str(path) in want]
            self._playlist_pane.select_rows(new_indices)

    def _on_playlist_merge(self, rows: list):
        """Merge/append selected queue rows (media files / URLs) into a playlist.

        - Single or multi-selection is supported (mark several items, then
          'Save N items to playlist…').
        - Playlist children (the media inside a playlist) are accepted too:
          the context menu sends their URLs instead of row indices.
        - A whole playlist group (another .m3u/.pls/… in the queue) is
          resolved into its entries, so entire playlists can be merged.
        - Choose an existing playlist to extend (merge) or create a new one.
        - The selected entries are appended to the playlist and saved.
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        if not rows:
            return
        # Resolve the selected rows into media/URL entries. Rows are either
        # queue indices (int) or direct entries (str, e.g. playlist children).
        entries: list[str] = []
        for row in rows:
            if isinstance(row, int):
                try:
                    item = self.playlist_model.item(row)
                except PlaylistError:
                    continue
                text = str(item)
            else:
                text = str(row)
            # A playlist group itself is resolved into its entries, so merging
            # whole playlists works: every track of the selected playlist is
            # appended (deduplicated) to the target playlist. Remote URLs with
            # a playlist-like suffix are stream entries, not groups.
            if self._playlist_pane._is_playlist(text) and Path(text).is_file():
                try:
                    loaded = load_playlist_file(text)
                except (PlaylistError, OSError, ValueError) as exc:
                    self.toast(f"Could not read playlist {text}: {exc}")
                    continue
                for item in loaded.items:
                    entries.append(str(item))
                continue
            entries.append(text)
        if not entries:
            self.toast("Nothing to merge: no playable media/URL selected.")
            return

        # Collect existing playlists already in the queue (their .m3u/.pls/...
        # files), so the user can extend one of them.
        playlists = self._queue_playlists()
        target = self._choose_playlist_target(
            playlists, title="Merge into playlist",
            label="Choose a playlist to append the selected items to, or create a new one:")
        if target is None:
            return

        # Merge: load existing, append new entries (deduplicated), save.
        try:
            model = load_playlist_file(target)
        except (PlaylistError, OSError, ValueError):
            model = PlaylistModel()
        added = 0
        for entry in entries:
            before = len(model.items)
            model.add((entry,))
            if len(model.items) > before:
                added += 1
        try:
            save_playlist_file(target, model)
        except (PlaylistError, OSError) as exc:
            self.toast(f"Could not save playlist: {exc}")
            return
        self.toast(f"Added {added} item(s) to {target.name}")
        self.status(f"Playlist updated · {target.name}")
        # The target playlist file changed: the logical playback sequence
        # must reflect the new contents next time it is used.
        self._invalidate_play_seq()
        # Refresh the playlist group in the queue if it is already present;
        # keep the selection (order/markings stay visible after the merge).
        sel = rows[0] if isinstance(rows[0], int) else -1
        self._render_playlist(sel)

    def _queue_playlists(self) -> list:
        """Playlist files (groups) currently present in the queue."""
        playlists: list[Path] = []
        for idx in range(len(self.playlist_model)):
            try:
                item = self.playlist_model.item(idx)
            except PlaylistError:
                continue
            if not isinstance(item, str) and item.suffix.lower() in PlaylistPane.PLAYLIST_SUFFIXES:
                playlists.append(item)
        return playlists

    def _choose_playlist_target(self, playlists: list, *, title: str,
                                label: str) -> Path | None:
        """Dialog to pick an existing queue playlist or create a new one."""
        from PySide6.QtWidgets import QInputDialog
        choices = ["<Create new playlist>"] + [str(p) for p in playlists]
        if len(choices) == 1:
            target_name, ok = QInputDialog.getText(
                self, "New playlist", "Playlist name (e.g. mylist.m3u):")
            if not ok or not target_name.strip():
                return None
            return self._resolve_playlist_target(target_name.strip())
        choice, ok = QInputDialog.getItem(
            self, title, label, choices, 0, False)
        if not ok:
            return None
        if choice == "<Create new playlist>":
            target_name, ok2 = QInputDialog.getText(
                self, "New playlist", "Playlist name (e.g. mylist.m3u):")
            if not ok2 or not target_name.strip():
                return None
            return self._resolve_playlist_target(target_name.strip())
        return Path(choice)

    def _on_child_remove_from_playlist(self, entries: list):
        """'Remove from playlist': take the selected children OUT of their
        playlist file. The playlist group stays visible in the queue."""
        if not entries:
            return
        removed_total = 0
        touched: set = set()
        for entry in entries:
            playlist = self._containing_playlist(entry)
            if playlist is None:
                continue
            touched.add(str(playlist))
            try:
                model = load_playlist_file(playlist)
            except (PlaylistError, OSError, ValueError):
                continue
            indices = [i for i in range(len(model.items))
                       if str(model.items[i]) == str(entry)]
            if not indices:
                continue
            try:
                model.remove(indices)
                save_playlist_file(playlist, model)
            except (PlaylistError, OSError) as exc:
                self.toast(f"Could not update {playlist.name}: {exc}")
                continue
            removed_total += len(indices)
        for path in touched:
            self._playlist_pane.refresh_group(Path(path))
        self._invalidate_play_seq()
        if removed_total:
            self.toast(f"Removed {removed_total} item(s) from playlist")
            self.status(f"Playlist updated · {removed_total} item(s) removed")

    def _on_child_move_to_playlist(self, entries: list):
        """'Move to playlist': take the selected children OUT of their source
        playlist file and append them to a target playlist (choose or create).
        Both playlists stay visible in the queue."""
        if not entries:
            return
        target = self._choose_playlist_target(
            self._queue_playlists(), title="Move to playlist",
            label="Choose a playlist to move the selected items to, or create a new one:")
        if target is None:
            return
        removed_total = 0
        touched: set = set()
        for entry in entries:
            playlist = self._containing_playlist(entry)
            if playlist is None or str(playlist) == str(target):
                continue
            touched.add(str(playlist))
            try:
                model = load_playlist_file(playlist)
            except (PlaylistError, OSError, ValueError):
                continue
            indices = [i for i in range(len(model.items))
                       if str(model.items[i]) == str(entry)]
            if not indices:
                continue
            try:
                model.remove(indices)
                save_playlist_file(playlist, model)
            except (PlaylistError, OSError) as exc:
                self.toast(f"Could not update {playlist.name}: {exc}")
                continue
            removed_total += len(indices)
        try:
            model = load_playlist_file(target)
        except (PlaylistError, OSError, ValueError):
            model = PlaylistModel()
        added = 0
        for entry in entries:
            before = len(model.items)
            model.add((entry,))
            if len(model.items) > before:
                added += 1
        try:
            save_playlist_file(target, model)
        except (PlaylistError, OSError) as exc:
            self.toast(f"Could not save {target.name}: {exc}")
            return
        for path in touched:
            self._playlist_pane.refresh_group(Path(path))
        self._invalidate_play_seq()
        self.toast(f"Moved {added} item(s) to {target.name}")
        self.status(f"Playlist updated · {target.name}")

    def _resolve_playlist_target(self, name: str) -> Path:
        """Ensure the playlist name has a supported extension and resolve it
        relative to a sensible location (home dir)."""
        from pathlib import Path as _P
        name = name.strip()
        if not name:
            raise ValueError("empty playlist name")
        p = _P(name).expanduser()
        if not p.suffix or p.suffix.lower() not in PlaylistPane.PLAYLIST_SUFFIXES:
            p = p.with_suffix(".m3u")
        if not p.is_absolute():
            p = _P.home() / p
        return p

    def remove_selected(self):
        selected = self._selected_playlist_row()
        if selected < 0:
            return
        try:
            self.playlist_model.remove([selected])
        except PlaylistError as exc:
            self.status(str(exc))
            return
        self._invalidate_play_seq()
        self._render_playlist()

    def save_playlist(self):
        from PySide6.QtWidgets import QFileDialog
        if not len(self.playlist_model):
            self.toast("The queue is empty — nothing to save")
            return
        dialog = QFileDialog(self, "Save playlist")
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        filters = [
            ("M3U playlist", "*.m3u", ".m3u"),
            ("PLS playlist", "*.pls", ".pls"),
            ("XSPF playlist", "*.xspf", ".xspf"),
            ("MPCASU JSON", "*.json", ".json"),
        ]
        for label, pattern, _suffix in filters:
            dialog.setNameFilter(f"{label} ({pattern})")
        dialog.selectNameFilter("M3U playlist (*.m3u)")
        if not dialog.exec():
            return
        target = Path(dialog.selectedFiles()[0])
        if not target.suffix:
            chosen = dialog.selectedNameFilter()
            for label, _pattern, suffix in filters:
                if label in chosen:
                    target = target.with_suffix(suffix)
                    break
            else:
                target = target.with_suffix(".m3u")
        # Save the queue as one flat playlist: playlist groups inside the
        # queue are resolved into their entries so the saved file contains
        # real media/URLs, never references to other playlist files.
        flat = PlaylistModel()
        for idx in range(len(self.playlist_model)):
            try:
                item = self.playlist_model.item(idx)
            except PlaylistError:
                continue
            if not isinstance(item, str) and item.suffix.lower() in PlaylistPane.PLAYLIST_SUFFIXES:
                try:
                    flat.add(load_playlist_file(item).items)
                    continue
                except (PlaylistError, OSError, ValueError):
                    pass
            flat.add((item,))
        try:
            saved = save_playlist_file(target, flat)
        except (PlaylistError, OSError) as exc:
            self.toast(f"Could not save playlist: {exc}")
            return
        self.status(f"Playlist saved · {saved.name}")
        self.toast(f"Playlist saved · {saved}")

    def load_playlist(self):
        from PySide6.QtWidgets import QFileDialog
        dialog = QFileDialog(self, "Load playlist")
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilter(
            "Playlists (*.m3u *.m3u8 *.pls *.json *.wpl *.xspf *.jspf *.asx *.wmx *.wvx *.rmp *.ram);;"
            "M3U / M3U8 (*.m3u *.m3u8);;PLS (*.pls);;XSPF (*.xspf);;WPL (*.wpl);;"
            "ASX (*.asx *.wmx *.wvx);;RealMedia (*.rmp *.ram);;MPCASU JSON (*.json);;All files (*.*)")
        if not dialog.exec():
            return
        source = Path(dialog.selectedFiles()[0])
        if not source.is_file():
            self.toast("Could not load playlist: file not found")
            return
        try:
            loaded = load_playlist_file(source)
            added = self.playlist_model.add(list(loaded.items))
        except (PlaylistError, OSError, ValueError) as exc:
            self.toast(f"Could not load playlist: {exc}")
            return
        self._invalidate_play_seq()
        self._render_playlist()
        self.status(f"Playlist loaded · {source.name} · {added} item(s) added")
        self.toast(f"Playlist loaded · {added} item(s) added")

    def _apply_queue_order(self, order: list):
        values = [value for value in order if value]
        if len(values) != len(self.playlist_model):
            return
        try:
            self.playlist_model = PlaylistModel.from_payload(
                {"version": 1, "items": [str(value) for value in values]})
        except PlaylistError:
            return
        self._invalidate_play_seq()
        if self.current is not None:
            index = self.playlist_model.index_of(self.current)
            if index is not None:
                self._playlist_pane.select_row(index)
        self.status("Queue reordered")

    def _on_queue_child_play(self, source: str):
        # Playing a child of an expandable playlist group plays exactly that
        # entry; the group stays in the queue (visible), playback continues
        # through the logical sequence afterwards. The expanded/collapsed UI
        # state never changes playback.
        self._play_playlist_entry(source)

    def add_watched_folder(self):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "Select folder to watch")
        if not folder:
            return
        folder = str(Path(folder).expanduser().resolve())
        if folder not in self._watched_folders:
            self._watched_folders.append(folder)
        try:
            scanned = self.media_library.scan([folder])
            self._save_effective_settings()
            self.status(f"Library scan complete · {len(scanned)} file(s) seen")
        except (OSError, ValueError) as exc:
            self.status(f"Library scan failed: {exc}")
        self.show_library_dialog()

    def refresh_watched_folders(self):
        folders = list(self.settings_store.load().watched_folders)
        self._watched_folders = folders
        if not folders:
            self.status("No watched folders configured")
            return
        self.status(f"Scanning {len(folders)} folder(s) with all subfolders…")
        self.toast("Scanning library folders…")

        def worker():
            try:
                scanned = self.media_library.scan(folders)
                count = len(scanned)
                error = ""
            except Exception as exc:  # noqa: BLE001 - surface scan failures
                count = -1
                error = f"{type(exc).__name__}: {exc}"
            self._scan_bridge.resultReady.emit((count, error))

        threading.Thread(target=worker, daemon=True).start()

    def _scan_done(self, payload):
        count, error = payload
        if count >= 0:
            self.status(f"Library refreshed · {count} file(s) indexed")
            self.toast(f"Library refreshed · {count} file(s)")
        else:
            self.status(f"Library refresh failed: {error}")
        try:
            self._library_page._refresh()
        except Exception:  # noqa: BLE001 - page refresh is optional
            pass

    def show_library_dialog(self):
        self._library_page._refresh()
        self._show_page(self._library_page, "LIBRARY")
        self._sidebar.set_active("LIBRARY")

    def _show_page(self, page, title: str):
        if page not in self._pages:
            self._center_stack.addWidget(page)
            self._pages.append(page)
        self._center_stack.setCurrentWidget(page)
        if hasattr(self, "_topbar_title"):
            self._topbar_title.setText(title)
        if hasattr(self, "_back_btn"):
            self._back_btn.show()

    def show_media_info(self):
        path = self.current or self.selected_path()
        if not path or not path.is_file():
            self.status("No local media selected for information")
            return
        try:
            native = False
            native_v2 = False
            if path.suffix.lower() == ".casu":
                with path.open("rb") as handle:
                    magic = handle.read(8)
                    native = magic == b"CASUNAT1"
                    native_v2 = magic == b"CASUNAT2"

            if native_v2:
                container = read_native_v2(path)
                manifest = container.manifest
                source = path
                streams = []
                for item in manifest.get("streams", []):
                    stream = dict(item)
                    stream["codec_type"] = stream.get("type")
                    stream["codec_name"] = "casu-" + str(stream.get("type", "data"))
                    streams.append(stream)
                probe = {
                    "streams": streams,
                    "format": {
                        "format_name": "CASUNAT2 segmented media",
                        "duration": self.backend.duration() if isinstance(self.backend, NativeCasuBackend) else "unknown",
                        "size": path.stat().st_size,
                        "tags": manifest.get("metadata", {}),
                    },
                }
            elif native:
                manifest = read_native(path, verify_payload=True).manifest
                source = path
                probe = {
                    "streams": manifest.get("streams", []),
                    "format": {
                        "format_name": "CASU native container",
                        "duration": manifest.get("source", {}).get("duration_s", "unknown"),
                        "size": path.stat().st_size,
                    },
                }
            else:
                source = self._source_for(path)
                probe = ffprobe(source)

            lines = [
                f"File: {path.name}",
                f"Source: {source.name}",
                f"Container: {probe.get('format', {}).get('format_name', 'unknown')}",
                f"Duration: {probe.get('format', {}).get('duration', 'unknown')} s",
                f"Size: {probe.get('format', {}).get('size', 'unknown')} bytes",
            ]
            metadata = probe.get("format", {}).get("tags", {})
            if isinstance(metadata, dict):
                for key in ("title", "artist", "album", "album_artist", "date", "genre"):
                    value = metadata.get(key)
                    if value not in (None, ""):
                        lines.append(f"{key.replace('_', ' ').title()}: {value}")
            if path.suffix.lower() == ".casu":
                lines.extend([
                    "CASU: verified native CASUNAT2" if native_v2 else
                    "CASU: verified CASUNAT1 compatibility envelope" if native else
                    "CASU: validated envelope manifest",
                    f"Segment hints: {len(self._visual_segments)}",
                ])
            for index, stream in enumerate(probe.get("streams", [])):
                details = [
                    f"stream {index}: {stream.get('codec_type', 'unknown')}",
                    str(stream.get('codec_name', 'unknown')),
                ]
                if stream.get("tags", {}).get("language"):
                    details.append(f"language={stream['tags']['language']}")
                if stream.get("width") and stream.get("height"):
                    details.append(f"{stream['width']}×{stream['height']}")
                if stream.get("sample_rate"):
                    details.append(f"{stream['sample_rate']} Hz")
                if stream.get("channels"):
                    details.append(f"{stream['channels']} channels")
                if stream.get("avg_frame_rate") and stream.get("avg_frame_rate") != "0/0":
                    details.append(f"fps={stream['avg_frame_rate']}")
                lines.append(" · ".join(details))

            dlg = QDialog(self)
            dlg.setWindowTitle("Media information")
            dlg.setMinimumSize(600, 400)
            dlg.setStyleSheet(stylesheet())
            layout = QVBoxLayout(dlg)
            browser = QTextBrowser()
            browser.setPlainText("\n".join(lines))
            browser.setObjectName("Panel")
            layout.addWidget(browser)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(dlg.accept)
            layout.addWidget(close_btn, 0, Qt.AlignRight)
            dlg.exec()

        except (CasuError, NativeCasuError, NativeV2Error, OSError, ValueError) as exc:
            self.toast(f"Media information unavailable: {exc}")

    # --- Sync delays ---

    def set_audio_delay_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Audio delay")
        dlg.setStyleSheet(stylesheet())
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Milliseconds (-5000 to 5000):"))
        spin = QDoubleSpinBox()
        spin.setRange(-5000, 5000)
        spin.setValue(self._audio_delay_ms)
        layout.addWidget(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(lambda: (self._set_media_delay("audio", spin.value()), dlg.accept()))
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        dlg.exec()

    def set_subtitle_delay_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Subtitle delay")
        dlg.setStyleSheet(stylesheet())
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Milliseconds (-5000 to 5000):"))
        spin = QDoubleSpinBox()
        spin.setRange(-5000, 5000)
        spin.setValue(self._subtitle_delay_ms)
        layout.addWidget(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(lambda: (self._set_media_delay("subtitle", spin.value()), dlg.accept()))
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        dlg.exec()

    def _set_media_delay(self, kind: str, milliseconds: float):
        value = max(-5000.0, min(5000.0, float(milliseconds)))
        if self.backend:
            try:
                if kind == "audio":
                    value = self.backend.set_audio_delay(value)
                else:
                    value = self.backend.set_subtitle_delay(value)
            except BackendError as exc:
                self.status(str(exc))
                return
        if kind == "audio":
            self._audio_delay_ms = value
        else:
            self._subtitle_delay_ms = value
        self._persist_media_preferences()
        self.status(f"{kind.title()} delay {value:+g} ms")

    # --- CASU visual state ---

    def _load_visual_state(self, path: Path):
        self._visual_state = "legacy"
        self._visual_segments = []
        self._visual_video_segments = []
        self._visual_audio_segments = []
        self._scheduler = None
        if path.suffix.lower() != ".casu":
            return
        try:
            with path.open("rb") as handle:
                magic = handle.read(8)
            if magic == b"CASUNAT2":
                container = read_native_v2(path)
                self._visual_state = "CASUNAT2 native state stream"
                self._visual_segments = [
                    {"start_s": 0.0, "end_s": 0.0, "state": chunk.chunk_type.name}
                    for chunk in container.chunks
                    if chunk.chunk_type in {ChunkType.VIDEO_KEY_STATE, ChunkType.VIDEO_TILE_UPDATE}
                ]
                self._visual_video_segments = list(self._visual_segments)
                return
            manifest = (read_native(path, verify_payload=True).manifest if magic == b"CASUNAT1"
                        else json.loads(path.read_text(encoding="utf-8")))
            errors = validate_manifest(manifest)
            if errors:
                self._visual_state = "invalid CASU: " + errors[0]
                return
            self._visual_video_segments = [s for s in manifest.get("video", {}).get("segments", []) if isinstance(s, dict)]
            self._visual_audio_segments = [s for s in manifest.get("audio", {}).get("segments", []) if isinstance(s, dict)]
            self._visual_segments = self._visual_video_segments + self._visual_audio_segments
            self._scheduler = CasuScheduler.from_manifest(manifest, "video" if self._visual_video_segments else "audio")
            self._visual_state = "CASU state map" if self._visual_segments else "CASU empty map"
        except (OSError, ValueError, TypeError, NativeCasuError, NativeV2Error):
            self._visual_state = "invalid CASU"

    # --- Settings persistence ---

    def _apply_backend_settings(self):
        if not self.backend:
            return
        self._volume = self.backend.set_volume(self._volume)
        self.backend.set_mute(self._muted)
        if self._audio_device:
            try:
                self.backend.set_audio_device(self._audio_device)
            except BackendError:
                self._audio_device = None

    def _apply_playback_rate(self):
        if not self.backend:
            return
        try:
            self._rate = self.backend.set_rate(self._rate)
        except BackendError:
            if not isinstance(self.backend, NativeCasuBackend):
                raise
            self._rate = self.backend.set_rate(1.0)
        self._rate_btn.setText(f"{self._rate:g}×")

    def _apply_media_preferences(self):
        if not self.backend or not self.current or not self.current.is_file():
            return
        preferences = self.media_library.playback_preferences(self.current)
        for identifier, setter in (
            (preferences.audio_track, self.backend.set_audio_track),
            (preferences.video_track, self.backend.set_video_track),
            (preferences.subtitle_track, self.backend.set_subtitle_track),
        ):
            if identifier is not None:
                try:
                    setter(identifier)
                except BackendError:
                    pass
        self._audio_delay_ms = preferences.audio_delay_ms
        self._subtitle_delay_ms = preferences.subtitle_delay_ms
        try:
            self._audio_delay_ms = self.backend.set_audio_delay(self._audio_delay_ms)
        except BackendError:
            self._audio_delay_ms = 0.0
        try:
            self._subtitle_delay_ms = self.backend.set_subtitle_delay(self._subtitle_delay_ms)
        except BackendError:
            self._subtitle_delay_ms = 0.0

    def _persist_media_preferences(self):
        if not self.backend or not self.current or not self.current.is_file():
            return
        try:
            audio_track = self.backend.audio_track()
            video_track = self.backend.video_track()
            preferences = PlaybackPreferences(
                audio_track=audio_track if audio_track >= 0 else None,
                video_track=video_track if video_track >= 0 else None,
                subtitle_track=self.backend.subtitle_track(),
                audio_delay_ms=self._audio_delay_ms,
                subtitle_delay_ms=self._subtitle_delay_ms,
            )
            self.media_library.set_playback_preferences(self.current, preferences)
        except (BackendError, OSError, ValueError):
            pass

    def _save_effective_settings(self):
        current = self.settings_store.load()
        updated = replace(
            current,
            volume=self._volume,
            muted=self._muted,
            rate=self._rate,
            audio_device=self._audio_device,
            watched_folders=tuple(self._watched_folders),
        )
        self.settings_store.save(updated)

    # --- Session ---

    def _restore_session(self):
        try:
            payload = json.loads(self._session_file.read_text(encoding="utf-8"))
            self.add_files([Path(v) for v in payload.get("playlist", []) if Path(v).is_file()])
            self._resume_source = str(payload.get("current", "")) or None
            self._resume_position = max(0.0, float(payload.get("position", 0.0)))
            geometry = payload.get("geometry")
            if isinstance(geometry, str) and geometry:
                try:
                    vals = geometry.split("+")
                    if len(vals) >= 3:
                        self.resize(int(vals[0].split("x")[0]), int(vals[0].split("x")[1]))
                except (ValueError, IndexError):
                    pass
        except (OSError, ValueError, TypeError):
            pass

    def closeEvent(self, event):
        if getattr(self, "_yt_stream", None) is not None:
            self._yt_stream.stop()
        resume_position = self.backend.position() if self.backend else self._seek_slider._position
        self._persist_media_preferences()
        try:
            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._session_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "playlist": [str(item) for item in self.playlist_model.items],
                "volume": self._volume,
                "muted": self._muted,
                "rate": self._rate,
                "current": str(self.current) if self.current else None,
                "position": resume_position,
                "geometry": f"{self.width()}x{self.height()}+{self.x()}+{self.y()}",
            }, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self._session_file)
        except OSError:
            pass
        if self.current and self.current.is_file():
            try:
                self.media_library.record_progress(self.current, resume_position, self.duration or None)
            except OSError:
                pass
        try:
            self._save_effective_settings()
        except OSError:
            pass
        if self._mpris_notifier is not None:
            self._mpris_notifier.close()
        self.controller.close()
        self.backend = None
        self.media_library.close()
        event.accept()

    # --- Backend events ---

    def _backend_event(self, state: PlaybackState):
        QTimer.singleShot(0, lambda s=state: self._apply_backend_event(s))
    def _apply_backend_event(self, state: PlaybackState):
        if state == PlaybackState.PLAYING:
            self._paused = False
            self._play_btn.setText("| |")
        elif state == PlaybackState.PAUSED:
            self._paused = True
            self._play_btn.setText("▶")
        elif state == PlaybackState.ERROR:
            detail_reader = getattr(self.backend, "last_error", None)
            detail = detail_reader() if callable(detail_reader) else None
            self.status("Playback error — " + (detail or "decoder or output failed"))
            self._diagnostics_bar.set_values(support="backend error; inspect media information/logs")
        elif state == PlaybackState.ENDED and not self._advancing and not self._end_handled:
            self._handle_ended()

    def _check_playback_start(self):
        if not self.backend or not self.current or self._paused:
            return
        if self.current.as_uri().startswith(("http:", "https:", "rtsp:")):
            return
        if self.backend.state() == PlaybackState.PLAYING and not self.backend.is_actively_playing():
            self.status("Playback unavailable — libVLC did not enter active playback")
            self._diagnostics_bar.set_values(support="backend opened; decoder or output unavailable")

    def _source_for(self, path: Path) -> Path:
        if path.suffix.lower() != ".casu":
            return path
        try:
            with path.open("rb") as handle:
                if handle.read(8) in {b"CASUNAT1", b"CASUNAT2"}:
                    return path
        except OSError as exc:
            raise CasuError(f"could not read CASU container: {path}") from exc
        manifest = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_manifest(manifest)
        if errors:
            raise CasuError(f"invalid CASU manifest: {errors[0]}")
        return resolve_casu_source(path)

    # --- Polling ---

    def _sync_position(self):
        if self.backend and not self._paused:
            real = self.backend.duration()
            if real > 0 and (not self.duration or abs(self.duration - real) > 0.5):
                # The real duration may arrive asynchronously; keep the slider
                # in sync so a click always maps to the correct time.
                self.duration = real
                self._seek_slider.set_duration(real)
            pos = min(self.duration, self.backend.position())
            self._seek_slider.set_position(pos)
            self._update_time_labels(pos)
            # Robust end-of-media detection: even if libVLC's event/state API
            # is unavailable, reaching the end of the track must advance the
            # queue / honour repeat and shuffle.
            if (self.duration > 0 and pos >= self.duration - 0.25
                    and not self._paused):
                self._handle_ended()

    def _handle_ended(self):
        """Advance/loop after a track ends (guarded against double fire)."""
        if self._advancing or self._end_handled or not self.backend:
            return
        if self._ab_a is not None and self._ab_b is not None:
            return  # the A–B loop owns the end
        self._end_handled = True
        self._advancing = True
        try:
            self.play_next(automatic=True)
        finally:
            self._advancing = False

    def _update_time_labels(self, pos: float):
        self._time_current.setText(format_duration(pos))
        self._visualizer.set_position(pos)
        if self._ab_a is not None and self._ab_b is not None and self.backend is not None:
            if pos >= self._ab_b - 0.05:
                try:
                    self.backend.seek(self._ab_a)
                    if not self._paused:
                        self.backend.play()
                except (BackendError, CasuError):
                    pass
        if self.duration > 0:
            self._time_total.setText(format_duration(self.duration))
        elif self._network_source:
            self._time_total.setText("LIVE")
        else:
            self._time_total.setText(format_duration(None))

    def _poll(self):
        if self._mpris_notifier is not None:
            self._mpris_notifier.refresh()
        if self.backend and not self._dragging and not self._paused:
            self._sync_position()
            state = self.backend.state()
            if state == PlaybackState.ENDED and not self._advancing and not self._end_handled:
                self._handle_ended()
            elif state == PlaybackState.ERROR:
                self.status("Playback error detected")
                self._video_surface.set_video_active(False)


    def _backend_event(self, state: PlaybackState):
        QTimer.singleShot(0, lambda s=state: self._apply_backend_event(s))
# --- MPRIS D-Bus (org.mpris.MediaPlayer2.*) — desktop remote control -------
#
# Exposes the player on the session bus so GNOME Shell (top-right media
# menu), playerctl and every other MPRIS client can Play/Pause/Next/Previous,
# read status/metadata and control volume/loop/shuffle. Registration is best
# effort: without a session bus (or QtDBus) the player simply runs without it.

_MPRIS_SERVICE = "org.mpris.MediaPlayer2.casu"
_MPRIS_PATH = "/org/mpris/MediaPlayer2"
_MPRIS_PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"

try:
    from PySide6.QtDBus import (
        QDBusAbstractAdaptor, QDBusConnection, QDBusMessage, QDBusObjectPath,
    )
    _HAVE_QTDBUS = True
except ImportError:  # headless or minimal PySide6 builds
    QDBusAbstractAdaptor = None  # type: ignore[assignment]
    _HAVE_QTDBUS = False

try:
    from PySide6.QtCore import ClassInfo as _QtClassInfo  # PySide6 >= 6.10
except ImportError:
    _QtClassInfo = None
try:
    from PySide6.QtCore import Q_CLASSINFO as _QtQClassInfo  # PySide6 < 6.10
except ImportError:
    _QtQClassInfo = None


def _mpris_iface_decorator(name: str):
    """Class decorator registering the 'D-Bus Interface' class info."""
    if _QtClassInfo is not None:
        return _QtClassInfo(**{"D-Bus Interface": name})
    return lambda cls: cls


def _mpris_iface_body(name: str):
    """Legacy in-class-body spelling of the same 'D-Bus Interface' info."""
    if _QtQClassInfo is not None:
        return _QtQClassInfo("D-Bus Interface", name)
    return None


if _HAVE_QTDBUS:

    @_mpris_iface_decorator("org.mpris.MediaPlayer2")
    class _MprisRoot(QDBusAbstractAdaptor):
        """org.mpris.MediaPlayer2 — application identity/lifecycle."""

        _mpris_iface_body("org.mpris.MediaPlayer2")

        def __init__(self, window):
            super().__init__(window)
            self._window = window

        @Slot()
        def Raise(self):
            window = self._window
            window.showNormal()
            window.raise_()
            window.activateWindow()

        @Slot()
        def Quit(self):
            self._window.close()

        def _identity(self) -> str:
            return "MPCASU"

        def _desktop_entry(self) -> str:
            return "mpcasu"  # packaging/mpcasu.desktop

        def _uri_schemes(self) -> list:
            return ["file", "http", "https", "rtsp", "rtmp", "udp", "rtp",
                    "spotify", "ytdl"]

        def _mime_types(self) -> list:
            return sorted(
                f"{kind}/x-{ext.lstrip('.')}" if ext == ".casu" else f"{kind}/{ext.lstrip('.')}"
                for ext, kind in (
                    (".mp3", "audio"), (".flac", "audio"), (".wav", "audio"),
                    (".ogg", "audio"), (".m4a", "audio"), (".opus", "audio"),
                    (".aac", "audio"), (".aiff", "audio"), (".mp4", "video"),
                    (".mkv", "video"), (".webm", "video"), (".mov", "video"),
                    (".casu", "application"),
                ))

        Identity = Property(str, _identity, constant=True)
        DesktopEntry = Property(str, _desktop_entry, constant=True)
        CanQuit = Property(bool, lambda self: True, constant=True)
        CanRaise = Property(bool, lambda self: True, constant=True)
        HasTrackList = Property(bool, lambda self: False, constant=True)
        SupportedUriSchemes = Property("QStringList", _uri_schemes, constant=True)
        SupportedMimeTypes = Property("QStringList", _mime_types, constant=True)

    @_mpris_iface_decorator(_MPRIS_PLAYER_INTERFACE)
    class _MprisPlayer(QDBusAbstractAdaptor):
        """org.mpris.MediaPlayer2.Player — transport, status and metadata."""

        _mpris_iface_body(_MPRIS_PLAYER_INTERFACE)

        # Declared as a Qt signal so QtDBus broadcasts it with the correct
        # interface and an int64 ('x') payload.
        Seeked = Signal("qlonglong")

        def __init__(self, window):
            super().__init__(window)
            self._window = window

        # --- property backends ---

        def _playback_status(self) -> str:
            window = self._window
            backend = getattr(window, "backend", None)
            if backend is None:
                return "Stopped"
            if getattr(window, "_paused", False):
                return "Paused"
            try:
                state = backend.state()
            except Exception:
                return "Stopped"
            if state in {PlaybackState.PLAYING, PlaybackState.LOADING,
                         PlaybackState.READY}:
                return "Playing"
            if state == PlaybackState.PAUSED:
                return "Paused"
            return "Stopped"

        def _loop_status(self) -> str:
            return {"off": "None", "one": "Track",
                    "all": "Playlist"}[getattr(self._window, "_repeat_mode", "off")]

        def _set_loop_status(self, value) -> None:
            mode = {"None": "off", "Track": "one",
                    "Playlist": "all"}.get(str(value))
            if mode is not None:
                self._window._set_repeat_mode(mode)

        def _shuffle(self) -> bool:
            return bool(getattr(self._window, "_shuffle", False))

        def _set_shuffle(self, value) -> None:
            self._window._toggle_shuffle(bool(value))

        def _metadata(self) -> dict:
            window = self._window
            current = getattr(window, "current", None)
            # pathlib collapses "//" in URLs, so prefer the untouched
            # original string the player was started with.
            network = str(getattr(window, "_network_source", None) or "")
            if current is None and not network:
                return {}
            source_text = network or str(current)
            if "://" in source_text:
                url = source_text
            else:
                url = source_text
                try:
                    url = current.as_uri()
                except (ValueError, AttributeError):
                    pass
            meta = {
                "mpris:trackid": QDBusObjectPath(
                    "/org/mpcasu/track/"
                    + hashlib.sha1(source_text.encode("utf-8", "replace")).hexdigest()[:16]),
                "xesam:url": url,
            }
            try:
                title = window._display_title(Path(source_text))
            except Exception:
                title = getattr(current, "name", "")
            if title:
                meta["xesam:title"] = str(title)
            duration = float(getattr(window, "duration", 0.0) or 0.0)
            if duration > 0:
                meta["mpris:length"] = int(duration * 1_000_000)
            return meta

        def _volume(self) -> float:
            window = self._window
            if getattr(window, "_muted", False):
                return 0.0
            return max(0.0, min(2.0, float(getattr(window, "_volume", 100)) / 100.0))

        def _set_volume(self, value) -> None:
            clamped = max(0.0, min(2.0, float(value)))
            self._window._on_volume_slider(int(round(clamped * 100)))

        def _position_us(self) -> int:
            backend = getattr(self._window, "backend", None)
            if backend is None:
                return 0
            try:
                pos = float(backend.position())
            except Exception:
                pos = 0.0
            return int(max(0.0, pos) * 1_000_000)

        def _rate(self) -> float:
            return float(getattr(self._window, "_rate", 1.0) or 1.0)

        PlaybackStatus = Property(str, _playback_status)
        LoopStatus = Property(str, _loop_status, _set_loop_status)
        Shuffle = Property(bool, _shuffle, _set_shuffle)
        Metadata = Property("QVariantMap", _metadata)
        Volume = Property(float, _volume, _set_volume)
        Position = Property("qlonglong", _position_us)
        Rate = Property(float, _rate)
        MinimumRate = Property(float, _rate, constant=True)
        MaximumRate = Property(float, _rate, constant=True)
        CanControl = Property(bool, lambda self: True, constant=True)
        CanPlay = Property(bool, lambda self: True, constant=True)
        CanPause = Property(bool, lambda self: True, constant=True)
        CanSeek = Property(bool, lambda self: True, constant=True)
        CanGoNext = Property(bool, lambda self: True, constant=True)
        CanGoPrevious = Property(bool, lambda self: True, constant=True)

        # --- transport methods ---

        @Slot()
        def Play(self):
            window = self._window
            if window.backend is None:
                window.play_selected()
            elif window._paused:
                window.pause()

        @Slot()
        def Pause(self):
            window = self._window
            if window.backend is not None and not window._paused:
                window.pause()

        @Slot()
        def PlayPause(self):
            self._window.toggle_playback()

        @Slot()
        def Stop(self):
            self._window.stop()

        @Slot()
        def Next(self):
            self._window.play_next()

        @Slot()
        def Previous(self):
            self._window.play_previous()

        @Slot("qlonglong")
        def Seek(self, offset_us):
            window = self._window
            if window.backend is None:
                return
            limit = float(getattr(window, "duration", 0.0) or 0.0)
            target = float(self.Position) + float(offset_us) / 1_000_000
            if limit > 0:
                target = min(target, limit)
            window._do_seek(max(0.0, target))

        @Slot(QDBusObjectPath, "qlonglong")
        def SetPosition(self, track_id, position_us):
            if self._window.backend is None:
                return
            self._window._do_seek(max(0.0, float(position_us) / 1_000_000))

        @Slot(str)
        def OpenUri(self, uri):
            window = self._window
            text = str(uri)
            if "://" in text or text.startswith(("spotify:", "ytdl:")):
                window._play_network_source(text)
            else:
                window.play_selected(Path(text))

    class _MprisNotifier:
        """Diff-based org.freedesktop.DBus.Properties.PropertiesChanged emitter.

        MainWindow._poll() calls refresh() every 200 ms; changed properties
        are broadcast so desktop clients stay in sync without polling.
        """

        _TRACKED = ("PlaybackStatus", "LoopStatus", "Shuffle", "Metadata",
                    "Volume")

        def __init__(self, window, bus, player, service):
            self._window = window
            self._bus = bus
            self._player = player
            self._service = service
            self._last: dict = {}

        def _value(self, name: str):
            value = getattr(self._player, name)
            return value() if callable(value) else value

        def _snapshot_value(self, value):
            if isinstance(value, dict):
                return {key: self._dbus_path_str(item)
                        if isinstance(item, QDBusObjectPath) else item
                        for key, item in value.items()}
            return value

        @staticmethod
        def _dbus_path_str(item) -> str:
            # str(QDBusObjectPath) yields the object repr (no __str__), so
            # always go through path() for a stable, comparable value.
            getter = getattr(item, "path", None)
            return str(getter()) if callable(getter) else str(item)

        def refresh(self) -> None:
            changed = {}
            for name in self._TRACKED:
                value = self._value(name)
                if self._last.get(name) != self._snapshot_value(value):
                    self._last[name] = self._snapshot_value(value)
                    changed[name] = value
            if not changed:
                return
            message = QDBusMessage.createSignal(
                _MPRIS_PATH, "org.freedesktop.DBus.Properties",
                "PropertiesChanged")
            message.setArguments([_MPRIS_PLAYER_INTERFACE, changed, []])
            self._bus.send(message)

        def seeked(self, seconds: float) -> None:
            try:
                self._player.Seeked.emit(int(round(float(seconds) * 1_000_000)))
            except (RuntimeError, TypeError, ValueError):
                pass

        def close(self) -> None:
            try:
                self._bus.unregisterService(self._service)
            except Exception:
                pass


def _register_mpris(window):
    """Export the player on the session bus; returns a notifier or None."""
    if not _HAVE_QTDBUS:
        return None
    try:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return None
        root = _MprisRoot(window)
        player = _MprisPlayer(window)
        service = _MPRIS_SERVICE
        if not bus.registerService(service):
            service = f"{_MPRIS_SERVICE}.instance{os.getpid()}"
            if not bus.registerService(service):
                return None
        if not bus.registerObject(_MPRIS_PATH, window,
                                  QDBusConnection.ExportAdaptors):
            return None
        return _MprisNotifier(window, bus, player, service)
    except Exception:
        return None

# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# Copyright (C) 2026 Lino Casu
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the CASU Anti-Capitalist License 1.4.
# ---------------------------------------------------------------------
"""MPCASU settings pages: Library / Options / EPG / About.

Extracted from main_window.py in the v7.8 modularization pass. These page
widgets own their layout only; playback state stays in MainWindow.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
from pathlib import Path

from dataclasses import replace

from casu import __version__
from casu.core import CasuError
from casu.playlist import PlaylistError, load_playlist_file, playlist_names

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

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
        self._groups_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._groups_list.itemDoubleClicked.connect(lambda _item: self._add_playlist_groups())
        self._groups_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._groups_list.customContextMenuRequested.connect(self._playlist_group_menu)
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
        self._add_playlist_btn = QPushButton("Add playlist(s) to queue")
        self._add_playlist_btn.setObjectName("PrimaryButton")
        self._add_playlist_btn.clicked.connect(self._add_playlist_groups)
        bottom.addWidget(self._add_playlist_btn)
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
        self._add_playlist_btn.setVisible(mode == "playlists")
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
        from casu.playlist import PLAYLIST_SUFFIXES
        self._groups_list.blockSignals(True)
        self._groups_list.clear()
        self._playlist_files.clear()
        folders = list(self._settings_store.load().watched_folders) if self._settings_store else []
        # v7.8: NEVER fall back to the whole home directory — rglob over ~130k
        # entries froze the UI thread for seconds ("playlist click lag").
        # Default to the XDG music dir instead (exists on every desktop).
        if not folders:
            music = Path.home() / "Music"
            folders = [str(music) if music.is_dir() else str(Path.cwd())]
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mpcasu/youtube-playlists"
        candidates: list = []
        for folder in [*folders, str(data)]:
            try:
                candidates.extend(Path(folder).expanduser().rglob("*"))
            except OSError:
                continue
        seen = set()
        for path in sorted(candidates):
            try:
                if path.suffix.lower() not in PLAYLIST_SUFFIXES or not path.is_file():
                    continue
                path = path.resolve()
                if path in seen: continue
                seen.add(path)
                if self._query() and self._query() not in path.stem.casefold(): continue
                label = path.stem
                counter = 2
                while label in self._playlist_files:
                    label = f"{path.stem} ({counter})"
                    counter += 1
                self._playlist_files[label] = path
                row = QListWidgetItem(label)
                row.setData(Qt.UserRole, path)
                row.setToolTip(str(path))
                self._groups_list.addItem(row)
            except OSError:
                continue
        self._groups_list.blockSignals(False)
        self._groups_list.setEnabled(True)
        if self._groups_list.count(): self._groups_list.setCurrentRow(0)
        else: self._count_label.setText("No playlist files found")

    def _on_playlist_group_selected(self, current):
        self._tracks.clear()
        self._tracks_list.clear()
        if current is None: return
        path = self._playlist_files.get(current.text())
        if path is None: return
        try:
            entries = self._parse_playlist_file(path)
        except (OSError, ValueError, PlaylistError) as error:
            self._count_label.setText(f"Cannot read playlist: {error}")
            return
        titles = playlist_names(path)
        for source in entries:
            label = titles.get(str(source)) or (source.name if isinstance(source, Path) else str(source))
            row = QListWidgetItem(label)
            row.setData(Qt.UserRole, source)
            row.setToolTip(str(source))
            self._tracks_list.addItem(row)
            self._tracks.append(source)
        self._count_label.setText(f"{len(self._tracks)} tracks")

    @staticmethod
    def _parse_playlist_file(path: Path) -> list:
        return list(load_playlist_file(path).items)

    def _add_playlist_groups(self):
        if self._mode() != "playlists": return
        selected = self._groups_list.selectedItems()
        if not selected and self._groups_list.currentItem():
            selected = [self._groups_list.currentItem()]
        paths = [self._playlist_files[item.text()] for item in selected if item.text() in self._playlist_files]
        if paths: self.addRequested.emit(paths)

    def _playlist_group_menu(self, position):
        if self._mode() != "playlists": return
        item = self._groups_list.itemAt(position)
        if item is None: return
        if not item.isSelected(): self._groups_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Add playlist(s) to queue", self._add_playlist_groups)
        menu.exec(self._groups_list.viewport().mapToGlobal(position))

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
        elif self._mode() == "playlists":
            self._add_playlist_groups()

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
        # cache_limit_mib is intentionally NOT editable here (v7.8.1): the
        # value had no consumer anywhere (no yt-dlp --limit-rate/cache-size
        # wiring), so the spin box was a dead option that made Apply look
        # like it "does not save". The persisted field is kept for settings
        # compatibility; only the cache-clearing action remains.
        clear_btn = QPushButton("Clear yt-dlp temp cache")
        clear_btn.setObjectName("IconButton")
        clear_btn.clicked.connect(lambda: self.actionRequested.emit("clear-cache"))
        cache_row = QHBoxLayout()
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
                          # cache_limit_mib intentionally carried over from
                          # the loaded settings: no consumer exists (dead
                          # option removed from the UI, field kept for
                          # settings-file compatibility).
                          cache_limit_mib=settings.cache_limit_mib,
                          recordings_dir=self._recordings_entry.text().strip(),
                          record_split_minutes=self._split_spin.value(),
                          record_split_mode=str(self._split_mode_combo.currentData()),
                          record_format=str(self._format_combo.currentText()),
                          watched_folders=self._library_folders())
        try:
            self._settings_store.save(updated)
        except (OSError, CasuError) as exc:
            # A Qt slot must never die on a full disk / read-only config dir:
            # the user gets visible feedback instead of a silent no-op that
            # looks like "Apply does not save".
            self.actionRequested.emit(f"save-error:{exc}")
            return
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
        return "\n".join(lines)

    def reload(self):
        settings = self._settings_store.load()
        self._volume_spin.setValue(settings.volume)
        self._muted_cb.setChecked(settings.muted)
        self._rate_spin.setValue(settings.rate)
        self._resume_cb.setChecked(settings.resume_playback)
        self._consent_cb.setChecked(settings.ytdlp_consent)
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
        # Loading M3U/XMLTV (file or URL) happens off the GUI thread: a slow
        # or dead IPTV URL used to freeze the whole window for the HTTP
        # timeout. Results come back through a queued bridge signal.
        from mpcasu_qt.threads import _ThreadBridge
        self._load_bridge = _ThreadBridge()
        self._load_bridge.resultReady.connect(self._apply_loaded_source)
        self._load_bridge.errorReady.connect(self._show_load_error)
        self._load_token = 0
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

        filters = QHBoxLayout()
        self._channel_search = QLineEdit()
        self._channel_search.setPlaceholderText("Search channels or groups…")
        self._channel_group = QComboBox()
        self._channel_group.addItem("All groups", "")
        self._channel_count = QLabel()
        self._channel_page = 0
        self._previous_channels = QPushButton("Previous")
        self._next_channels = QPushButton("Next")
        filters.addWidget(self._channel_group)
        filters.addWidget(self._channel_search, 1)
        filters.addWidget(self._channel_count)
        filters.addWidget(self._previous_channels)
        filters.addWidget(self._next_channels)
        outer.addLayout(filters)
        self._channel_search.textChanged.connect(self._filter_channels)
        self._channel_group.currentIndexChanged.connect(self._filter_channels)
        self._previous_channels.clicked.connect(lambda: self._turn_channels(-1))
        self._next_channels.clicked.connect(lambda: self._turn_channels(1))

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
        self._load_token += 1
        token = self._load_token
        self._status.setText("Loading playlist / guide…")

        def worker():
            try:
                if source.endswith((".xml", ".xmltv")):
                    from casu.epg import load_xmltv, fetch_xmltv
                    guide = fetch_xmltv(source) if source.startswith(("http://", "https://")) \
                        else load_xmltv(source)
                    self._load_bridge.resultReady.emit((token, "guide", guide))
                    return
                from casu.epg import load_m3u, fetch_m3u
                catalog = fetch_m3u(source) if source.startswith(("http://", "https://")) \
                    else load_m3u(source)
                self._load_bridge.resultReady.emit((token, "catalog", catalog))
            except Exception as exc:  # noqa: BLE001 - loader failures are UI errors
                self._load_bridge.errorReady.emit((token, str(exc)))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _apply_loaded_source(self, payload):
        token, kind, loaded = payload
        if token != self._load_token:
            return  # a newer load superseded this one
        if kind == "guide":
            self._guide = loaded
            self._status.setText(
                f"Guide loaded: {len(self._guide.entries) if hasattr(self._guide, 'entries') else ''} programmes")
        else:
            self._catalog = loaded
            self._status.setText(f"{len(self._catalog.channels)} channels loaded")
        self._sync_host_epg()
        self._render()

    def _show_load_error(self, payload):
        token, detail = payload
        if token != self._load_token:
            return
        self._status.setText(f"Load failed: {detail}")

    def _sync_host_epg(self):
        # After reparenting into the center stack, parent() is the QStackedWidget,
        # not the MainWindow — self.window() reaches the actual main window.
        host = self.window()
        if not hasattr(host, "_epg_catalog"):
            return
        host._epg_catalog = self._catalog
        host._epg_guide = self._guide
        host._diagnostics_bar.set_values(guide=host._epg_now_next())

    def _now_next(self, channel):
        if self._guide is None:
            return ""
        current, upcoming = self._guide.now_next(getattr(channel, "epg_id", "") or channel.name)
        parts = []
        if current is not None: parts.append(f"NOW · {current.title}")
        if upcoming is not None: parts.append(f"NEXT · {upcoming.title}")
        return "  |  ".join(parts)

    def _filter_channels(self, *_):
        self._channel_page = 0
        self._render()

    def _turn_channels(self, direction):
        self._channel_page = max(0, self._channel_page + direction)
        self._render()

    def _render(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if self._catalog is None:
            return
        groups = sorted({getattr(ch, "group", "") or "Ungrouped" for ch in self._catalog.channels})
        selected = self._channel_group.currentData() or ""
        if groups != [self._channel_group.itemData(i) for i in range(1, self._channel_group.count())]:
            self._channel_group.blockSignals(True)
            self._channel_group.clear()
            self._channel_group.addItem("All groups", "")
            for group in groups: self._channel_group.addItem(group, group)
            self._channel_group.setCurrentIndex(max(0, self._channel_group.findData(selected)))
            self._channel_group.blockSignals(False)
            selected = self._channel_group.currentData() or ""
        query = self._channel_search.text().strip().casefold()
        channels = [ch for ch in self._catalog.channels
                    if (not selected or (getattr(ch, "group", "") or "Ungrouped") == selected)
                    and (not query or query in (ch.name + " " + (getattr(ch, "group", "") or "")).casefold())]
        self._channel_page = min(self._channel_page, max(0, (len(channels) - 1) // 100))
        start = self._channel_page * 100
        self._channel_count.setText(f"{len(channels)} channels · {self._channel_page + 1}/{max(1, (len(channels) + 99) // 100)}")
        self._previous_channels.setEnabled(start > 0)
        self._next_channels.setEnabled(start + 100 < len(channels))
        for index, channel in enumerate(channels[start:start + 100]):
            card = QFrame()
            card.setObjectName("EpgChannel")
            card.setCursor(Qt.PointingHandCursor)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            name = QLabel(channel.name)
            name.setObjectName("NowPlayingTitle")
            name.setStyleSheet("font-size: 16px;")
            name.setWordWrap(True)
            cl.addWidget(name)
            now = self._now_next(channel)
            meta = QLabel(now or (getattr(channel, "group", "") or ""))
            meta.setObjectName("NowPlayingMeta")
            meta.setWordWrap(True)
            cl.addWidget(meta)
            card.mousePressEvent = lambda event, ch=channel: self.channelActivated.emit(ch)
            # v7.8: two-column card grid (was: all 100 cards stacked in column 0)
            self._grid.addWidget(card, index // 2, index % 2)


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
        info = QLabel(f"Version {__version__}\nMedia Player for CASU & Legacy Media\nIn-process playback · No external player")
        info.setObjectName("NowPlayingMeta")
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)
        layout.addSpacing(12)
        note = QLabel("Design inspired by VLC and Webamp — independent original code.\nAnti-Capitalist License 1.4 · Lino Casu")
        note.setObjectName("NowPlayingMeta")
        note.setAlignment(Qt.AlignCenter)
        note.setWordWrap(True)
        layout.addWidget(note)

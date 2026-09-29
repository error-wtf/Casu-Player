# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""MPRIS D-Bus integration (org.mpris.MediaPlayer2.*) - desktop remote control.

Extracted from main_window.py in v7.8 (module split, phase 3.4): exposes the
player on the session bus so GNOME Shell, playerctl and every other MPRIS
client can control playback. Registration is best effort: without a session
bus (or QtDBus) the player simply runs without it.
"""

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot
from mpcasu_backend import PlaybackState
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

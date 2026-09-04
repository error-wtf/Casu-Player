# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Internal MPCASU playback backends.

The backend uses libVLC through its shared library API.  No player executable
is launched: decoding, clocking, seeking and video-window ownership remain
under MPCASU control.  CASU manifests are validated before their immutable
source is opened by the same in-process media pipeline.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import tempfile
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from casu.core import CasuError, resolve_casu_source
from casu.schema import validate_manifest
from casu.native import NativeCasuError, read_native
from casu.mp5 import Mp5Error, extract_source as mp5_extract_source
from casu.media import (AudioDeviceDescriptor, ChapterDescriptor,
                        TrackDescriptor, TrackKind)
import json
from urllib.parse import parse_qsl, urlencode, urlparse


_SENSITIVE_QUERY_KEYS = frozenset({
    "access_token", "api_key", "apikey", "auth", "authorization", "key",
    "passwd", "password", "sig", "signature", "token", "x-amz-signature",
    "x-goog-signature",
})


def display_media_source(source: str | Path) -> str:
    """Return a bounded UI/error-safe source with common credentials removed."""
    value = str(source)
    try:
        parsed = urlparse(value)
    except ValueError:
        return (value[:2047] + "…") if len(value) > 2048 else value
    if parsed.netloc and "@" in parsed.netloc:
        parsed = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1])
    if parsed.query:
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True,
                              max_num_fields=256)
        except ValueError:
            pairs = []
        if pairs:
            sanitized = [(key, "[redacted]" if key.casefold() in _SENSITIVE_QUERY_KEYS else val)
                         for key, val in pairs]
            parsed = parsed._replace(query=urlencode(sanitized, doseq=True))
    result = parsed.geturl()
    return (result[:2047] + "…") if len(result) > 2048 else result


class PlaybackState(str, Enum):
    EMPTY = "EMPTY"; LOADING = "LOADING"; READY = "READY"
    PLAYING = "PLAYING"; PAUSED = "PAUSED"; STOPPED = "STOPPED"
    ENDED = "ENDED"; ERROR = "ERROR"


# Stable libvlc_event_e media-player values used by libVLC 2.x/3.x/4.x.
# Keep this table explicit and tested: confusing EndReached (0x109) with
# EncounteredError (0x10A) makes successful playback look like a decoder fault.
LIBVLC_PLAYER_EVENT_STATES = {
    0x102: PlaybackState.LOADING,  # MediaPlayerOpening
    0x103: PlaybackState.LOADING,  # MediaPlayerBuffering
    0x104: PlaybackState.PLAYING,  # MediaPlayerPlaying
    0x105: PlaybackState.PAUSED,   # MediaPlayerPaused
    0x106: PlaybackState.STOPPED,  # MediaPlayerStopped
    0x109: PlaybackState.ENDED,    # MediaPlayerEndReached
    0x10A: PlaybackState.ERROR,    # MediaPlayerEncounteredError
}


class BackendError(CasuError):
    pass


class _TrackDescription(ctypes.Structure):
    pass


_TrackDescription._fields_ = [
    ("identifier", ctypes.c_int),
    ("name", ctypes.c_char_p),
    ("next", ctypes.POINTER(_TrackDescription)),
]


class _AudioOutputDevice(ctypes.Structure):
    pass


_AudioOutputDevice._fields_ = [
    ("next", ctypes.POINTER(_AudioOutputDevice)),
    ("device", ctypes.c_char_p),
    ("description", ctypes.c_char_p),
]


class LibVLCBackend:
    """Minimal, real in-process libVLC backend for the MPCASU window."""

    EMBEDDED_RUNTIME_OPTIONS = ("--no-video-title-show", "--avcodec-hw=none")
    SAFE_MEDIA_OPTIONS = (":avcodec-hw=none",)

    def __init__(self, video_widget, *, runtime_options: tuple[str, ...] = ()):
        # Python/ctypes does not inherit the plugin-path setup that the VLC
        # launcher normally performs. Point libVLC at its installed modules so
        # H.264/AAC and other codecs are discovered by the in-process player.
        plugin_candidates = []
        configured_plugins = os.environ.get("VLC_PLUGIN_PATH")
        if configured_plugins:
            plugin_candidates.append(configured_plugins)
        if sys.platform.startswith("linux"):
            plugin_candidates.extend(("/usr/lib/x86_64-linux-gnu/vlc/plugins", "/usr/lib/vlc/plugins"))
        elif sys.platform == "darwin":
            executable_dir = Path(sys.executable).resolve().parent
            bundled_vlc = executable_dir.parent / "Frameworks" / "VLC"
            plugin_candidates.extend((
                str(bundled_vlc / "plugins"),
                "/Applications/VLC.app/Contents/MacOS/plugins",
            ))
        plugin_path = next((candidate for candidate in plugin_candidates if os.path.isdir(candidate)), None)
        if plugin_path:
            os.environ.setdefault("VLC_PLUGIN_PATH", plugin_path)
        library_names = self.library_candidates(sys.platform)
        load_error = None
        # VLC's macOS distribution keeps libvlc and libvlccore side by side.
        # A frozen executable does not inherit VLC.app's launcher environment,
        # so make the core symbols globally available before loading libvlc.
        if sys.platform == "darwin":
            executable_dir = Path(sys.executable).resolve().parent
            bundled_lib = executable_dir.parent / "Frameworks" / "VLC" / "lib"
            bundled_core = bundled_lib / "libvlccore.dylib"
            if bundled_core.is_file():
                try:
                    self._bundled_vlc_core = ctypes.CDLL(
                        str(bundled_core), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                except OSError as exc:
                    load_error = exc
        for library_name in library_names:
            try:
                self.lib = ctypes.CDLL(library_name)
                break
            except OSError as exc:
                load_error = exc
        else:
            detail = f": {load_error}" if load_error else ""
            raise BackendError(f"libVLC shared library is unavailable{detail}") from load_error
        self.widget = video_widget
        self.runtime_options = self.validate_runtime_options(runtime_options)
        # VLC 3.x discovers modules through VLC_PLUGIN_PATH. The historical
        # --plugin-path command-line option is no longer accepted and can
        # prevent codec modules from loading in embedded libVLC builds.
        # Hardware decode is intentionally disabled for the embedded player.
        # On hybrid Intel/NVIDIA desktops libVLC/VDPAU can enter a permanent
        # YUVA blending-error loop, monopolise the compositor, and make other
        # GPU-backed applications flicker or appear frozen. Software decoding
        # is deterministic and isolates MPCASU from the system compositor.
        options = [*(value.encode("utf-8") for value in self.EMBEDDED_RUNTIME_OPTIONS),
                   *(value.encode("utf-8") for value in self.runtime_options)]
        argv = (ctypes.c_char_p * len(options))(*options)
        self.instance = self._call("libvlc_new", ctypes.c_void_p, [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)])(len(options), argv)
        if not self.instance:
            raise BackendError("libVLC could not be initialized")
        self.media = None; self.player = None; self.path: Path | None = None
        self._native_temp: Path | None = None
        self._state = PlaybackState.EMPTY
        # Asynchronous libVLC teardown state. libvlc_media_player_stop can
        # block indefinitely (wedged input thread, observed with loopback
        # HTTP sources); every stop/release therefore happens off the
        # caller's thread and is tracked so handles are never released
        # while a stop on them is still running.
        self._pending_stops: dict[int, threading.Thread] = {}
        self._retiring: set[threading.Thread] = set()
        self._teardown_lock = threading.Lock()
        self._play_requested_at: float | None = None
        self._user_stop_monotonic: float | None = None
        self._seen_playing = False
        self._event_manager = None
        self._event_callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
        self._event_callbacks: list[tuple[int, Any]] = []
        self._event_api = False
        self.on_event = None
        self._last_error_detail = ""
        self._install("libvlc_media_new_path", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p])
        self._subtitle_option_api = self._optional_install("libvlc_media_add_option", None, [ctypes.c_void_p, ctypes.c_char_p])
        self._install("libvlc_media_player_new_from_media", ctypes.c_void_p, [ctypes.c_void_p])
        self._install("libvlc_media_player_release", None, [ctypes.c_void_p])
        self._install("libvlc_media_release", None, [ctypes.c_void_p])
        self._install("libvlc_release", None, [ctypes.c_void_p])
        self._media_state_api = self._optional_install("libvlc_media_get_state", ctypes.c_int, [ctypes.c_void_p])
        self._player_state_api = self._optional_install(
            "libvlc_media_player_get_state", ctypes.c_int, [ctypes.c_void_p])
        self._install("libvlc_media_player_play", ctypes.c_int, [ctypes.c_void_p])
        self._install("libvlc_media_player_set_pause", None, [ctypes.c_void_p, ctypes.c_int])
        self._install("libvlc_media_player_stop", None, [ctypes.c_void_p])
        self._install("libvlc_media_player_is_playing", ctypes.c_int, [ctypes.c_void_p])
        self._install("libvlc_media_player_get_time", ctypes.c_int64, [ctypes.c_void_p])
        self._install("libvlc_media_player_get_length", ctypes.c_int64, [ctypes.c_void_p])
        self._install("libvlc_media_player_set_time", None, [ctypes.c_void_p, ctypes.c_int64])
        self._chapter_api = all(self._optional_install(name, restype, args) for name, restype, args in (
            ("libvlc_media_player_get_title_count", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_media_player_get_title", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_media_player_set_title", None, [ctypes.c_void_p, ctypes.c_int]),
            ("libvlc_media_player_get_chapter_count", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            ("libvlc_media_player_get_chapter", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_media_player_set_chapter", None, [ctypes.c_void_p, ctypes.c_int]),
        ))
        # libVLC 3.x and 4.x declare this API as ``void``. Treating the
        # register contents as an integer return value creates random frame-
        # step failures even though libVLC accepted the command.
        self._frame_step_api = self._optional_install(
            "libvlc_media_player_next_frame", None, [ctypes.c_void_p])
        self._snapshot_api = self._optional_install(
            "libvlc_video_take_snapshot", ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_uint, ctypes.c_char_p,
             ctypes.c_uint, ctypes.c_uint])
        self._aspect_api = all(self._optional_install(name, restype, args)
                               for name, restype, args in (
            ("libvlc_video_get_aspect_ratio", ctypes.c_void_p, [ctypes.c_void_p]),
            ("libvlc_video_set_aspect_ratio", None, [ctypes.c_void_p, ctypes.c_char_p]),
            ("libvlc_free", None, [ctypes.c_void_p]),
        ))
        self._crop_api = all(self._optional_install(name, restype, args)
                             for name, restype, args in (
            ("libvlc_video_get_crop_geometry", ctypes.c_void_p, [ctypes.c_void_p]),
            ("libvlc_video_set_crop_geometry", None, [ctypes.c_void_p, ctypes.c_char_p]),
            ("libvlc_free", None, [ctypes.c_void_p]),
        ))
        self._scale_api = all(self._optional_install(name, restype, args)
                              for name, restype, args in (
            ("libvlc_video_get_scale", ctypes.c_float, [ctypes.c_void_p]),
            ("libvlc_video_set_scale", None, [ctypes.c_void_p, ctypes.c_float]),
        ))
        self._deinterlace_api = self._optional_install(
            "libvlc_video_set_deinterlace", None,
            [ctypes.c_void_p, ctypes.c_char_p])
        self._install("libvlc_media_player_set_rate", ctypes.c_int, [ctypes.c_void_p, ctypes.c_float])
        self._install("libvlc_media_player_get_rate", ctypes.c_float, [ctypes.c_void_p])
        self._install("libvlc_audio_set_volume", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._install("libvlc_audio_get_volume", ctypes.c_int, [ctypes.c_void_p])
        self._install("libvlc_audio_set_mute", None, [ctypes.c_void_p, ctypes.c_int])
        self._audio_channel_api = all(self._optional_install(name, restype, args)
                                      for name, restype, args in (
            ("libvlc_audio_get_channel", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_audio_set_channel", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
        ))
        self._equalizer_api = all(self._optional_install(name, restype, args)
                                  for name, restype, args in (
            ("libvlc_audio_equalizer_get_preset_count", ctypes.c_uint, []),
            ("libvlc_audio_equalizer_get_preset_name", ctypes.c_char_p, [ctypes.c_uint]),
            ("libvlc_audio_equalizer_new_from_preset", ctypes.c_void_p, [ctypes.c_uint]),
            ("libvlc_audio_equalizer_release", None, [ctypes.c_void_p]),
            ("libvlc_media_player_set_equalizer", ctypes.c_int,
             [ctypes.c_void_p, ctypes.c_void_p]),
        ))
        self._audio_delay_api = self._optional_install(
            "libvlc_audio_set_delay", ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int64])
        self._install("libvlc_audio_get_track_count", ctypes.c_int, [ctypes.c_void_p])
        self._install("libvlc_audio_get_track", ctypes.c_int, [ctypes.c_void_p])
        self._install("libvlc_audio_set_track", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._audio_description_api = self._install_descriptions("libvlc_audio_get_track_description")
        self._audio_device_api = all(self._optional_install(name, restype, args) for name, restype, args in (
            ("libvlc_audio_output_device_enum", ctypes.POINTER(_AudioOutputDevice), [ctypes.c_void_p]),
            ("libvlc_audio_output_device_list_release", None, [ctypes.POINTER(_AudioOutputDevice)]),
            ("libvlc_audio_output_device_set", None, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]),
        ))
        self._video_track_api = all(self._optional_install(name, restype, args) for name, restype, args in (
            ("libvlc_video_get_track_count", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_video_get_track", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_video_set_track", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
        ))
        self._video_description_api = self._install_descriptions("libvlc_video_get_track_description")
        self._subtitle_api = all(self._optional_install(name, restype, args) for name, restype, args in (
            ("libvlc_video_get_spu_count", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_video_get_spu", ctypes.c_int, [ctypes.c_void_p]),
            ("libvlc_video_set_spu", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
        ))
        self._subtitle_description_api = self._install_descriptions("libvlc_video_get_spu_description")
        self._subtitle_delay_api = self._optional_install(
            "libvlc_video_set_spu_delay", ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int64])
        self._event_api = all(self._optional_install(name, restype, args) for name, restype, args in (
            ("libvlc_media_player_event_manager", ctypes.c_void_p, [ctypes.c_void_p]),
            ("libvlc_event_attach", ctypes.c_int, [ctypes.c_void_p, ctypes.c_uint, self._event_callback_type, ctypes.c_void_p]),
            ("libvlc_event_detach", None, [ctypes.c_void_p, ctypes.c_uint, self._event_callback_type, ctypes.c_void_p]),
        ))
        if sys.platform.startswith("linux"):
            self._install("libvlc_media_player_set_xwindow", None, [ctypes.c_void_p, ctypes.c_uint32])
        elif sys.platform.startswith("win"):
            self._install("libvlc_media_player_set_hwnd", None, [ctypes.c_void_p, ctypes.c_void_p])
        elif sys.platform == "darwin":
            self._install("libvlc_media_player_set_nsobject", None, [ctypes.c_void_p, ctypes.c_void_p])

    @staticmethod
    def library_candidates(platform: str) -> list[str]:
        if platform.startswith("win"):
            return ["libvlc.dll", "libvlc-5.dll"]
        if platform == "darwin":
            executable_dir = Path(sys.executable).resolve().parent
            bundled = executable_dir.parent / "Frameworks" / "VLC" / "lib" / "libvlc.dylib"
            return [str(bundled), "libvlc.dylib"]
        discovered = ctypes.util.find_library("vlc")
        return list(dict.fromkeys(value for value in
                                  (discovered, "libvlc.so.5", "libvlc.so") if value))

    @staticmethod
    def validate_runtime_options(options: tuple[str, ...]) -> tuple[str, ...]:
        """Bound explicit libVLC options used by controlled runtime probes.

        Production callers normally pass no options. The hook lets the codec
        matrix select dummy audio/video sinks and exercise demuxing, decoding
        and clock progression independently of physical host devices.
        """
        if not isinstance(options, tuple):
            raise BackendError("libVLC runtime options must be a tuple")
        if len(options) > 16:
            raise BackendError("too many libVLC runtime options")
        validated: list[str] = []
        for value in options:
            if not isinstance(value, str) or not value.startswith("--"):
                raise BackendError("invalid libVLC runtime option")
            if "\x00" in value or len(value.encode("utf-8")) > 256:
                raise BackendError("invalid libVLC runtime option")
            validated.append(value)
        return tuple(validated)

    def _install(self, name, restype, args):
        setattr(self, name, self._call(name, restype, args))

    def _optional_install(self, name, restype, args) -> bool:
        try:
            self._install(name, restype, args)
        except BackendError:
            return False
        return True

    def _install_descriptions(self, name) -> bool:
        try:
            self._install(name, ctypes.POINTER(_TrackDescription), [ctypes.c_void_p])
            self._install("libvlc_track_description_release", None, [ctypes.POINTER(_TrackDescription)])
        except BackendError:
            return False
        return True

    def _call(self, name, restype, args):
        try: function = getattr(self.lib, name)
        except AttributeError as exc: raise BackendError(f"libVLC API missing: {name}") from exc
        function.restype = restype; function.argtypes = args
        return function

    @staticmethod
    def supports(source: str | Path) -> bool:
        """Return whether the universal backend can accept this source form."""
        value = str(source)
        # Protocol support belongs to the installed libVLC access modules.
        # MPCASU rejects only values that cannot be passed safely through the
        # C API; it does not maintain a smaller protocol marketing list.
        return bool(value) and "\x00" not in value

    @staticmethod
    def _is_location(value: str) -> bool:
        parsed = urlparse(value)
        windows_drive = (len(parsed.scheme) == 1 and len(value) >= 3
                         and value[1] == ":" and value[2] in {"/", "\\"})
        return bool(parsed.scheme and parsed.scheme != "file" and not windows_drive)

    def capabilities(self) -> dict[str, str]:
        """Expose runtime facts instead of claiming a static format matrix."""
        version = self._call("libvlc_get_version", ctypes.c_char_p, [])()
        changeset_api = self._optional_install("libvlc_get_changeset", ctypes.c_char_p, [])
        changeset = self.libvlc_get_changeset() if changeset_api else None
        return {
            "backend": "libVLC shared library",
            "version": version.decode("utf-8", "replace") if version else "unknown",
            "changeset": changeset.decode("utf-8", "replace") if changeset else "unknown",
            "plugin_path": os.environ.get("VLC_PLUGIN_PATH", "runtime default"),
            "network": "available",
            "hardware_decode": "disabled for compositor and process isolation",
            "player_process": "none",
            "runtime_options": " ".join(self.runtime_options) or "default",
        }

    def open(self, path: Path, subtitle: Path | None = None) -> None:
        self.open_source(path, subtitle=subtitle)

    def open_source(self, source: str | Path, subtitle: Path | None = None) -> None:
        if not self.supports(source):
            raise BackendError(f"unsupported media source: {display_media_source(source)}")
        self.close_media()
        self._seen_playing = False
        self.path = Path(source).resolve() if isinstance(source, Path) else None
        self._state = PlaybackState.LOADING
        value = str(source)
        parsed = urlparse(value)
        if self._is_location(value):
            self._install("libvlc_media_new_location", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p])
            self.media = self.libvlc_media_new_location(self.instance, value.encode("utf-8"))
        else:
            local = self.path or Path(parsed.path)
            self.media = self.libvlc_media_new_path(self.instance, os_path(local))
        if not self.media:
            self._state = PlaybackState.ERROR
            self._last_error_detail = (
                f"libVLC could not create the media object for {display_media_source(source)}")
            raise BackendError(
                f"libVLC could not open {display_media_source(source)}")
        # Some VLC 3 builds let the persisted user preference override the
        # instance argument. Repeat the safety setting as a per-media option.
        if self._subtitle_option_api:
            for option in self.SAFE_MEDIA_OPTIONS:
                self.libvlc_media_add_option(self.media, option.encode("utf-8"))
        if subtitle is not None:
            subtitle = subtitle.expanduser().resolve()
            if not subtitle.is_file():
                raise BackendError(f"subtitle file does not exist: {subtitle}")
            if not self._subtitle_option_api:
                raise BackendError("external subtitle loading is unavailable in this libVLC build")
            option = f":sub-file={subtitle}".encode("utf-8")
            self.libvlc_media_add_option(self.media, option)
        self.player = self.libvlc_media_player_new_from_media(self.media)
        if not self.player: self._state = PlaybackState.ERROR; raise BackendError("libVLC could not create media player")
        if sys.platform.startswith("linux"):
            self.libvlc_media_player_set_xwindow(self.player, self.widget.winfo_id())
        elif sys.platform.startswith("win"):
            self.libvlc_media_player_set_hwnd(self.player, ctypes.c_void_p(self.widget.winfo_id()))
        elif sys.platform == "darwin":
            self.libvlc_media_player_set_nsobject(self.player, ctypes.c_void_p(self.widget.winfo_id()))
        self._attach_events()
        self._state = PlaybackState.READY

    def add_external_subtitle(self, subtitle: Path) -> None:
        """Reopen the current source with a real libVLC subtitle option."""
        if self.path is None:
            raise BackendError("external subtitles require a local media source")
        position = self.position()
        was_playing = self.is_actively_playing()
        self.open_source(self.path, subtitle=subtitle)
        if was_playing:
            self.play()
            if position > 0:
                self.seek(position)

    def _attach_events(self) -> None:
        """Map libVLC lifecycle events to the backend state machine."""
        if not self._event_api or not self.player:
            return
        manager = self.libvlc_media_player_event_manager(self.player)
        if not manager:
            return
        self._event_manager = manager
        # Values are libvlc_event_e media-player constants.  Keeping this
        # optional lets older/minimal libVLC builds continue through polling.
        for event_type, state in LIBVLC_PLAYER_EVENT_STATES.items():
            def callback(_event, _user_data, state=state):
                # The native "stopped" event carries nothing that the
                # synchronous stop() has not already recorded — and when it
                # follows EncounteredError/EndReached it ERASES the terminal
                # fact (a failed open then showed as plain STOPPED and real
                # errors became invisible). Lifecycle noise is ignored; only
                # stop() itself may set STOPPED.
                if state is PlaybackState.STOPPED:
                    return
                if state is PlaybackState.ENDED and self._recent_user_stop():
                    return
                if state is PlaybackState.LOADING:
                    if self._seen_playing:
                        # libVLC re-emits Opening/Buffering while the output
                        # pipeline recovers (observed as an endless buffering
                        # storm when no audio device exists). Once playback
                        # demonstrably started, those events must not
                        # downgrade UI/MPRIS back to LOADING.
                        return
                    self._seen_playing = False
                if state is PlaybackState.PLAYING:
                    self._seen_playing = True
                self._state = state
                listener = self.on_event
                if listener is not None:
                    try:
                        listener(state)
                    except Exception:
                        # Backend callbacks must never bring down libVLC's
                        # worker thread because a UI listener failed.
                        pass
            callback_ref = self._event_callback_type(callback)
            if self.libvlc_event_attach(manager, event_type, callback_ref, None) == 0:
                self._event_callbacks.append((event_type, callback_ref))

    def play(self):
        if not self.player or self.libvlc_media_player_play(self.player) != 0: raise BackendError("libVLC playback could not start")
        self._play_requested_at = time.monotonic()
        self._state = PlaybackState.PLAYING

    def pause(self):
        if self.player: self.libvlc_media_player_set_pause(self.player, 1); self._state = PlaybackState.PAUSED

    def resume(self):
        if self.player: self.libvlc_media_player_set_pause(self.player, 0); self._state = PlaybackState.PLAYING

    def stop(self):
        if self.player:
            # Freeze the clock synchronously so UI/MPRIS never show progress
            # past the user's stop, then hand the potentially slow native
            # stop to a background thread (see _retire_player: the input
            # thread can wedge and libvlc_media_player_stop would otherwise
            # block the caller — historically freezing the whole GUI).
            self.libvlc_media_player_set_pause(self.player, 1)
            self._spawn_stop(self.player)
        self._user_stop_monotonic = time.monotonic()
        self._state = PlaybackState.STOPPED

    def _recent_user_stop(self) -> bool:
        """True within the teardown window after an explicit user stop."""
        stamp = getattr(self, "_user_stop_monotonic", None)
        return stamp is not None and (time.monotonic() - stamp) < 2.0

    def seek(self, seconds: float):
        if self.player: self.libvlc_media_player_set_time(self.player, int(max(0.0, seconds) * 1000))

    def next_frame(self) -> None:
        if not self._frame_step_api or not self.player:
            raise BackendError("frame stepping is unavailable in this libVLC build")
        self.libvlc_media_player_next_frame(self.player)
        self._state = PlaybackState.PAUSED

    def take_snapshot(self, path: str | Path, *, width: int = 0,
                      height: int = 0) -> Path:
        if not self._snapshot_api or not self.player:
            raise BackendError("video snapshots are unavailable in this libVLC build")
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() != ".png":
            raise BackendError("video snapshots must use a .png destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        width = max(0, min(16384, int(width))); height = max(0, min(16384, int(height)))
        if self.libvlc_video_take_snapshot(self.player, 0, os_path(target),
                                           width, height) != 0:
            raise BackendError("libVLC could not capture the current video frame")
        return target

    @staticmethod
    def _video_geometry(value: str | None, *, crop: bool = False) -> bytes | None:
        allowed = ({"16:10", "16:9", "4:3", "5:4", "1:1", "2.21:1", "2.35:1", "2.39:1"}
                   if not crop else
                   {"16:10", "16:9", "4:3", "5:4", "1:1", "2.21:1", "2.35:1", "2.39:1"})
        if value in {None, "", "default", "original"}:
            return None
        if value not in allowed:
            raise BackendError("unsupported video geometry")
        return value.encode("ascii")

    def _owned_vlc_text(self, function) -> str:
        pointer = function(self.player)
        if not pointer:
            return "default"
        try:
            return ctypes.string_at(pointer).decode("utf-8", "replace")
        finally:
            self.libvlc_free(pointer)

    def aspect_ratio(self) -> str:
        if not self._aspect_api or not self.player:
            return "default"
        return self._owned_vlc_text(self.libvlc_video_get_aspect_ratio)

    def set_aspect_ratio(self, value: str | None) -> str:
        if not self._aspect_api or not self.player:
            raise BackendError("aspect-ratio control is unavailable")
        self.libvlc_video_set_aspect_ratio(
            self.player, self._video_geometry(value))
        return value or "default"

    def crop_geometry(self) -> str:
        if not self._crop_api or not self.player:
            return "default"
        return self._owned_vlc_text(self.libvlc_video_get_crop_geometry)

    def set_crop_geometry(self, value: str | None) -> str:
        if not self._crop_api or not self.player:
            raise BackendError("crop control is unavailable")
        self.libvlc_video_set_crop_geometry(
            self.player, self._video_geometry(value, crop=True))
        return value or "default"

    def scale(self) -> float:
        return float(self.libvlc_video_get_scale(self.player)) if self._scale_api and self.player else 0.0

    def set_scale(self, value: float) -> float:
        if not self._scale_api or not self.player:
            raise BackendError("video zoom is unavailable")
        scale = float(value)
        if scale != 0.0 and not 0.25 <= scale <= 4.0:
            raise BackendError("video zoom must be automatic or between 0.25x and 4x")
        self.libvlc_video_set_scale(self.player, ctypes.c_float(scale))
        return scale

    def set_deinterlace(self, mode: str | None) -> str:
        if not self._deinterlace_api or not self.player:
            raise BackendError("deinterlacing is unavailable")
        allowed = {None, "", "off", "auto", "blend", "bob", "discard", "linear", "mean", "x", "yadif", "yadif2x"}
        if mode not in allowed:
            raise BackendError("unsupported deinterlace mode")
        encoded = None if mode in {None, "", "off"} else str(mode).encode("ascii")
        self.libvlc_video_set_deinterlace(self.player, encoded)
        return mode or "off"

    def title_count(self) -> int:
        return max(0, int(self.libvlc_media_player_get_title_count(self.player))) if self._chapter_api and self.player else 0

    def title(self) -> int:
        return int(self.libvlc_media_player_get_title(self.player)) if self._chapter_api and self.player else -1

    def set_title(self, title: int) -> None:
        if not self._chapter_api or not self.player:
            raise BackendError("title selection is unavailable")
        value = int(title)
        if value < 0 or value >= self.title_count():
            raise BackendError("title index is out of range")
        self.libvlc_media_player_set_title(self.player, value)

    def chapter_count(self) -> int:
        if not self._chapter_api or not self.player:
            return 0
        title = int(self.libvlc_media_player_get_title(self.player))
        return max(0, int(self.libvlc_media_player_get_chapter_count(self.player, title)))

    def chapter(self) -> int:
        return int(self.libvlc_media_player_get_chapter(self.player)) if self._chapter_api and self.player else -1

    def set_chapter(self, chapter: int) -> None:
        if not self._chapter_api or not self.player:
            raise BackendError("chapter selection is unavailable in this libVLC build")
        self.libvlc_media_player_set_chapter(self.player, int(chapter))

    def chapter_descriptors(self) -> tuple[ChapterDescriptor, ...]:
        return tuple(ChapterDescriptor(index, 0.0, f"Chapter {index + 1}")
                     for index in range(self.chapter_count()))

    def set_rate(self, rate: float) -> float:
        if not self.player:
            raise BackendError("no active media player")
        rate = max(0.25, min(4.0, float(rate)))
        if self.libvlc_media_player_set_rate(self.player, ctypes.c_float(rate)) == -1:
            raise BackendError("libVLC rejected playback rate")
        return float(self.libvlc_media_player_get_rate(self.player))

    def rate(self) -> float:
        return float(self.libvlc_media_player_get_rate(self.player)) if self.player else 1.0

    def position(self) -> float:
        return max(0.0, float(self.libvlc_media_player_get_time(self.player) if self.player else 0) / 1000.0)

    def duration(self) -> float:
        return max(0.0, float(self.libvlc_media_player_get_length(self.player) if self.player else 0) / 1000.0)

    def state(self) -> PlaybackState:
        # After an explicit user stop the winding-down player may report
        # "ended" for a while. Real faults (raw state 7) always surface; a
        # synthetic ENDED inside the user-stop window must not, because it
        # would re-trigger end-of-media auto-advance after a manual stop.
        ended_reconcile_allowed = not self._recent_user_stop()
        if getattr(self, "_player_state_api", False) and self.player:
            player_state = int(self.libvlc_media_player_get_state(self.player))
            if player_state == 7:
                self._note_error()
                self._state = PlaybackState.ERROR
            elif (player_state == 6 and self._state is not PlaybackState.ERROR
                    and ended_reconcile_allowed):
                self._state = PlaybackState.ENDED
        if self._media_state_api and self.media:
            # libVLC media states: 6=Ended, 7=Error.  Opening/buffering are
            # deliberately left to the requested controller state.
            media_state = int(self.libvlc_media_get_state(self.media))
            if media_state == 7:
                self._note_error()
                self._state = PlaybackState.ERROR
            elif (media_state == 6 and self._state is not PlaybackState.ERROR
                    and ended_reconcile_allowed):
                self._state = PlaybackState.ENDED
        # NOTE: deliberately NO forced PLAYING reconciliation here. A variant
        # that flipped the requested state to PLAYING whenever
        # libvlc_media_player_is_playing() said so shipped in a build that
        # regressed against the verified v5.0.0 release (phantom PLAYING
        # during async teardown windows). The verified release reconciles
        # only via the event table above.
        if self.player and self._state == PlaybackState.PLAYING and not self.libvlc_media_player_is_playing(self.player):
            if self.duration() and self.position() >= self.duration() - 0.2: self._state = PlaybackState.ENDED
        if (self.player and self._state is PlaybackState.ENDED
                and self.position() == 0.0 and self.duration() == 0.0
                and self.audio_track_count() == 0 and self.video_track_count() == 0
                and getattr(self, "_play_requested_at", None) is not None
                and time.monotonic() - self._play_requested_at >= 0.5):
            # VLC 3 can report Ended rather than EncounteredError when an
            # access module (for example HTTP 404) never opened any stream.
            # Zero-time EOF with no playable track is not successful EOF.
            self._note_error()
            self._state = PlaybackState.ERROR
        return self._state

    def _note_error(self) -> None:
        """Record a short libVLC diagnostic the first time an error state hits."""
        if self._state is PlaybackState.ERROR:
            return
        try:
            duration = self.duration()
            position = self.position()
            audio = self.audio_track_count()
            video = self.video_track_count()
        except Exception:
            # Partial-init/stub backends may lack optional track APIs.
            duration = position = audio = video = 0.0
        media_state = self.media_state_code()
        self._last_error_detail = (
            f"libVLC access/demux failed · media_state={media_state} "
            f"duration={duration:.1f}s position={position:.1f}s "
            f"audio_tracks={audio} video_tracks={video}")

    def last_error(self) -> str:
        """Short diagnostic for the last libVLC error transition."""
        detail = getattr(self, "_last_error_detail", "")
        return detail or "libVLC reported an access/demux failure"

    def media_state_code(self) -> int | None:
        """Return the raw libVLC media state when that API exists."""
        return int(self.libvlc_media_get_state(self.media)) if self._media_state_api and self.media else None

    def is_actively_playing(self) -> bool:
        """Return the backend's real playing flag, not just requested state."""
        return bool(self.player and self.libvlc_media_player_is_playing(self.player))

    def set_volume(self, value: int) -> int:
        if not self.player: return 0
        value = max(0, min(200, int(value)))
        if self.libvlc_audio_set_volume(self.player, value) != 0:
            raise BackendError("libVLC rejected the requested volume")
        return value

    def volume(self) -> int:
        return max(0, int(self.libvlc_audio_get_volume(self.player) if self.player else 0))

    def set_mute(self, muted: bool) -> None:
        if self.player: self.libvlc_audio_set_mute(self.player, int(bool(muted)))

    def audio_channel(self) -> int:
        if not self._audio_channel_api or not self.player:
            return 0
        return int(self.libvlc_audio_get_channel(self.player))

    def set_audio_channel(self, channel: int) -> int:
        if not self._audio_channel_api or not self.player:
            raise BackendError("audio channel control is unavailable")
        value = int(channel)
        if value not in {1, 2, 3, 4, 5}:
            raise BackendError("unsupported audio channel mode")
        if self.libvlc_audio_set_channel(self.player, value) != 0:
            raise BackendError("libVLC rejected the audio channel mode")
        return value

    def equalizer_presets(self) -> tuple[str, ...]:
        if not self._equalizer_api:
            return ()
        count = min(256, int(self.libvlc_audio_equalizer_get_preset_count()))
        return tuple(
            ((self.libvlc_audio_equalizer_get_preset_name(index) or b"")
             .decode("utf-8", "replace") or f"Preset {index + 1}")
            for index in range(count))

    def set_equalizer_preset(self, preset: int | None) -> str:
        if not self._equalizer_api or not self.player:
            raise BackendError("audio equalizer is unavailable")
        if preset is None:
            if self.libvlc_media_player_set_equalizer(self.player, None) != 0:
                raise BackendError("libVLC could not disable the equalizer")
            return "off"
        value = int(preset)
        names = self.equalizer_presets()
        if value < 0 or value >= len(names):
            raise BackendError("equalizer preset index is out of range")
        equalizer = self.libvlc_audio_equalizer_new_from_preset(value)
        if not equalizer:
            raise BackendError("libVLC could not create the equalizer preset")
        try:
            if self.libvlc_media_player_set_equalizer(self.player, equalizer) != 0:
                raise BackendError("libVLC rejected the equalizer preset")
        finally:
            self.libvlc_audio_equalizer_release(equalizer)
        return names[value]

    def set_audio_delay(self, milliseconds: float) -> float:
        value = max(-5000.0, min(5000.0, float(milliseconds)))
        if not self._audio_delay_api or not self.player:
            raise BackendError("audio delay is unavailable in this libVLC build")
        if self.libvlc_audio_set_delay(self.player, int(value * 1000)) != 0:
            raise BackendError("libVLC rejected audio delay")
        return value

    def set_subtitle_delay(self, milliseconds: float) -> float:
        value = max(-5000.0, min(5000.0, float(milliseconds)))
        if not self._subtitle_delay_api or not self.player:
            raise BackendError("subtitle delay is unavailable in this libVLC build")
        if self.libvlc_video_set_spu_delay(self.player, int(value * 1000)) != 0:
            raise BackendError("libVLC rejected subtitle delay")
        return value

    def audio_track_count(self) -> int:
        return max(0, int(self.libvlc_audio_get_track_count(self.player) if self.player else 0))

    def audio_track(self) -> int:
        return int(self.libvlc_audio_get_track(self.player) if self.player else -1)

    def set_audio_track(self, track: int) -> None:
        if self.player and self.libvlc_audio_set_track(self.player, int(track)) != 0:
            raise BackendError(f"libVLC rejected audio track {track}")

    def audio_track_descriptions(self) -> list[tuple[int, str]]:
        return self._track_descriptions(self.libvlc_audio_get_track_description) if self._audio_description_api and self.player else []

    def video_track_count(self) -> int:
        return max(0, int(self.libvlc_video_get_track_count(self.player) if self._video_track_api and self.player else 0))

    def video_track(self) -> int:
        return int(self.libvlc_video_get_track(self.player) if self._video_track_api and self.player else -1)

    def set_video_track(self, track: int) -> None:
        if not self._video_track_api:
            raise BackendError("video track selection is unavailable in this libVLC build")
        if self.player and self.libvlc_video_set_track(self.player, int(track)) != 0:
            raise BackendError(f"libVLC rejected video track {track}")

    def video_track_descriptions(self) -> list[tuple[int, str]]:
        return self._track_descriptions(self.libvlc_video_get_track_description) if self._video_description_api and self.player else []

    def subtitle_track_count(self) -> int:
        if not self._subtitle_api:
            return 0
        return max(0, int(self.libvlc_video_get_spu_count(self.player) if self.player else 0))

    def subtitle_track(self) -> int:
        if not self._subtitle_api:
            return -1
        return int(self.libvlc_video_get_spu(self.player) if self.player else -1)

    def set_subtitle_track(self, track: int) -> None:
        if not self._subtitle_api:
            raise BackendError("subtitle selection is unavailable in this libVLC build")
        if self.player and self.libvlc_video_set_spu(self.player, int(track)) != 0:
            raise BackendError(f"libVLC rejected subtitle track {track}")

    def subtitle_track_descriptions(self) -> list[tuple[int, str]]:
        return self._track_descriptions(self.libvlc_video_get_spu_description) if self._subtitle_description_api and self.player else []

    def track_descriptors(self, kind: TrackKind) -> tuple[TrackDescriptor, ...]:
        getters = {
            TrackKind.VIDEO: self.video_track_descriptions,
            TrackKind.AUDIO: self.audio_track_descriptions,
            TrackKind.SUBTITLE: self.subtitle_track_descriptions,
        }
        return tuple(TrackDescriptor(identifier, kind, label or f"{kind.value} {identifier}")
                     for identifier, label in getters[TrackKind(kind)]())

    def audio_devices(self) -> tuple[AudioDeviceDescriptor, ...]:
        if not self._audio_device_api or not self.player:
            return ()
        pointer = self.libvlc_audio_output_device_enum(self.player)
        if not pointer:
            return ()
        devices = []
        current = pointer
        try:
            seen = 0
            while current and seen < 1024:
                item = current.contents
                identifier = (item.device or b"").decode("utf-8", "replace")
                label = (item.description or item.device or b"").decode("utf-8", "replace")
                if identifier:
                    devices.append(AudioDeviceDescriptor(identifier, label, "libVLC"))
                current = item.next
                seen += 1
        finally:
            self.libvlc_audio_output_device_list_release(pointer)
        return tuple(devices)

    def set_audio_device(self, identifier: str) -> None:
        if not self._audio_device_api or not self.player:
            raise BackendError("audio-device selection is unavailable in this libVLC build")
        self.libvlc_audio_output_device_set(self.player, None, str(identifier).encode("utf-8"))

    def _track_descriptions(self, getter) -> list[tuple[int, str]]:
        pointer = getter(self.player)
        if not pointer:
            return []
        values: list[tuple[int, str]] = []
        try:
            current = pointer
            seen = 0
            while current and seen < 256:
                item = current.contents
                # libVLC commonly prepends a synthetic -1 "Disable" entry.
                # It is not a media track, but it must not terminate traversal.
                if item.identifier >= 0:
                    values.append((
                        int(item.identifier),
                        (item.name or b"").decode("utf-8", "replace")))
                current = item.next
                seen += 1
        finally:
            self.libvlc_track_description_release(pointer)
        return values

    def _spawn_stop(self, player) -> threading.Thread:
        """Run libvlc_media_player_stop for *player* off the calling thread."""
        key = id(player)
        with self._teardown_lock:
            pending = self._pending_stops.get(key)
            if pending is not None and pending.is_alive():
                return pending  # a stop on this handle is already running

        def worker():
            try:
                self.libvlc_media_player_stop(player)
            finally:
                with self._teardown_lock:
                    self._pending_stops.pop(key, None)

        thread = threading.Thread(target=worker, name="mpcasu-vlc-stop",
                                  daemon=True)
        with self._teardown_lock:
            self._pending_stops[key] = thread
        thread.start()
        return thread

    def _retire_player(self, player, media) -> None:
        """Stop + release an out-of-service player without blocking anyone.

        Called with handles that are no longer reachable from the public
        backend state. The stop of a PREVIOUS async stop (if still running)
        is joined here — in this background thread, never in the caller's.
        Releases rely on libVLC refcounting (verified clean over repeated
        open/play/stop cycles with correctly typed handles): the dangerous
        pattern is blocking the CALLER on a wedged input thread, which this
        design makes impossible.
        """
        try:
            with self._teardown_lock:
                pending = self._pending_stops.pop(id(player), None)
            if pending is not None:
                pending.join()
            self.libvlc_media_player_stop(player)
            time.sleep(0.05)
        finally:
            self.libvlc_media_player_release(player)
            if media is not None:
                self.libvlc_media_release(media)

    def close_media(self):
        if self._event_manager and self._event_api:
            for event_type, callback_ref in self._event_callbacks:
                try:
                    self.libvlc_event_detach(self._event_manager, event_type, callback_ref, None)
                except (OSError, ctypes.ArgumentError):
                    pass
        self._event_callbacks.clear()
        self._event_manager = None
        # Detach first, then hand the native objects to a retirement thread.
        # Releasing inline would re-introduce the GUI freeze this class
        # exists to prevent: close_media runs on every source switch.
        player, self.player = self.player, None
        media, self.media = self.media, None
        self._user_stop_monotonic = None
        if player is not None:
            def retire(p=player, m=media):
                try:
                    self._retire_player(p, m)
                finally:
                    with self._teardown_lock:
                        for thread in list(self._retiring):
                            if not thread.is_alive():
                                self._retiring.discard(thread)
            worker = threading.Thread(target=retire,
                                      name="mpcasu-vlc-retire", daemon=True)
            with self._teardown_lock:
                self._retiring.add(worker)
            worker.start()
        elif media is not None:
            self.libvlc_media_release(media)
        if self._native_temp is not None:
            try:
                self._native_temp.unlink(missing_ok=True)
            except OSError:
                pass
            self._native_temp = None

    def close(self):
        self.close_media()
        # No waiting on retirement threads here: libVLC refcounting keeps
        # the instance alive until the last player/media is released, so an
        # in-flight retirement can never dangle. Waiting would re-introduce
        # a GUI freeze exactly in the wedge case this class defends against.
        if self.instance: self.libvlc_release(self.instance)
        self.instance = None; self._state = PlaybackState.EMPTY


class LegacyCasuBackend(LibVLCBackend):
    """CASUNAT1/JSON compatibility path, intentionally separate from CASUNAT2."""

    def capabilities(self) -> dict[str, str]:
        values = super().capabilities()
        values.update({"backend_path": "CASUNAT1 envelope or JSON sidecar via libVLC",
                       "native_casu_payload": "no; verified compatibility extraction"})
        return values

    def open_casu(self, manifest_path: Path) -> None:
        manifest_path = manifest_path.expanduser().resolve()
        try:
            with manifest_path.open("rb") as handle:
                magic = handle.read(8)
        except OSError as exc:
            raise BackendError(f"could not read CASU file: {manifest_path}") from exc
        if magic == b"CASUMP5\x00":
            try:
                extracted = mp5_extract_source(
                    manifest_path, Path(tempfile.mkdtemp(prefix="mpcasu-mp5-")))
            except (Mp5Error, OSError) as exc:
                raise BackendError(f"invalid MP5 container: {exc}") from exc
            self.open_source(extracted)
            self._native_temp = extracted
            self.path = manifest_path
            return
        is_native = magic == b"CASUNAT1"
        if is_native:
            try:
                container = read_native(manifest_path, verify_payload=True)
                suffix = Path(container.manifest.get("source", {}).get("filename", "media.bin")).suffix or ".bin"
                fd, temporary = tempfile.mkstemp(prefix="mpcasu-native-", suffix=suffix)
                os.close(fd)
                extracted = container.extract_payload(Path(temporary))
                # open_source closes any previous media; assign ownership only
                # after it succeeds so cleanup cannot remove the active file.
                self.open_source(extracted)
                self._native_temp = extracted
                self.path = manifest_path
                return
            except (NativeCasuError, OSError, BackendError) as exc:
                raise BackendError(f"invalid native CASU container: {exc}") from exc
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BackendError(f"invalid CASU manifest: {manifest_path}") from exc
        errors = validate_manifest(manifest)
        if errors:
            raise BackendError(f"invalid CASU manifest: {errors[0]}")
        self.open(resolve_casu_source(manifest_path))


# Public compatibility alias retained for existing callers.  The actual
# native decoder is NativeCasuBackend in mpcasu_native_backend.py.
CasuBackend = LegacyCasuBackend


def os_path(path: Path) -> bytes:
    return str(path).encode(sys.getfilesystemencoding(), errors="surrogateescape")

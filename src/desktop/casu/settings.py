"""Validated, atomic MPCASU settings."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .core import CasuError
from .fileio import atomic_write_json, read_bounded_json


MAX_SETTINGS_BYTES = 1024 * 1024
MAX_WATCHED_FOLDERS = 100
MAX_SETTING_TEXT_BYTES = 4096


@dataclass(frozen=True)
class PlayerSettings:
    volume: int = 100
    muted: bool = False
    rate: float = 1.0
    audio_device: str | None = None
    watched_folders: tuple[str, ...] = ()
    ytdlp_consent: bool = False
    visualizer: str = "waveform"
    resume_playback: bool = True
    cache_limit_mib: int = 512
    recordings_dir: str = ""
    record_split_minutes: int = 0
    record_format: str = "mkv"
    shuffle: bool = False
    repeat_mode: str = "off"
    record_split_mode: str = "continuous"

    def validated(self) -> "PlayerSettings":
        rate = float(self.rate)
        if not math.isfinite(rate):
            rate = 1.0
        folders = tuple(str(Path(value).expanduser()) for value in self.watched_folders)
        if len(folders) > MAX_WATCHED_FOLDERS or any(
                "\0" in value or len(value.encode("utf-8")) > MAX_SETTING_TEXT_BYTES
                for value in folders):
            folders = ()
        device = str(self.audio_device) if self.audio_device else None
        if device and ("\0" in device or len(device.encode("utf-8")) > MAX_SETTING_TEXT_BYTES):
            device = None
        visualizer = str(self.visualizer)
        # Spectrum/FFT modes were retired by product decision — the player
        # ships the oscilloscope waveform only. Old saved values migrate
        # instead of silently falling back to a mode that no longer exists.
        if visualizer not in {"waveform", "off"}:
            visualizer = "waveform"
        try:
            cache_limit = int(self.cache_limit_mib)
        except (TypeError, ValueError):
            cache_limit = 512
        recordings = str(self.recordings_dir or "")
        if recordings and ("\0" in recordings
                           or len(recordings.encode("utf-8")) > MAX_SETTING_TEXT_BYTES):
            recordings = ""
        try:
            split_minutes = int(self.record_split_minutes)
        except (TypeError, ValueError):
            split_minutes = 0
        record_format = str(self.record_format or "mkv").lower()
        if record_format not in {"mkv", "mp4", "ts", "webm", "ogg", "mp3", "flac", "wav"}:
            record_format = "mkv"
        repeat_mode = str(self.repeat_mode or "off")
        if repeat_mode not in {"off", "all", "one"}:
            repeat_mode = "off"
        split_mode = str(self.record_split_mode or "continuous")
        if split_mode not in {"continuous", "time", "track", "tags"}:
            split_mode = "continuous"
        return PlayerSettings(max(0, min(200, int(self.volume))), bool(self.muted),
                              max(0.25, min(4.0, rate)), device, folders,
                              bool(self.ytdlp_consent), visualizer,
                              bool(self.resume_playback),
                              max(0, min(65536, cache_limit)),
                              recordings,
                              max(0, min(24 * 60, split_minutes)),
                              record_format,
                              bool(self.shuffle), repeat_mode, split_mode)


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def load(self) -> PlayerSettings:
        try:
            value = read_bounded_json(self.path, max_bytes=MAX_SETTINGS_BYTES,
                                      label="player settings")
            if not isinstance(value, dict):
                return PlayerSettings()
            if value.get("version") != 1:
                return PlayerSettings()
            settings = value.get("player", {})
            if not isinstance(settings, dict):
                return PlayerSettings()
            watched = settings.get("watched_folders", ())
            if not isinstance(watched, (list, tuple)):
                watched = ()
            return PlayerSettings(
                settings.get("volume", 100), settings.get("muted", False),
                settings.get("rate", 1.0), settings.get("audio_device"),
                tuple(watched),
                settings.get("ytdlp_consent", False),
                settings.get("visualizer", "waveform"),
                settings.get("resume_playback", True),
                settings.get("cache_limit_mib", 512),
                settings.get("recordings_dir", ""),
                settings.get("record_split_minutes", 0),
                settings.get("record_format", "mkv"),
                settings.get("shuffle", False),
                settings.get("repeat_mode", "off"),
                settings.get("record_split_mode", "continuous"),
            ).validated()
        except (OSError, TypeError, ValueError, CasuError):
            return PlayerSettings()

    def save(self, settings: PlayerSettings) -> None:
        atomic_write_json(self.path,
                          {"version": 1, "player": asdict(settings.validated())},
                          max_bytes=MAX_SETTINGS_BYTES)

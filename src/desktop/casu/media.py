"""Backend-neutral immutable media and playback contracts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class TrackKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"


@dataclass(frozen=True)
class TrackDescriptor:
    identifier: int
    kind: TrackKind
    label: str
    language: str | None = None
    codec: str | None = None
    default: bool = False
    forced: bool = False
    stream_index: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    sample_rate: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class ChapterDescriptor:
    identifier: int
    start_seconds: float
    title: str
    end_seconds: float | None = None


@dataclass(frozen=True)
class AudioDeviceDescriptor:
    identifier: str
    label: str
    backend: str
    default: bool = False


@dataclass(frozen=True)
class PlaybackEvent:
    state: str
    position_seconds: float
    message: str | None = None
    error_code: str | None = None


@runtime_checkable
class MediaBackend(Protocol):
    """Transport surface required by PlaybackController and MPCASU UI."""
    on_event: object

    def play(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def position(self) -> float: ...
    def duration(self) -> float: ...
    def state(self): ...
    def set_rate(self, rate: float) -> float: ...
    def set_volume(self, value: int) -> int: ...
    def set_mute(self, muted: bool) -> None: ...
    def track_descriptors(self, kind: TrackKind) -> tuple[TrackDescriptor, ...]: ...
    def chapter_descriptors(self) -> tuple[ChapterDescriptor, ...]: ...
    def set_audio_delay(self, milliseconds: float) -> float: ...
    def set_subtitle_delay(self, milliseconds: float) -> float: ...
    def capabilities(self) -> dict[str, str]: ...
    def close(self) -> None: ...

# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Playback state/control boundary used by MPCASU's UI.

The controller owns lifecycle and transport semantics; concrete backends own
decoding. This keeps the UI independent from libVLC and from CASU parsing.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class ControllerState(str, Enum):
    EMPTY = "EMPTY"
    LOADING = "LOADING"
    READY = "READY"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ENDED = "ENDED"
    ERROR = "ERROR"


class PlaybackController:
    def __init__(self) -> None:
        self.backend: Any | None = None
        self.source: Any | None = None
        self.state = ControllerState.EMPTY
        self.last_error: str | None = None

    def attach(self, backend: Any, source: Any) -> None:
        self.close()
        self.state = ControllerState.LOADING
        self.backend = backend
        self.source = source
        try:
            self.state = ControllerState.READY
        except Exception as exc:  # defensive boundary for third-party backends
            self.state = ControllerState.ERROR
            self.last_error = str(exc)
            raise

    def play(self) -> None:
        self._require_backend()
        self.backend.play()
        self.state = ControllerState.PLAYING

    def pause_or_resume(self) -> None:
        self._require_backend()
        if self.state == ControllerState.PAUSED:
            self.backend.resume()
            self.state = ControllerState.PLAYING
        else:
            self.backend.pause()
            self.state = ControllerState.PAUSED

    def stop(self) -> None:
        if self.backend is not None:
            self.backend.stop()
        self.state = ControllerState.STOPPED if self.backend is not None else ControllerState.EMPTY

    def seek(self, seconds: float) -> None:
        self._require_backend()
        self.backend.seek(max(0.0, float(seconds)))

    def position(self) -> float:
        return float(self.backend.position()) if self.backend is not None else 0.0

    def duration(self) -> float:
        return float(self.backend.duration()) if self.backend is not None else 0.0

    def close(self) -> None:
        if self.backend is not None:
            self.backend.close()
        self.backend = None
        self.source = None
        self.state = ControllerState.EMPTY

    def detach(self) -> Any | None:
        """Give ownership back without invoking a potentially blocking decoder."""
        backend = self.backend
        self.backend = None
        self.source = None
        self.state = ControllerState.EMPTY
        return backend

    def _require_backend(self) -> None:
        if self.backend is None:
            raise RuntimeError("no media backend is attached")

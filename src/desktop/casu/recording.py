"""Atomic, fail-closed recording for MPCASU local and network sources."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .core import CasuError, ffprobe


class RecordingError(CasuError):
    pass


class MediaRecorder:
    """Record all available streams and publish only a verified final file."""

    def __init__(self, source: str | Path, destination: str | Path):
        self.source = str(source)
        if not self.source or "\0" in self.source or len(self.source.encode("utf-8")) > 8192:
            raise RecordingError("recording source is invalid")
        self.destination = Path(destination).expanduser().resolve()
        if not self.destination.suffix or self.destination.suffix.lower() not in {
                ".mkv", ".mp4", ".mov", ".ts", ".m2ts", ".webm", ".ogg", ".mp3", ".flac", ".wav"}:
            raise RecordingError("recording destination format is unsupported")
        try:
            if Path(self.source).expanduser().resolve() == self.destination:
                raise RecordingError("recording cannot overwrite its source")
        except (OSError, ValueError):
            pass
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.destination.stem}.recording-",
            suffix=self.destination.suffix, dir=self.destination.parent)
        os.close(fd); os.unlink(temporary)
        self.temporary = Path(temporary)
        self.process: subprocess.Popen | None = None
        self.started_at = 0.0

    @property
    def active(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.process is not None:
            raise RecordingError("recording has already been started")
        executable = shutil.which("ffmpeg")
        if not executable:
            raise RecordingError("FFmpeg is required for recording")
        command = self._build_command(executable)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
        except OSError as exc:
            self._cleanup()
            raise RecordingError(f"could not start recording: {exc}") from exc
        self.started_at = time.monotonic()

    def _build_command(self, executable: str) -> list[str]:
        """Build the ffmpeg command depending on target format.

        Video containers (mp4/mkv/webm/ts/mov/m2ts) stream-copy ALL streams so
        video AND audio are recorded and the video picture is never switched
        off. Audio-only containers (mp3/ogg/flac/wav) drop the video track and
        keep/transcode just the audio — recording a video to MP3 works.
        """
        suffix = self.destination.suffix.lower()
        audio_only = suffix in {".mp3", ".ogg", ".flac", ".wav"}
        command = [executable, "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-i", self.source]
        if audio_only:
            # Keep audio only; transcode to the target codec inside the container.
            command += ["-map", "0:a:0?"]
            if suffix == ".mp3":
                command += ["-acodec", "libmp3lame", "-q:a", "2"]
            elif suffix == ".ogg":
                command += ["-acodec", "libvorbis", "-q:a", "5"]
            elif suffix == ".flac":
                command += ["-acodec", "flac"]
            elif suffix == ".wav":
                command += ["-acodec", "pcm_s16le"]
            command += ["-vn"]
        else:
            command += ["-map", "0", "-map_metadata", "0", "-map_chapters", "0",
                        "-c", "copy"]
        command += ["-y", str(self.temporary)]
        return command

    def finish(self, *, timeout: float = 10.0) -> Path:
        if self.process is None:
            raise RecordingError("recording was not started")
        process = self.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=max(1.0, min(30.0, float(timeout))))
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        # FFmpeg returns 255 on a clean SIGTERM in some builds. The actual
        # muxed file and probe are authoritative; a missing/invalid trailer is
        # rejected below.
        if not self.temporary.is_file() or self.temporary.stat().st_size <= 0:
            self._cleanup(); raise RecordingError("recording produced no media")
        try:
            probe = ffprobe(self.temporary)
            if not any(item.get("codec_type") in {"audio", "video"}
                       for item in probe.get("streams", [])):
                raise RecordingError("recording has no playable audio/video stream")
            os.replace(self.temporary, self.destination)
            try:
                directory = os.open(self.destination.parent, os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            except OSError:
                pass
        except (OSError, CasuError, ValueError) as exc:
            self._cleanup()
            if isinstance(exc, RecordingError): raise
            raise RecordingError(f"recording verification failed: {exc}") from exc
        return self.destination

    def abort(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
        self._cleanup()

    def _cleanup(self) -> None:
        try: self.temporary.unlink()
        except FileNotFoundError: pass

    def __del__(self):  # pragma: no cover - defensive process/file cleanup
        try:
            if self.active: self.abort()
        except Exception:
            pass

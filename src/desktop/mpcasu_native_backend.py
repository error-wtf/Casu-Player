# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Native CASUNAT2 playback without legacy-container extraction.

The decoder reconstructs CASU video states from indexed key states and tile
updates and sends canonical PCM blocks directly to an audio sink.  The sinks
are deliberately small interfaces so the same clock can drive the real UI
and deterministic instrumented acceptance tests.
"""
from __future__ import annotations

import binascii
import ctypes
import json
import struct
import sys
import threading
import time
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Protocol

import numpy as np
import zlib

from casu.libass import LibassError, LibassRenderer
from casu.native_v2 import (NativeV2Error, decode_attachment,
                            decode_bitmap_subtitle, read_native_v2)
from casu.native_v2.audio import AudioBlock, decode_audio_block
from casu.native_v2.format import ChunkType
from casu.native_v2.text import decode_chapter_table, decode_subtitle_packet
from casu.media import (AudioDeviceDescriptor, ChapterDescriptor,
                        TrackDescriptor, TrackKind)
from casu.probe import ProbeError, run_bounded
from casu.strict.canonical import CanonicalFrame
from mpcasu_backend import BackendError, PlaybackState

MAX_AUDIO_LATENCY_SECONDS = 60.0
MAX_AUDIO_DEVICES = 128


def pipewire_audio_devices() -> tuple[AudioDeviceDescriptor, ...]:
    """Return bounded PipeWire sink nodes with a universal default fallback."""
    fallback = AudioDeviceDescriptor("default", "System Default", "PulseAudio", True)
    try:
        payload = json.loads(run_bounded(
            ["pw-dump"], max_output_bytes=4 * 1024 * 1024,
            max_error_bytes=256 * 1024, timeout_seconds=2.0))
    except (ProbeError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return (fallback,)
    if not isinstance(payload, list) or len(payload) > 10_000:
        return (fallback,)
    devices = [fallback]
    seen = {"default"}
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "PipeWire:Interface:Node":
            continue
        props = item.get("info", {}).get("props", {})
        if not isinstance(props, dict) or props.get("media.class") != "Audio/Sink":
            continue
        identifier = str(props.get("node.name") or "")[:512]
        if not identifier or "\0" in identifier or identifier in seen:
            continue
        label = str(props.get("node.description") or props.get("node.nick")
                    or identifier)[:256]
        devices.append(AudioDeviceDescriptor(identifier, label, "PipeWire", False))
        seen.add(identifier)
        if len(devices) >= MAX_AUDIO_DEVICES:
            break
    return tuple(devices)


class VideoSink(Protocol):
    def present(self, frame: CanonicalFrame, pts_seconds: float) -> None: ...
    def present_cover(self, data: bytes, media_type: str) -> None: ...
    def present_subtitle_rgba(self, rgba: np.ndarray, pts_seconds: float) -> None: ...
    def clear_subtitle(self) -> None: ...
    def invalidate(self) -> None: ...
    def close(self) -> None: ...


class AudioSink(Protocol):
    def write(self, block: AudioBlock) -> None: ...
    def flush(self) -> None: ...
    def reset_format(self) -> None: ...
    def close(self) -> None: ...
    def set_volume(self, value: int) -> None: ...
    def set_mute(self, muted: bool) -> None: ...
    def latency_seconds(self) -> float | None: ...


class NullVideoSink:
    def present(self, frame: CanonicalFrame, pts_seconds: float) -> None:
        pass

    def invalidate(self) -> None:
        pass

    def present_cover(self, data: bytes, media_type: str) -> None:
        pass

    def present_subtitle_rgba(self, rgba: np.ndarray, pts_seconds: float) -> None:
        pass

    def clear_subtitle(self) -> None:
        pass

    def present_subtitle(self, text: str | None, pts_seconds: float) -> None:
        pass

    def close(self) -> None:
        pass


class NullAudioSink:
    def write(self, block: AudioBlock) -> None:
        pass

    def flush(self) -> None:
        pass

    def reset_format(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_volume(self, value: int) -> None:
        pass

    def set_mute(self, muted: bool) -> None:
        pass

    def latency_seconds(self) -> float | None:
        return None


def resample_audio_block(block: AudioBlock, rate: float) -> AudioBlock:
    """Resample interleaved s16le PCM for bounded native speed playback.

    This is deterministic linear interpolation, not pitch-preserving time
    stretching. The sink sample rate stays unchanged while frame count changes.
    """
    value = float(rate)
    if not np.isfinite(value) or value < 0.25 or value > 4.0:
        raise BackendError("native audio rate must be finite and between 0.25x and 4x")
    if value == 1.0 or block.sample_count == 0:
        return block
    if block.sample_format != "s16le" or block.channels <= 0:
        raise BackendError("native rate resampling requires interleaved s16le PCM")
    samples = np.frombuffer(block.pcm, dtype="<i2")
    expected = block.sample_count * block.channels
    if samples.size != expected:
        raise BackendError("native PCM block size does not match its sample geometry")
    frames = samples.reshape(block.sample_count, block.channels).astype(np.float64)
    output_count = max(1, int(round(block.sample_count / value)))
    positions = np.minimum(np.arange(output_count, dtype=np.float64) * value,
                           block.sample_count - 1)
    lower = np.floor(positions).astype(np.int64)
    upper = np.minimum(lower + 1, block.sample_count - 1)
    weight = (positions - lower)[:, None]
    output = frames[lower] * (1.0 - weight) + frames[upper] * weight
    pcm = np.clip(np.rint(output), -32768, 32767).astype("<i2").tobytes()
    return replace(block, sample_count=output_count, pcm=pcm)


def canonical_to_rgb(frame: CanonicalFrame) -> np.ndarray:
    """Convert a canonical frame to display RGB while retaining source size."""
    fmt = frame.pixel_format
    height, width = frame.shape
    if fmt in {"rgb24", "bgr24", "rgba", "bgra", "argb", "abgr"}:
        packed = frame.planes[0].reshape(height, width, -1)
        orders = {
            "rgb24": (0, 1, 2), "bgr24": (2, 1, 0),
            "rgba": (0, 1, 2), "bgra": (2, 1, 0),
            "argb": (1, 2, 3), "abgr": (3, 2, 1),
        }
        return np.ascontiguousarray(packed[..., list(orders[fmt])].astype(np.uint8))
    if fmt == "rgba64le":
        packed = frame.planes[0].reshape(height, width, 4)
        return np.ascontiguousarray((packed[..., :3] >> 8).astype(np.uint8))
    if fmt in {"gray", "gray8", "gray16le"}:
        values = frame.planes[0]
        if values.dtype.itemsize == 2:
            values = values >> 8
        return np.repeat(values.astype(np.uint8)[..., None], 3, axis=2)
    if not (fmt.startswith("yuv") or fmt.startswith("yuva")):
        raise BackendError(f"native display does not support pixel format {fmt}")

    layouts = frame.plane_layouts
    depth = layouts[0].bit_depth
    scale = float((1 << depth) - 1)
    y = frame.planes[0].astype(np.float32) / scale
    u = frame.planes[1]
    v = frame.planes[2]
    u = np.repeat(np.repeat(u, 1 << layouts[1].subsample_y, axis=0),
                  1 << layouts[1].subsample_x, axis=1)[:height, :width]
    v = np.repeat(np.repeat(v, 1 << layouts[2].subsample_y, axis=0),
                  1 << layouts[2].subsample_x, axis=1)[:height, :width]
    u = u.astype(np.float32) / scale - 0.5
    v = v.astype(np.float32) / scale - 0.5
    metadata = dict(frame.color_metadata)
    # BT.709 is the safe HD default; BT.601 coefficients cover SD sources.
    if metadata.get("color_space") in {"bt470bg", "smpte170m", "bt601"}:
        r, g, b = y + 1.402 * v, y - 0.344136 * u - 0.714136 * v, y + 1.772 * u
    else:
        r, g, b = y + 1.5748 * v, y - 0.187324 * u - 0.468124 * v, y + 1.8556 * u
    return np.ascontiguousarray(np.clip(np.stack((r, g, b), axis=2) * 255.0, 0, 255).astype(np.uint8))


class TkCanvasVideoSink:
    """Present decoded RGB frames in the MPCASU-owned Tk canvas."""
    def __init__(self, canvas):
        self.canvas = canvas
        self._generation = 0
        self._image = None

    def present(self, frame: CanonicalFrame, pts_seconds: float) -> None:
        rgb = canonical_to_rgb(frame)
        height, width, _ = rgb.shape
        ppm = f"P6\n{width} {height}\n255\n".encode("ascii") + rgb.tobytes()
        generation = self._generation

        def update() -> None:
            if generation != self._generation or not self.canvas.winfo_exists():
                return
            import tkinter as tk
            image = tk.PhotoImage(data=ppm, format="PPM")
            self._image = image
            self.canvas.delete("native-video")
            self.canvas.create_image(self.canvas.winfo_width() // 2,
                                     self.canvas.winfo_height() // 2,
                                     image=image, anchor="center", tags="native-video")

        try:
            self.canvas.after(0, update)
        except Exception:
            pass

    def present_cover(self, data: bytes, media_type: str) -> None:
        if media_type != "image/png":
            return
        generation = self._generation

        def update() -> None:
            if generation != self._generation or not self.canvas.winfo_exists():
                return
            import base64
            import tkinter as tk
            image = tk.PhotoImage(data=base64.b64encode(data), format="PNG")
            self._image = image
            self.canvas.delete("native-video")
            self.canvas.create_image(self.canvas.winfo_width() // 2,
                                     self.canvas.winfo_height() // 2,
                                     image=image, anchor="center", tags="native-video")

        try:
            self.canvas.after(0, update)
        except Exception:
            pass

    def invalidate(self) -> None:
        self._generation += 1
        try:
            self.canvas.after(0, lambda: (self.canvas.delete("native-subtitle"),
                                          self.canvas.delete("native-rich-subtitle"),
                                          self.canvas.delete("native-video")))
        except Exception:
            pass

    def present_subtitle(self, text: str | None, pts_seconds: float) -> None:
        generation = self._generation
        def update() -> None:
            if generation != self._generation or not self.canvas.winfo_exists():
                return
            self.canvas.delete("native-subtitle")
            if text:
                self.canvas.create_text(self.canvas.winfo_width() // 2,
                                        max(20, self.canvas.winfo_height() - 48),
                                        text=text, fill="white", anchor="s",
                                        font=("TkDefaultFont", 14, "bold"),
                                        width=max(100, self.canvas.winfo_width() - 80),
                                        tags="native-subtitle")
        try:
            self.canvas.after(0, update)
        except Exception:
            pass

    @staticmethod
    def _rgba_png(rgba: np.ndarray) -> bytes:
        height, width, channels = rgba.shape
        if channels != 4 or rgba.dtype != np.uint8:
            raise BackendError("subtitle overlay must be uint8 RGBA")
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF))
        scanlines = b"".join(b"\0" + rgba[row].tobytes() for row in range(height))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b""))

    def present_subtitle_rgba(self, rgba: np.ndarray, pts_seconds: float) -> None:
        png = self._rgba_png(rgba)
        generation = self._generation
        def update() -> None:
            if generation != self._generation or not self.canvas.winfo_exists():
                return
            import base64
            import tkinter as tk
            self.canvas.delete("native-rich-subtitle")
            if np.any(rgba[..., 3]):
                image = tk.PhotoImage(data=base64.b64encode(png), format="PNG")
                self._subtitle_image = image
                self.canvas.create_image(self.canvas.winfo_width() // 2,
                                         self.canvas.winfo_height() // 2,
                                         image=image, anchor="center",
                                         tags="native-rich-subtitle")
        try:
            self.canvas.after(0, update)
        except Exception:
            pass

    def clear_subtitle(self) -> None:
        try:
            self.canvas.after(0, lambda: (self.canvas.delete("native-subtitle"),
                                          self.canvas.delete("native-rich-subtitle")))
        except Exception:
            pass

    def close(self) -> None:
        self.invalidate()


class PulseAudioSink:
    """Direct s16le output through libpulse-simple; no player subprocess."""
    class _SampleSpec(ctypes.Structure):
        _fields_ = [("format", ctypes.c_int), ("rate", ctypes.c_uint32),
                    ("channels", ctypes.c_uint8)]

    @staticmethod
    def probe(device_name: str = "default") -> bool:
        """Return True only if a PulseAudio stream can actually be opened now.

        Lets players choose video-only fallback up front on headless systems
        instead of failing mid-playback when the lazy stream open runs.
        """
        try:
            lib = ctypes.CDLL("libpulse-simple.so.0")
        except OSError:
            return False
        spec = PulseAudioSink._SampleSpec(3 if sys.byteorder == "little" else 4,
                                          44100, 2)
        error = ctypes.c_int()
        lib.pa_simple_new.restype = ctypes.c_void_p
        lib.pa_simple_new.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
                                      ctypes.c_char_p, ctypes.c_char_p,
                                      ctypes.POINTER(PulseAudioSink._SampleSpec),
                                      ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_int)]
        handle = lib.pa_simple_new(
            None, b"MPCASU", 1,
            None if device_name == "default" else device_name.encode("utf-8"),
            b"CASU probe", ctypes.byref(spec), None, None, ctypes.byref(error))
        if not handle:
            return False
        lib.pa_simple_free.argtypes = [ctypes.c_void_p]
        lib.pa_simple_free(handle)
        return True

    def __init__(self, device_name: str = "default"):
        try:
            self.lib = ctypes.CDLL("libpulse-simple.so.0")
        except OSError as exc:
            raise BackendError("PulseAudio simple library is unavailable") from exc
        spec_pointer = ctypes.POINTER(self._SampleSpec)
        error_pointer = ctypes.POINTER(ctypes.c_int)
        self.lib.pa_simple_new.restype = ctypes.c_void_p
        self.lib.pa_simple_new.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
                                           ctypes.c_char_p, ctypes.c_char_p, spec_pointer,
                                           ctypes.c_void_p, ctypes.c_void_p, error_pointer]
        self.lib.pa_simple_write.restype = ctypes.c_int
        self.lib.pa_simple_write.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_size_t, error_pointer]
        self.lib.pa_simple_drain.restype = ctypes.c_int
        self.lib.pa_simple_drain.argtypes = [ctypes.c_void_p, error_pointer]
        self.lib.pa_simple_flush.restype = ctypes.c_int
        self.lib.pa_simple_flush.argtypes = [ctypes.c_void_p, error_pointer]
        self.lib.pa_simple_get_latency.restype = ctypes.c_uint64
        self.lib.pa_simple_get_latency.argtypes = [ctypes.c_void_p, error_pointer]
        self.lib.pa_simple_free.restype = None
        self.lib.pa_simple_free.argtypes = [ctypes.c_void_p]
        self._handle = None
        self._format: tuple[int, int] | None = None
        self._volume = 100
        self._muted = False
        self._device_name = "default"
        self.set_device(device_name)

    def _open(self, block: AudioBlock) -> None:
        if block.sample_format != "s16le":
            raise BackendError(f"unsupported native audio format {block.sample_format}")
        spec = self._SampleSpec(3 if sys.byteorder == "little" else 4,
                                block.sample_rate, block.channels)
        error = ctypes.c_int()
        self._handle = self.lib.pa_simple_new(
            None, b"MPCASU", 1,
            None if self._device_name == "default" else self._device_name.encode("utf-8"),
            b"CASUNAT2 playback", ctypes.byref(spec),
            None, None, ctypes.byref(error))
        if not self._handle:
            raise BackendError(f"PulseAudio output could not be opened (error {error.value})")
        self._format = (block.sample_rate, block.channels)

    def write(self, block: AudioBlock) -> None:
        if self._handle is None:
            self._open(block)
        if self._format != (block.sample_rate, block.channels):
            raise BackendError("mid-stream native audio format change is unsupported")
        pcm = block.pcm
        if self._muted or self._volume != 100:
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
            scale = 0.0 if self._muted else self._volume / 100.0
            pcm = np.clip(samples * scale, -32768, 32767).astype("<i2").tobytes()
        buffer = ctypes.create_string_buffer(pcm)
        error = ctypes.c_int()
        if self.lib.pa_simple_write(self._handle, buffer, len(pcm), ctypes.byref(error)) < 0:
            raise BackendError(f"PulseAudio write failed (error {error.value})")

    def flush(self) -> None:
        if self._handle:
            error = ctypes.c_int()
            self.lib.pa_simple_flush(self._handle, ctypes.byref(error))

    def reset_format(self) -> None:
        """Drop the current Pulse stream so a new track may change format."""
        if self._handle:
            self.lib.pa_simple_free(self._handle)
        self._handle = None
        self._format = None

    def audio_devices(self) -> tuple[AudioDeviceDescriptor, ...]:
        return pipewire_audio_devices()

    def set_device(self, identifier: str) -> None:
        value = str(identifier)
        if not value or len(value) > 512 or "\0" in value:
            raise BackendError(f"unknown native audio device {value!r}")
        self._device_name = value

    def set_volume(self, value: int) -> None:
        self._volume = max(0, min(200, int(value)))

    def set_mute(self, muted: bool) -> None:
        self._muted = bool(muted)

    def latency_seconds(self) -> float | None:
        if not self._handle:
            return None
        error = ctypes.c_int()
        latency = int(self.lib.pa_simple_get_latency(self._handle,
                                                     ctypes.byref(error)))
        if error.value or latency == ctypes.c_uint64(-1).value:
            return None
        return latency / 1_000_000.0

    def close(self) -> None:
        if self._handle:
            error = ctypes.c_int()
            self.lib.pa_simple_drain(self._handle, ctypes.byref(error))
            self.lib.pa_simple_free(self._handle)
        self._handle = None
        self._format = None


@dataclass(frozen=True)
class _Event:
    seconds: Fraction
    kind: str
    stream_id: int
    pts: int
    chunk_offset: int | None = None
    duration: Fraction = Fraction(0)


class NativeCasuBackend:
    """CASUNAT2 state/audio decoder with an independent playback clock."""
    def __init__(self, video_sink: VideoSink | None = None,
                 audio_sink: AudioSink | None = None, *, clock=time.monotonic):
        self.video_sink = video_sink or NullVideoSink()
        self.audio_sink = audio_sink or NullAudioSink()
        self._clock = clock
        self.container = None
        self.path: Path | None = None
        self.on_event = None
        self._events: tuple[_Event, ...] = ()
        self._duration = 0.0
        self._state = PlaybackState.EMPTY
        self._offset = 0.0
        self._started = 0.0
        self._rate = 1.0
        self._volume = 100
        self._muted = False
        self._generation = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._transition_lock = threading.RLock()
        self._worker_stop_timeout = 2.0
        self._selected_audio = -1
        self._selected_video = -1
        self._selected_subtitle = -1
        self._audio_device = "default"
        self._chapters: tuple[dict, ...] = ()
        self._audio_clock_media: float | None = None
        self._audio_clock_observed = 0.0
        self._audio_delay_seconds = 0.0
        self._subtitle_delay_seconds = 0.0
        self._cover: tuple[bytes, str] | None = None
        self._rich_subtitles: dict[int, LibassRenderer] = {}
        self._last_error: str | None = None
        # Transport intent is separate from the instantaneous worker state.
        # A very short item may reach ENDED between two rapid seek/track/device
        # transactions; those transactions must still resume until the user
        # explicitly pauses or stops.
        self._play_requested = False

    @staticmethod
    def supports(path: str | Path) -> bool:
        try:
            with Path(path).open("rb") as handle:
                return handle.read(8) == b"CASUNAT2"
        except (OSError, TypeError):
            return False

    def open_casu(self, path: str | Path) -> None:
        self.close_media()
        source = Path(path).expanduser().resolve()
        try:
            container = read_native_v2(source, load_payloads=False)
        except (OSError, NativeV2Error) as exc:
            self._state = PlaybackState.ERROR
            raise BackendError(f"invalid CASUNAT2 container: {exc}") from exc
        descriptors = {int(item["stream_id"]): item for item in container.manifest.get("streams", [])}
        video_sizes = [(int(item.get("width", 0)), int(item.get("height", 0)))
                       for item in descriptors.values() if item.get("type") == "video"]
        render_width, render_height = next(((width, height)
                                            for width, height in video_sizes
                                            if width > 0 and height > 0),
                                           (1280, 720))
        rich_documents: dict[int, bytes] = {}
        subtitle_fonts: list[tuple[str, bytes]] = []
        events: list[_Event] = []
        duration = Fraction(0)
        for stream_id, descriptor in descriptors.items():
            kind = descriptor.get("type")
            if kind == "video":
                num, den = (int(value) for value in descriptor["time_base"])
                time_base = Fraction(num, den)
                if self._selected_video < 0:
                    self._selected_video = stream_id
                for frame in descriptor.get("frame_timeline", []):
                    pts = int(frame["pts"])
                    start = pts * time_base
                    events.append(_Event(start, "video", stream_id, pts))
                    frame_duration = int(frame.get("duration_pts") or 0) * time_base
                    duration = max(duration, start + frame_duration)
            elif kind == "audio":
                if self._selected_audio < 0:
                    self._selected_audio = stream_id
            elif kind == "subtitle" and self._selected_subtitle < 0:
                self._selected_subtitle = stream_id
        for offset, summary in zip(container.offsets, container.chunks):
            if summary.chunk_type == ChunkType.AUDIO_BLOCK:
                try:
                    meta = container.read_audio_block_meta_at(offset)
                    pts = int(meta["pts"])
                    time_base_num, time_base_den = (int(value) for value in meta["time_base"])
                    sample_rate = int(meta["sample_rate"])
                    sample_count = int(meta["sample_count"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise BackendError("invalid CASUNAT2 audio block metadata") from exc
                if time_base_num <= 0 or time_base_den <= 0 or sample_rate <= 0 or sample_count < 0:
                    raise BackendError("invalid CASUNAT2 audio block timing")
                start = Fraction(pts * time_base_num, time_base_den)
                events.append(_Event(start, "audio", summary.stream_id, pts,
                                     chunk_offset=offset,
                                     duration=Fraction(sample_count, sample_rate)))
                duration = max(duration, start + Fraction(sample_count, sample_rate))
                continue
            if summary.chunk_type not in {ChunkType.SUBTITLE_PACKET,
                                          ChunkType.SUBTITLE_BITMAP,
                                          ChunkType.CHAPTER_TABLE, ChunkType.ATTACHMENT}:
                continue
            chunk, _following = container.read_chunk_at(offset)
            if chunk.chunk_type == ChunkType.SUBTITLE_PACKET:
                packet = decode_subtitle_packet(chunk.payload)
                start = Fraction(packet.start_pts, 1000); end = Fraction(packet.end_pts, 1000)
                events.append(_Event(start, "subtitle", chunk.stream_id,
                                     packet.start_pts, chunk_offset=offset,
                                     duration=end - start))
                events.append(_Event(end, "subtitle-clear", chunk.stream_id,
                                     packet.end_pts))
                duration = max(duration, end)
            elif chunk.chunk_type == ChunkType.SUBTITLE_BITMAP:
                packet = decode_bitmap_subtitle(chunk.payload)
                start = Fraction(packet.start_pts, 1000)
                end = Fraction(packet.end_pts, 1000)
                events.append(_Event(start, "subtitle-bitmap-show", chunk.stream_id,
                                     packet.start_pts, chunk_offset=offset,
                                     duration=end - start))
                events.append(_Event(end, "subtitle-bitmap-clear", chunk.stream_id,
                                     packet.end_pts))
                duration = max(duration, end)
            elif chunk.chunk_type == ChunkType.CHAPTER_TABLE:
                self._chapters = tuple(decode_chapter_table(chunk.payload))
            elif chunk.chunk_type == ChunkType.ATTACHMENT:
                attachment = decode_attachment(chunk.payload)
                if (attachment.role == "cover-art"
                        and attachment.media_type.startswith("image/")
                        and self._cover is None):
                    self._cover = (attachment.data, attachment.media_type)
                elif attachment.role == "subtitle-source":
                    rich_documents[chunk.stream_id] = attachment.data
                elif attachment.role == "subtitle-font":
                    subtitle_fonts.append((attachment.filename, attachment.data))
        for stream_id, document in rich_documents.items():
            try:
                self._rich_subtitles[stream_id] = LibassRenderer(
                    document, render_width, render_height,
                    fonts=tuple(subtitle_fonts))
            except LibassError:
                # The paired UTF-8 packet remains a truthful fallback.
                pass
        self.container = container
        self.path = source
        self._events = tuple(sorted(events, key=lambda item: (item.seconds, item.kind)))
        self._duration = float(duration)
        self._offset = 0.0
        self._state = PlaybackState.READY
        self._present_cover()

    def _present_cover(self) -> None:
        if self._cover is None or self._selected_video >= 0:
            return
        presenter = getattr(self.video_sink, "present_cover", None)
        if callable(presenter):
            presenter(*self._cover)

    def capabilities(self) -> dict[str, str]:
        return {"backend": "native CASUNAT2 decoder", "version": "CASUNAT2",
                "native_casu_payload": "key-state/tile/PCM direct",
                "temporary_legacy_file": "none",
                "audio_clock": "sink latency feedback" if
                callable(getattr(self.audio_sink, "latency_seconds", None)) and
                not isinstance(self.audio_sink, NullAudioSink)
                else "monotonic fallback"}

    def _observe_audio_clock(self, block: AudioBlock, *,
                             media_end_seconds: float | None = None) -> None:
        latency_reader = getattr(self.audio_sink, "latency_seconds", None)
        if not callable(latency_reader):
            return
        try:
            latency = latency_reader()
        except Exception:
            return
        if (latency is None or not np.isfinite(latency) or latency < 0
                or latency > MAX_AUDIO_LATENCY_SECONDS):
            return
        block_start = (block.pts * block.time_base_num /
                       block.time_base_den)
        block_end = (block_start + block.sample_count / block.sample_rate
                     if media_end_seconds is None else float(media_end_seconds))
        candidate = (block_end - float(latency) * self._rate
                     + self._audio_delay_seconds)
        if self._audio_clock_media is not None:
            previous = (self._audio_clock_media
                        + (self._clock() - self._audio_clock_observed) * self._rate)
            candidate = max(candidate, previous)
        self._audio_clock_media = candidate
        self._audio_clock_observed = self._clock()

    def _reset_audio_clock(self) -> None:
        self._audio_clock_media = None
        self._audio_clock_observed = 0.0

    def _scheduler_position(self) -> float:
        if self._state == PlaybackState.PLAYING and self._audio_clock_media is not None:
            return (self._audio_clock_media
                    + (self._clock() - self._audio_clock_observed) * self._rate)
        if self._state == PlaybackState.PLAYING:
            return (self._clock() - self._started) * self._rate
        return self._offset

    def _notify(self, state: PlaybackState) -> None:
        self._state = state
        if self.on_event:
            try:
                self.on_event(state)
            except Exception:
                pass

    def play(self) -> None:
        if self.container is None:
            raise BackendError("no CASUNAT2 container is open")
        with self._transition_lock:
            with self._lock:
                if self._state == PlaybackState.PLAYING:
                    return
                if self._thread is not None and self._thread.is_alive():
                    raise BackendError("previous native playback worker is still active")
                if self._state == PlaybackState.ENDED or self._offset >= self._duration:
                    self._offset = 0.0
                    self.video_sink.invalidate()
                    self.audio_sink.flush()
                    self._reset_audio_clock()
                    self._present_cover()
                self._play_requested = True
                self._started = self._clock() - self._offset / self._rate
                self._stop.clear()
                generation = self._generation
                self._thread = threading.Thread(target=self._run, args=(generation,),
                                                name="mpcasu-native", daemon=True)
                self._last_error = None
                self._notify(PlaybackState.PLAYING)
                self._thread.start()

    def _run(self, generation: int) -> None:
        try:
            for event in self._events:
                event_time = float(event.seconds)
                delay = (self._audio_delay_seconds if event.kind == "audio" else
                         self._subtitle_delay_seconds
                         if event.kind.startswith("subtitle") else 0.0)
                due_time = max(0.0, event_time + delay)
                overlaps_seek = (
                    event.kind in {"audio", "subtitle", "subtitle-bitmap-show"}
                    and event.duration > 0 and
                    due_time < self._offset and
                    float(event.seconds + event.duration) + delay > self._offset
                )
                if due_time + 1e-9 < self._offset and not overlaps_seek:
                    continue
                if overlaps_seek:
                    due_time = max(due_time, self._offset)
                while not self._stop.is_set() and generation == self._generation:
                    remaining = due_time - self._scheduler_position()
                    if remaining <= 0:
                        break
                    self._stop.wait(min(remaining / self._rate, 0.02))
                if self._stop.is_set() or generation != self._generation:
                    return
                if event.kind == "video" and event.stream_id == self._selected_video:
                    frame = self.container.reconstruct_video(event.stream_id, event.pts)
                    self.video_sink.present(frame, event_time)
                    self._present_rich_subtitle(event_time - self._subtitle_delay_seconds)
                elif event.kind == "audio" and event.stream_id == self._selected_audio and event.chunk_offset is not None:
                    chunk, _ = self.container.read_chunk_at(event.chunk_offset)
                    block = decode_audio_block(chunk.payload)
                    if event_time + delay < self._offset:
                        skipped = min(
                            block.sample_count,
                            max(0, int((self._offset - event_time - delay) *
                                       block.sample_rate)),
                        )
                        byte_offset = skipped * block.channels * 2
                        pts_delta = round(
                            skipped / block.sample_rate *
                            block.time_base_den / block.time_base_num
                        )
                        block = replace(
                            block,
                            pts=block.pts + pts_delta,
                            sample_count=block.sample_count - skipped,
                            pcm=block.pcm[byte_offset:],
                        )
                    if block.sample_count:
                        media_end = (block.pts * block.time_base_num /
                                     block.time_base_den
                                     + block.sample_count / block.sample_rate)
                        output_block = resample_audio_block(block, self._rate)
                        self.audio_sink.write(output_block)
                        self._observe_audio_clock(
                            output_block, media_end_seconds=media_end)
                elif event.kind == "subtitle" and event.stream_id == self._selected_subtitle and event.chunk_offset is not None:
                    chunk, _ = self.container.read_chunk_at(event.chunk_offset)
                    packet = decode_subtitle_packet(chunk.payload)
                    presenter = getattr(self.video_sink, "present_subtitle", None)
                    rich_presented = self._present_rich_subtitle(event_time)
                    if presenter and not rich_presented:
                        presenter(packet.text, event_time)
                elif event.kind == "subtitle-clear" and event.stream_id == self._selected_subtitle:
                    presenter = getattr(self.video_sink, "present_subtitle", None)
                    if presenter:
                        presenter(None, event_time)
                    self._present_rich_subtitle(event_time)
                elif (event.kind == "subtitle-bitmap-show"
                      and event.stream_id == self._selected_subtitle
                      and event.chunk_offset is not None):
                    chunk, _ = self.container.read_chunk_at(event.chunk_offset)
                    packet = decode_bitmap_subtitle(chunk.payload)
                    presenter = getattr(self.video_sink, "present_subtitle_rgba", None)
                    if callable(presenter):
                        presenter(packet.canvas_rgba(), event_time)
                elif (event.kind == "subtitle-bitmap-clear"
                      and event.stream_id == self._selected_subtitle):
                    clearer = getattr(self.video_sink, "clear_subtitle", None)
                    if callable(clearer):
                        clearer()
            with self._lock:
                if generation == self._generation and not self._stop.is_set():
                    self._offset = self._duration
                    self._notify(PlaybackState.ENDED)
        except Exception as exc:
            failure_generation = generation + 1
            with self._lock:
                if generation != self._generation:
                    return
                self._offset = max(
                    0.0, min(self._duration, self._scheduler_position()))
                self._generation = failure_generation
                self._play_requested = False
                self._stop.set()
                self._reset_audio_clock()
                self._last_error = f"{type(exc).__name__}: {exc}"[:1000]
            try:
                self.audio_sink.flush()
            except Exception:
                pass
            try:
                self.video_sink.invalidate()
                clearer = getattr(self.video_sink, "clear_subtitle", None)
                if callable(clearer):
                    clearer()
            except Exception:
                pass
            with self._lock:
                if failure_generation == self._generation:
                    self._notify(PlaybackState.ERROR)

    def _present_rich_subtitle(self, seconds: float) -> bool:
        renderer = self._rich_subtitles.get(self._selected_subtitle)
        presenter = getattr(self.video_sink, "present_subtitle_rgba", None)
        if renderer is None or not callable(presenter):
            return False
        try:
            overlay = renderer.render(round(max(0.0, seconds) * 1000))
        except LibassError:
            renderer.close()
            self._rich_subtitles.pop(self._selected_subtitle, None)
            return False
        presenter(overlay, seconds)
        return True

    def pause(self) -> None:
        with self._transition_lock:
            with self._lock:
                self._play_requested = False
                if self._state != PlaybackState.PLAYING:
                    return
                self._offset = self.position()
            self._stop_thread()
            with self._lock:
                self.audio_sink.flush()
                self._reset_audio_clock()
                self._notify(PlaybackState.PAUSED)

    def resume(self) -> None:
        self.play()

    def stop(self) -> None:
        with self._transition_lock:
            with self._lock:
                self._play_requested = False
            self._stop_thread()
            with self._lock:
                self.audio_sink.flush()
                self._reset_audio_clock()
                self._offset = 0.0
                if self.container is not None:
                    self._notify(PlaybackState.STOPPED)

    def seek(self, seconds: float) -> None:
        target = max(0.0, min(self._duration, float(seconds)))
        with self._transition_lock:
            with self._lock:
                playing = self._play_requested
            self._stop_thread()
            with self._lock:
                self._offset = target
                self._generation += 1
                self.video_sink.invalidate()
                self._present_cover()
                self.audio_sink.flush()
                self._reset_audio_clock()
                self._notify(PlaybackState.PAUSED if not playing else PlaybackState.READY)
            if playing:
                self.play()

    def _stop_thread(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self._worker_stop_timeout)
            if thread.is_alive():
                with self._lock:
                    if self._thread is thread:
                        self._notify(PlaybackState.ERROR)
                raise BackendError(
                    "native playback worker did not stop; output restart refused"
                )
        with self._lock:
            if self._thread is thread:
                self._thread = None
            self._stop.clear()

    def position(self) -> float:
        if self._state == PlaybackState.PLAYING:
            return max(0.0, min(self._duration, self._scheduler_position()))
        return self._offset

    def duration(self) -> float:
        return self._duration

    def state(self) -> PlaybackState:
        return self._state

    def last_error(self) -> str | None:
        return self._last_error

    def is_actively_playing(self) -> bool:
        return self._state == PlaybackState.PLAYING and bool(self._thread and self._thread.is_alive())

    def set_rate(self, rate: float) -> float:
        requested = float(rate)
        if not np.isfinite(requested):
            raise BackendError("native playback rate must be finite")
        value = max(0.25, min(4.0, requested))
        with self._transition_lock:
            with self._lock:
                position = self.position()
                playing = self._play_requested
            if playing:
                self._stop_thread()
            with self._lock:
                self._rate = value
                self._offset = position
                self._reset_audio_clock()
                if playing:
                    self.video_sink.invalidate()
                    self._present_cover()
                    self.audio_sink.flush()
                    self._notify(PlaybackState.READY)
            if playing:
                self.play()
        return value

    def rate(self) -> float:
        return self._rate

    def set_volume(self, value: int) -> int:
        self._volume = max(0, min(200, int(value)))
        self.audio_sink.set_volume(self._volume)
        return self._volume

    def volume(self) -> int:
        return self._volume

    def set_mute(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.audio_sink.set_mute(self._muted)

    def set_audio_delay(self, milliseconds: float) -> float:
        value = max(-5000.0, min(5000.0, float(milliseconds)))
        self._audio_delay_seconds = value / 1000.0
        self._reset_audio_clock()
        return value

    def set_subtitle_delay(self, milliseconds: float) -> float:
        value = max(-5000.0, min(5000.0, float(milliseconds)))
        self._subtitle_delay_seconds = value / 1000.0
        return value

    def audio_track_count(self) -> int:
        return sum(item.get("type") == "audio" for item in self.container.manifest.get("streams", [])) if self.container else 0

    def video_track_count(self) -> int:
        return sum(item.get("type") == "video" for item in self.container.manifest.get("streams", [])) if self.container else 0

    def audio_track(self) -> int:
        return self._selected_audio

    def video_track(self) -> int:
        return self._selected_video

    def _descriptions(self, kind: str) -> list[tuple[int, str]]:
        if not self.container:
            return []
        return [(int(item["stream_id"]), str(item.get("language") or item.get("codec_origin") or kind))
                for item in self.container.manifest.get("streams", []) if item.get("type") == kind]

    def audio_track_descriptions(self) -> list[tuple[int, str]]:
        return self._descriptions("audio")

    def video_track_descriptions(self) -> list[tuple[int, str]]:
        return self._descriptions("video")

    def track_descriptors(self, kind: TrackKind) -> tuple[TrackDescriptor, ...]:
        if not self.container:
            return ()
        return tuple(
            TrackDescriptor(int(item["stream_id"]), kind,
                            str(item.get("language") or item.get("codec_origin") or kind.value),
                            language=item.get("language"), codec=item.get("codec_origin"),
                            default=bool(item.get("default")), forced=bool(item.get("forced")),
                            stream_index=item.get("source_index"),
                            channels=int(item["channels"]) if item.get("channels") else None,
                            channel_layout=item.get("channel_layout"),
                            sample_rate=int(item["sample_rate"]) if item.get("sample_rate") else None,
                            width=int(item["width"]) if item.get("width") else None,
                            height=int(item["height"]) if item.get("height") else None)
            for item in self.container.manifest.get("streams", [])
            if item.get("type") == kind.value
        )

    def audio_devices(self) -> tuple[AudioDeviceDescriptor, ...]:
        reader = getattr(self.audio_sink, "audio_devices", None)
        if callable(reader):
            devices = tuple(reader())[:MAX_AUDIO_DEVICES]
            if devices:
                return devices
        return (AudioDeviceDescriptor("default", "System Default", "PulseAudio", True),)

    def set_audio_device(self, identifier: str) -> None:
        value = str(identifier)
        if value not in {item.identifier for item in self.audio_devices()}:
            raise BackendError(f"unknown native audio device {value!r}")
        if value == self._audio_device:
            return
        selector = getattr(self.audio_sink, "set_device", None)
        if not callable(selector):
            raise BackendError("native audio sink does not support device selection")
        with self._transition_lock:
            with self._lock:
                position = self.position()
                playing = self._play_requested
            if playing:
                self._stop_thread()
            with self._lock:
                self.audio_sink.flush()
                resetter = getattr(self.audio_sink, "reset_format", None)
                if callable(resetter):
                    resetter()
                selector(value)
                self._audio_device = value
                self._offset = position
                self._reset_audio_clock()
                self.video_sink.invalidate()
                if playing:
                    self._notify(PlaybackState.READY)
            if playing:
                self.play()

    def _select_track(self, attribute: str, track: int,
                      descriptions: list[tuple[int, str]], kind: str) -> None:
        selected = int(track)
        if selected != -1 and selected not in dict(descriptions):
            raise BackendError(f"unknown native {kind} stream {selected}")
        with self._transition_lock:
            with self._lock:
                if int(getattr(self, attribute)) == selected:
                    return
                position = self.position()
                playing = self._play_requested
            if playing:
                self._stop_thread()
            with self._lock:
                setattr(self, attribute, selected)
                self._offset = position
                self._reset_audio_clock()
                self.audio_sink.flush()
                if attribute == "_selected_audio":
                    resetter = getattr(self.audio_sink, "reset_format", None)
                    if callable(resetter):
                        resetter()
                self.video_sink.invalidate()
                clearer = getattr(self.video_sink, "clear_subtitle", None)
                if callable(clearer):
                    clearer()
                self._present_cover()
                if playing:
                    self._notify(PlaybackState.READY)
            if playing:
                self.play()

    def set_audio_track(self, track: int) -> None:
        self._select_track("_selected_audio", track,
                           self.audio_track_descriptions(), "audio")

    def set_video_track(self, track: int) -> None:
        self._select_track("_selected_video", track,
                           self.video_track_descriptions(), "video")

    def subtitle_track_count(self) -> int:
        return sum(item.get("type") == "subtitle" for item in self.container.manifest.get("streams", [])) if self.container else 0

    def subtitle_track(self) -> int:
        return self._selected_subtitle

    def subtitle_track_descriptions(self) -> list[tuple[int, str]]:
        return self._descriptions("subtitle")

    def set_subtitle_track(self, track: int) -> None:
        self._select_track("_selected_subtitle", track,
                           self.subtitle_track_descriptions(), "subtitle")

    def chapter_count(self) -> int:
        return len(self._chapters)

    def chapter(self) -> int:
        position_ns = int(self.position() * 1_000_000_000)
        active = [index for index, chapter in enumerate(self._chapters)
                  if int(chapter["start_pts"]) <= position_ns]
        return active[-1] if active else -1

    def set_chapter(self, chapter: int) -> None:
        if chapter < 0 or chapter >= len(self._chapters):
            raise BackendError(f"unknown native chapter {chapter}")
        self.seek(int(self._chapters[chapter]["start_pts"]) / 1_000_000_000)

    def chapter_descriptors(self) -> tuple[ChapterDescriptor, ...]:
        return tuple(
            ChapterDescriptor(
                index,
                int(item["start_pts"]) / 1_000_000_000,
                str(item.get("title") or f"Chapter {index + 1}"),
                int(item["end_pts"]) / 1_000_000_000
                if item.get("end_pts") is not None else None,
            )
            for index, item in enumerate(self._chapters)
        )

    def next_frame(self) -> None:
        video = [event for event in self._events if event.kind == "video" and float(event.seconds) > self.position()]
        if not video:
            raise BackendError("no next native video frame")
        self.seek(float(video[0].seconds))
        frame = self.container.reconstruct_video(video[0].stream_id, video[0].pts)
        self.video_sink.present(frame, float(video[0].seconds))

    def add_external_subtitle(self, subtitle: Path) -> None:
        raise BackendError("external subtitles are supported by the libVLC compatibility path only")

    def close_media(self) -> None:
        with self._transition_lock:
            with self._lock:
                had_media = self.container is not None
            self._stop_thread()
            with self._lock:
                if had_media:
                    self.audio_sink.flush()
                self._reset_audio_clock()
                self.container = None
                self.path = None
                self._events = ()
                self._duration = 0.0
                self._offset = 0.0
                self._selected_audio = self._selected_video = self._selected_subtitle = -1
                self._audio_device = "default"
                self._cover = None
                self._last_error = None
                self._play_requested = False
                for renderer in self._rich_subtitles.values():
                    renderer.close()
                self._rich_subtitles.clear()
                self._chapters = ()
                self._state = PlaybackState.EMPTY

    def close(self) -> None:
        self.close_media()
        self.video_sink.close()
        self.audio_sink.close()

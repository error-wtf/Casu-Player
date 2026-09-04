"""Bounded measured audio waveform extraction for MPCASU.

Provides static full-file waveform/spectrum analysis plus live/animated
window-based extraction for real-time visualization.
"""
from __future__ import annotations

import array
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .native_v2 import ChunkType, decode_audio_block, read_native_v2
from .probe import ProbeError, run_bounded


class WaveformError(ValueError):
    pass


def _peaks(samples, points: int) -> tuple[float, ...]:
    if not samples:
        return ()
    width = max(1, math.ceil(len(samples) / points))
    values = []
    for start in range(0, len(samples), width):
        peak = max(abs(int(value)) for value in samples[start:start + width])
        values.append(min(1.0, peak / 32768.0))
    return tuple(values[:points])


def _decode_native_audio(source: Path) -> tuple[array.array, int, int]:
    """Decode all PCM from a CASUNAT2 container.  Returns (samples, sample_rate, channels)."""
    container = read_native_v2(source, load_payloads=False)
    audio = next((stream for stream in container.manifest.get("streams", [])
                  if stream.get("type") == "audio"), None)
    if audio is None:
        return array.array("h"), 0, 0
    rate = int(audio.get("sample_rate", 0))
    samples = array.array("h")
    stream_id = int(audio["stream_id"])
    for offset, summary in zip(container.offsets, container.chunks):
        if summary.stream_id == stream_id and summary.chunk_type == ChunkType.AUDIO_BLOCK:
            chunk, _following = container.read_chunk_at(offset)
            block = decode_audio_block(chunk.payload)
            block_samples = array.array("h")
            block_samples.frombytes(block.pcm)
            if block_samples.itemsize == 2 and sys.byteorder != "little":
                block_samples.byteswap()
            channels = max(1, block.channels)
            samples.extend(block_samples[::channels])
            if len(samples) > 8_000_000:
                break
    return samples, rate, channels


def decode_all_pcm(path: str | Path) -> tuple[np.ndarray | None, int, int]:
    """Decode the entire audio file into a float32 numpy array (mono).

    Returns (pcm_buffer, sample_rate, channels) where pcm_buffer is a
    1-D float32 array in [-1.0, 1.0] or None on failure.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        return None, 0, 0
    try:
        with source.open("rb") as handle:
            native_v2 = handle.read(8) == b"CASUNAT2"
        if native_v2:
            samples, rate, channels = _decode_native_audio(source)
            if not samples:
                return None, 0, 0
            pcm = np.asarray(samples, dtype=np.float32) / 32768.0
            return pcm, rate, channels
        raw = run_bounded([
            "ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0",
            "-ac", "1", "-ar", "44100", "-f", "s16le", "pipe:1",
        ], max_output_bytes=88_200_000 * 2, timeout_seconds=60)
        samples = array.array("h")
        samples.frombytes(raw)
        if samples.itemsize == 2 and sys.byteorder != "little":
            samples.byteswap()
        pcm = np.asarray(samples, dtype=np.float32) / 32768.0
        rate = 44100
        return pcm, rate, 1
    except (OSError, ValueError, ProbeError, WaveformError):
        return None, 0, 0


def window_peaks(pcm: np.ndarray, rate: int, position_s: float, *,
                 window_s: float = 0.6, points: int = 320) -> tuple[float, ...]:
    """Extract a PCM window around *position_s* and downsample to *points* peaks.

    The window is centred on the playhead so the waveform appears to scroll
    smoothly as playback progresses.
    """
    if pcm is None or rate <= 0 or points < 8:
        return ()
    window_samples = max(64, int(rate * window_s))
    centre = max(0, min(len(pcm) - 1, int(position_s * rate)))
    half = window_samples // 2
    start = max(0, centre - half)
    end = min(len(pcm), start + window_samples)
    if end - start < 64:
        return ()
    chunk = np.abs(pcm[start:end])
    width = max(1, math.ceil(len(chunk) / points))
    values = []
    for i in range(0, len(chunk), width):
        values.append(min(1.0, float(np.max(chunk[i:i + width]))))
    return tuple(values[:points])


def window_wave(pcm: np.ndarray, rate: int, position_s: float, *,
                window_s: float = 0.045, points: int = 2048) -> tuple[float, ...]:
    """Raw time-domain samples ending at the playhead, downsampled.

    Mirrors the web player's oscilloscope waveform
    (``AnalyserNode.getByteTimeDomainData``): the window is the most recent
    ``window_s`` seconds up to the current position (the analyser's live
    buffer), exactly like the web page.  Returns values roughly in [-1.0, 1.0].
    """
    if pcm is None or rate <= 0 or points < 8:
        return ()
    window_samples = max(64, int(rate * window_s))
    centre = max(0, min(len(pcm) - 1, int(position_s * rate)))
    start = max(0, centre - window_samples)
    end = centre
    if end - start < 32:
        return ()
    chunk = pcm[start:end]
    width = max(1, math.ceil(len(chunk) / points))
    values = [float(chunk[i]) for i in range(0, len(chunk), width)]
    return tuple(values[:points])


def live_fft(pcm: np.ndarray, rate: int, position_s: float, *,
             fft_size: int = 2048, bins: int = 1024) -> tuple[float, ...]:
    """Raw FFT magnitudes exactly like the web player's getByteFrequencyData.

    Returns ``bins`` linear frequency magnitudes in [0,1] from an FFT window
    at *position_s*; the web player draws all ``frequencyBinCount`` (fft_size/2)
    bins as its bars.
    """
    if pcm is None or rate <= 0 or bins < 8:
        return ()
    fft_size = max(64, min(len(pcm), fft_size))
    centre = max(0, min(len(pcm) - 1, int(position_s * rate)))
    # The analyser's live buffer ends at the current moment (like the web
    # player's AnalyserNode), it is not centred on the playhead.
    start = max(0, centre - fft_size)
    end = centre
    chunk = pcm[start:end]
    if len(chunk) < 64:
        return ()
    values = chunk - chunk.mean()
    window = np.hanning(len(values))
    magnitudes = np.abs(np.fft.rfft(values * window))
    count = min(bins, len(magnitudes))
    output = magnitudes[:count]
    maximum = float(output.max()) if output.size else 0.0
    return tuple(float(value / maximum) if maximum > 0 else 0.0
                 for value in output)


def live_spectrum(pcm: np.ndarray, rate: int, position_s: float, *,
                  fft_size: int = 2048, bands: int = 32) -> tuple[float, ...]:
    """Compute a short FFT on a PCM window at *position_s*.

    Returns *bands* log-spaced frequency magnitudes in [0.0, 1.0] for
    real-time spectrum-bar animation.
    """
    if pcm is None or rate <= 0 or bands < 4:
        return ()
    fft_size = max(64, min(len(pcm), fft_size))
    centre = max(0, min(len(pcm) - 1, int(position_s * rate)))
    half = fft_size // 2
    start = max(0, centre - half)
    end = min(len(pcm), start + fft_size)
    chunk = pcm[start:end]
    if len(chunk) < 32:
        return ()
    values = chunk - chunk.mean()
    window = np.hanning(len(values))
    magnitudes = np.abs(np.fft.rfft(values * window))
    freqs = np.fft.rfftfreq(len(values), 1.0 / rate)
    edges = np.geomspace(20.0, min(rate / 2.0, 20_000.0), bands + 1)
    indices = np.clip(np.searchsorted(freqs, edges, side="right") - 1,
                      0, len(magnitudes) - 1)
    output = []
    for band in range(bands):
        low = indices[band]
        high = indices[band + 1]
        segment = magnitudes[low:high]
        output.append(float(np.sqrt(np.mean(segment ** 2))) if segment.size else 0.0)
    maximum = max(output, default=0.0)
    return tuple(value / maximum if maximum > 0 else 0.0 for value in output)


def waveform_peaks(path: str | Path, *, points: int = 320) -> tuple[float, ...]:
    """Return normalized measured PCM peaks with explicit resource bounds."""
    source = Path(path).expanduser().resolve()
    if not source.is_file() or not 16 <= int(points) <= 2048:
        raise WaveformError("waveform source or point count is invalid")
    try:
        with source.open("rb") as handle:
            native_v2 = handle.read(8) == b"CASUNAT2"
        if native_v2:
            container = read_native_v2(source, load_payloads=False)
            audio = next((stream for stream in container.manifest.get("streams", [])
                          if stream.get("type") == "audio"), None)
            if audio is None:
                return ()
            samples = array.array("h")
            stream_id = int(audio["stream_id"])
            for offset, summary in zip(container.offsets, container.chunks):
                if (summary.stream_id == stream_id and
                        summary.chunk_type == ChunkType.AUDIO_BLOCK):
                    chunk, _following = container.read_chunk_at(offset)
                    block = decode_audio_block(chunk.payload)
                    block_samples = array.array("h")
                    block_samples.frombytes(block.pcm)
                    if block_samples.itemsize == 2 and sys.byteorder != "little":
                        block_samples.byteswap()
                    channels = max(1, block.channels)
                    samples.extend(block_samples[::channels])
                    if len(samples) > 8_000_000:
                        raise WaveformError("native waveform sample budget exceeded")
            return _peaks(samples, int(points))
        raw = run_bounded([
            "ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0",
            "-ac", "1", "-ar", "1000", "-f", "s16le", "pipe:1",
        ], max_output_bytes=16 * 1024 * 1024, timeout_seconds=30)
        samples = array.array("h"); samples.frombytes(raw)
        if samples.itemsize == 2 and sys.byteorder != "little":
            samples.byteswap()
        return _peaks(samples, int(points))
    except (OSError, ValueError, ProbeError) as exc:
        if isinstance(exc, WaveformError):
            raise
        raise WaveformError(f"could not decode audio waveform: {exc}") from exc


def spectrum_bands(path: str | Path, *, bands: int = 32,
                   seconds: float = 15.0) -> tuple[float, ...]:
    """Return logarithmic measured FFT bands from a bounded decoded PCM window."""
    source = Path(path).expanduser().resolve()
    count = int(bands)
    if not source.is_file() or not 8 <= count <= 128 or not 1 <= float(seconds) <= 30:
        raise WaveformError("spectrum source, band count or duration is invalid")
    rate = 16_000
    try:
        with source.open("rb") as handle:
            native_v2 = handle.read(8) == b"CASUNAT2"
        samples = array.array("h")
        if native_v2:
            container = read_native_v2(source, load_payloads=False)
            audio = next((stream for stream in container.manifest.get("streams", [])
                          if stream.get("type") == "audio"), None)
            if audio is None:
                return ()
            rate = int(audio.get("sample_rate", 0))
            if rate < 1000 or rate > 384_000:
                raise WaveformError("native spectrum sample rate is invalid")
            limit = int(rate * float(seconds))
            stream_id = int(audio["stream_id"])
            for offset, summary in zip(container.offsets, container.chunks):
                if summary.stream_id != stream_id or summary.chunk_type != ChunkType.AUDIO_BLOCK:
                    continue
                chunk, _following = container.read_chunk_at(offset)
                block = decode_audio_block(chunk.payload)
                values = array.array("h"); values.frombytes(block.pcm)
                if values.itemsize == 2 and sys.byteorder != "little": values.byteswap()
                samples.extend(values[::max(1, block.channels)])
                if len(samples) >= limit:
                    del samples[limit:]; break
        else:
            raw = run_bounded([
                "ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0",
                "-t", f"{float(seconds):g}", "-ac", "1", "-ar", str(rate),
                "-f", "s16le", "pipe:1",
            ], max_output_bytes=rate * int(seconds + 1) * 2,
                timeout_seconds=30)
            samples.frombytes(raw)
            if samples.itemsize == 2 and sys.byteorder != "little": samples.byteswap()
        if len(samples) < 64:
            return ()
        values = np.asarray(samples, dtype=np.float64) / 32768.0
        values -= values.mean()
        window = np.hanning(len(values))
        magnitudes = np.abs(np.fft.rfft(values * window))
        frequencies = np.fft.rfftfreq(len(values), 1.0 / rate)
        edges = np.geomspace(20.0, max(21.0, min(rate / 2.0, 20_000.0)), count + 1)
        output = []
        for low, high in zip(edges[:-1], edges[1:]):
            selected = magnitudes[(frequencies >= low) & (frequencies < high)]
            output.append(float(np.sqrt(np.mean(selected ** 2))) if selected.size else 0.0)
        maximum = max(output, default=0.0)
        return tuple(value / maximum if maximum > 0 else 0.0 for value in output)
    except (OSError, ValueError, ProbeError) as exc:
        if isinstance(exc, WaveformError):
            raise
        raise WaveformError(f"could not decode audio spectrum: {exc}") from exc

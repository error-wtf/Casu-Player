"""Bounded, source-versioned media thumbnails for the MPCASU library."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def _native_cover(source: Path) -> tuple[str, bytes] | None:
    """Return the first verified cover attachment without trusting filenames."""
    try:
        with source.open("rb") as handle:
            if handle.read(8) != b"CASUNAT2":
                return None
        from casu.native_v2 import ChunkType, decode_attachment, read_native_v2
        container = read_native_v2(source, load_payloads=False)
        for chunk, offset in zip(container.chunks, container.offsets):
            if chunk.chunk_type != ChunkType.ATTACHMENT:
                continue
            stored, _following = container.read_chunk_at(offset)
            attachment = decode_attachment(stored.payload)
            if attachment.role == "cover-art" and attachment.media_type.startswith("image/"):
                return attachment.filename, attachment.data
    except (OSError, ValueError):
        return None
    return None


MAX_THUMBNAIL_BYTES = 4 * 1024 * 1024


def thumbnail_for(source: str | Path, cache_directory: str | Path,
                  *, timeout: float = 20.0) -> Path | None:
    """Return a cached PPM thumbnail, or ``None`` for non-video/failed input."""
    media = Path(source).expanduser().resolve()
    if not media.is_file():
        return None
    stat = media.stat()
    identity = f"{media}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8")
    name = hashlib.sha256(identity).hexdigest() + ".ppm"
    cache = Path(cache_directory).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    if target.is_file() and 0 < target.stat().st_size <= MAX_THUMBNAIL_BYTES:
        return target
    fd, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=cache)
    os.close(fd)
    temporary = Path(temporary_name)
    cover = _native_cover(media)
    cover_input: Path | None = None
    if cover is not None:
        cover_fd, cover_name = tempfile.mkstemp(prefix="casu-cover-",
                                                suffix=Path(cover[0]).suffix or ".img")
        os.close(cover_fd)
        cover_input = Path(cover_name)
        try:
            cover_input.write_bytes(cover[1])
        except OSError:
            cover_input.unlink(missing_ok=True)
            cover_input = None
    command = [
        "ffmpeg", "-v", "error", "-ss", "0" if cover_input else "1",
        "-i", str(cover_input or media),
        "-map", "0:v:0", "-frames:v", "1",
        "-vf", "scale=320:180:force_original_aspect_ratio=decrease",
        "-f", "image2", "-vcodec", "ppm", "-y", str(temporary),
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=timeout,
                                check=False)
        if result.returncode != 0 or not temporary.is_file():
            return None
        size = temporary.stat().st_size
        if size <= 0 or size > MAX_THUMBNAIL_BYTES:
            return None
        with temporary.open("rb") as handle:
            if handle.read(2) != b"P6":
                return None
        os.replace(temporary, target)
        return target
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if cover_input is not None:
            cover_input.unlink(missing_ok=True)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

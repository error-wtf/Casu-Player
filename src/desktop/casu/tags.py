# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Lightweight media-tag extraction for the library.

Reads audio tags (title/artist/album/genre/track/year) with ffprobe and falls
back to a sensible filename/path-structure parse ("Artist - Title",
"Artist/Album/01 - Title.mp3", …) so the library stays usable without tags.
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import ffprobe

_TAG_KEYS = ("title", "artist", "album_artist", "album", "genre",
             "track", "date", "year", "comment")
_LEADING_TRACK = re.compile(r"^(\d{1,3})\s*[-._)\s]+\s*(.+)$")
_YEAR_END = re.compile(r"[(\[]?(\d{4})[)\]]?\s*$")
_MEDIA_EXTENSIONS = frozenset({
    ".mp3", ".mp4", ".m4a", ".m4v", ".mov", ".mkv", ".webm", ".flac",
    ".wav", ".ogg", ".opus", ".aac", ".aiff", ".alac", ".wma", ".mpg",
    ".mpeg", ".ts", ".m2ts", ".avi", ".casu", ".mp5",
})


def metadata_for(path: str | Path) -> dict:
    """Best-effort metadata dict for *path* (tags first, filename fallback).

    Returns a JSON-safe dict with ``title``, ``artist``, ``album``, ``genre``,
    ``track``, ``year`` and ``duration`` when they can be determined.  Non
    media files are returned empty immediately (no probe).
    """
    source = Path(path).expanduser().resolve()
    result: dict = {}
    if source.suffix.lower() not in _MEDIA_EXTENSIONS:
        return result
    try:
        probe = ffprobe(source, timeout_seconds=5.0)
        fmt = (probe.get("format") or {})
        tags = dict(fmt.get("tags") or {})
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "audio":
                stream_tags = dict(stream.get("tags") or {})
                for key in ("title", "artist", "album_artist", "album",
                            "genre", "track", "date"):
                    if key not in tags and stream_tags.get(key):
                        tags[key] = stream_tags[key]
        for key in _TAG_KEYS:
            value = str(tags.get(key) or "").strip()
            if value:
                result[key] = value
        duration = float(fmt.get("duration") or 0.0)
        if duration > 0:
            result["duration"] = duration
    except Exception:  # noqa: BLE001 - tag lookup is best effort
        pass
    _parse_filename(source, result)
    if "year" not in result and "date" in result:
        match = re.search(r"(\d{4})", str(result.get("date", "")))
        if match:
            result["year"] = match.group(1)
    return result


def _parse_filename(path: Path, result: dict) -> None:
    """Fill missing metadata from the file name and folder structure."""
    name = Path(path).stem
    parts = list(path.parts[:-1])

    if " - " in name:
        artist, title = name.split(" - ", 1)
        result.setdefault("artist", artist.strip())
        result.setdefault("title", title.strip())
    else:
        result.setdefault("title", name)

    title = str(result.get("title", ""))
    match = _LEADING_TRACK.match(title)
    if match:
        result.setdefault("track", match.group(1))
        result["title"] = match.group(2).strip()

    if parts:
        result.setdefault("album", parts[-1].strip())
    if len(parts) >= 2:
        result.setdefault("artist", parts[-2].strip())

    year = _YEAR_END.search(str(result.get("title", "")))
    if year:
        result.setdefault("year", year.group(1))
    elif "year" not in result and len(parts) >= 1:
        album_match = _YEAR_END.search(parts[-1])
        if album_match:
            result.setdefault("year", album_match.group(1))

    if not result.get("artist") and len(parts) >= 3:
        result.setdefault("artist", parts[-3].strip())

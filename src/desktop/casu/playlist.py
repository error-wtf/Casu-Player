"""Bounded playlist model shared by MPCASU queue presentations.

Supports JSON (MPCASU payload), M3U / Extended M3U, PLS, WPL, XSPF,
JSPF, ASX and RealMedia (RMP/RAM) playlist formats with content- and
extension-based auto-detection. Local entries are resolved relative to
the playlist directory; remote stream URLs are kept verbatim.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from .core import CasuError
from .fileio import atomic_write_json, read_bounded_json


MAX_PLAYLIST_ITEMS = 10_000
MAX_PLAYLIST_PATH_BYTES = 4096
MAX_PLAYLIST_FILE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 4096

#: Native playlist file extensions understood by the loaders (for file dialogs).
PLAYLIST_EXTENSIONS = (
    "*.cue *.m3u *.m3u8 *.pls *.json *.wpl *.xspf *.jspf *.asx *.wmx *.wvx *.rmp *.ram"
)
PLAYLIST_SUFFIXES = frozenset({
    ".cue", ".m3u", ".m3u8", ".pls", ".json", ".wpl", ".xspf", ".jspf",
    ".asx", ".wmx", ".wvx", ".rmp", ".ram",
})

_EXT_M3U = frozenset({".m3u", ".m3u8"})
_EXT_PLS = frozenset({".pls"})
_EXT_WPL = frozenset({".wpl"})
_EXT_XSPF = frozenset({".xspf"})
_EXT_JSPF = frozenset({".jspf"})
_EXT_ASX = frozenset({".asx", ".wmx", ".wvx", ".axs"})
_EXT_RMP = frozenset({".rmp", ".ram", ".rmm"})

_PARSERS = {}


class PlaylistError(ValueError):
    pass


def _bounded_text(value: str, label: str, maximum: int = MAX_PLAYLIST_PATH_BYTES) -> str:
    text = str(value).strip()
    if "\0" in text or len(text.encode("utf-8")) > maximum:
        raise PlaylistError(f"{label} exceeds safety limit")
    return text


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("iso-8859-1")


def _first_unquoted_comma(value: str) -> int:
    in_quote = False
    for index, char in enumerate(value):
        if char == '"':
            in_quote = not in_quote
        elif char == "," and not in_quote:
            return index
    return -1


def _is_remote(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return False
    if parsed.scheme.lower() == "file":
        return False
    return bool(parsed.scheme and parsed.netloc)


def _entry(value: str, base: Path | None):
    """Normalize one playlist entry: remote URL stays a str, local -> Path."""
    text = value.strip()
    if not text:
        return None
    if _is_remote(text):
        return _bounded_text(text, "stream URL")
    if text.lower().startswith("file://"):
        parsed = urllib.parse.urlparse(text)
        path_part = urllib.parse.unquote(parsed.path)
        if parsed.netloc:
            path_part = f"//{parsed.netloc}{path_part}"
        candidate = Path(path_part)
    else:
        candidate = Path(urllib.parse.unquote(text))
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    return Path(candidate).expanduser().resolve()


def _parse_m3u_entries(text: str, base: Path | None) -> list:
    lines = text.splitlines()
    if any(len(line.encode("utf-8")) > MAX_LINE_BYTES for line in lines):
        raise PlaylistError("playlist line exceeds safety limit")
    entries: list = []
    pending: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            comma = _first_unquoted_comma(line)
            pending = line[comma + 1:].strip()[:300] if comma >= 0 else None
            continue
        if line.startswith("#"):
            continue
        entry = _entry(line, base)
        if entry is not None:
            entries.append((entry, pending or ""))
        pending = None
    return entries


def _parse_pls_entries(text: str, base: Path | None) -> list:
    files: dict = {}
    titles: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        mfile = re.match(r"^File(\d+)\s*=\s*(.+)$", line, re.IGNORECASE)
        mtitle = re.match(r"^Title(\d+)\s*=\s*(.*)$", line, re.IGNORECASE)
        if mfile:
            files[int(mfile.group(1))] = mfile.group(2).strip()
        elif mtitle:
            titles[int(mtitle.group(1))] = mtitle.group(2).strip()[:300]
    entries: list = []
    for index in sorted(files):
        entry = _entry(files[index], base)
        if entry is not None:
            entries.append((entry, titles.get(index, "")))
    return entries


def _parse_xspf_entries(text: str, base: Path | None) -> list:
    root = ET.fromstring(text)
    ns = "{http://xspf.org/ns/0/}"
    entries: list = []
    for track in root.findall(f".//{ns}track"):
        title = (track.findtext(f"{ns}title") or "").strip()[:300]
        for location in track.findall(f"{ns}location"):
            entry = _entry("".join(location.itertext()).strip(), base)
            if entry is not None:
                entries.append((entry, title))
    return entries


def _parse_wpl_entries(text: str, base: Path | None) -> list:
    root = ET.fromstring(text)
    entries: list = []
    for media in root.iter("media"):
        src = _attr_ci(media, "src")
        if not src:
            continue
        entry = _entry(src, base)
        if entry is not None:
            title = (_attr_ci(media, "title") or "").strip()[:300]
            entries.append((entry, title))
    return entries


def _attr_ci(node, name: str):
    """Case-insensitive ElementTree attribute lookup (ASX/WPL tags vary in case)."""
    for key, value in node.attrib.items():
        if key.lower() == name.lower():
            return value
    return None


def _parse_asx_entries(text: str, base: Path | None) -> list:
    root = ET.fromstring(text)
    entries: list = []
    seen: set = set()

    def _hrefs(node) -> list:
        return [_attr_ci(child, "href") for child in node.iter()
                if str(child.tag).lower() == "ref" and _attr_ci(child, "href")]

    def _urls(node) -> list:
        return [_attr_ci(child, "value") for child in node.iter()
                if str(child.tag).lower() == "param"
                and (_attr_ci(child, "name") or "").lower() == "url"
                and _attr_ci(child, "value")]

    for entry_node in root.iter():
        if str(entry_node.tag).lower() != "entry":
            continue
        title = ""
        for child in entry_node:
            if str(child.tag).lower() == "title" and child.text:
                title = child.text.strip()[:300]
                break
        sources = _hrefs(entry_node) + _urls(entry_node)
        for source in sources:
            entry = _entry(source, base)
            if entry is not None and str(entry) not in seen:
                seen.add(str(entry))
                entries.append((entry, title))
    if not entries:
        for source in _hrefs(root):
            entry = _entry(source, base)
            if entry is not None and str(entry) not in seen:
                seen.add(str(entry))
                entries.append((entry, ""))
    return entries


def _parse_jspf_entries(text: str, base: Path | None) -> list:
    payload = json.loads(text)
    playlist = payload.get("playlist") if isinstance(payload, dict) else None
    if not isinstance(playlist, dict):
        playlist = payload
    tracks = playlist.get("track") if isinstance(playlist, dict) else None
    if not isinstance(tracks, list):
        tracks = []
    entries: list = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        title = str(track.get("title") or "").strip()[:300]
        location = track.get("location")
        locations = location if isinstance(location, list) else ([location] if location else [])
        for item in locations:
            entry = _entry(str(item), base)
            if entry is not None:
                entries.append((entry, title))
    return entries


def _parse_rmp_entries(text: str, base: Path | None) -> list:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return _parse_ram_entries(text, base)
    entries: list = []
    for node in root.iter():
        tag = str(node.tag).lower()
        if tag.endswith("ref"):
            source = _attr_ci(node, "src") or _attr_ci(node, "href")
        elif tag in {"audio", "video", "media", "entry"}:
            source = _attr_ci(node, "src") or _attr_ci(node, "href")
        else:
            source = None
        if not source:
            continue
        entry = _entry(source, base)
        if entry is not None:
            entries.append((entry, ""))
    return entries


def _parse_ram_entries(text: str, base: Path | None) -> list:
    entries: list = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry = _entry(line, base)
        if entry is not None:
            entries.append((entry, ""))
    return entries


def _parse_json_entries(text: str, base: Path | None) -> list:
    payload = json.loads(text)
    if not isinstance(payload, dict) or not (payload.get("version") == 1 or payload.get("type") == "mpcasu-playlist"):
        raise PlaylistError("unsupported playlist document")
    values = payload.get("items")
    if not isinstance(values, list) or len(values) > MAX_PLAYLIST_ITEMS:
        raise PlaylistError("invalid playlist items")
    result = []
    for value in values:
        title = ""
        if isinstance(value, dict) and payload.get("type") == "mpcasu-playlist":
            title = str(value.get("title") or "")
            value = value.get("sourceUrl") or value.get("url") or value.get("path")
        if not isinstance(value, str): raise PlaylistError("invalid playlist entry")
        entry = _entry(value, base)
        if entry is not None: result.append((entry, title))
    return result


def _parse_cue_entries(text: str, base: Path | None) -> list:
    entries = []
    for line in text.splitlines():
        match = re.match(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+\S+\s*$', line, re.IGNORECASE)
        if match:
            source = _entry(match.group(1) or match.group(2), base)
            if source is not None:
                entries.append((source, ""))
    return entries


_PARSERS.update({
    "json": _parse_json_entries,
    "cue": _parse_cue_entries,
    "m3u": _parse_m3u_entries,
    "pls": _parse_pls_entries,
    "wpl": _parse_wpl_entries,
    "xspf": _parse_xspf_entries,
    "jspf": _parse_jspf_entries,
    "asx": _parse_asx_entries,
    "rmp": _parse_rmp_entries,
})


def _looks_like_jspf(raw: bytes) -> bool:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return "playlist" in payload or "track" in payload or "tracks" in payload


class PlaylistModel:
    def __init__(self, items: Iterable[str | Path] = ()):
        self._items: list[Path | str] = []
        self.add(items)

    @property
    def items(self) -> tuple[Path | str, ...]:
        return tuple(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def item(self, index: int) -> Path | str:
        try:
            return self._items[int(index)]
        except (IndexError, ValueError) as exc:
            raise PlaylistError("playlist index is out of range") from exc

    def index_of(self, value: str | Path) -> int | None:
        path = self._path(value)
        try:
            return self._items.index(path)
        except ValueError:
            return None

    @staticmethod
    def _path(value: str | Path) -> Path | str:
        text = str(value)
        if not text or "\0" in text or len(text.encode("utf-8")) > MAX_PLAYLIST_PATH_BYTES:
            raise PlaylistError("playlist path is invalid")
        if _is_remote(text):
            return text
        return Path(text).expanduser().resolve()

    def add(self, values: Iterable, *, existing_only: bool = False) -> int:
        """Add items (Path or str URLs) to playlist."""
        added = 0
        known = {str(item) for item in self._items}
        for value in values:
            if isinstance(value, str) and _is_remote(value):
                if value in known:
                    continue
                if len(self._items) >= MAX_PLAYLIST_ITEMS:
                    raise PlaylistError("playlist item count exceeds limit")
                self._items.append(value)
                known.add(value)
                added += 1
            else:
                path = self._path(value)
                if isinstance(path, str):
                    if path in known:
                        continue
                    if len(self._items) >= MAX_PLAYLIST_ITEMS:
                        raise PlaylistError("playlist item count exceeds limit")
                    self._items.append(path)
                    known.add(path)
                    added += 1
                    continue
                if existing_only and not path.is_file():
                    continue
                if str(path) in known:
                    continue
                if len(self._items) >= MAX_PLAYLIST_ITEMS:
                    raise PlaylistError("playlist item count exceeds limit")
                self._items.append(path)
                known.add(str(path))
                added += 1
        return added

    def remove(self, indices: Iterable[int]) -> None:
        unique = sorted({int(index) for index in indices}, reverse=True)
        if any(index < 0 or index >= len(self._items) for index in unique):
            raise PlaylistError("playlist index is out of range")
        for index in unique:
            del self._items[index]

    def move(self, index: int, delta: int) -> int:
        source, target = int(index), int(index) + int(delta)
        if source < 0 or source >= len(self._items):
            raise PlaylistError("playlist index is out of range")
        if target < 0 or target >= len(self._items):
            return source
        self._items[source], self._items[target] = self._items[target], self._items[source]
        return target

    def move_many(self, indices: Iterable[int], delta: int) -> None:
        """Move a multi-selection (Ctrl/Shift) one step up or down as a unit.
        Every selected row keeps its relative order; the whole block shifts.
        Playlist groups move together with their (UI-only) children."""
        rows = sorted({int(index) for index in indices})
        if any(index < 0 or index >= len(self._items) for index in rows):
            raise PlaylistError("playlist index is out of range")
        if delta > 0:
            for index in reversed(rows):
                self.move(index, 1)
        elif delta < 0:
            for index in rows:
                self.move(index, -1)

    def clear(self) -> None:
        self._items.clear()

    def to_payload(self) -> dict:
        return {"version": 1, "items": [str(item) for item in self._items]}

    @classmethod
    def from_payload(cls, payload: object, *, existing_only: bool = False):
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise PlaylistError("unsupported playlist document")
        values = payload.get("items")
        if not isinstance(values, list) or len(values) > MAX_PLAYLIST_ITEMS:
            raise PlaylistError("playlist items must be a bounded array")
        if not all(isinstance(value, str) for value in values):
            raise PlaylistError("playlist items must be paths")
        result = cls()
        result.add(values, existing_only=existing_only)
        return result


def detect_playlist_format(path: str | Path) -> str:
    """Detect playlist format by extension first, then by content."""
    source = Path(path).expanduser().resolve()
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise PlaylistError(f"could not read playlist: {exc}") from exc
    if len(raw) > MAX_PLAYLIST_FILE_BYTES:
        raise PlaylistError("playlist exceeds safety limit")
    ext = source.suffix.lower()
    if ext == ".cue":
        return "cue"
    if ext in _EXT_M3U:
        return "m3u"
    if ext in _EXT_PLS:
        return "pls"
    if ext in _EXT_WPL:
        return "wpl"
    if ext in _EXT_XSPF:
        return "xspf"
    if ext in _EXT_JSPF:
        return "jspf"
    if ext in _EXT_ASX:
        return "asx"
    if ext in _EXT_RMP:
        return "rmp"
    if ext == ".json":
        return "jspf" if _looks_like_jspf(raw) else "json"
    text = raw.lstrip()
    if text.startswith(b"{"):
        return "jspf" if _looks_like_jspf(raw) else "json"
    if text.startswith(b"<"):
        lowered = text[:4096].lower()
        if b"xspf" in lowered or b"<tracklist" in lowered:
            return "xspf"
        if b"<asx" in lowered or b"<entry" in lowered or b"<ref" in lowered:
            return "asx"
        if b"<?wpl" in lowered or b"<media " in lowered:
            return "wpl"
        if b"<track" in lowered:
            return "xspf"
        return "unknown"
    if text.startswith(b"#EXTM3U") or text.startswith(b"#EXTINF"):
        return "m3u"
    if text.startswith(b"[playlist]"):
        return "pls"
    line = text.split(b"\n", 1)[0].strip()
    if b"File1=" in line:
        return "pls"
    if re.search(rb"^[^#\s]", line):
        return "m3u"
    return "unknown"


def m3u_names(text: str) -> dict:
    """Map normalized M3U entries to their #EXTINF display names."""
    return {str(entry): title for entry, title in _parse_m3u_entries(text, None) if title}


def pls_names(text: str) -> dict:
    """Map normalized PLS entries to their TitleN= display names."""
    return {str(entry): title for entry, title in _parse_pls_entries(text, None) if title}


def playlist_names(path: str | Path) -> dict:
    """Display names for playlist entries, resolved like the loader (empty on failure)."""
    try:
        fmt = detect_playlist_format(path)
        parser = _PARSERS.get(fmt)
        if parser is None:
            return {}
        raw = _read_bytes(path)
        base = Path(path).expanduser().resolve().parent
        entries = parser(_decode(raw), base)
    except Exception:  # noqa: BLE001 - names are best-effort
        return {}
    return {str(entry): title for entry, title in entries if title}


def parse_m3u(raw: bytes, base: Path | None = None) -> list:
    return [entry for entry, _ in _parse_m3u_entries(_decode(raw), base)]


def parse_pls(raw: bytes, base: Path | None = None) -> list:
    return [entry for entry, _ in _parse_pls_entries(_decode(raw), base)]


def _read_bytes(path: str | Path) -> bytes:
    source = Path(path).expanduser().resolve()
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise PlaylistError(f"could not read playlist: {exc}") from exc
    if len(raw) > MAX_PLAYLIST_FILE_BYTES:
        raise PlaylistError("playlist exceeds safety limit")
    return raw


def load_playlist_file(path: str | Path, *, existing_only: bool = False) -> PlaylistModel:
    source = Path(path).expanduser().resolve()
    fmt = detect_playlist_format(source)
    parser = _PARSERS.get(fmt)
    if parser is None:
        raise PlaylistError(f"unknown playlist format: {source.suffix or 'content'}")
    raw = _read_bytes(source)
    try:
        if fmt == "json":
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise PlaylistError("playlist must be UTF-8 JSON") from exc
        else:
            text = _decode(raw)
        entries = parser(text, source.parent)
    except ET.ParseError as exc:
        raise PlaylistError(f"malformed playlist XML: {exc}") from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PlaylistError(f"malformed playlist: {exc}") from exc
    result = PlaylistModel()
    result.add([entry for entry, _ in entries], existing_only=existing_only)
    return result


def save_playlist_file(path: str | Path, model: PlaylistModel) -> Path:
    target = Path(path).expanduser().resolve()
    suffix = target.suffix.lower()
    if suffix in _EXT_M3U:
        lines = ["#EXTM3U", ""]
        for item in model.items:
            lines.append(str(item))
        text = "\n".join(lines) + "\n"
        if len(text.encode("utf-8")) > MAX_PLAYLIST_FILE_BYTES:
            raise PlaylistError("playlist exceeds safety limit")
        target.write_text(text, encoding="utf-8")
        return target
    if suffix in _EXT_PLS:
        lines = ["[playlist]", f"NumberOfEntries={len(model.items)}", ""]
        for index, item in enumerate(model.items, 1):
            lines.append(f"File{index}={item}")
            lines.append(f"Title{index}={Path(str(item)).name}")
        lines.append("Version=2")
        text = "\n".join(lines) + "\n"
        if len(text.encode("utf-8")) > MAX_PLAYLIST_FILE_BYTES:
            raise PlaylistError("playlist exceeds safety limit")
        target.write_text(text, encoding="utf-8")
        return target
    if suffix in _EXT_XSPF:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<playlist version="1" xmlns="http://xspf.org/ns/0/">',
                 "  <trackList>"]
        for item in model.items:
            title = Path(str(item)).name
            lines.append("    <track>")
            lines.append(f"      <location>{_xml_escape(str(item))}</location>")
            lines.append(f"      <title>{_xml_escape(title)}</title>")
            lines.append("    </track>")
        lines.append("  </trackList>")
        lines.append("</playlist>")
        text = "\n".join(lines) + "\n"
        if len(text.encode("utf-8")) > MAX_PLAYLIST_FILE_BYTES:
            raise PlaylistError("playlist exceeds safety limit")
        target.write_text(text, encoding="utf-8")
        return target
    text = None
    if suffix in _EXT_WPL:
        text = '<smil><body><seq>' + ''.join(f'<media src="{_xml_escape(str(item))}"/>' for item in model.items) + '</seq></body></smil>'
    elif suffix in _EXT_ASX:
        text = '<asx version="3.0">' + ''.join(f'<entry><ref href="{_xml_escape(str(item))}"/></entry>' for item in model.items) + '</asx>'
    elif suffix in _EXT_JSPF:
        text = json.dumps({"playlist": {"track": [{"location": [str(item)]} for item in model.items]}}, ensure_ascii=False)
    elif suffix in _EXT_RMP:
        text = "\n".join(str(item) for item in model.items) + "\n"
    if text is not None:
        if len(text.encode("utf-8")) > MAX_PLAYLIST_FILE_BYTES:
            raise PlaylistError("playlist exceeds safety limit")
        target.write_text(text, encoding="utf-8")
        return target
    try:
        return atomic_write_json(target, model.to_payload(), max_bytes=MAX_PLAYLIST_FILE_BYTES)
    except CasuError as exc:
        raise PlaylistError(str(exc)) from exc


def _xml_escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def detect_media_type(path: str | Path) -> str:
    """Quick media-type label for display (does not probe content)."""
    source = Path(path).expanduser().resolve()
    ext = source.suffix.lower().lstrip(".")
    if ext in {"mp3", "flac", "wav", "aac", "ogg", "opus", "m4a", "wma",
               "aiff", "alac", "ape", "wv", "tta", "dts", "mpc", "voc", "au"}:
        return ext.upper()
    if ext in {"mp4", "mkv", "webm", "avi", "mov", "m4v", "flv", "wmv",
               "mpeg", "mpg", "m2ts", "mts", "ts", "vob", "ogv", "3gp",
               "divx", "rm", "rmvb", "mxf", "asf"}:
        return ext.upper()
    if ext in {"m3u", "m3u8", "pls", "wpl", "xspf", "jspf", "asx", "wmx",
               "wvx", "rmp", "ram"}:
        return "PLAYLIST"
    if ext in {"casu", "mp5"}:
        return "CASU"
    return "MEDIA"


def detect_entry_type(path: str | Path) -> str:
    """Classify a playlist entry as local file, stream URL, YouTube, CASU, etc."""
    source = str(path)
    from urllib.parse import urlparse
    try:
        parsed = urlparse(source)
        if parsed.scheme and parsed.netloc:
            host = parsed.hostname.lower() if parsed.hostname else ""
            if host in _YOUTUBE_HOSTS:
                return "youtube"
            if parsed.scheme in {"http", "https"}:
                return "http-stream"
            if parsed.scheme in {"rtsp", "rtsps"}:
                return "rtsp-stream"
            if parsed.scheme in {"rtmp", "rtmps"}:
                return "rtmp-stream"
            if parsed.scheme in {"mmsh", "mmst"}:
                return "mms-stream"
            if parsed.scheme in {"udp", "srt", "rist"}:
                return "udp-stream"
            if parsed.scheme == "ftp":
                return "ftp-stream"
            return "network-stream"
    except ValueError:
        pass
    suffix = Path(source).suffix.lower()
    if suffix == ".casu":
        return "casu"
    if suffix in {".mp5", ".mp5a"}:
        return "mp5"
    if suffix in _EXT_M3U or suffix in _EXT_PLS or suffix in _EXT_WPL or \
       suffix in _EXT_XSPF or suffix in _EXT_JSPF or suffix in _EXT_ASX or \
       suffix in _EXT_RMP:
        return "playlist"
    try:
        parsed = urlparse(str(source))
        if parsed.hostname and "spotify.com" in parsed.hostname.lower():
            return "spotify"
    except ValueError:
        pass
    return "local-file"


_YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
})

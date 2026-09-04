"""Bounded Extended-M3U and XMLTV support shared by MPCASU front ends."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


MAX_PLAYLIST_BYTES = 8 * 1024 * 1024
MAX_XMLTV_BYTES = 32 * 1024 * 1024
MAX_CHANNELS = 10_000
MAX_PROGRAMMES = 100_000
MAX_LINE_BYTES = 4096
MAX_TEXT_BYTES = 4096
MAX_URL_BYTES = 8192
FETCH_TIMEOUT_SECONDS = 20.0
STREAM_SCHEMES = frozenset({
    "http", "https", "ftp", "ftps", "rtsp", "rtsps", "rtmp", "rtmps",
    "rtp", "udp", "srt", "rist", "smb", "mmsh", "mmst",
})


class EpgError(ValueError):
    pass


@dataclass(frozen=True)
class StreamChannel:
    url: str
    name: str
    epg_id: str = ""
    group: str = ""
    logo: str = ""


@dataclass(frozen=True)
class Programme:
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    description: str = ""
    category: str = ""


@dataclass(frozen=True)
class StreamCatalog:
    channels: tuple[StreamChannel, ...]
    epg_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpgGuide:
    channel_names: dict[str, str]
    programmes: tuple[Programme, ...]

    def schedule(self, channel_id: str, *, now: datetime | None = None,
                 limit: int = 20) -> tuple[Programme, ...]:
        current = _utc(now or datetime.now(timezone.utc))
        maximum = max(1, min(200, int(limit)))
        values = [item for item in self.programmes
                  if item.channel_id == channel_id and item.stop > current]
        return tuple(values[:maximum])

    def now_next(self, channel_id: str, *, now: datetime | None = None
                 ) -> tuple[Programme | None, Programme | None]:
        current = _utc(now or datetime.now(timezone.utc))
        values = [item for item in self.programmes
                  if item.channel_id == channel_id and item.stop > current]
        active = next((item for item in values
                       if item.start <= current < item.stop), None)
        upcoming = next((item for item in values if item.start >= current
                         and item is not active), None)
        return active, upcoming


_ATTRIBUTE = re.compile(r'''([A-Za-z0-9_-]+)=(?:"([^"]*)"|'([^']*)'|([^\s]+))''')


def _bounded(value: object, label: str, maximum: int = MAX_TEXT_BYTES) -> str:
    text = str(value or "").strip()
    if "\0" in text or len(text.encode("utf-8")) > maximum:
        raise EpgError(f"{label} exceeds its safety limit")
    return text


def _decode(raw: bytes, label: str) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("iso-8859-1")
        except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 is total
            raise EpgError(f"{label} has an unsupported text encoding") from exc


def _read_bytes(path: str | Path, maximum: int, label: str) -> bytes:
    source = Path(path).expanduser().resolve()
    try:
        with source.open("rb") as handle:
            raw = handle.read(maximum + 1)
    except OSError as exc:
        raise EpgError(f"{label} is unavailable") from exc
    if len(raw) > maximum:
        raise EpgError(f"{label} exceeds its safety limit")
    return raw


def fetch_document(url: str, *, max_bytes: int, timeout: float = FETCH_TIMEOUT_SECONDS) -> bytes:
    """Fetch one explicit HTTP(S) guide/catalog with hard size and time bounds."""
    value = _bounded(url, "catalog URL", MAX_URL_BYTES)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise EpgError("remote catalog URL must use HTTP or HTTPS")
    request = urllib.request.Request(value, headers={"User-Agent": "MPCASU/1.0 EPG"})
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, min(60.0, timeout))) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme.lower() not in {"http", "https"}:
                raise EpgError("catalog redirect left HTTP(S)")
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > max_bytes:
                raise EpgError("remote catalog exceeds its safety limit")
            raw = response.read(max_bytes + 1)
    except (OSError, ValueError) as exc:
        if isinstance(exc, EpgError):
            raise
        raise EpgError(f"could not download catalog: {exc}") from exc
    if len(raw) > max_bytes:
        raise EpgError("remote catalog exceeds its safety limit")
    return raw


def _stream_value(value: str) -> str:
    result = _bounded(value, "stream location", MAX_URL_BYTES)
    parsed = urllib.parse.urlsplit(result)
    if parsed.scheme and parsed.scheme.lower() not in STREAM_SCHEMES:
        raise EpgError(f"unsupported stream URL scheme: {parsed.scheme}")
    return result


def parse_m3u(raw: bytes | str, *, base: Path | None = None) -> StreamCatalog:
    if isinstance(raw, bytes):
        if len(raw) > MAX_PLAYLIST_BYTES:
            raise EpgError("stream playlist exceeds its safety limit")
        text = _decode(raw, "stream playlist")
    else:
        text = raw
        if len(text.encode("utf-8")) > MAX_PLAYLIST_BYTES:
            raise EpgError("stream playlist exceeds its safety limit")
    lines = text.splitlines()
    if any(len(line.encode("utf-8")) > MAX_LINE_BYTES for line in lines):
        raise EpgError("stream playlist line exceeds its safety limit")
    channels: list[StreamChannel] = []
    epg_urls: list[str] = []
    pending: dict[str, str] | None = None
    pending_name = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXTM3U"):
            for key, quoted, single, bare in _ATTRIBUTE.findall(line):
                if key.casefold() in {"url-tvg", "x-tvg-url", "tvg-url"}:
                    for value in (quoted or single or bare).split(","):
                        candidate = value.strip()
                        if candidate and candidate not in epg_urls:
                            epg_urls.append(_bounded(candidate, "EPG URL", MAX_URL_BYTES))
            continue
        if upper.startswith("#EXTINF:"):
            head, separator, name = line.partition(",")
            pending = {}
            for key, quoted, single, bare in _ATTRIBUTE.findall(head):
                pending[key.casefold()] = _bounded(quoted or single or bare,
                                                    "playlist attribute")
            pending_name = _bounded(name if separator else "", "channel name")
            continue
        if line.startswith("#"):
            continue
        try:
            location = _stream_value(line)
        except EpgError:
            pending = None; pending_name = ""
            continue
        if not urllib.parse.urlsplit(location).scheme and base is not None:
            location = str((base / location).expanduser().resolve())
        attrs = pending or {}
        name = pending_name or attrs.get("tvg-name") or Path(
            urllib.parse.urlsplit(location).path).name or "Unnamed stream"
        channels.append(StreamChannel(
            location, _bounded(name, "channel name"),
            _bounded(attrs.get("tvg-id", ""), "EPG channel id"),
            _bounded(attrs.get("group-title", ""), "channel group"),
            _bounded(attrs.get("tvg-logo", ""), "channel logo", MAX_URL_BYTES),
        ))
        pending = None; pending_name = ""
        if len(channels) > MAX_CHANNELS:
            raise EpgError(f"stream playlist exceeds {MAX_CHANNELS} channels")
    return StreamCatalog(tuple(channels), tuple(epg_urls[:32]))


def load_m3u(path: str | Path) -> StreamCatalog:
    source = Path(path).expanduser().resolve()
    return parse_m3u(_read_bytes(source, MAX_PLAYLIST_BYTES, "stream playlist"),
                     base=source.parent)


def fetch_m3u(url: str) -> StreamCatalog:
    return parse_m3u(fetch_document(url, max_bytes=MAX_PLAYLIST_BYTES))


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _tag(child) == name:
            return _bounded("".join(child.itertext()), f"XMLTV {name}")
    return ""


def parse_xmltv(raw: bytes) -> EpgGuide:
    if len(raw) > MAX_XMLTV_BYTES:
        raise EpgError("XMLTV guide exceeds its safety limit")
    prefix = raw[:4096].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise EpgError("XMLTV DTD/entities are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EpgError("XMLTV guide is malformed") from exc
    if _tag(root) != "tv":
        raise EpgError("XMLTV root element must be tv")
    channel_names: dict[str, str] = {}
    programmes: list[Programme] = []
    for node in root:
        kind = _tag(node)
        if kind == "channel":
            identifier = _bounded(node.attrib.get("id", ""), "XMLTV channel id")
            if identifier:
                channel_names[identifier] = _child_text(node, "display-name") or identifier
                if len(channel_names) > MAX_CHANNELS:
                    raise EpgError(f"XMLTV exceeds {MAX_CHANNELS} channels")
        elif kind == "programme":
            channel = _bounded(node.attrib.get("channel", ""), "XMLTV channel id")
            title = _child_text(node, "title")
            if not channel or not title:
                continue
            start = parse_xmltv_time(node.attrib.get("start", ""))
            stop = parse_xmltv_time(node.attrib.get("stop", ""))
            if stop <= start:
                continue
            programmes.append(Programme(channel, start, stop, title,
                                         _child_text(node, "desc"),
                                         _child_text(node, "category")))
            if len(programmes) > MAX_PROGRAMMES:
                raise EpgError(f"XMLTV exceeds {MAX_PROGRAMMES} programmes")
    programmes.sort(key=lambda item: (item.channel_id, item.start, item.stop))
    return EpgGuide(channel_names, tuple(programmes))


def load_xmltv(path: str | Path) -> EpgGuide:
    return parse_xmltv(_read_bytes(path, MAX_XMLTV_BYTES, "XMLTV guide"))


def fetch_xmltv(url: str) -> EpgGuide:
    return parse_xmltv(fetch_document(url, max_bytes=MAX_XMLTV_BYTES))


def parse_xmltv_time(value: str) -> datetime:
    text = _bounded(value, "XMLTV timestamp", 64)
    match = re.fullmatch(r"(\d{14})(?:\s*([+-]\d{4}|Z))?", text)
    if not match:
        raise EpgError("XMLTV timestamp is invalid")
    stamp, offset = match.groups()
    if offset == "Z":
        offset = "+0000"
    try:
        parsed = datetime.strptime(stamp + (offset or "+0000"), "%Y%m%d%H%M%S%z")
    except ValueError as exc:
        raise EpgError("XMLTV timestamp is invalid") from exc
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))

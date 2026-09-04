"""CASU MP5 reader with zstd decompression."""
from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    import zstd
except ImportError:
    zstd = None

from .format import (CHUNK_HEADER, FOOTER_SIZE, HEADER, MAGIC, VERSION,
                     ChunkType, MAX_CHUNK_PAYLOAD, SeekEntry)


class Mp5Error(ValueError):
    pass


@dataclass
class Mp5Container:
    path: Path
    manifest: dict
    chunks: list[tuple]
    size: int

    def read_chunk_at(self, offset: int) -> tuple:
        with self.path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(CHUNK_HEADER.size)
            if len(raw) < CHUNK_HEADER.size:
                raise Mp5Error("truncated chunk header")
            chunk_type, stream_id, pts, comp_length = CHUNK_HEADER.unpack(raw)
            comp = handle.read(comp_length)
            if len(comp) < comp_length:
                raise Mp5Error("truncated chunk payload")
            payload = _decompress(comp)
            return ChunkType(chunk_type), stream_id, pts, payload


def _decompress(data: bytes) -> bytes:
    if zstd is not None:
        try:
            return zstd.decompress(data)
        except zstd.Error:
            pass
    import zlib
    try:
        return zlib.decompress(data)
    except zlib.error as exc:
        raise Mp5Error("chunk payload decompression failed") from exc


def read_mp5(path: str | Path) -> Mp5Container:
    source = Path(path).expanduser().resolve()
    size = source.stat().st_size
    with source.open("rb") as handle:
        raw = handle.read(HEADER.size)
        if len(raw) < HEADER.size:
            raise Mp5Error("file too small for MP5 header")
        magic, version, flags, manifest_length, _reserved = HEADER.unpack(raw)
        if magic != MAGIC:
            raise Mp5Error(f"not a CASU MP5 file (magic={magic!r})")
        if version != VERSION:
            raise Mp5Error(f"unsupported MP5 version {version}")
        manifest_raw = handle.read(manifest_length)
        if len(manifest_raw) < manifest_length:
            raise Mp5Error("truncated manifest")
        manifest = json.loads(manifest_raw.decode("utf-8"))
        chunks = []
        while True:
            pos = handle.tell()
            head = handle.read(CHUNK_HEADER.size)
            if len(head) < CHUNK_HEADER.size:
                break
            chunk_type, stream_id, pts, comp_length = CHUNK_HEADER.unpack(head)
            try:
                ct = ChunkType(chunk_type)
            except ValueError:
                break
            if ct == ChunkType.END:
                break
            chunks.append((ct, stream_id, pts, comp_length, pos))
            handle.seek(pos + CHUNK_HEADER.size + comp_length)
    return Mp5Container(source, manifest, tuple(chunks), size)


def _split_attachment(payload: bytes) -> tuple[dict, bytes]:
    if len(payload) < 2:
        raise Mp5Error("attachment chunk too small")
    (meta_length,) = struct.unpack("<H", payload[:2])
    if len(payload) < 2 + meta_length:
        raise Mp5Error("truncated attachment metadata")
    meta = json.loads(payload[2:2 + meta_length].decode("utf-8"))
    return meta, payload[2 + meta_length:]


def extract_attachment(path: str | Path) -> tuple[str, bytes]:
    """Reassemble the verified original source carried inside an MP5 file."""
    container = read_mp5(path)
    integrity: dict | None = None
    parts: dict[int, bytes] = {}
    filename = "media.bin"
    expected_parts = 1
    for chunk_type, _stream_id, pts, _comp_length, pos in container.chunks:
        if chunk_type == ChunkType.INTEGRITY_TABLE:
            _ct, _sid, _pts, payload = container.read_chunk_at(pos)
            integrity = json.loads(payload.decode("utf-8"))
        elif chunk_type == ChunkType.ATTACHMENT:
            _ct, _sid, _pts, payload = container.read_chunk_at(pos)
            meta, data = _split_attachment(payload)
            filename = str(meta.get("filename", filename))
            expected_parts = int(meta.get("parts", expected_parts))
            parts[int(meta.get("part", pts))] = data
    if not parts:
        raise Mp5Error("MP5 container carries no attachment payload")
    if len(parts) != expected_parts:
        raise Mp5Error(f"MP5 attachment incomplete: {len(parts)}/{expected_parts} parts")
    payload_bytes = b"".join(parts[index] for index in range(expected_parts))
    if integrity:
        expected = integrity.get("attachment_sha256")
        if expected and hashlib.sha256(payload_bytes).hexdigest() != expected:
            raise Mp5Error("MP5 attachment failed SHA-256 verification")
    return filename, payload_bytes


def extract_source(path: str | Path, target_dir: str | Path) -> Path:
    """Extract the original source into target_dir and return the file path."""
    filename, payload_bytes = extract_attachment(path)
    suffix = Path(filename).suffix or ".bin"
    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="mpcasu-mp5-", suffix=suffix, dir=target)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload_bytes)
    return Path(tmp)


def verify_mp5(path: str | Path) -> list[str]:
    """Return integrity problems; an empty list means the container is valid."""
    issues: list[str] = []
    try:
        container = read_mp5(path)
    except Mp5Error as exc:
        return [str(exc)]
    try:
        with container.path.open("rb") as handle:
            handle.seek(-36, 2)
            tail = handle.read(36)
    except OSError as exc:
        return [f"could not read footer: {exc}"]
    if len(tail) != 36:
        issues.append("missing footer")
    else:
        count, digest = struct.unpack("<I32s", tail)
        manifest_bytes = json.dumps(container.manifest, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(manifest_bytes).digest() != digest:
            issues.append("manifest digest mismatch")
        if count not in (len(container.chunks), len(container.chunks) + 1):
            issues.append("footer chunk count mismatch")
    try:
        extract_attachment(path)
    except Mp5Error as exc:
        issues.append(f"attachment: {exc}")
    return issues

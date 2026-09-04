from __future__ import annotations
import hashlib
import hmac
import json
import struct
import copy
import string
from dataclasses import replace
from dataclasses import dataclass, field
from pathlib import Path
from .format import DEFAULT_LIMITS, CasuLimits, ChunkType, NativeChunk, SeekEntry
from .jsonutil import StrictJsonError, strict_json_loads
from .validation import NativeV2PayloadValidator, NativeV2ValidationError, validate_manifest
MAGIC = b"CASUNAT2"
VERSION = 2
HEADER = struct.Struct(">8sHHQ")
CHUNK_HEADER = struct.Struct(">BBHqQQ")
from .video import TileStateCache

class NativeV2Error(ValueError):
    pass
_U32 = struct.Struct('>I')

def _decode_recovery_point(payload: bytes, *, offset: int, prefix_sha256: str, prior_chunks: dict[int, NativeChunk], allow_legacy_verified: bool=False) -> dict:
    try:
        value = strict_json_loads(payload)
        if not isinstance(value, dict):
            raise ValueError
        boundary = int(value['last_complete_chunk_offset'])
        declared_checkpoint = value.pop('checkpoint_sha256', None)
        declared_prefix = value.get('sha256_before_recovery')
        if value.get('version') != 1 or boundary not in prior_chunks or boundary >= offset:
            raise ValueError
        if declared_checkpoint is None or declared_prefix is None:
            if not allow_legacy_verified:
                raise ValueError
        else:
            declared_checkpoint = str(declared_checkpoint)
            checkpoint = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
            if str(declared_prefix) != prefix_sha256 or len(declared_checkpoint) != 64 or (not hmac.compare_digest(hashlib.sha256(checkpoint).hexdigest(), declared_checkpoint)):
                raise ValueError
        for field_name, expected_type in (('key_state_offsets', ChunkType.VIDEO_KEY_STATE), ('audio_block_offsets', ChunkType.AUDIO_BLOCK)):
            entries = value.get(field_name)
            if not isinstance(entries, dict):
                raise ValueError
            for raw_stream_id, raw_offset in entries.items():
                referenced = prior_chunks.get(int(raw_offset))
                if referenced is None or referenced.chunk_type != expected_type or referenced.stream_id != int(raw_stream_id) or (int(raw_offset) > boundary):
                    raise ValueError
        if declared_checkpoint is not None:
            value['checkpoint_sha256'] = declared_checkpoint
        return value
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise NativeV2Error('invalid CASUNAT2 recovery point') from exc

@dataclass(frozen=True)
class NativeV2Recovery:
    """Verified prefix that can be resumed after an interrupted write.

    Recovery deliberately does not claim full-container integrity: the
    trailing seek/integrity/END chunks may be absent after a crash.
    """
    path: Path
    manifest: dict
    chunks: tuple[NativeChunk, ...]
    recovery_point: dict
    complete_chunk_offset: int

@dataclass(frozen=True)
class ReconstructionPlan:
    stream_id: int
    target_pts: int
    key_state_pts: int
    key_state_offset: int
    first_update_offset: int

@dataclass(frozen=True)
class NativeV2Container:
    path: Path
    manifest: dict
    chunks: tuple[NativeChunk, ...]
    offsets: tuple[int, ...]
    seek_entries: tuple[SeekEntry, ...]
    integrity_verified: bool
    recovery_points: tuple[dict, ...] = ()
    chunk_hashes: tuple[tuple[int, str], ...] = ()
    limits: CasuLimits = field(default=DEFAULT_LIMITS, repr=False)

    def chunks_at_or_after(self, pts: int, stream_id: int | None=None):
        return tuple((chunk for chunk in self.chunks if (stream_id is None or chunk.stream_id == stream_id) and chunk.pts >= pts))

    def read_chunk_at(self, offset: int) -> tuple[NativeChunk, int]:
        """Read one chunk using a real file seek, returning chunk and next offset."""
        size = self.path.stat().st_size
        if size > self.limits.max_file_bytes or offset < HEADER.size or offset + CHUNK_HEADER.size > size:
            raise NativeV2Error('chunk offset is outside CASUNAT2 file')
        with self.path.open('rb') as handle:
            handle.seek(offset)
            header = handle.read(CHUNK_HEADER.size)
            if len(header) != CHUNK_HEADER.size:
                raise NativeV2Error('truncated chunk at indexed offset')
            kind, stream_id, flags, pts, payload_length, uncompressed = CHUNK_HEADER.unpack(header)
            if payload_length > self.limits.max_chunk_bytes or payload_length > size - handle.tell() or uncompressed < payload_length or (uncompressed > self.limits.max_chunk_bytes) or (flags != 0):
                raise NativeV2Error('indexed chunk payload exceeds file')
            payload = handle.read(payload_length)
        expected_hash = dict(self.chunk_hashes).get(offset)
        if expected_hash is None:
            raise NativeV2Error('indexed chunk is absent from CASUNAT2 hash table')
        if hashlib.sha256(header + payload).hexdigest() != expected_hash:
            raise NativeV2Error('on-disk CASUNAT2 chunk changed after verification')
        try:
            chunk_type = ChunkType(kind)
        except ValueError as exc:
            raise NativeV2Error('indexed chunk has unknown type') from exc
        return (NativeChunk(chunk_type, stream_id, pts, payload, flags, uncompressed), offset + CHUNK_HEADER.size + payload_length)

    def read_audio_block_meta_at(self, offset: int) -> dict:
        """Read only the JSON metadata prefix of an audio block chunk.

        Fast path for event-timeline construction: the PCM payload stays on
        disk uncompressed and unhashed. Full payload integrity remains
        covered by verify_payloads/read_chunk_at.
        """
        size = self.path.stat().st_size
        if size > self.limits.max_file_bytes or offset < HEADER.size or offset + CHUNK_HEADER.size > size:
            raise NativeV2Error('chunk offset is outside CASUNAT2 file')
        with self.path.open('rb') as handle:
            handle.seek(offset)
            header = handle.read(CHUNK_HEADER.size)
            if len(header) != CHUNK_HEADER.size:
                raise NativeV2Error('truncated chunk at indexed offset')
            kind, _stream_id, flags, _pts, payload_length, _uncompressed = CHUNK_HEADER.unpack(header)
            if kind != int(ChunkType.AUDIO_BLOCK) or flags != 0:
                raise NativeV2Error('indexed chunk is not an audio block')
            if payload_length > self.limits.max_chunk_bytes or payload_length > size - handle.tell():
                raise NativeV2Error('indexed chunk payload exceeds file')
            prefix = handle.read(min(payload_length, _U32.size + self.limits.max_audio_meta_bytes))
        if len(prefix) < _U32.size:
            raise NativeV2Error('audio block metadata prefix is truncated')
        meta_length, = _U32.unpack_from(prefix)
        if meta_length > self.limits.max_audio_meta_bytes or _U32.size + meta_length > len(prefix):
            raise NativeV2Error('audio block metadata exceeds limit')
        try:
            meta = strict_json_loads(prefix[_U32.size:_U32.size + meta_length])
        except StrictJsonError as exc:
            raise NativeV2Error('invalid audio block metadata') from exc
        if not isinstance(meta, dict):
            raise NativeV2Error('audio block metadata must be an object')
        return meta

    def seek_video(self, stream_id: int, target_pts: int) -> ReconstructionPlan:
        candidates = [entry for entry in self.seek_entries if entry.stream_id == stream_id and entry.key_state_pts <= target_pts]
        if not candidates:
            raise NativeV2Error('no video key state at or before target PTS')
        entry = max(candidates, key=lambda value: (value.key_state_pts, value.key_state_offset))
        return ReconstructionPlan(stream_id, int(target_pts), entry.key_state_pts, entry.key_state_offset, entry.first_update_offset)

    def reconstruct_video(self, stream_id: int, target_pts: int):
        """Seek to a byte-indexed key state and apply dependencies through target."""
        plan = self.seek_video(stream_id, target_pts)
        cache = TileStateCache()
        offset = plan.key_state_offset
        first = True
        dependencies = 0
        while offset < self.path.stat().st_size:
            chunk, following = self.read_chunk_at(offset)
            if chunk.stream_id == stream_id:
                if first:
                    if chunk.chunk_type != ChunkType.VIDEO_KEY_STATE or chunk.pts != plan.key_state_pts:
                        raise NativeV2Error('seek index does not reference its video key state')
                    cache.apply_key_state(chunk.payload)
                    first = False
                elif chunk.chunk_type == ChunkType.VIDEO_KEY_STATE:
                    if chunk.pts > target_pts:
                        break
                    cache.apply_key_state(chunk.payload)
                elif chunk.chunk_type == ChunkType.VIDEO_TILE_UPDATE:
                    if chunk.pts > target_pts:
                        break
                    dependencies += 1
                    if dependencies > self.limits.max_dependency_depth:
                        raise NativeV2Error('CASUNAT2 video dependency depth exceeds limit')
                    cache.apply_tile_update(chunk.payload)
            if chunk.chunk_type in (ChunkType.SEEK_INDEX, ChunkType.INTEGRITY_TABLE, ChunkType.END):
                break
            offset = following
        if cache.frame is None:
            raise NativeV2Error('video reconstruction produced no frame')
        return cache.frame

def read_native_v2(path: str | Path, *, max_manifest_bytes: int=64 * 1024 * 1024, max_chunk_bytes: int=512 * 1024 * 1024, max_chunks: int=10000000, max_file_bytes: int=4 * 1024 * 1024 * 1024, load_payloads: bool=True, verify_payloads: bool | None=None, limits: CasuLimits | None=None) -> NativeV2Container:
    source = Path(path)
    effective_limits = limits or replace(DEFAULT_LIMITS, max_manifest_bytes=max_manifest_bytes, max_chunk_bytes=max_chunk_bytes, max_chunks=max_chunks, max_file_bytes=max_file_bytes)
    effective_limits.validate()
    if verify_payloads is None:
        verify_payloads = load_payloads
    if verify_payloads and (not load_payloads):
        raise NativeV2Error('payload verification requires loaded payloads')
    size = source.stat().st_size
    if size > effective_limits.max_file_bytes:
        raise NativeV2Error('CASUNAT2 file exceeds configured size limit')
    chunks: list[NativeChunk] = []
    offsets: list[int] = []
    seek_entries: tuple[SeekEntry, ...] = ()
    integrity_expected: str | None = None
    integrity_offset: int | None = None
    recovery_points: list[dict] = []
    chunk_hashes: tuple[tuple[int, str], ...] = ()
    observed_chunk_hashes: list[tuple[int, str]] = []
    parsed_by_offset: dict[int, NativeChunk] = {}
    digest = hashlib.sha256()
    with source.open('rb') as handle:
        header = handle.read(HEADER.size)
        if len(header) != HEADER.size:
            raise NativeV2Error('truncated CASUNAT2 header')
        magic, version, header_flags, manifest_length = HEADER.unpack(header)
        if magic != MAGIC or version != VERSION or header_flags != 0:
            raise NativeV2Error('unsupported CASUNAT2 header/version')
        if manifest_length > effective_limits.max_manifest_bytes or manifest_length > size - HEADER.size:
            raise NativeV2Error('invalid CASUNAT2 manifest length')
        manifest_bytes = handle.read(manifest_length)
        if len(manifest_bytes) != manifest_length:
            raise NativeV2Error('truncated CASUNAT2 manifest')
        try:
            manifest = strict_json_loads(manifest_bytes)
        except StrictJsonError as exc:
            raise NativeV2Error('invalid CASUNAT2 manifest') from exc
        try:
            topology = NativeV2PayloadValidator(manifest, effective_limits, semantic=False)
        except NativeV2ValidationError as exc:
            raise NativeV2Error(str(exc)) from exc
        digest.update(header)
        digest.update(manifest_bytes)
        seen_integrity = False
        while handle.tell() < size:
            if len(chunks) >= effective_limits.max_chunks:
                raise NativeV2Error('excessive CASUNAT2 chunks')
            offset = handle.tell()
            chunk_header = handle.read(CHUNK_HEADER.size)
            if len(chunk_header) != CHUNK_HEADER.size:
                raise NativeV2Error('truncated CASUNAT2 chunk header')
            kind, stream_id, flags, pts, payload_length, uncompressed = CHUNK_HEADER.unpack(chunk_header)
            if payload_length > effective_limits.max_chunk_bytes or payload_length > size - handle.tell() or uncompressed < payload_length or (uncompressed > effective_limits.max_chunk_bytes):
                raise NativeV2Error('invalid CASUNAT2 chunk length')
            payload = handle.read(payload_length)
            if len(payload) != payload_length:
                raise NativeV2Error('truncated CASUNAT2 chunk payload')
            try:
                chunk_type = ChunkType(kind)
            except ValueError as exc:
                raise NativeV2Error(f'unknown CASUNAT2 chunk type {kind}') from exc
            if seen_integrity and chunk_type != ChunkType.END:
                raise NativeV2Error('CASUNAT2 contains data after integrity table')
            digest_before_chunk = digest.hexdigest()
            if chunk_type == ChunkType.INTEGRITY_TABLE:
                if seen_integrity:
                    raise NativeV2Error('duplicate CASUNAT2 integrity table')
                seen_integrity = True
            elif not seen_integrity:
                digest.update(chunk_header)
                digest.update(payload)
                observed_chunk_hashes.append((offset, hashlib.sha256(chunk_header + payload).hexdigest()))
            raw_chunk = NativeChunk(chunk_type, stream_id, pts, payload, flags, uncompressed)
            try:
                topology.feed(raw_chunk, allow_system=True)
            except NativeV2ValidationError as exc:
                raise NativeV2Error(str(exc)) from exc
            stored_payload = payload if load_payloads or chunk_type in {ChunkType.SEEK_INDEX, ChunkType.INTEGRITY_TABLE, ChunkType.RECOVERY_POINT, ChunkType.END} else b''
            chunks.append(NativeChunk(chunk_type, stream_id, pts, stored_payload, flags, uncompressed))
            offsets.append(offset)
            if chunk_type == ChunkType.SEEK_INDEX:
                try:
                    values = strict_json_loads(payload)['entries']
                    if not isinstance(values, list) or len(values) > effective_limits.max_chunks:
                        raise TypeError('invalid seek entries')
                    parsed_entries = []
                    for item in values:
                        if not isinstance(item, dict) or set(item) != {'stream_id', 'target_pts', 'key_state_pts', 'key_state_offset', 'first_update_offset'}:
                            raise TypeError('invalid seek entry')
                        if any((isinstance(value, bool) or not isinstance(value, int) for value in item.values())):
                            raise TypeError('invalid seek entry values')
                        parsed_entries.append(SeekEntry(**item))
                    seek_entries = tuple(parsed_entries)
                except (KeyError, TypeError, ValueError, StrictJsonError) as exc:
                    raise NativeV2Error('invalid CASUNAT2 seek index') from exc
            elif chunk_type == ChunkType.INTEGRITY_TABLE:
                integrity_offset = offset
                try:
                    integrity_values = strict_json_loads(payload)
                    integrity_expected = str(integrity_values['sha256_before_integrity'])
                    hashes = integrity_values.get('chunk_sha256', [])
                    if not isinstance(hashes, list) or len(hashes) > effective_limits.max_chunks:
                        raise TypeError('invalid chunk hash table')
                    normalized_hashes = []
                    for item in hashes:
                        if not isinstance(item, dict) or set(item) != {'offset', 'sha256'} or isinstance(item['offset'], bool) or (not isinstance(item['offset'], int)) or (not isinstance(item['sha256'], str)):
                            raise TypeError('invalid chunk hash entry')
                        normalized_hashes.append((item['offset'], item['sha256']))
                    chunk_hashes = tuple(normalized_hashes)
                    hexadecimal = set(string.hexdigits)
                    if len(integrity_expected) != 64 or any((character not in hexadecimal for character in integrity_expected)) or any((offset < HEADER.size or len(value) != 64 or any((character not in hexadecimal for character in value)) for offset, value in chunk_hashes)):
                        raise ValueError('invalid chunk hash')
                except (KeyError, TypeError, StrictJsonError, ValueError) as exc:
                    raise NativeV2Error('invalid CASUNAT2 integrity table') from exc
            elif chunk_type == ChunkType.RECOVERY_POINT:
                recovery_points.append(_decode_recovery_point(payload, offset=offset, prefix_sha256=digest_before_chunk, prior_chunks=parsed_by_offset, allow_legacy_verified=True))
            parsed_by_offset[offset] = raw_chunk
            if chunk_type == ChunkType.END:
                if handle.tell() != size:
                    raise NativeV2Error('trailing bytes after CASUNAT2 END')
                break
    if not chunks or chunks[-1].chunk_type != ChunkType.END:
        raise NativeV2Error('CASUNAT2 is missing END chunk')
    verified = False
    if integrity_expected is None or integrity_offset is None:
        raise NativeV2Error('CASUNAT2 is missing integrity table')
    verified = digest.hexdigest() == integrity_expected
    if not verified:
        raise NativeV2Error('CASUNAT2 integrity verification failed')
    try:
        topology.finalize(require_system=True)
    except NativeV2ValidationError as exc:
        raise NativeV2Error(str(exc)) from exc
    offset_map = dict(zip(offsets, chunks))
    previous_by_stream: dict[int, tuple[int, int]] = {}
    for entry in seek_entries:
        key = offset_map.get(entry.key_state_offset)
        first_update = offset_map.get(entry.first_update_offset)
        if entry.target_pts != entry.key_state_pts or key is None or key.chunk_type != ChunkType.VIDEO_KEY_STATE or (key.stream_id != entry.stream_id) or (key.pts != entry.key_state_pts):
            raise NativeV2Error('CASUNAT2 seek index key-state offset is invalid')
        if first_update is None or first_update.stream_id != entry.stream_id or first_update.chunk_type not in (ChunkType.VIDEO_KEY_STATE, ChunkType.VIDEO_TILE_UPDATE):
            raise NativeV2Error('CASUNAT2 seek index dependency offset is invalid')
        prior = previous_by_stream.get(entry.stream_id)
        marker = (entry.key_state_pts, entry.key_state_offset)
        if prior is not None and marker <= prior:
            raise NativeV2Error('CASUNAT2 seek index is not strictly ordered')
        previous_by_stream[entry.stream_id] = marker
    expected_offsets = {offset for offset, chunk in zip(offsets, chunks) if chunk.chunk_type not in {ChunkType.INTEGRITY_TABLE, ChunkType.END}}
    if {offset for offset, _value in chunk_hashes} != expected_offsets or len(dict(chunk_hashes)) != len(chunk_hashes) or dict(chunk_hashes) != dict(observed_chunk_hashes):
        raise NativeV2Error('CASUNAT2 chunk hash table does not cover the verified prefix')
    indexed_keys = {(entry.stream_id, entry.key_state_offset) for entry in seek_entries}
    actual_keys = {(chunk.stream_id, offset) for offset, chunk in zip(offsets, chunks) if chunk.chunk_type == ChunkType.VIDEO_KEY_STATE}
    if indexed_keys != actual_keys:
        raise NativeV2Error('CASUNAT2 seek index does not cover every video key state')
    if verify_payloads:
        try:
            semantic = NativeV2PayloadValidator(manifest, effective_limits, semantic=True)
            for chunk in chunks:
                semantic.feed(chunk, allow_system=True)
            semantic.finalize(require_system=True)
        except NativeV2ValidationError as exc:
            raise NativeV2Error(str(exc)) from exc
    return NativeV2Container(source, manifest, tuple(chunks), tuple(offsets), seek_entries, verified, tuple(recovery_points), chunk_hashes, effective_limits)

def _chunk_offsets(raw: bytes, manifest_length: int):
    pos = HEADER.size + manifest_length
    while pos + CHUNK_HEADER.size <= len(raw):
        offset = pos
        _kind, _stream, _flags, _pts, payload_length, _uncompressed = CHUNK_HEADER.unpack_from(raw, pos)
        pos += CHUNK_HEADER.size
        if payload_length > len(raw) - pos:
            break
        yield offset
        pos += payload_length

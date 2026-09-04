"""Native CASU container foundation.

The first native revision is deliberately lossless and conservative: it
embeds the original source payload alongside a validated manifest. It provides
standalone, versioned I/O and integrity boundaries while the segmented tile
payload writer is developed separately. No lossy or time-altering operation is
performed here.
"""
from __future__ import annotations
import hashlib
import json
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from .schema import validate_manifest
MAGIC = b'CASUNAT1'
VERSION = 1
HEADER = struct.Struct('<8sHHQQ32s32s')
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024 * 1024

class NativeCasuError(ValueError):
    pass

def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

@dataclass(frozen=True)
class NativeContainer:
    path: Path
    manifest: dict
    payload_offset: int
    payload_length: int
    payload_sha256: str

    def iter_payload(self, chunk_size: int=1024 * 1024) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise NativeCasuError('chunk size must be positive')
        remaining = self.payload_length
        with self.path.open('rb') as handle:
            handle.seek(self.payload_offset)
            while remaining:
                chunk = handle.read(min(chunk_size, remaining))
                if not chunk:
                    raise NativeCasuError('native CASU payload is truncated')
                remaining -= len(chunk)
                yield chunk

    def extract_payload(self, destination: Path) -> Path:
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', dir=destination.parent)
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as target:
                for chunk in self.iter_payload():
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            if digest.hexdigest() != self.payload_sha256:
                raise NativeCasuError('native CASU payload integrity mismatch')
            os.replace(temporary, destination)
            return destination
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

def read_native(path: Path, *, verify_payload: bool=True) -> NativeContainer:
    path = path.expanduser().resolve()
    try:
        with path.open('rb') as handle:
            raw = handle.read(HEADER.size)
            if len(raw) != HEADER.size:
                raise NativeCasuError('native CASU header is truncated')
            magic, version, _flags, manifest_length, payload_length, manifest_hash, payload_hash = HEADER.unpack(raw)
            if magic != MAGIC or version != VERSION:
                raise NativeCasuError('unsupported native CASU version or magic')
            if manifest_length > MAX_MANIFEST_BYTES or payload_length > MAX_PAYLOAD_BYTES:
                raise NativeCasuError('native CASU section exceeds safety limit')
            manifest_bytes = handle.read(manifest_length)
            if len(manifest_bytes) != manifest_length or hashlib.sha256(manifest_bytes).digest() != manifest_hash:
                raise NativeCasuError('native CASU manifest integrity mismatch')
            manifest = json.loads(manifest_bytes.decode('utf-8'))
            errors = validate_manifest(manifest)
            if errors:
                raise NativeCasuError(f'native CASU manifest is invalid: {errors[0]}')
            expected_size = HEADER.size + manifest_length + payload_length
            if path.stat().st_size != expected_size:
                raise NativeCasuError('native CASU file size does not match header')
            offset = HEADER.size + manifest_length
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, struct.error) as exc:
        raise NativeCasuError(f'could not read native CASU: {path}') from exc
    container = NativeContainer(path, manifest, offset, payload_length, payload_hash.hex())
    if verify_payload:
        digest = hashlib.sha256()
        for chunk in container.iter_payload():
            digest.update(chunk)
        if digest.digest() != payload_hash:
            raise NativeCasuError('native CASU payload integrity mismatch')
    return container

"""Bounded lossless CASUNAT2 attachment payload."""
from __future__ import annotations
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from .jsonutil import StrictJsonError, strict_json_loads
_U32 = struct.Struct('>I')
MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
MAX_ATTACHMENT_METADATA_BYTES = 64 * 1024

class AttachmentPayloadError(ValueError):
    pass

@dataclass(frozen=True)
class Attachment:
    filename: str
    media_type: str
    data: bytes
    sha256: str
    role: str | None = None

def decode_attachment(payload: bytes) -> Attachment:
    if len(payload) < _U32.size:
        raise AttachmentPayloadError('truncated attachment')
    length = _U32.unpack_from(payload)[0]
    if length > len(payload) - _U32.size or length > MAX_ATTACHMENT_METADATA_BYTES:
        raise AttachmentPayloadError('invalid attachment metadata length')
    try:
        meta = strict_json_loads(payload[_U32.size:_U32.size + length])
        expected = int(meta['raw_length'])
        if meta.get('version') != 1 or meta.get('compression', 'zlib') != 'zlib' or expected < 0 or (expected > MAX_ATTACHMENT_BYTES):
            raise ValueError
        compressed = payload[_U32.size + length:]
        if len(compressed) != int(meta['compressed_length']):
            raise ValueError
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, expected + 1)
        if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError
        filename = str(meta['filename'])
        media_type = str(meta['media_type'])
        if filename != filename.replace('\\', '/').rsplit('/', 1)[-1] or filename in {'', '.', '..'} or len(filename.encode('utf-8')) > 4096 or (len(media_type.encode('utf-8')) > 1024):
            raise ValueError
        digest = str(meta['sha256'])
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError
        role_value = meta.get('role')
        if role_value is not None and (not isinstance(role_value, str) or not role_value.strip() or len(role_value) > 64):
            raise ValueError
        return Attachment(filename, media_type, raw, digest, role_value)
    except (KeyError, TypeError, ValueError, StrictJsonError, zlib.error) as exc:
        raise AttachmentPayloadError('invalid attachment payload') from exc

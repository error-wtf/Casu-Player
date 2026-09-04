"""Bounded lossless bitmap-subtitle payloads for CASUNAT2."""
from __future__ import annotations
import hashlib
import hmac
import json
import struct
import zlib
from dataclasses import dataclass
import numpy as np
from .jsonutil import StrictJsonError, strict_json_loads
_U32 = struct.Struct('>I')
MAX_BITMAP_RAW_BYTES = 256 * 1024 * 1024
MAX_BITMAP_DIMENSION = 16384
MAX_BITMAP_COMPRESSED_BYTES = MAX_BITMAP_RAW_BYTES + 1024 * 1024

class BitmapSubtitleError(ValueError):
    pass

@dataclass(frozen=True)
class BitmapSubtitle:
    start_pts: int
    end_pts: int
    canvas_width: int
    canvas_height: int
    x: int
    y: int
    width: int
    height: int
    rgba: bytes
    sha256: str

    def canvas_rgba(self) -> np.ndarray:
        canvas = np.zeros((self.canvas_height, self.canvas_width, 4), dtype=np.uint8)
        region = np.frombuffer(self.rgba, dtype=np.uint8).reshape(self.height, self.width, 4)
        canvas[self.y:self.y + self.height, self.x:self.x + self.width] = region
        return canvas

def decode_bitmap_subtitle(payload: bytes) -> BitmapSubtitle:
    if len(payload) < _U32.size:
        raise BitmapSubtitleError('truncated bitmap subtitle')
    length = _U32.unpack_from(payload)[0]
    if length > len(payload) - _U32.size or length > 64 * 1024:
        raise BitmapSubtitleError('invalid bitmap subtitle metadata length')
    try:
        meta = strict_json_loads(payload[_U32.size:_U32.size + length])
        if not isinstance(meta, dict):
            raise ValueError
        if meta.get('version') != 1 or meta.get('pixel_format') != 'rgba' or meta.get('compression', 'zlib') != 'zlib':
            raise ValueError
        if meta.get('time_base') != [1, 1000]:
            raise ValueError
        values = tuple((int(meta[key]) for key in ('start_pts', 'end_pts', 'canvas_width', 'canvas_height', 'x', 'y', 'width', 'height', 'raw_length', 'compressed_length')))
        start, end, canvas_w, canvas_h, left, top, width, height, raw_length, compressed_length = values
        if end < start or canvas_w <= 0 or canvas_h <= 0 or (canvas_w > MAX_BITMAP_DIMENSION) or (canvas_h > MAX_BITMAP_DIMENSION) or (canvas_w * canvas_h * 4 > MAX_BITMAP_RAW_BYTES) or (width <= 0) or (height <= 0) or (left < 0) or (top < 0) or (left + width > canvas_w) or (top + height > canvas_h) or (raw_length != width * height * 4) or (raw_length > MAX_BITMAP_RAW_BYTES) or (compressed_length < 0) or (compressed_length > MAX_BITMAP_COMPRESSED_BYTES):
            raise ValueError
        compressed = payload[_U32.size + length:]
        if len(compressed) != compressed_length:
            raise ValueError
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, raw_length + 1)
        if len(raw) != raw_length or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise ValueError
        digest = str(meta['sha256'])
        if len(digest) != 64 or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
            raise ValueError
        return BitmapSubtitle(start, end, canvas_w, canvas_h, left, top, width, height, raw, digest)
    except (AttributeError, KeyError, TypeError, ValueError, StrictJsonError, zlib.error) as exc:
        raise BitmapSubtitleError('invalid bitmap subtitle payload') from exc

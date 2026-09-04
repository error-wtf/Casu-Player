"""Lossless canonical video payloads used by CASUNAT2 key/update chunks."""
from __future__ import annotations
import json
import struct
import zlib
import numpy as np
from casu.strict.canonical import CanonicalFrame, PlaneLayout, canonical_frame
from casu.strict.tiles import canonical_tile_hash, frame_identity_prefix, tile_digest_with_prefix
from .jsonutil import StrictJsonError, strict_json_loads
_U32 = struct.Struct('>I')

class VideoPayloadError(ValueError):
    pass
MAX_DECODED_PLANE_BYTES = 512 * 1024 * 1024
MAX_VIDEO_METADATA_BYTES = 1024 * 1024
MAX_VIDEO_PLANES = 8
MAX_VIDEO_DIMENSION = 32768

def _decompress_exact(compressed: bytes, expected: int) -> bytes:
    if expected < 0 or expected > MAX_DECODED_PLANE_BYTES:
        raise VideoPayloadError('decoded video plane exceeds safety limit')
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise VideoPayloadError('invalid compressed video plane') from exc
    if len(raw) != expected or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise VideoPayloadError('decoded plane length mismatch')
    return raw

def _meta(frame: CanonicalFrame) -> dict:
    return {'pixel_format': frame.pixel_format, 'source_shape': list(frame.shape), 'color_metadata': dict(frame.color_metadata), 'planes': [{'shape': list(plane.shape), 'dtype': str(plane.dtype)} for plane in frame.planes]}

def _pack(meta: dict, blobs: list[bytes]) -> bytes:
    header = json.dumps(meta, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return _U32.pack(len(header)) + header + b''.join(blobs)

def _unpack(payload: bytes) -> tuple[dict, list[bytes]]:
    if len(payload) < _U32.size:
        raise VideoPayloadError('truncated video payload')
    length = _U32.unpack_from(payload)[0]
    if length > len(payload) - _U32.size or length > MAX_VIDEO_METADATA_BYTES:
        raise VideoPayloadError('invalid video payload header length')
    try:
        meta = strict_json_loads(payload[_U32.size:_U32.size + length])
        if not isinstance(meta, dict) or not isinstance(meta.get('planes'), list) or (not 0 < len(meta['planes']) <= MAX_VIDEO_PLANES):
            raise ValueError
    except (StrictJsonError, ValueError) as exc:
        raise VideoPayloadError('invalid video payload metadata') from exc
    pos = _U32.size + length
    blobs = []
    try:
        for plane in meta['planes']:
            if not isinstance(plane, dict):
                raise ValueError
            compressed_length = int(plane['compressed_length'])
            if compressed_length < 0 or compressed_length > len(payload) - pos:
                raise ValueError
            blobs.append(payload[pos:pos + compressed_length])
            pos += compressed_length
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise VideoPayloadError('invalid compressed plane length') from exc
    if pos != len(payload):
        raise VideoPayloadError('trailing bytes in video payload')
    return (meta, blobs)

def decode_format_change(payload: bytes) -> dict:
    if len(payload) > MAX_VIDEO_METADATA_BYTES:
        raise VideoPayloadError('video format change exceeds safety limit')
    try:
        value = strict_json_loads(payload)
        if not isinstance(value, dict) or value.get('version') != 1:
            raise ValueError
        shape = value.get('source_shape')
        if not isinstance(shape, list) or len(shape) != 2 or any((isinstance(item, bool) for item in shape)):
            raise ValueError
        height, width = (int(item) for item in shape)
        pixel_format = value.get('pixel_format')
        color = value.get('color_metadata', {})
        if not 0 < width <= MAX_VIDEO_DIMENSION or not 0 < height <= MAX_VIDEO_DIMENSION or (not isinstance(pixel_format, str)) or (not pixel_format) or (len(pixel_format) > 64) or (not isinstance(color, dict)):
            raise ValueError
        return {'version': 1, 'pixel_format': pixel_format, 'source_shape': [height, width], 'color_metadata': color}
    except (TypeError, ValueError, StrictJsonError) as exc:
        raise VideoPayloadError('invalid video format change') from exc

def decode_key_state(payload: bytes) -> CanonicalFrame:
    meta, blobs = _unpack(payload)
    planes = []
    total_decoded = 0
    for descriptor, compressed in zip(meta['planes'], blobs):
        if descriptor.get('compression', 'zlib') != 'zlib':
            raise VideoPayloadError('unsupported video plane compression')
        shape = tuple((int(value) for value in descriptor['shape']))
        if len(shape) != 2 or any((value <= 0 for value in shape)):
            raise VideoPayloadError('invalid decoded plane shape')
        dtype = np.dtype(descriptor['dtype'])
        if dtype.kind != 'u' or dtype.itemsize not in (1, 2):
            raise VideoPayloadError('invalid decoded plane dtype')
        expected = int(np.prod(shape, dtype=np.int64)) * dtype.itemsize
        if max(shape) > MAX_VIDEO_DIMENSION or expected < 0 or total_decoded + expected > MAX_DECODED_PLANE_BYTES:
            raise VideoPayloadError('decoded video frame exceeds safety limit')
        total_decoded += expected
        if expected != int(descriptor['raw_length']):
            raise VideoPayloadError('video plane metadata length mismatch')
        raw = _decompress_exact(compressed, expected)
        array = np.frombuffer(raw, dtype=dtype).reshape(shape).copy()
        planes.append(array)
    return canonical_frame(tuple(planes), pixel_format=str(meta['pixel_format']), source_shape=tuple(meta['source_shape']), color_metadata=meta.get('color_metadata', {}))

def _bounds(layout: PlaneLayout, x: int, y: int, w: int, h: int):
    x0 = (x >> layout.subsample_x) * layout.components
    y0 = y >> layout.subsample_y
    x1 = (x + w + (1 << layout.subsample_x) - 1 >> layout.subsample_x) * layout.components
    y1 = y + h + (1 << layout.subsample_y) - 1 >> layout.subsample_y
    return (x0, y0, x1, y1)

def _slice(plane: np.ndarray, layout: PlaneLayout, x: int, y: int, w: int, h: int):
    x0, y0, x1, y1 = _bounds(layout, x, y, w, h)
    return plane[y0:min(y1, plane.shape[0]), x0:min(x1, plane.shape[1])]

class TileStateCache:
    """Reconstruct a source-resolution frame without legacy payload extraction."""

    def __init__(self) -> None:
        self._frame: CanonicalFrame | None = None
        self._prefix: bytes | None = None

    @property
    def frame(self) -> CanonicalFrame | None:
        return self._frame

    @frame.setter
    def frame(self, value: CanonicalFrame | None) -> None:
        self._frame = value
        self._prefix = None

    def _identity_prefix(self) -> bytes:
        if self._prefix is None:
            if self._frame is None:
                raise VideoPayloadError('tile update requires a key state')
            self._prefix = frame_identity_prefix(self._frame)
        return self._prefix

    def apply_key_state(self, payload: bytes) -> CanonicalFrame:
        self.frame = decode_key_state(payload)
        return self.frame

    def apply_tile_update(self, payload: bytes) -> CanonicalFrame:
        if self.frame is None:
            raise VideoPayloadError('tile update requires a key state')
        meta, blobs = _unpack(payload)
        if len(meta['planes']) != len(self.frame.planes):
            raise VideoPayloadError('tile update plane count differs from key state')
        if tuple(meta['source_shape']) != self.frame.shape or meta['pixel_format'] != self.frame.pixel_format:
            raise VideoPayloadError('tile update format differs from cached key state')
        x, y, width, height = (int(value) for value in meta['region'])
        region = (x, y, width, height)
        prefix = self._identity_prefix()
        expected_base = meta.get('base_state_hash')
        if expected_base is not None and tile_digest_with_prefix(self.frame, region, prefix) != expected_base:
            raise VideoPayloadError('tile update base state hash mismatch')
        planes = list(self.frame.planes)
        total_decoded = 0
        for index, (descriptor, compressed) in enumerate(zip(meta['planes'], blobs)):
            if descriptor.get('compression', 'zlib') != 'zlib':
                raise VideoPayloadError('unsupported tile plane compression')
            shape = tuple((int(value) for value in descriptor['shape']))
            dtype = np.dtype(descriptor['dtype'])
            if len(shape) != 2 or any((value <= 0 for value in shape)):
                raise VideoPayloadError('invalid tile plane shape')
            expected = int(np.prod(shape, dtype=np.int64)) * dtype.itemsize
            if dtype.kind != 'u' or dtype.itemsize not in (1, 2) or expected != int(descriptor['raw_length']) or (max(shape) > MAX_VIDEO_DIMENSION) or (total_decoded + expected > MAX_DECODED_PLANE_BYTES):
                raise VideoPayloadError('invalid tile plane layout')
            total_decoded += expected
            raw = _decompress_exact(compressed, expected)
            tile = np.frombuffer(raw, dtype=dtype).reshape(shape)
            target = planes[index]
            layout = self.frame.plane_layouts[index]
            if target.dtype != dtype:
                raise VideoPayloadError('tile plane dtype differs from key state')
            if layout.bit_depth < layout.bytes_per_sample * 8 and tile.size and (int(tile.max()) >= 1 << layout.bit_depth):
                raise VideoPayloadError('tile plane samples outside bit depth range')
            x0, y0, x1, y1 = _bounds(layout, x, y, width, height)
            target.setflags(write=True)
            try:
                view = target[y0:min(y1, target.shape[0]), x0:min(x1, target.shape[1])]
                if tile.shape != view.shape:
                    raise VideoPayloadError('tile plane shape mismatch')
                view[:] = tile
            finally:
                target.setflags(write=False)
        expected_new = meta.get('new_state_hash')
        if not isinstance(expected_new, str) or tile_digest_with_prefix(self.frame, region, prefix) != expected_new:
            raise VideoPayloadError('tile update new state hash mismatch')
        return self.frame

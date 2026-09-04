from __future__ import annotations

import hashlib

from .canonical import CanonicalFrame, PlaneLayout, _hash_identity
from .model import RationalTime, StrictTileState


def _regions(shape: tuple[int, int], tile_width: int, tile_height: int):
    height, width = shape
    ordinal = 0
    for y in range(0, height, tile_height):
        for x in range(0, width, tile_width):
            yield (f"tile-{ordinal:08d}", x, y,
                   min(tile_width, width - x), min(tile_height, height - y))
            ordinal += 1


def compare_frames(previous: CanonicalFrame | None, current: CanonicalFrame, *,
                   valid_from: RationalTime, valid_until: RationalTime | None = None,
                   tile_width: int = 64, tile_height: int = 64,
                   previous_hashes: "dict[tuple[int, int, int, int], str] | None" = None) -> list[StrictTileState]:
    if tile_width <= 0 or tile_height <= 0:
        raise ValueError("tile dimensions must be positive")
    format_change = previous is None or previous.format_identity != current.format_identity
    current_prefix = frame_identity_prefix(current)
    previous_prefix = None
    if previous is not None and not format_change and previous_hashes is None:
        previous_prefix = frame_identity_prefix(previous)
    result: list[StrictTileState] = []
    for tile_id, x, y, width, height in _regions(current.shape, tile_width, tile_height):
        region = (x, y, width, height)
        current_hash = tile_digest_with_prefix(current, region, current_prefix)
        reference_hash = None
        if previous is not None and not format_change:
            if previous_hashes is not None:
                reference_hash = previous_hashes.get(region)
                if reference_hash is None:
                    reference_hash = canonical_tile_hash(previous, region)
            else:
                reference_hash = tile_digest_with_prefix(previous, region, previous_prefix)
        state = "KEY_STATE" if format_change else "HOLD" if current_hash == reference_hash else "UPDATE"
        result.append(StrictTileState(
            tile_id, {"x": x, "y": y, "w": width, "h": height}, state,
            valid_from, valid_until, current_hash, reference_hash,
            len(current.planes), format_change,
        ))
    return result


def frame_identity_prefix(frame: CanonicalFrame) -> bytes:
    """Frame-level identity bytes shared by every tile digest of the frame."""
    return b"".join((
        b"CASU-STRICT-TILE-v1\0",
        frame.pixel_format.encode("ascii"),
        repr(frame.shape).encode("ascii"),
        repr(frame.color_metadata).encode("utf-8"),
        repr(tuple(layout.identity() for layout in frame.plane_layouts)).encode("ascii"),
    ))


def _update_tile_bytes(digest: "hashlib._Hash", frame: CanonicalFrame,
                       region: tuple[int, int, int, int]) -> None:
    for plane, layout in zip(frame.planes, frame.plane_layouts):
        x, y, width, height = region
        x0 = x >> layout.subsample_x
        y0 = y >> layout.subsample_y
        x1 = (x + width + (1 << layout.subsample_x) - 1) >> layout.subsample_x
        y1 = (y + height + (1 << layout.subsample_y) - 1) >> layout.subsample_y
        x0 *= layout.components
        x1 *= layout.components
        tile = plane[y0:min(y1, plane.shape[0]), x0:min(x1, plane.shape[1])]
        if tile.flags.c_contiguous:
            digest.update(tile)
        else:
            for row in tile:
                digest.update(row)


def tile_digest_with_prefix(frame: CanonicalFrame, region: tuple[int, int, int, int],
                            prefix: bytes | None = None) -> str:
    digest = hashlib.sha256()
    if prefix is None:
        _hash_identity(digest, frame, region)
    else:
        digest.update(prefix)
        digest.update(repr(region).encode("ascii"))
    _update_tile_bytes(digest, frame, region)
    return digest.hexdigest()


def canonical_tile_hash(frame: CanonicalFrame, region: tuple[int, int, int, int]) -> str:
    digest = hashlib.sha256()
    _hash_identity(digest, frame, region)
    _update_tile_bytes(digest, frame, region)
    return digest.hexdigest()


_tile_digest = canonical_tile_hash


def _plane_tile(plane, layout: PlaneLayout, x: int, y: int, width: int, height: int):
    """Map a luma/display tile exactly onto a native active plane."""
    x0 = x >> layout.subsample_x
    y0 = y >> layout.subsample_y
    x1 = (x + width + (1 << layout.subsample_x) - 1) >> layout.subsample_x
    y1 = (y + height + (1 << layout.subsample_y) - 1) >> layout.subsample_y
    x0 *= layout.components
    x1 *= layout.components
    return plane[y0:min(y1, plane.shape[0]), x0:min(x1, plane.shape[1])]

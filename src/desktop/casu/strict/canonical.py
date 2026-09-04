from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


class StrictCanonicalError(ValueError):
    pass


@dataclass(frozen=True)
class PlaneLayout:
    index: int
    width: int
    height: int
    bit_depth: int
    bytes_per_sample: int
    subsample_x: int
    subsample_y: int
    components: int = 1

    def identity(self) -> tuple[int, ...]:
        return (self.index, self.width, self.height, self.bit_depth,
                self.bytes_per_sample, self.subsample_x, self.subsample_y,
                self.components)


@dataclass(frozen=True)
class CanonicalFrame:
    """Padding-free native samples plus all metadata relevant to identity."""

    planes: tuple[np.ndarray, ...]
    pixel_format: str
    color_metadata: tuple[tuple[str, str], ...] = ()
    source_width: int | None = None
    source_height: int | None = None
    plane_layouts: tuple[PlaneLayout, ...] = ()

    @property
    def shape(self) -> tuple[int, int]:
        if self.source_width is not None and self.source_height is not None:
            return (self.source_height, self.source_width)
        return tuple(int(value) for value in self.planes[0].shape)  # type: ignore[return-value]

    @property
    def format_identity(self) -> tuple[object, ...]:
        return (self.shape, self.pixel_format, self.color_metadata,
                tuple(layout.identity() for layout in self.plane_layouts))

    def digest(self) -> str:
        digest = hashlib.sha256()
        _hash_identity(digest, self, None)
        for plane in self.planes:
            digest.update(plane.tobytes(order="C"))
        return digest.hexdigest()


def _format_spec(pixel_format: str) -> tuple[int, int, tuple[tuple[int, int, int], ...]]:
    """Return bit depth, bytes/sample and (x-sub, y-sub, components) per plane."""
    fmt = pixel_format.lower()
    packed = {
        "rgb24": (8, 1, ((0, 0, 3),)),
        "bgr24": (8, 1, ((0, 0, 3),)),
        "rgba": (8, 1, ((0, 0, 4),)),
        "bgra": (8, 1, ((0, 0, 4),)),
        "argb": (8, 1, ((0, 0, 4),)),
        "abgr": (8, 1, ((0, 0, 4),)),
        "rgba64le": (16, 2, ((0, 0, 4),)),
    }
    if fmt in packed:
        return packed[fmt]
    if fmt in {"gray", "gray8"}:
        return (8, 1, ((0, 0, 1),))
    if fmt in {"gray16le"}:
        return (16, 2, ((0, 0, 1),))

    depth = 8
    for candidate in (16, 14, 12, 10, 9):
        if str(candidate) in fmt:
            depth = candidate
            break
    bytes_per_sample = 1 if depth <= 8 else 2
    alpha = fmt.startswith("yuva")
    if fmt.startswith("yuv420") or fmt.startswith("yuva420"):
        layouts = [(0, 0, 1), (1, 1, 1), (1, 1, 1)]
    elif fmt.startswith("yuv422") or fmt.startswith("yuva422"):
        layouts = [(0, 0, 1), (1, 0, 1), (1, 0, 1)]
    elif fmt.startswith("yuv444") or fmt.startswith("yuva444"):
        layouts = [(0, 0, 1), (0, 0, 1), (0, 0, 1)]
    elif fmt.startswith("gbrp"):
        layouts = [(0, 0, 1), (0, 0, 1), (0, 0, 1)]
    else:
        raise StrictCanonicalError(f"unsupported canonical pixel format: {pixel_format}")
    if alpha:
        layouts.append((0, 0, 1))
    return depth, bytes_per_sample, tuple(layouts)


def _ceil_shift(value: int, shift: int) -> int:
    return (value + (1 << shift) - 1) >> shift


def canonical_frame(planes: np.ndarray | Sequence[np.ndarray], *, pixel_format: str = "gray8",
                    color_metadata: Mapping[str, Any] | None = None,
                    source_shape: tuple[int, int] | None = None) -> CanonicalFrame:
    values = (planes,) if isinstance(planes, np.ndarray) else tuple(planes)
    if not values:
        raise StrictCanonicalError("at least one decoded plane is required")
    bit_depth, bytes_per_sample, specs = _format_spec(str(pixel_format))
    if len(values) != len(specs):
        raise StrictCanonicalError(
            f"{pixel_format} requires {len(specs)} planes, got {len(values)}")

    if source_shape is None:
        first = np.asarray(values[0])
        components = specs[0][2]
        if first.ndim != 2 or first.shape[1] % components:
            raise StrictCanonicalError("cannot derive source geometry from first plane")
        source_height, source_width = int(first.shape[0]), int(first.shape[1] // components)
    else:
        if len(source_shape) != 2 or any(int(value) <= 0 for value in source_shape):
            raise StrictCanonicalError("source_shape must be (height, width)")
        source_height, source_width = (int(source_shape[0]), int(source_shape[1]))

    canonical: list[np.ndarray] = []
    layouts: list[PlaneLayout] = []
    for index, (value, (sub_x, sub_y, components)) in enumerate(zip(values, specs)):
        array = np.asarray(value)
        expected_h = _ceil_shift(source_height, sub_y)
        expected_w = _ceil_shift(source_width, sub_x)
        expected_shape = (expected_h, expected_w * components)
        if array.ndim != 2 or tuple(array.shape) != expected_shape:
            raise StrictCanonicalError(
                f"plane {index} shape {tuple(array.shape)} does not match active {expected_shape}")
        if array.dtype.kind != "u" or array.dtype.itemsize != bytes_per_sample:
            raise StrictCanonicalError(
                f"plane {index} must use unsigned {bytes_per_sample * 8}-bit storage")
        if bit_depth < bytes_per_sample * 8 and array.size and int(array.max()) >= (1 << bit_depth):
            raise StrictCanonicalError(f"plane {index} contains samples outside {bit_depth}-bit range")
        active = np.ascontiguousarray(array)
        active.setflags(write=False)
        canonical.append(active)
        layouts.append(PlaneLayout(index, expected_w, expected_h, bit_depth,
                                   bytes_per_sample, sub_x, sub_y, components))
    metadata = tuple(sorted((str(key), str(value)) for key, value in
                            (color_metadata or {}).items() if value is not None))
    return CanonicalFrame(tuple(canonical), str(pixel_format).lower(), metadata,
                          source_width, source_height, tuple(layouts))


def _hash_identity(digest: "hashlib._Hash", frame: CanonicalFrame,
                   region: tuple[int, int, int, int] | None) -> None:
    digest.update(b"CASU-STRICT-TILE-v1\0")
    digest.update(frame.pixel_format.encode("ascii"))
    digest.update(repr(frame.shape).encode("ascii"))
    digest.update(repr(frame.color_metadata).encode("utf-8"))
    digest.update(repr(tuple(layout.identity() for layout in frame.plane_layouts)).encode("ascii"))
    digest.update(repr(region).encode("ascii"))

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .canonical import CanonicalFrame


@dataclass(frozen=True, order=True)
class RationalTime:
    pts: int
    time_base_num: int
    time_base_den: int

    def __post_init__(self) -> None:
        if self.time_base_num <= 0 or self.time_base_den <= 0:
            raise ValueError("time base numerator and denominator must be positive")

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.pts * self.time_base_num, self.time_base_den)

    def seconds(self) -> float:
        return float(self.fraction)

    def as_dict(self) -> dict[str, int]:
        return {"pts": self.pts, "time_base_num": self.time_base_num,
                "time_base_den": self.time_base_den}


@dataclass(frozen=True)
class StrictFrame:
    pts: int
    time_base_num: int
    time_base_den: int
    frame: CanonicalFrame
    duration_pts: int | None = None

    @property
    def time(self) -> RationalTime:
        return RationalTime(self.pts, self.time_base_num, self.time_base_den)

    @property
    def timestamp_s(self) -> float:
        return self.time.seconds()


@dataclass(frozen=True)
class StrictTileState:
    tile_id: str
    region: dict[str, int]
    state: str
    valid_from: RationalTime
    valid_until: RationalTime | None
    state_hash: str
    reference_hash: str | None
    plane_count: int
    format_change: bool = False

    def as_dict(self) -> dict[str, Any]:
        start_s = self.valid_from.seconds()
        end_s = self.valid_until.seconds() if self.valid_until is not None else None
        return {
            "tile_id": self.tile_id,
            "region": self.region,
            "state": self.state,
            "lifecycle": self.state,
            "valid_from": self.valid_from.as_dict(),
            "valid_until": self.valid_until.as_dict() if self.valid_until else None,
            "valid_from_s": start_s,
            "valid_until_s": end_s,
            "state_hash": self.state_hash,
            "reference_hash": self.reference_hash,
            "plane_count": self.plane_count,
            "format_change": self.format_change,
            "fidelity": "SOURCE_RESOLUTION_STRICT",
        }

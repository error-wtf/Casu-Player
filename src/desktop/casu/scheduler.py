# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Deterministic CASU state scheduler for validated sidecar manifests.

This layer deliberately schedules *metadata states* only. It never invents a
frame, changes source timestamps, or replaces the media decoder.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class SegmentState:
    start_s: float
    end_s: float
    state: str
    source: str
    segment_id: str | None = None
    region: dict[str, Any] | None = None
    lifecycle: str = "UPDATE"
    priority: int = 0
    deadline_s: float | None = None
    reference_state: str | None = None


class CasuScheduler:
    def __init__(self, segments: Iterable[SegmentState]):
        self._segments = tuple(sorted(segments, key=lambda item: (item.start_s, item.end_s)))
        # Manifests are validated as non-overlapping intervals.  Keeping a
        # parallel start index makes timeline lookup logarithmic instead of
        # scanning every segment on every UI tick.
        self._starts = tuple(item.start_s for item in self._segments)

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any], source: str = "video") -> "CasuScheduler":
        section = manifest.get(source) or {}
        raw = section.get("segments", []) if isinstance(section, dict) else []
        parsed: list[SegmentState] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                region = item.get("region")
                parsed.append(SegmentState(
                    float(item["start_s"]),
                    float(item["end_s"]),
                    str(item["state"]),
                    source,
                    str(item["segment_id"]) if item.get("segment_id") is not None else None,
                    dict(region) if isinstance(region, dict) else None,
                    str(item.get("lifecycle", "UPDATE")),
                    int(item.get("priority", 0)),
                    float(item["deadline_s"]) if item.get("deadline_s") is not None else None,
                    str(item["reference_state"]) if item.get("reference_state") is not None else None,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(parsed)

    def state_at(self, timestamp_s: float) -> SegmentState | None:
        """Return the state active at a source timestamp, if covered."""
        value = float(timestamp_s)
        index = bisect_right(self._starts, value) - 1
        if index < 0:
            return None
        segment = self._segments[index]
        return segment if value < segment.end_s else None

    def summary(self, timestamp_s: float) -> dict[str, Any]:
        active = self.state_at(timestamp_s)
        return {
            "source": self._segments[0].source if self._segments else "unknown",
            "segment_count": len(self._segments),
            "active_state": active.state if active else None,
            "active_interval": [active.start_s, active.end_s] if active else None,
            "active_segment_id": active.segment_id if active else None,
            "active_lifecycle": active.lifecycle if active else None,
            "active_priority": active.priority if active else None,
            "active_deadline_s": active.deadline_s if active else None,
            "covered": active is not None,
        }

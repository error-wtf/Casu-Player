# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
from __future__ import annotations

import math
import re
from pathlib import PurePath
from typing import Any


# Defensive parser bounds. These limits prevent a malformed manifest from
# causing unbounded validation work or memory use before it reaches playback.
MAX_SEGMENTS_PER_STREAM = 1_000_000
MAX_STREAMS = 256
MAX_METADATA_KEYS = 256
MAX_TEXT_LENGTH = 4096
MAX_SEGMENT_PRIORITY = 1_000_000
SEGMENT_LIFECYCLES = frozenset({"CREATE", "UPDATE", "HOLD", "MOVE", "REPLACE", "INVALIDATE", "RELEASE"})
SUPPORTED_CASU_VERSIONS = frozenset({"1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.3", "1.0.4", "1.0.5", "1.0.6", "2.0.0", "3.0.0", "1.0.0rc1", "1.0.0rc2", "1.0.0rc3", "1.0.0rc4", "1.0.0rc5", "1.0.0rc6", "1.0.0rc7", "1.0.0rc8", "1.0.0rc9"})
MAX_SEEK_ENTRIES = 2_000_000


class CasuManifestError(ValueError):
    pass


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return all structural problems without changing the source media."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    identity = manifest.get("casu") or {}
    format_info = manifest.get("format") or {}
    if not isinstance(identity, dict):
        errors.append("casu must be an object")
        identity = {}
    if not isinstance(format_info, dict):
        errors.append("format must be an object")
        format_info = {}
    if format_info and format_info.get("magic") not in (None, "MPCASU\\0"):
        errors.append("format.magic must be MPCASU\\0 when present")
    if identity.get("name") != "CASU":
        errors.append("casu.name must be CASU")
    if identity.get("container_extension") != ".casu":
        errors.append("casu.container_extension must be .casu")
    if identity.get("version") not in SUPPORTED_CASU_VERSIONS:
        errors.append("casu.version must be a supported CASU version")
    if format_info.get("schema") not in (None, "0.2"):
        errors.append("format.schema is not supported")
    if identity.get("analysis_mode") is not None and identity.get("analysis_mode") not in {"strict", "visually_lossless", "adaptive"}:
        errors.append("casu.analysis_mode is not a supported CASU mode")
    source = manifest.get("source") or {}
    if not isinstance(source, dict):
        errors.append("source must be an object")
        source = {}
    if not isinstance(source.get("filename"), str) or not source.get("filename"):
        errors.append("source.filename must be a non-empty string")
    elif (len(source["filename"]) > MAX_TEXT_LENGTH
          or "\\" in source["filename"]
          or PurePath(source["filename"]).name != source["filename"]
          or source["filename"] in {".", ".."}):
        errors.append("source.filename must be a bounded basename without path traversal")
    source_path = source.get("path")
    if source_path is not None:
        if not isinstance(source_path, str) or not source_path or len(source_path) > MAX_TEXT_LENGTH:
            errors.append("source.path must be a bounded string when present")
        elif isinstance(source.get("filename"), str) and PurePath(source_path.replace("\\", "/")).name != source["filename"]:
            errors.append("source.path basename must match source.filename")
    if "duration_s" not in source:
        errors.append("source.duration_s is required")
    try:
        duration = float(source.get("duration_s") or 0)
    except (TypeError, ValueError):
        errors.append("source.duration_s must be numeric")
        duration = 0.0
    if not math.isfinite(duration) or duration < 0:
        errors.append("source.duration_s must be finite and non-negative")
    if source.get("size_bytes") is not None:
        try:
            size_bytes = float(source.get("size_bytes") or 0)
            if not math.isfinite(size_bytes) or size_bytes < 0:
                errors.append("source.size_bytes must be finite and non-negative")
        except (TypeError, ValueError):
            errors.append("source.size_bytes must be numeric")
    if source.get("sha256") is not None and (
        not isinstance(source.get("sha256"), str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", source["sha256"]) is None
    ):
        errors.append("source.sha256 must be a 64-character hex digest when present")
    streams = manifest.get("streams", [])
    if not isinstance(streams, list):
        errors.append("streams must be an array")
        streams = []
    elif len(streams) > MAX_STREAMS:
        errors.append(f"streams exceeds safety limit of {MAX_STREAMS}")
    for index, stream in enumerate(streams[:MAX_STREAMS]):
        if not isinstance(stream, dict):
            errors.append(f"streams[{index}] must be an object")
            continue
        codec_type = stream.get("codec_type")
        if codec_type not in {"video", "audio", "subtitle", "attachment", "data"}:
            errors.append(f"streams[{index}].codec_type is unsupported")
        codec_name = stream.get("codec_name")
        if codec_name is not None and (not isinstance(codec_name, str) or len(codec_name) > MAX_TEXT_LENGTH):
            errors.append(f"streams[{index}].codec_name is invalid")
    metadata = manifest.get("metadata", {})
    if metadata is not None:
        if not isinstance(metadata, dict):
            errors.append("metadata must be an object")
        elif len(metadata) > MAX_METADATA_KEYS:
            errors.append(f"metadata exceeds safety limit of {MAX_METADATA_KEYS} keys")
        else:
            for key, value in metadata.items():
                if not isinstance(key, str) or len(key) > MAX_TEXT_LENGTH:
                    errors.append("metadata keys must be bounded strings")
                if not isinstance(value, (str, int, float, bool)) and value is not None:
                    errors.append(f"metadata[{key!r}] must be a scalar value")
    for media_key in ("video", "audio"):
        section = manifest.get(media_key) or {}
        if not isinstance(section, dict):
            errors.append(f"{media_key} must be an object")
            continue
        segments = section.get("segments", [])
        if not isinstance(segments, list):
            errors.append(f"{media_key}.segments must be an array")
            continue
        if len(segments) > MAX_SEGMENTS_PER_STREAM:
            errors.append(f"{media_key}.segments exceeds safety limit of {MAX_SEGMENTS_PER_STREAM}")
            continue
        previous_end = 0.0
        segment_ids: set[str] = set()
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                errors.append(f"{media_key}.segments[{index}] must be an object")
                continue
            try:
                start, end = float(segment["start_s"]), float(segment["end_s"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{media_key}.segments[{index}] lacks numeric start/end")
                continue
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > duration + 0.5:
                errors.append(f"{media_key}.segments[{index}] is outside source duration")
            if "duration_s" in segment:
                try:
                    segment_duration = float(segment["duration_s"])
                    if not math.isfinite(segment_duration) or segment_duration < 0:
                        errors.append(f"{media_key}.segments[{index}].duration_s must be finite and non-negative")
                    elif abs(segment_duration - (end - start)) > 1e-5:
                        errors.append(f"{media_key}.segments[{index}].duration_s must equal end_s-start_s")
                except (TypeError, ValueError):
                    errors.append(f"{media_key}.segments[{index}].duration_s must be numeric")
            if start < previous_end - 1e-6:
                errors.append(f"{media_key}.segments[{index}] overlaps the preceding segment")
            previous_end = max(previous_end, end)
            if not isinstance(segment.get("state"), str) or not segment.get("state", "").strip():
                errors.append(f"{media_key}.segments[{index}].state must be a non-empty string")
            elif len(segment["state"]) > MAX_TEXT_LENGTH:
                errors.append(f"{media_key}.segments[{index}].state is too long")
            segment_id = segment.get("segment_id")
            if segment_id is not None:
                if not isinstance(segment_id, str) or not segment_id.strip() or len(segment_id) > MAX_TEXT_LENGTH:
                    errors.append(f"{media_key}.segments[{index}].segment_id must be a bounded non-empty string")
                elif segment_id in segment_ids:
                    errors.append(f"{media_key}.segments[{index}].segment_id must be unique")
                else:
                    segment_ids.add(segment_id)
            lifecycle = segment.get("lifecycle", "UPDATE")
            if lifecycle not in SEGMENT_LIFECYCLES:
                errors.append(f"{media_key}.segments[{index}].lifecycle is unsupported")
            priority = segment.get("priority", 0)
            if isinstance(priority, bool) or not isinstance(priority, int) or abs(priority) > MAX_SEGMENT_PRIORITY:
                errors.append(f"{media_key}.segments[{index}].priority must be a bounded integer")
            reference_state = segment.get("reference_state")
            if reference_state is not None and (not isinstance(reference_state, str) or len(reference_state) > MAX_TEXT_LENGTH):
                errors.append(f"{media_key}.segments[{index}].reference_state is invalid")
            region = segment.get("region")
            if region is not None:
                if not isinstance(region, dict):
                    errors.append(f"{media_key}.segments[{index}].region must be an object")
                else:
                    for region_key in ("x", "y", "w", "h"):
                        value = region.get(region_key)
                        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                            errors.append(f"{media_key}.segments[{index}].region.{region_key} must be a non-negative integer")
            for timing_key in ("valid_until_s", "deadline_s"):
                if timing_key in segment:
                    try:
                        timing = float(segment[timing_key])
                        if not math.isfinite(timing) or timing < start:
                            errors.append(f"{media_key}.segments[{index}].{timing_key} must be finite and >= start_s")
                        elif abs(timing - end) > 1e-5:
                            errors.append(f"{media_key}.segments[{index}].{timing_key} must equal end_s")
                    except (TypeError, ValueError):
                        errors.append(f"{media_key}.segments[{index}].{timing_key} must be numeric")
    seek_index = manifest.get("seek_index")
    if seek_index is not None:
        if not isinstance(seek_index, dict):
            errors.append("seek_index must be an object")
        else:
            if "native_key_states" in seek_index and not isinstance(seek_index["native_key_states"], bool):
                errors.append("seek_index.native_key_states must be boolean")
            entries = seek_index.get("entries", [])
            if not isinstance(entries, list):
                errors.append("seek_index.entries must be an array")
            elif len(entries) > MAX_SEEK_ENTRIES:
                errors.append(f"seek_index.entries exceeds safety limit of {MAX_SEEK_ENTRIES}")
            else:
                previous_timestamp = -1.0
                for index, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        errors.append(f"seek_index.entries[{index}] must be an object")
                        continue
                    try:
                        timestamp = float(entry["timestamp_s"])
                        if not math.isfinite(timestamp) or timestamp < 0 or timestamp > duration + 0.5:
                            errors.append(f"seek_index.entries[{index}].timestamp_s is outside source duration")
                        elif timestamp < previous_timestamp - 1e-6:
                            errors.append("seek_index.entries must be sorted by timestamp_s")
                        previous_timestamp = max(previous_timestamp, timestamp)
                    except (KeyError, TypeError, ValueError):
                        errors.append(f"seek_index.entries[{index}].timestamp_s must be numeric")
                    if entry.get("stream") not in {"video", "audio"}:
                        errors.append(f"seek_index.entries[{index}].stream is unsupported")
                    segment_id = entry.get("segment_id")
                    if segment_id is not None and (not isinstance(segment_id, str) or not segment_id.strip() or len(segment_id) > MAX_TEXT_LENGTH):
                        errors.append(f"seek_index.entries[{index}].segment_id is invalid")
    integrity = manifest.get("integrity") or {}
    if not isinstance(integrity, dict):
        errors.append("integrity must be an object")
        integrity = {}
    if integrity.get("timestamps_are_source_of_truth") is not True:
        errors.append("integrity.timestamps_are_source_of_truth must be true")
    return errors

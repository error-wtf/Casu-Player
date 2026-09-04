"""Structural and semantic validation for the CASUNAT2 format contract."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping

from .attachment import decode_attachment
from .audio import decode_audio_block
from .bitmap import decode_bitmap_subtitle
from .format import CasuLimits, ChunkType, NativeChunk
from .jsonutil import StrictJsonError, strict_json_loads
from .text import decode_chapter_table, decode_subtitle_packet
from .video import TileStateCache, decode_format_change, decode_key_state


class NativeV2ValidationError(ValueError):
    pass


_STREAM_CHUNKS = {
    ChunkType.STREAM_CONFIG,
    ChunkType.VIDEO_KEY_STATE,
    ChunkType.VIDEO_TILE_UPDATE,
    ChunkType.VIDEO_FORMAT_CHANGE,
    ChunkType.AUDIO_BLOCK,
    ChunkType.SUBTITLE_PACKET,
    ChunkType.SUBTITLE_BITMAP,
    ChunkType.ATTACHMENT,
}
_GLOBAL_CHUNKS = {
    ChunkType.CHAPTER_TABLE,
    ChunkType.RECOVERY_POINT,
    ChunkType.SEEK_INDEX,
    ChunkType.INTEGRITY_TABLE,
    ChunkType.END,
}
_EXPECTED_STREAM_TYPES = {
    ChunkType.VIDEO_KEY_STATE: {"video"},
    ChunkType.VIDEO_TILE_UPDATE: {"video"},
    ChunkType.VIDEO_FORMAT_CHANGE: {"video"},
    ChunkType.AUDIO_BLOCK: {"audio"},
    ChunkType.SUBTITLE_PACKET: {"subtitle"},
    ChunkType.SUBTITLE_BITMAP: {"subtitle"},
    ChunkType.ATTACHMENT: {"attachment", "subtitle"},
}


def _bounded_json_tree(value: object, limits: CasuLimits) -> None:
    stack = [(value, 0)]
    nodes = 0
    string_bytes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_json_nodes or depth > limits.max_json_depth:
            raise NativeV2ValidationError("CASUNAT2 JSON structure exceeds limits")
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise NativeV2ValidationError("CASUNAT2 JSON keys must be strings")
                try:
                    string_bytes += len(key.encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise NativeV2ValidationError(
                        "CASUNAT2 JSON contains invalid Unicode") from exc
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            try:
                string_bytes += len(current.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise NativeV2ValidationError(
                    "CASUNAT2 JSON contains invalid Unicode") from exc
        elif isinstance(current, int) and not isinstance(current, bool):
            if current < -2**63 or current > 2**63 - 1:
                raise NativeV2ValidationError("CASUNAT2 JSON integer exceeds int64")
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise NativeV2ValidationError("CASUNAT2 JSON number is not finite")
        elif current is not None and not isinstance(current, bool):
            raise NativeV2ValidationError("CASUNAT2 JSON contains an invalid value")
        if string_bytes > limits.max_manifest_bytes:
            raise NativeV2ValidationError("CASUNAT2 JSON strings exceed limits")


def _time_base(value: object) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or isinstance(value[0], bool) or isinstance(value[1], bool)):
        raise NativeV2ValidationError("CASUNAT2 stream has an invalid time base")
    try:
        numerator, denominator = int(value[0]), int(value[1])
    except (TypeError, ValueError) as exc:
        raise NativeV2ValidationError("CASUNAT2 stream has an invalid time base") from exc
    if numerator <= 0 or denominator <= 0 or max(numerator, denominator) > 2**63 - 1:
        raise NativeV2ValidationError("CASUNAT2 stream has an invalid time base")
    return numerator, denominator


def validate_manifest(manifest: object, limits: CasuLimits) -> dict[int, dict]:
    limits.validate()
    if not isinstance(manifest, dict):
        raise NativeV2ValidationError("CASUNAT2 manifest must be an object")
    _bounded_json_tree(manifest, limits)
    if manifest.get("format") != "CASUNAT2" or manifest.get("version") != 2:
        raise NativeV2ValidationError("CASUNAT2 manifest format/version is invalid")
    streams = manifest.get("streams")
    if not isinstance(streams, list) or len(streams) > limits.max_streams:
        raise NativeV2ValidationError("CASUNAT2 manifest stream table is invalid")
    descriptors: dict[int, dict] = {}
    for descriptor in streams:
        if not isinstance(descriptor, dict):
            raise NativeV2ValidationError("CASUNAT2 stream descriptor is invalid")
        stream_id = descriptor.get("stream_id")
        if (isinstance(stream_id, bool) or not isinstance(stream_id, int)
                or stream_id <= 0 or stream_id > limits.max_streams
                or stream_id in descriptors):
            raise NativeV2ValidationError("CASUNAT2 stream id is invalid or duplicated")
        kind = descriptor.get("type")
        if kind not in {"video", "audio", "subtitle", "attachment"}:
            raise NativeV2ValidationError("CASUNAT2 stream type is invalid")
        _time_base(descriptor.get("time_base"))
        if kind == "audio":
            try:
                rate, channels = int(descriptor["sample_rate"]), int(descriptor["channels"])
            except (KeyError, TypeError, ValueError) as exc:
                raise NativeV2ValidationError("CASUNAT2 audio descriptor is incomplete") from exc
            if not (0 < rate <= limits.max_sample_rate and 0 < channels <= limits.max_channels):
                raise NativeV2ValidationError("CASUNAT2 audio descriptor exceeds limits")
        if kind == "video":
            width, height = descriptor.get("width"), descriptor.get("height")
            if width is not None or height is not None:
                try:
                    width, height = int(width), int(height)
                except (TypeError, ValueError) as exc:
                    raise NativeV2ValidationError("CASUNAT2 video geometry is invalid") from exc
                if not (0 < width <= limits.max_width and 0 < height <= limits.max_height):
                    raise NativeV2ValidationError("CASUNAT2 video geometry exceeds limits")
        timeline = descriptor.get("frame_timeline", [])
        if not isinstance(timeline, list) or len(timeline) > limits.max_chunks:
            raise NativeV2ValidationError("CASUNAT2 frame timeline is invalid")
        previous_pts: int | None = None
        for frame in timeline:
            if not isinstance(frame, Mapping) or isinstance(frame.get("pts"), bool):
                raise NativeV2ValidationError("CASUNAT2 frame timeline entry is invalid")
            try:
                pts = int(frame["pts"])
                duration = frame.get("duration_pts")
                if duration is not None and int(duration) < 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise NativeV2ValidationError("CASUNAT2 frame timeline entry is invalid") from exc
            if previous_pts is not None and pts < previous_pts:
                raise NativeV2ValidationError("CASUNAT2 frame timeline is not ordered")
            previous_pts = pts
        descriptors[stream_id] = descriptor
    provenance = manifest.get("source_provenance")
    if isinstance(provenance, dict):
        if any(key in provenance for key in ("path", "source_path", "absolute_path")):
            raise NativeV2ValidationError("CASUNAT2 source provenance must not contain a path")
        filename = provenance.get("filename")
        if (filename is not None and (not isinstance(filename, str) or not filename
                or filename != filename.replace("\\", "/").rsplit("/", 1)[-1])):
            raise NativeV2ValidationError("CASUNAT2 source filename is unsafe")
    return descriptors


class NativeV2PayloadValidator:
    """Validate chunk topology and decoded payload semantics incrementally."""

    def __init__(self, manifest: dict, limits: CasuLimits, *, semantic: bool) -> None:
        self.limits = limits
        self.descriptors = validate_manifest(manifest, limits)
        self.semantic = semantic
        self.video = {stream_id: TileStateCache() for stream_id, descriptor
                      in self.descriptors.items() if descriptor["type"] == "video"}
        self.video_format_override: dict[int, dict] = {}
        self.video_needs_key: set[int] = set()
        self.video_dependency_depth: dict[int, int] = {
            stream_id: 0 for stream_id in self.video}
        self.last_pts: dict[int, int] = {}
        self.stream_configs: set[int] = set()
        self.chapter_seen = False
        self.system_seen: set[ChunkType] = set()

    def feed(self, chunk: NativeChunk, *, allow_system: bool = True) -> None:
        if chunk.flags != 0:
            raise NativeV2ValidationError("CASUNAT2 chunk uses unknown flags")
        if not (-2**63 <= int(chunk.pts) <= 2**63 - 1):
            raise NativeV2ValidationError("CASUNAT2 chunk PTS is outside int64")
        if chunk.uncompressed_length is not None:
            value = int(chunk.uncompressed_length)
            if value < len(chunk.payload) or value > self.limits.max_chunk_bytes:
                raise NativeV2ValidationError("CASUNAT2 uncompressed chunk length is invalid")
        kind = chunk.chunk_type
        if kind in _GLOBAL_CHUNKS:
            if chunk.stream_id != 0:
                raise NativeV2ValidationError("CASUNAT2 global chunk has a stream id")
            if not allow_system and kind in {
                    ChunkType.RECOVERY_POINT, ChunkType.SEEK_INDEX,
                    ChunkType.INTEGRITY_TABLE, ChunkType.END}:
                raise NativeV2ValidationError("reserved CASUNAT2 chunk supplied to writer")
            if kind in {ChunkType.SEEK_INDEX, ChunkType.INTEGRITY_TABLE, ChunkType.END}:
                if kind in self.system_seen:
                    raise NativeV2ValidationError("duplicate CASUNAT2 structural chunk")
                self.system_seen.add(kind)
            if kind == ChunkType.CHAPTER_TABLE:
                if self.chapter_seen:
                    raise NativeV2ValidationError("duplicate CASUNAT2 chapter table")
                self.chapter_seen = True
                if self.semantic:
                    decode_chapter_table(chunk.payload)
            return
        if kind not in _STREAM_CHUNKS or chunk.stream_id not in self.descriptors:
            raise NativeV2ValidationError("CASUNAT2 chunk references an unknown stream")
        descriptor = self.descriptors[chunk.stream_id]
        expected = _EXPECTED_STREAM_TYPES.get(kind)
        if expected is not None and descriptor["type"] not in expected:
            raise NativeV2ValidationError("CASUNAT2 chunk type does not match its stream")
        if kind == ChunkType.STREAM_CONFIG:
            if chunk.stream_id in self.stream_configs:
                raise NativeV2ValidationError("duplicate CASUNAT2 stream config")
            self.stream_configs.add(chunk.stream_id)
            if self.semantic:
                try:
                    configured = strict_json_loads(chunk.payload)
                except StrictJsonError as exc:
                    raise NativeV2ValidationError("invalid CASUNAT2 stream config") from exc
                if configured != descriptor:
                    raise NativeV2ValidationError("CASUNAT2 stream config differs from manifest")
            return
        if kind not in {ChunkType.ATTACHMENT}:
            previous = self.last_pts.get(chunk.stream_id)
            if previous is not None and chunk.pts < previous:
                raise NativeV2ValidationError("CASUNAT2 stream chunks are not PTS ordered")
            self.last_pts[chunk.stream_id] = chunk.pts
        if kind == ChunkType.VIDEO_FORMAT_CHANGE:
            if chunk.stream_id in self.video_needs_key:
                raise NativeV2ValidationError(
                    "video format change is not followed by a key state")
            self.video_needs_key.add(chunk.stream_id)
        elif kind == ChunkType.VIDEO_KEY_STATE:
            self.video_needs_key.discard(chunk.stream_id)
            self.video_dependency_depth[chunk.stream_id] = 0
        elif (kind == ChunkType.VIDEO_TILE_UPDATE
              and chunk.stream_id in self.video_needs_key):
            raise NativeV2ValidationError(
                "video format change is not followed by a key state")
        elif kind == ChunkType.VIDEO_TILE_UPDATE:
            depth = self.video_dependency_depth.get(chunk.stream_id, 0) + 1
            if depth > self.limits.max_dependency_depth:
                raise NativeV2ValidationError(
                    "CASUNAT2 video dependency depth exceeds limit")
            self.video_dependency_depth[chunk.stream_id] = depth
        if not self.semantic:
            return
        try:
            if kind == ChunkType.VIDEO_KEY_STATE:
                frame = decode_key_state(chunk.payload)
                self.video[chunk.stream_id].frame = frame
                expected_format = self.video_format_override.pop(
                    chunk.stream_id, None)
                expected_width = (expected_format["source_shape"][1]
                                  if expected_format is not None else
                                  descriptor.get("width"))
                expected_height = (expected_format["source_shape"][0]
                                   if expected_format is not None else
                                   descriptor.get("height"))
                expected_pixel_format = (expected_format["pixel_format"]
                                         if expected_format is not None else
                                         descriptor.get("pix_fmt"))
                if expected_width is not None and (
                        frame.shape[1] != int(expected_width)
                        or frame.shape[0] != int(expected_height)):
                    raise NativeV2ValidationError("video key state geometry differs from manifest")
                if (expected_pixel_format is not None
                        and frame.pixel_format != expected_pixel_format):
                    raise NativeV2ValidationError("video key state format differs from manifest")
            elif kind == ChunkType.VIDEO_TILE_UPDATE:
                self.video[chunk.stream_id].apply_tile_update(chunk.payload)
            elif kind == ChunkType.VIDEO_FORMAT_CHANGE:
                value = decode_format_change(chunk.payload)
                self.video_format_override[chunk.stream_id] = value
                self.video[chunk.stream_id].frame = None
            elif kind == ChunkType.AUDIO_BLOCK:
                block = decode_audio_block(chunk.payload)
                if (block.pts != chunk.pts or [block.time_base_num, block.time_base_den]
                        != descriptor["time_base"] or block.sample_rate != int(descriptor["sample_rate"])
                        or block.channels != int(descriptor["channels"])):
                    raise NativeV2ValidationError("audio block differs from stream descriptor")
            elif kind == ChunkType.SUBTITLE_PACKET:
                packet = decode_subtitle_packet(chunk.payload)
                if packet.start_pts != chunk.pts:
                    raise NativeV2ValidationError("subtitle packet PTS differs from chunk")
            elif kind == ChunkType.SUBTITLE_BITMAP:
                packet = decode_bitmap_subtitle(chunk.payload)
                if packet.start_pts != chunk.pts:
                    raise NativeV2ValidationError("bitmap subtitle PTS differs from chunk")
            elif kind == ChunkType.ATTACHMENT:
                attachment = decode_attachment(chunk.payload)
                role = descriptor.get("role")
                if role is not None and attachment.role != role:
                    raise NativeV2ValidationError("attachment role differs from descriptor")
        except NativeV2ValidationError:
            raise
        except (KeyError, TypeError, ValueError, StrictJsonError) as exc:
            raise NativeV2ValidationError(
                f"invalid CASUNAT2 {kind.name.lower()} payload") from exc

    def finalize(self, *, require_system: bool = False) -> None:
        if self.video_needs_key:
            raise NativeV2ValidationError(
                "video format change is not followed by a key state")
        if require_system and self.system_seen != {
                ChunkType.SEEK_INDEX, ChunkType.INTEGRITY_TABLE, ChunkType.END}:
            raise NativeV2ValidationError("CASUNAT2 structural chunks are incomplete")

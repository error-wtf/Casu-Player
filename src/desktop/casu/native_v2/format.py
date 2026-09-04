from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ChunkType(IntEnum):
    STREAM_CONFIG = 1
    VIDEO_KEY_STATE = 16
    VIDEO_TILE_UPDATE = 17
    VIDEO_FORMAT_CHANGE = 18
    AUDIO_BLOCK = 32
    SUBTITLE_PACKET = 48
    SUBTITLE_BITMAP = 49
    CHAPTER_TABLE = 64
    ATTACHMENT = 65
    RECOVERY_POINT = 224
    SEEK_INDEX = 240
    INTEGRITY_TABLE = 241
    END = 255


@dataclass(frozen=True)
class NativeChunk:
    chunk_type: ChunkType
    stream_id: int
    pts: int
    payload: bytes
    flags: int = 0
    uncompressed_length: int | None = None


@dataclass(frozen=True)
class SeekEntry:
    stream_id: int
    target_pts: int
    key_state_pts: int
    key_state_offset: int
    first_update_offset: int


@dataclass(frozen=True)
class CasuLimits:
    """Central fail-closed limits for untrusted CASUNAT2 input."""

    max_file_bytes: int = 4 * 1024 * 1024 * 1024
    max_manifest_bytes: int = 64 * 1024 * 1024
    max_streams: int = 255
    max_chunks: int = 10_000_000
    max_chunk_bytes: int = 512 * 1024 * 1024
    max_attachment_bytes: int = 64 * 1024 * 1024
    max_total_uncompressed_frame_bytes: int = 512 * 1024 * 1024
    max_width: int = 32_768
    max_height: int = 32_768
    max_channels: int = 64
    max_sample_rate: int = 768_000
    max_dependency_depth: int = 1_000_000
    max_json_depth: int = 32
    max_json_nodes: int = 1_000_000
    max_audio_meta_bytes: int = 64 * 1024

    def validate(self) -> None:
        values = tuple(self.__dict__.values())
        if any(not isinstance(value, int) or value <= 0 for value in values):
            raise ValueError("CASUNAT2 limits must be positive integers")
        if self.max_streams > 255:
            raise ValueError("CASUNAT2 stream limit cannot exceed uint8 capacity")


DEFAULT_LIMITS = CasuLimits()

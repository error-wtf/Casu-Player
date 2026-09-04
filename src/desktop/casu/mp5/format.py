"""CASU MP5 format constants and chunk definitions."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


MAGIC = b"CASUMP5\0"
VERSION = 1
HEADER = struct.Struct("<8sHHII")
CHUNK_HEADER = struct.Struct("<BHII")
FOOTER_SIZE = 4

MAX_CHUNK_PAYLOAD = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_STREAMS = 64
MAX_CHUNKS = 1_000_000


class ChunkType(IntEnum):
    STREAM_CONFIG = 0x01
    VIDEO_KEY_STATE = 0x10
    VIDEO_TILE_UPDATE = 0x11
    VIDEO_FORMAT_CHANGE = 0x12
    AUDIO_BLOCK = 0x20
    SUBTITLE_PACKET = 0x30
    SUBTITLE_BITMAP = 0x31
    CHAPTER_TABLE = 0x40
    ATTACHMENT = 0x50
    SEEK_INDEX = 0x60
    INTEGRITY_TABLE = 0x70
    RECOVERY_POINT = 0x71
    METADATA = 0x80
    END = 0xFF


@dataclass(frozen=True)
class ChunkSummary:
    chunk_type: ChunkType
    stream_id: int
    pts: int
    payload_length: int


@dataclass(frozen=True)
class SeekEntry:
    stream_id: int
    target_pts: int
    key_state_offset: int
    key_state_pts: int


@dataclass(frozen=True)
class CasuLimits:
    max_file_bytes: int = 16 * 1024 * 1024 * 1024
    max_chunk_payload: int = MAX_CHUNK_PAYLOAD
    max_streams: int = MAX_STREAMS
    max_chunks: int = MAX_CHUNKS


DEFAULT_LIMITS = CasuLimits()

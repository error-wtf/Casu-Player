"""Deterministic subtitle and chapter payloads for CASUNAT2."""
from __future__ import annotations
import json
from dataclasses import dataclass
from .jsonutil import StrictJsonError, strict_json_loads

class TextPayloadError(ValueError):
    pass
MAX_SUBTITLE_TEXT_BYTES = 1024 * 1024
MAX_CHAPTERS = 100000
MAX_CHAPTER_TABLE_BYTES = 64 * 1024 * 1024

def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

@dataclass(frozen=True)
class SubtitlePacket:
    start_pts: int
    end_pts: int
    text: str
    language: str = 'und'
    format: str = 'text'

def decode_subtitle_packet(payload: bytes) -> SubtitlePacket:
    if len(payload) > MAX_SUBTITLE_TEXT_BYTES + 16 * 1024:
        raise TextPayloadError('subtitle payload exceeds limit')
    try:
        value = strict_json_loads(payload)
        if value.get('version') != 1:
            raise ValueError
        packet = SubtitlePacket(int(value['start_pts']), int(value['end_pts']), str(value['text']), str(value.get('language', 'und')), str(value.get('format', 'text')))
        if packet.end_pts < packet.start_pts or not packet.text or len(packet.text.encode('utf-8')) > MAX_SUBTITLE_TEXT_BYTES or (len(packet.language.encode('utf-8')) > 64) or (len(packet.format.encode('utf-8')) > 64):
            raise ValueError
        return packet
    except (TypeError, ValueError, KeyError, StrictJsonError) as exc:
        raise TextPayloadError('invalid subtitle payload') from exc

def decode_chapter_table(payload: bytes) -> list[dict]:
    if len(payload) > MAX_CHAPTER_TABLE_BYTES:
        raise TextPayloadError('chapter table exceeds limit')
    try:
        value = strict_json_loads(payload)
        if value.get('version') != 1 or not isinstance(value['chapters'], list) or len(value['chapters']) > MAX_CHAPTERS:
            raise ValueError
        result = []
        for chapter in value['chapters']:
            start = int(chapter['start_pts'])
            end = int(chapter['end_pts'])
            title = str(chapter['title'])
            language = str(chapter.get('language', 'und'))
            if end < start or not title or len(title.encode('utf-8')) > 4096 or (len(language.encode('utf-8')) > 64):
                raise ValueError
            result.append({'start_pts': start, 'end_pts': end, 'title': title, 'language': language})
        return result
    except (TypeError, ValueError, KeyError, StrictJsonError) as exc:
        raise TextPayloadError('invalid chapter table') from exc

"""Strict JSON decoding for security-sensitive CASUNAT2 structures."""
from __future__ import annotations

import json


class StrictJsonError(ValueError):
    pass


def strict_json_loads(data: str | bytes | bytearray) -> object:
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise StrictJsonError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def invalid_constant(value: str):
        raise StrictJsonError(f"non-finite JSON value: {value}")

    try:
        return json.loads(data, object_pairs_hook=object_pairs,
                          parse_constant=invalid_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError,
            StrictJsonError) as exc:
        if isinstance(exc, StrictJsonError):
            raise
        raise StrictJsonError("invalid strict JSON") from exc

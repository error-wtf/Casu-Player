"""Bounded, durable helpers shared by user-facing CASU file operations."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .core import CasuError


def read_bounded_json(path: str | Path, *, max_bytes: int, label: str) -> Any:
    """Read one UTF-8 JSON document without a size-check/read race."""
    source = Path(path).expanduser().resolve()
    if max_bytes <= 0:
        raise ValueError("JSON byte limit must be positive")
    try:
        with source.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise CasuError(f"{label} is unavailable") from exc
    if len(raw) > max_bytes:
        raise CasuError(f"{label} exceeds its {max_bytes}-byte safety limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CasuError(f"{label} is not valid UTF-8 JSON") from exc


def atomic_write_bytes(path: str | Path, payload: bytes, *, max_bytes: int | None = None) -> Path:
    """Durably replace a file without exposing partial contents."""
    if max_bytes is not None and len(payload) > max_bytes:
        raise CasuError(f"output exceeds its {max_bytes}-byte safety limit")
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target


def atomic_write_json(path: str | Path, payload: Any, *, max_bytes: int,
                      indent: int = 2) -> Path:
    try:
        encoded = (json.dumps(payload, indent=indent, ensure_ascii=False,
                              allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CasuError("JSON output contains an unsupported value") from exc
    return atomic_write_bytes(path, encoded, max_bytes=max_bytes)

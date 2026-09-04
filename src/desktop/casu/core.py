"""Read-only media resolution helpers used by MPCASU Player."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .probe import ProbeError, run_json


class CasuError(RuntimeError):
    pass


MAX_MANIFEST_BYTES = 64 * 1024 * 1024
FFPROBE_TIMEOUT_SECONDS = 30.0


def _read_manifest_json(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise CasuError(f"CASU manifest is unavailable: {path}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise CasuError("CASU manifest exceeds its safety limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CasuError("invalid CASU manifest") from exc
    if not isinstance(value, dict):
        raise CasuError("invalid CASU manifest")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_casu_source(path: Path) -> Path:
    """Resolve and verify a legacy read-only CASU manifest."""
    path = Path(path).expanduser().resolve()
    manifest = _read_manifest_json(path)
    try:
        filename = manifest["source"]["filename"]
        recorded = Path(manifest["source"]["path"]).expanduser()
    except (KeyError, TypeError) as exc:
        raise CasuError("CASU source metadata is missing") from exc
    if recorded.name != filename:
        raise CasuError("CASU source filename mismatch")
    candidate = recorded.resolve() if recorded.is_file() else (path.parent / filename).resolve()
    if not candidate.is_file():
        raise CasuError("CASU source media not found")
    expected_size = manifest.get("source", {}).get("size_bytes")
    if expected_size is not None and candidate.stat().st_size != int(expected_size):
        raise CasuError("CASU source size mismatch")
    expected_hash = manifest.get("source", {}).get("sha256")
    if expected_hash and _sha256_file(candidate) != expected_hash:
        raise CasuError("CASU source integrity mismatch")
    return candidate


def require_tool(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise CasuError(f"required tool not found: {name}")
    return value


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True,
                          capture_output=capture)


def ffprobe(path: Path, *, timeout_seconds: float = FFPROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        return run_json([require_tool("ffprobe"), "-v", "error", "-show_format",
                         "-show_streams", "-show_chapters", "-of", "json", str(path)],
                        timeout_seconds=timeout_seconds)
    except ProbeError as exc:
        raise CasuError(str(exc)) from exc


def stream(probe: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((item for item in probe.get("streams", [])
                 if item.get("codec_type") == kind), None)


def duration(probe: dict[str, Any]) -> float:
    try:
        return float(probe.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        return 0.0

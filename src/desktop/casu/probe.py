"""Resource-bounded subprocess helpers for untrusted media probes."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable


class ProbeError(RuntimeError):
    pass


def run_bounded(command: list[str], *, max_output_bytes: int,
                timeout_seconds: float, max_error_bytes: int = 8 * 1024 * 1024,
                watched_paths: Iterable[tuple[str | Path, int]] = ()) -> bytes:
    """Run a subprocess while bounding stdout, stderr, time, and output files."""
    if max_output_bytes <= 0 or timeout_seconds <= 0:
        raise ValueError("subprocess budgets must be positive")
    watched = tuple((Path(path), int(limit)) for path, limit in watched_paths)
    if max_error_bytes <= 0 or any(limit <= 0 for _path, limit in watched):
        raise ValueError("subprocess budgets must be positive")
    with tempfile.TemporaryFile(mode="w+b") as output, tempfile.TemporaryFile(mode="w+b") as errors:
        try:
            process = subprocess.Popen(command, stdout=output, stderr=errors)
        except OSError as exc:
            raise ProbeError("could not start media probe") from exc
        deadline = time.monotonic() + timeout_seconds
        reason: str | None = None
        while process.poll() is None:
            if os.fstat(output.fileno()).st_size > max_output_bytes:
                reason = "subprocess output exceeds configured limit"
                process.kill()
                break
            if os.fstat(errors.fileno()).st_size > max_error_bytes:
                reason = "subprocess error output exceeds configured limit"
                process.kill()
                break
            if any(path.exists() and path.stat().st_size > limit
                   for path, limit in watched):
                reason = "subprocess file output exceeds configured limit"
                process.kill()
                break
            if time.monotonic() >= deadline:
                reason = "subprocess exceeded configured time limit"
                process.kill()
                break
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        process.wait()
        size = os.fstat(output.fileno()).st_size
        if reason is None and size > max_output_bytes:
            reason = "subprocess output exceeds configured limit"
        if reason is None and os.fstat(errors.fileno()).st_size > max_error_bytes:
            reason = "subprocess error output exceeds configured limit"
        if reason is None and any(path.exists() and path.stat().st_size > limit
                                  for path, limit in watched):
            reason = "subprocess file output exceeds configured limit"
        if reason is not None:
            raise ProbeError(reason)
        if process.returncode != 0:
            errors.seek(0)
            detail = errors.read(8192).decode("utf-8", errors="replace").strip()
            raise ProbeError(f"subprocess failed: {detail or process.returncode}")
        output.seek(0)
        return output.read()


def run_json(command: list[str], *, max_output_bytes: int = 256 * 1024 * 1024,
             timeout_seconds: float = 600.0) -> dict[str, Any]:
    """Run a JSON-producing command without accumulating unbounded stdout."""
    try:
        value = json.loads(run_bounded(command, max_output_bytes=max_output_bytes,
                                       timeout_seconds=timeout_seconds))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProbeError("media probe returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProbeError("media probe JSON root must be an object")
    return value

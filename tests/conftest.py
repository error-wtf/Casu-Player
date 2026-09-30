# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
"""Pytest bootstrap: makes `src/desktop` importable without manual PYTHONPATH.

Previously every invocation needed `PYTHONPATH=src/desktop pytest -q`; with this
conftest the suite runs out-of-the-box from the repo root (parity with the
CODEC repo, which uses pyproject `pythonpath`).
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "desktop"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

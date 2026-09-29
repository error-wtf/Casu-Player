# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# Copyright (C) 2026 Lino Casu
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the CASU Anti-Capitalist License 1.4.
# ---------------------------------------------------------------------
"""Cross-thread signal bridges for worker callbacks (v7.8 modularization)."""

from PySide6.QtCore import QObject, Signal


class _ThreadBridge(QObject):
    """Marshals worker-thread results onto the Qt event loop (no popups)."""

    resultReady = Signal(object)
    errorReady = Signal(object)

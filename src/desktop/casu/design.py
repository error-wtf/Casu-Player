# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Unified MPCASU design tokens.

Single source of truth for the official red/dark design shared by the web
player (``web/styles.css``), the desktop player (``mpcasu_player.py``) and
the Qt player (``mpcasu_qt/theme.py``).  The web player's ``:root`` token
set is the binding reference; the values below are copied verbatim from it
plus the stage/overlay colors the web player uses inline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesignTokens:
    bg: str = "#07090b"
    panel: str = "#101317"
    panel2: str = "#15191e"
    line: str = "#252a30"
    red: str = "#ff1e2d"
    red_dark: str = "#3a1015"
    red_glow: str = "#ff1e2d55"
    muted: str = "#858b93"
    text: str = "#f4f5f7"
    secondary: str = "#b9bec5"
    stage: str = "#050608"
    stage_border: str = "#1e2328"
    sidebar: str = "#0c0f12"
    button: str = "#161a1f"
    button_text: str = "#d7d9dc"
    input_bg: str = "#080a0c"
    input_border: str = "#333942"
    toast_bg: str = "#171b20"
    toast_border: str = "#444444"
    badge_bg: str = "#090b0d"
    badge_border: str = "#383d43"
    scrollbar: str = "#1b2026"
    font_stack: str = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    font_mono: str = "'JetBrains Mono', 'DejaVu Sans Mono', monospace"
    radius_shell: int = 18
    radius_panel: int = 10
    radius_control: int = 7
    sidebar_width: int = 240
    right_panel_width: int = 370
    topbar_height: int = 72
    transport_height: int = 66
    play_button: int = 52
    toast_ms: int = 2600


TOKENS = DesignTokens()

BG = TOKENS.bg
PANEL = TOKENS.panel
PANEL_ALT = TOKENS.panel2
LINE = TOKENS.line
RED = TOKENS.red
RED_DARK = TOKENS.red_dark
TEXT = TOKENS.text
SECONDARY = TOKENS.secondary
MUTED = TOKENS.muted
STAGE = TOKENS.stage
TOAST_BG = TOKENS.toast_bg
TOAST_BORDER = TOKENS.toast_border
BADGE_BG = TOKENS.badge_bg
BADGE_BORDER = TOKENS.badge_border
INPUT_BG = TOKENS.input_bg
INPUT_BORDER = TOKENS.input_border
SCROLLBAR = TOKENS.scrollbar
SIDEBAR = TOKENS.sidebar

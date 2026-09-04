# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""MPCASU dark/red visual theme — exact mirror of the web player.

Every colour, radius and metric is taken from ``casu.design.TOKENS`` (the
binding copy of ``web/styles.css`` ``:root``), so the Qt player, the web
player and the converter render as one product family.
"""
from __future__ import annotations

from dataclasses import dataclass

from casu import design


@dataclass(frozen=True)
class Palette:
    """Immutable colour set — values identical to web/styles.css tokens."""

    window: str = design.BG                      # --bg #07090b
    sidebar: str = design.SIDEBAR                # #0c0f12
    surface: str = design.PANEL                  # --panel #101317
    surface_alt: str = design.PANEL_ALT          # --panel2 #15191e
    card: str = design.PANEL
    border: str = design.LINE                    # --line #252a30
    border_strong: str = design.BADGE_BORDER     # #383d43
    shell_border: str = "#292d31"
    stage: str = design.STAGE                    # #050608
    stage_border: str = "#1e2328"
    button: str = "#161a1f"
    button_text: str = "#d7d9dc"
    button_hover_border: str = "#442222"
    input_bg: str = design.INPUT_BG              # #080a0c
    input_border: str = design.INPUT_BORDER      # #333942

    accent: str = design.RED                     # --red #ff1e2d
    accent_hot: str = design.RED
    accent_dim: str = design.RED_DARK            # #3a1015
    accent_wash: str = "#1b0a0d"
    accent_glow: str = design.TOKENS.red_glow    # #ff1e2d55

    text: str = design.TEXT                      # --text #f4f5f7
    text_muted: str = design.SECONDARY           # #b9bec5
    text_faint: str = design.MUTED               # --muted #858b93
    text_on_accent: str = "#ffffff"

    ok: str = "#25c065"
    warn: str = "#e0a010"
    error: str = "#ff4040"
    toast_bg: str = design.TOAST_BG              # #171b20
    toast_border: str = design.TOAST_BORDER      # #444444
    scrollbar: str = design.SCROLLBAR            # #1b2026


@dataclass(frozen=True)
class Metrics:
    """Layout constants — identical to the web shell metrics."""

    sidebar_width: int = design.TOKENS.sidebar_width        # 240
    playlist_width: int = design.TOKENS.right_panel_width   # 310
    topbar_height: int = design.TOKENS.topbar_height        # 72
    transport_height: int = design.TOKENS.transport_height  # 66
    radius: int = design.TOKENS.radius_panel                # 10
    radius_small: int = design.TOKENS.radius_control        # 7
    radius_large: int = design.TOKENS.radius_shell          # 18
    control_height: int = 38
    transport_button: int = 40
    play_button: int = design.TOKENS.play_button            # 52
    pad: int = 12
    pad_small: int = 8
    pad_large: int = 18
    thumbnail_width: int = 54
    thumbnail_height: int = 38


PALETTE = Palette()
METRICS = Metrics()


def apply_dark_combo_popup(combo, palette: Palette = PALETTE) -> None:
    """Force the real popup view dark; native Linux/macOS styles can ignore QSS ancestry."""
    from PySide6.QtGui import QColor, QPalette

    view = combo.view()
    qt_palette = view.palette()
    roles = {
        QPalette.Base: palette.input_bg,
        QPalette.Window: palette.input_bg,
        QPalette.Text: palette.text,
        QPalette.WindowText: palette.text,
        QPalette.Button: palette.input_bg,
        QPalette.ButtonText: palette.text,
        QPalette.Highlight: palette.accent_dim,
        QPalette.HighlightedText: palette.text_on_accent,
    }
    for role, colour in roles.items():
        qt_palette.setColor(QPalette.Active, role, QColor(colour))
        qt_palette.setColor(QPalette.Inactive, role, QColor(colour))
    qt_palette.setColor(QPalette.Disabled, QPalette.Text, QColor(palette.text_faint))
    qt_palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(palette.text_faint))
    view.setPalette(qt_palette)
    view.setAutoFillBackground(True)
    view.setStyleSheet(f"""
        QAbstractItemView {{ background-color: {palette.input_bg}; color: {palette.text};
          selection-background-color: {palette.accent_dim}; selection-color: {palette.text_on_accent}; }}
        QAbstractItemView::item {{ background-color: {palette.input_bg}; color: {palette.text}; padding: 5px 8px; }}
        QAbstractItemView::item:selected {{ background-color: {palette.accent_dim}; color: {palette.text_on_accent}; }}
        QAbstractItemView::item:disabled {{ background-color: {palette.input_bg}; color: {palette.text_faint}; }}
    """)


def format_duration(seconds: float) -> str:
    """Render a playback position as H:MM:SS / M:SS like the web player."""
    try:
        total = int(max(0.0, float(seconds)))
    except (TypeError, ValueError):
        return "00:00"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def stylesheet(palette: Palette = PALETTE, metrics: Metrics = METRICS) -> str:
    """Build the complete application stylesheet (web tokens, Qt syntax)."""
    p, m = palette, metrics
    from pathlib import Path as _Path
    assets = _Path(__file__).resolve().parent.parent / "assets"
    branch_closed = assets / "branch_closed.png"
    branch_open = assets / "branch_open.png"
    combo_arrow = assets / "combo_arrow.png"
    return f"""
QWidget {{
    background-color: {p.window};
    color: {p.text};
    font-family: "Inter", "Segoe UI", "Ubuntu", "DejaVu Sans", sans-serif;
    font-size: 13px;
}}
QMainWindow {{ background-color: {p.window}; }}

/* ---------- Sidebar (web .sidebar) ---------- */
#Sidebar {{
    background-color: {p.sidebar};
    border-right: 1px solid {p.border};
}}
#SidebarSection {{
    color: #656b73;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.5px;
    padding: 16px 10px 6px 10px;
    background: transparent;
}}
#BrandName {{
    color: {p.text};
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0.5px;
    background: transparent;
}}
#BrandSub {{
    color: {p.accent};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 4px;
    background: transparent;
}}

QPushButton#NavItem {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    color: {p.text_muted};
    text-align: left;
    padding: 11px 12px;
    font-size: 13px;
    font-family: "Noto Color Emoji", "Inter", "Ubuntu", sans-serif;
    border-radius: {m.radius_small}px;
}}
QPushButton#NavItem:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent_dim}, stop:1 #221217);
    color: {p.text};
}}
QPushButton#NavItem:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent_dim}, stop:1 #221217);
    border-left: 3px solid {p.accent};
    color: {p.accent};
    font-weight: 700;
}}
QPushButton#NavItem[active="true"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent_dim}, stop:1 #221217);
    border-left: 3px solid {p.accent};
    color: {p.accent};
}}

/* ---------- Panels / stage ---------- */
#Panel {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_small + 1}px;
}}
#VideoSurface {{
    background-color: {p.stage};
    border: 1px solid {p.stage_border};
    border-radius: {m.radius}px;
}}
#TopBar {{
    background: transparent;
    border: none;
}}
#NowPlayingTitle {{
    color: {p.accent};
    font-size: 15px;
    font-weight: 800;
    background: transparent;
}}
#NowPlayingMeta {{
    color: {p.text_faint};
    font-size: 11px;
    background: transparent;
}}
#StatusText {{
    color: {p.text_faint};
    font-size: 11px;
    background: transparent;
}}
#TimeLabel {{
    color: {p.text_muted};
    font-size: 11px;
    background: transparent;
}}

/* ---------- Buttons (web .transport button) ---------- */
QPushButton#TransportButton, QPushButton#IconButton {{
    background-color: {p.button};
    border: 1px solid transparent;
    border-radius: {m.radius_small}px;
    color: {p.button_text};
    min-width: {m.transport_button}px;
    min-height: {m.control_height}px;
    font-family: "DejaVu Sans", sans-serif;
}}
QPushButton#TransportButton:hover, QPushButton#IconButton:hover {{
    color: {p.accent};
    border-color: {p.button_hover_border};
}}
QPushButton#TransportButton[on="true"], QPushButton#IconButton[on="true"] {{
    color: {p.accent};
    box-shadow: inset 0 -2px {p.accent};
}}
QPushButton#PlayButton {{
    background-color: {p.accent_wash};
    border: 2px solid {p.accent};
    border-radius: {m.play_button // 2}px;
    color: {p.accent};
    font-size: 18px;
    font-family: "DejaVu Sans", sans-serif;
}}
QPushButton#PlayButton:hover {{
    background-color: #2a0d12;
}}
QPushButton#PrimaryButton {{
    background-color: {p.accent};
    border: 1px solid {p.accent};
    border-radius: {m.radius_small}px;
    color: {p.text_on_accent};
    font-weight: 600;
    min-height: {m.control_height}px;
    padding: 6px 14px;
}}
QPushButton#PrimaryButton:hover {{ background-color: {p.accent_hot}; }}

/* ---------- Inputs (web dialog input) ---------- */
QLineEdit {{
    background-color: {p.input_bg};
    border: 1px solid {p.input_border};
    border-radius: {m.radius_small}px;
    color: {p.text};
    padding: 8px 12px;
    selection-background-color: {p.accent_dim};
}}
QLineEdit:focus {{ border-color: {p.accent}; }}

/* ---------- All list/table popups & menus (dark, readable) ---------- */
QAbstractItemView {{
    background-color: {p.sidebar};
    alternate-background-color: {p.surface};
    color: {p.text};
    selection-background-color: {p.accent_dim};
    selection-color: {p.text};
    border: none;
    outline: none;
}}
QMenu {{
    background-color: {p.sidebar};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    color: {p.text};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px;
    border-radius: 4px;
    color: {p.text};
}}
QMenu::item:selected {{
    background-color: {p.accent_dim};
    color: {p.text};
}}
QMenu::item:disabled {{ color: {p.text_faint}; }}
QMenu::separator {{
    height: 1px;
    background: {p.border};
    margin: 4px 8px;
}}
QToolTip {{
    background-color: {p.sidebar};
    color: {p.text};
    border: 1px solid {p.border_strong};
    padding: 4px 8px;
    border-radius: 4px;
}}

/* ---------- Selects & number boxes (dark, readable popup) ---------- */
QComboBox {{
    background-color: {p.input_bg};
    border: 1px solid {p.input_border};
    border-radius: {m.radius_small}px;
    color: {p.text};
    padding: 5px 10px;
    min-height: 20px;
    selection-background-color: {p.accent_dim};
    selection-color: {p.text};
}}
QComboBox:hover {{ border-color: {p.accent}; }}
QComboBox:focus {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: url({combo_arrow});
    width: 11px;
    height: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {p.sidebar};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    color: {p.text};
    selection-background-color: {p.accent_dim};
    selection-color: {p.text};
    outline: none;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    background-color: {p.input_bg};
    color: {p.text};
    padding: 5px 8px;
    border-radius: 4px;
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {p.accent_dim};
    color: {p.text_on_accent};
}}
QSpinBox, QDoubleSpinBox {{
    background-color: {p.input_bg};
    border: 1px solid {p.input_border};
    border-radius: {m.radius_small}px;
    color: {p.text};
    padding: 4px 8px;
    min-height: 20px;
    selection-background-color: {p.accent_dim};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p.accent}; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 20px;
}}
QCheckBox {{ color: {p.text}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {p.input_border};
    border-radius: 4px;
    background: {p.input_bg};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
}}

/* ---------- Queue tree (web #queue) ---------- */
QTreeWidget {{
    background-color: {p.sidebar};
    border: none;
    outline: none;
    font-size: 12px;
}}
QTreeWidget::item {{
    border-bottom: 1px solid #20242a;
    border-radius: {m.radius_small}px;
    padding: 6px 4px;
}}
QTreeWidget::item:hover {{ background-color: #171b20; }}
QTreeWidget::item:selected {{
    background-color: #171b20;
    outline: 1px solid {p.accent};
    color: {p.accent};
}}
QTreeWidget::branch {{ background: transparent; border: none; }}
QTreeWidget::branch:has-children:closed {{
    image: url({branch_closed});
    border-left: 3px solid {p.accent};
}}
QTreeWidget::branch:has-children:open {{
    image: url({branch_open});
    border-left: 3px solid {p.accent};
    background-color: #1d0c10;
}}

/* ---------- Scrollbars (always visible, web-dark) ---------- */
QScrollBar:vertical {{
    background: {p.scrollbar};
    width: 10px;
    border-radius: 5px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.accent_dim};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.accent}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {p.scrollbar};
    height: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {p.accent_dim};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.accent}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- Sliders (web accent-color red) ---------- */
QSlider::groove:horizontal {{
    background: {p.border};
    height: 5px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {p.accent};
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 3px; }}

/* ---------- Menus ---------- */
QMenu {{
    background-color: #111418;
    border: 1px solid #343940;
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px;
    border-radius: 5px;
}}
QMenu::item:selected {{ background-color: {p.accent_dim}; color: {p.accent}; }}

/* ---------- Cards (web .cards) ---------- */
#Card {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_small + 1}px;
    padding: 12px;
}}
#CardTitle {{
    color: #a7abb0;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
    background: transparent;
}}
#CardValue {{
    color: {p.accent};
    font-size: 12px;
    font-weight: 700;
    background: transparent;
}}

/* ---------- Toast (web #toast) ---------- */
QLabel#Toast {{
    background-color: {p.toast_bg};
    border: 1px solid {p.toast_border};
    border-left: 3px solid {p.accent};
    border-radius: {m.radius_small}px;
    color: {p.text};
    padding: 12px 18px;
}}

/* ---------- Status bar ---------- */
QStatusBar {{
    background-color: {p.sidebar};
    border-top: 1px solid {p.border};
    color: {p.text_faint};
}}

/* ---------- In-window pages (web dialog look) ---------- */
#Page {{
    background-color: #0b0d10;
}}
#PagePanel {{
    background-color: #111418;
    border: 1px solid #343940;
    border-radius: 12px;
}}
#EpgChannel {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #171b20, stop:1 #0c0f12);
    border: 1px solid {p.border};
    border-left: 3px solid {p.accent};
    border-radius: {m.radius_small + 1}px;
    padding: 12px;
}}
"""

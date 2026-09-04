// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
#include "theme.hpp"

#include <QApplication>
#include <QCoreApplication>
#include <QDir>
#include <QTime>
#include <cmath>

namespace mpcasu {

QString format_duration(double seconds) {
    if (seconds < 0.0) return "00:00";
    const int total = static_cast<int>(std::llround(seconds));
    const int h = total / 3600;
    const int m = (total % 3600) / 60;
    const int s = total % 60;
    if (h > 0)
        return QString("%1:%2:%3").arg(h).arg(m, 2, 10, QChar('0')).arg(s, 2, 10, QChar('0'));
    return QString("%1:%2").arg(m, 2, 10, QChar('0')).arg(s, 2, 10, QChar('0'));
}

// Asset paths are resolved exe-relative (works in the build tree and in the
// packaged zip; never cwd-dependent). Mirrors theme.py's __file__-relative
// resolution on Linux.
static QString asset_path(const QString& name) {
    return QDir(QCoreApplication::applicationDirPath() + "/assets").filePath(name);
}

QString application_stylesheet() {
    const Palette& P = palette();
    const Metrics& M = metrics();
    const QString bg = P.bg, panel = P.panel, panel2 = P.panel2, line = P.line;
    const QString red = P.red, red_dark = P.red_dark, muted = P.muted, text = P.text;
    const QString secondary = P.secondary, stage = P.stage, sidebar = P.sidebar;
    const QString button = P.button, button_text = P.button_text;
    const QString input_bg = P.input_bg, input_border = P.input_border;
    const QString toast_bg = P.toast_bg, toast_border = P.toast_border;
    const QString scrollbar = P.scrollbar, border_strong = P.border_strong;
    const QString button_hover_border = P.button_hover_border;
    const QString accent_wash = P.accent_wash, stage_border = P.stage_border;
    const QString branch_closed = asset_path("branch_closed.png");
    const QString branch_open = asset_path("branch_open.png");
    const QString combo_arrow = asset_path("combo_arrow.png");

    // Exact mirror of mpcasu_qt/theme.py stylesheet() (binding reference:
    // web/styles.css :root). Widget objectNames must match the Linux UI:
    // Sidebar/SidebarSection/BrandName/BrandSub, NavItem, TopBar,
    // NowPlayingTitle/NowPlayingMeta/StatusText/TimeLabel, TransportButton/
    // IconButton/PlayButton/PrimaryButton, Card/CardTitle/CardValue,
    // VideoSurface, Page/PagePanel/EpgChannel, QLabel#Toast.
    return QString(R"QSS(
QWidget {
    background-color: %1;
    color: %2;
    font-family: "Inter", "Segoe UI", "Ubuntu", "DejaVu Sans", sans-serif;
    font-size: 13px;
}
QMainWindow { background-color: %1; }

/* ---------- Sidebar (web .sidebar) ---------- */
#Sidebar {
    background-color: %3;
    border-right: 1px solid %4;
}
#SidebarScroll, #SidebarScroll > QWidget > QWidget {
    background: transparent;
}
#SidebarSection {
    color: #656b73;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.5px;
    padding: 16px 10px 6px 10px;
    background: transparent;
}
#BrandName {
    color: %2;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0.5px;
    background: transparent;
}
#BrandSub {
    color: %5;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 4px;
    background: transparent;
}

QPushButton#NavItem {
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    color: %6;
    text-align: left;
    padding: 11px 12px;
    min-height: 20px;
    font-size: 13px;
    border-radius: %7px;
}
QPushButton#NavItem:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 %8, stop:1 #221217);
    color: %2;
}
QPushButton#NavItem:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 %8, stop:1 #221217);
    border-left: 3px solid %5;
    color: %5;
    font-weight: 700;
}
QPushButton#NavItem[active="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 %8, stop:1 #221217);
    border-left: 3px solid %5;
    color: %5;
}

/* ---------- Panels / stage ---------- */
#Panel {
    background-color: %9;
    border: 1px solid %4;
    border-radius: %10px;
}
#VideoSurface {
    background-color: %11;
    border: 1px solid %12;
    border-radius: %13px;
}
#TopBar {
    background: transparent;
    border: none;
}
#NowPlayingTitle {
    color: %5;
    font-size: 15px;
    font-weight: 800;
    background: transparent;
}
#NowPlayingMeta {
    color: %14;
    font-size: 11px;
    background: transparent;
}
#StatusText {
    color: %14;
    font-size: 11px;
    background: transparent;
}
#TimeLabel {
    color: %15;
    font-size: 11px;
    background: transparent;
}

/* ---------- Buttons (web .transport button) ---------- */
QPushButton#TransportButton, QPushButton#IconButton {
    background-color: %16;
    border: 1px solid transparent;
    border-radius: %7px;
    color: %17;
    min-width: %18px;
    min-height: %19px;
    font-family: "DejaVu Sans", sans-serif;
}
QPushButton#TransportButton:hover, QPushButton#IconButton:hover {
    color: %5;
    border-color: %20;
}
QPushButton#TransportButton[on="true"], QPushButton#IconButton[on="true"] {
    color: %5;
    box-shadow: inset 0 -2px %5;
}
QPushButton#PlayButton {
    background-color: %21;
    border: 2px solid %5;
    border-radius: %22px;
    color: %5;
    font-size: 18px;
    font-family: "DejaVu Sans", sans-serif;
}
QPushButton#PlayButton:hover {
    background-color: #2a0d12;
}
QPushButton#PrimaryButton {
    background-color: %5;
    border: 1px solid %5;
    border-radius: %7px;
    color: %23;
    font-weight: 600;
    min-height: %19px;
    padding: 6px 14px;
}
QPushButton#PrimaryButton:hover { background-color: %5; }

/* ---------- Inputs (web dialog input) ---------- */
QLineEdit {
    background-color: %24;
    border: 1px solid %25;
    border-radius: %7px;
    color: %2;
    padding: 8px 12px;
    selection-background-color: %8;
}
QLineEdit:focus { border-color: %5; }

/* ---------- All list/table popups & menus (dark, readable) ---------- */
QAbstractItemView {
    background-color: %3;
    alternate-background-color: %9;
    color: %2;
    selection-background-color: %8;
    selection-color: %2;
    border: none;
    outline: none;
}
QMenu {
    background-color: %3;
    border: 1px solid %26;
    border-radius: 6px;
    color: %2;
    padding: 4px;
}
QMenu::item {
    padding: 6px 22px;
    border-radius: 4px;
    color: %2;
}
QMenu::item:selected {
    background-color: %8;
    color: %2;
}
QMenu::item:disabled { color: %14; }
QMenu::separator {
    height: 1px;
    background: %4;
    margin: 4px 8px;
}
QToolTip {
    background-color: %3;
    color: %2;
    border: 1px solid %26;
    padding: 4px 8px;
    border-radius: 4px;
}

/* ---------- Selects & number boxes (dark, readable popup) ---------- */
QComboBox {
    background-color: %24;
    border: 1px solid %25;
    border-radius: %7px;
    color: %2;
    padding: 5px 10px;
    min-height: 20px;
    selection-background-color: %8;
    selection-color: %2;
}
QComboBox:hover { border-color: %5; }
QComboBox:focus { border-color: %5; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow {
    image: url(%27);
    width: 11px;
    height: 6px;
}
QComboBox QAbstractItemView {
    background-color: %3;
    border: 1px solid %26;
    border-radius: 6px;
    color: %2;
    selection-background-color: %8;
    selection-color: %2;
    outline: none;
    padding: 4px;
}
QComboBox QAbstractItemView::item {
    background-color: %24;
    color: %2;
    padding: 5px 8px;
    border-radius: 4px;
}
QComboBox QAbstractItemView::item:selected {
    background-color: %8;
    color: #ffffff;
}
QComboBox QAbstractItemView::item:disabled {
    background-color: %24;
    color: %14;
}
QSpinBox, QDoubleSpinBox {
    background-color: %24;
    border: 1px solid %25;
    border-radius: %7px;
    color: %2;
    padding: 4px 8px;
    min-height: 20px;
    selection-background-color: %8;
}
QSpinBox:focus, QDoubleSpinBox:focus { border-color: %5; }
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background: transparent;
    border: none;
    width: 20px;
}
QCheckBox { color: %2; spacing: 8px; }
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid %25;
    border-radius: 4px;
    background: %24;
}
QCheckBox::indicator:hover { border-color: %5; }
QCheckBox::indicator:checked {
    background: %5;
    border-color: %5;
}

/* ---------- Queue tree (web #queue) ---------- */
QTreeWidget {
    background-color: %3;
    border: none;
    outline: none;
    font-size: 12px;
}
QTreeWidget::item {
    border-bottom: 1px solid #20242a;
    border-radius: %7px;
    padding: 6px 4px;
}
QTreeWidget::item:hover { background-color: #171b20; }
QTreeWidget::item:selected {
    background-color: #171b20;
    outline: 1px solid %5;
    color: %5;
}
QTreeWidget::branch { background: transparent; border: none; }
QTreeWidget::branch:has-children:closed {
    image: url(%28);
    border-left: 3px solid %5;
}
QTreeWidget::branch:has-children:open {
    image: url(%29);
    border-left: 3px solid %5;
    background-color: #1d0c10;
}

/* ---------- Scrollbars (always visible, web-dark) ---------- */
QScrollBar:vertical {
    background: %30;
    width: 10px;
    border-radius: 5px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: %8;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: %5; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: %30;
    height: 10px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: %8;
    border-radius: 5px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background: %5; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---------- Sliders (web accent-color red) ---------- */
QSlider::groove:horizontal {
    background: %4;
    height: 5px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: %5;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
QSlider::sub-page:horizontal { background: %5; border-radius: 3px; }

/* ---------- Menus (second block, web .queue-menu) ---------- */
QMenu {
    background-color: #111418;
    border: 1px solid #343940;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 22px;
    border-radius: 5px;
}
QMenu::item:selected { background-color: %8; color: %5; }

/* ---------- Cards (web .cards) ---------- */
#Card {
    background-color: %9;
    border: 1px solid %4;
    border-radius: %10px;
    padding: 12px;
}
#CardTitle {
    color: #a7abb0;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
    background: transparent;
}
#CardValue {
    color: %5;
    font-size: 12px;
    font-weight: 700;
    background: transparent;
}

/* ---------- Toast (web #toast) ---------- */
QLabel#Toast {
    background-color: %31;
    border: 1px solid %32;
    border-left: 3px solid %5;
    border-radius: %7px;
    color: %2;
    padding: 12px 18px;
}

/* ---------- Fullscreen overlay (Linux parity) ---------- */
QFrame#FsOverlay {
    background-color: %31;
    border: 1px solid %32;
    border-radius: %7px;
}

/* ---------- Status bar ---------- */
QStatusBar {
    background-color: %3;
    border-top: 1px solid %4;
    color: %14;
}

/* ---------- In-window pages (web dialog look) ---------- */
#Page {
    background-color: #0b0d10;
}
#PagePanel {
    background-color: #111418;
    border: 1px solid #343940;
    border-radius: 12px;
}
#EpgChannel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #171b20, stop:1 #0c0f12);
    border: 1px solid %4;
    border-left: 3px solid %5;
    border-radius: %10px;
    padding: 12px;
}
)QSS")
        .arg(bg, text, sidebar, line, red, secondary,
             QString::number(M.radius_control), red_dark,
             panel, QString::number(M.radius_control + 1),
             stage, stage_border, QString::number(M.radius_panel),
             muted, secondary, button, button_text,
             QString::number(M.transport_button), QString::number(M.control_height),
             button_hover_border, accent_wash,
             QString::number(M.play_button / 2), P.text_on_accent,
             input_bg, input_border, border_strong,
             combo_arrow, branch_closed, branch_open,
             scrollbar, toast_bg, toast_border);
}

}  // namespace mpcasu

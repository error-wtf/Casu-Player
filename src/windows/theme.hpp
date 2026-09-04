// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// MPCASU design tokens + Qt stylesheet (mirrors casu/design.py +
// mpcasu_qt/theme.py; binding reference: web/styles.css :root).
#pragma once
#include <QString>

namespace mpcasu {

struct Palette {
    const char* bg = "#07090b";
    const char* panel = "#101317";
    const char* panel2 = "#15191e";
    const char* card = "#101317";
    const char* line = "#252a30";
    const char* border_strong = "#383d43";
    const char* shell_border = "#292d31";
    const char* red = "#ff1e2d";
    const char* red_dark = "#3a1015";
    const char* red_glow = "#ff1e2d55";
    const char* accent_wash = "#1b0a0d";
    const char* muted = "#858b93";
    const char* text = "#f4f5f7";
    const char* secondary = "#b9bec5";
    const char* text_on_accent = "#ffffff";
    const char* stage = "#050608";
    const char* stage_border = "#1e2328";
    const char* sidebar = "#0c0f12";
    const char* button = "#161a1f";
    const char* button_text = "#d7d9dc";
    const char* button_hover_border = "#442222";
    const char* input_bg = "#080a0c";
    const char* input_border = "#333942";
    const char* toast_bg = "#171b20";
    const char* toast_border = "#444444";
    const char* badge_bg = "#090b0d";
    const char* badge_border = "#383d43";
    const char* scrollbar = "#1b2026";
    const char* ok = "#25c065";
    const char* warn = "#e0a010";
    const char* error = "#ff4040";
};

struct Metrics {
    int sidebar_width = 240;
    int right_panel_width = 370;
    int topbar_height = 72;
    int transport_height = 66;
    int radius_shell = 18;
    int radius_panel = 10;
    int radius_control = 7;
    int control_height = 38;
    int transport_button = 40;
    int play_button = 52;
    int thumbnail_width = 54;
    int thumbnail_height = 38;
    int pad = 12;
    int pad_small = 8;
    int pad_large = 18;
};

inline const Palette& palette() {
    static const Palette p;
    return p;
}

inline const Metrics& metrics() {
    static const Metrics m;
    return m;
}

// mm:ss (or hh:mm:ss) time formatting, same as theme.format_duration.
QString format_duration(double seconds);

// Global application stylesheet fed from the palette.
QString application_stylesheet();

}  // namespace mpcasu

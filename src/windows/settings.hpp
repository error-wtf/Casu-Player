// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Validated, atomic MPCASU settings — full port of casu/settings.py plus the
// Linux player's separate session.json.
// Parity notes: versioned envelope {"version":1,"player":{...}}; identical
// clamps in validated() (volume [0,200], finite rate [0.25,4.0], cache limit
// [0,65536] default 512, split minutes [0,1440], record-format whitelist,
// repeat/visualizer enums, watched-folder cap 100 with per-path bounds);
// 1 MiB bounded reads; atomic tmp+fsync+replace writes (indent=2 +
// trailing newline like the reference).
#pragma once
#include <QString>
#include <QStringList>

namespace mpcasu {

constexpr int kMaxSettingsBytes = 1024 * 1024;
constexpr int kMaxWatchedFolders = 100;
constexpr int kMaxSettingTextBytes = 4096;

struct PlayerSettings {
    int volume = 100;
    bool muted = false;
    double rate = 1.0;
    QString audio_device;  // empty == None
    QStringList watched_folders;
    bool ytdlp_consent = false;
    QString visualizer = "waveform";
    bool resume_playback = true;
    int cache_limit_mib = 512;
    QString recordings_dir;
    int record_split_minutes = 0;
    QString record_format = "mkv";
    bool shuffle = false;
    QString repeat_mode = "off";

    // Applies the exact reference clamp/validation rules; returns a new
    // normalized copy (never throws).
    PlayerSettings validated() const;
};

// Session payload persisted beside settings.json (separate file, Linux
// format). The three *_dir/playlist extras are Windows UI state kept here so
// settings.json stays byte-shape-identical to the reference.
struct SessionState {
    QStringList playlist;
    QString current;
    double position = 0.0;
    int volume = 100;
    bool muted = false;
    double rate = 1.0;
    int width = 0;
    int height = 0;
    int x = 0;
    int y = 0;
    // Windows UI extras:
    QString snapshot_dir;
    QString library_dir;
    QString last_playlist;
};

// UI-side aggregate: reference player settings plus Windows-only UI state
// (persisted inside session.json, never inside settings.json).
struct AppSettings {
    PlayerSettings player;
    QString snapshot_dir;
    QString library_dir;
    QString last_playlist;
};

class SettingsStore {
public:
    SettingsStore(QString settings_path, QString session_path);
    explicit SettingsStore(QString settings_path);  // session ops disabled

    PlayerSettings load() const;
    void save(const PlayerSettings& s) const;

    SessionState load_session() const;
    void save_session(const SessionState& s) const;

private:
    QString path_;
    QString session_path_;
};

// Config directory (beside the exe for portability).
QString app_config_dir();

}  // namespace mpcasu

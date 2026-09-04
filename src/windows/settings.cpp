// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Full port of casu/settings.py (validated atomic settings) + the Linux
// player's session.json handling. See header for parity notes.
#include "settings.hpp"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStandardPaths>
#include <QTemporaryFile>
#include <charconv>
#include <filesystem>
#include <cstdio>
#include <cmath>

#ifdef _WIN32
#include <io.h>
#include <process.h>
#else
#include <unistd.h>
#endif

namespace mpcasu {

namespace {





QString expand_user(const QString& value) {
    if (value.startsWith(u'~')) {
        const QString home = QDir::homePath();
        if (value == QStringLiteral("~")) return home;
        if (value.startsWith(QStringLiteral("~/")))
            return home + value.mid(1);
    }
    return value;
}

bool text_ok(const QString& value) {
    return !value.contains(QChar(u'\0')) &&
           value.toUtf8().size() <= kMaxSettingTextBytes;
}

int clamp_int(qint64 v, int lo, int hi) {
    return static_cast<int>(std::max<qint64>(lo, std::min<qint64>(hi, v)));
}

QStringList coerce_string_list(const QJsonValue& v, bool* ok_list) {
    QStringList out;
    if (!v.isArray()) {
        *ok_list = false;
        return out;
    }
    *ok_list = true;
    for (const QJsonValue& item : v.toArray())
        out.append(item.toString());
    return out;
}

// Byte-compatible json.dumps escaping (ensure_ascii=False).
void py_json_escape(const QString& s, QString* out) {
    out->append(u'"');
    for (const QChar c : s) {
        switch (c.unicode()) {
            case u'"': out->append(QStringLiteral("\\\"")); break;
            case u'\\': out->append(QStringLiteral("\\\\")); break;
            case u'\b': out->append(QStringLiteral("\\b")); break;
            case u'\f': out->append(QStringLiteral("\\f")); break;
            case u'\n': out->append(QStringLiteral("\\n")); break;
            case u'\r': out->append(QStringLiteral("\\r")); break;
            case u'\t': out->append(QStringLiteral("\\t")); break;
            default:
                if (c.unicode() < 0x20) {
                    out->append(QStringLiteral("\\u%1")
                                    .arg(uint(c.unicode()), 4, 16, u'0'));
                } else {
                    out->append(c);
                }
        }
    }
    out->append(u'"');
}

// Shortest round-trip float text with Python's ".0" suffix convention.
QString py_float_text(double d) {
    if (!std::isfinite(d)) return QStringLiteral("null");
    char tmp[64];
    const auto res = std::to_chars(tmp, tmp + sizeof(tmp), d);
    QString s = QString::fromLatin1(tmp, static_cast<qsizetype>(res.ptr - tmp));
    if (!s.contains(u'.') && !s.contains(u'e') && !s.contains(u'E'))
        s += QStringLiteral(".0");
    return s;
}


// Atomic durable write mirroring atomic_write_bytes: unique temp file in
// the target directory, flush + fsync, then rename over the target.
// NOTE: deliberately NOT using QTemporaryFile here — its delete-on-close
// handling makes subsequent renames fail under Wine.
bool atomic_write(const QString& path, const QByteArray& payload,
                  qint64 max_bytes) {
    if (payload.size() > max_bytes) return false;
    const QFileInfo info(path);
    const QDir dir = info.absoluteDir();
    if (!dir.exists()) QDir().mkpath(dir.absolutePath());
    QString tmp_path;
    QFile tmp;
    for (int attempt = 0; attempt < 64 && !tmp.isOpen(); ++attempt) {
        tmp_path = dir.absoluteFilePath(
            QStringLiteral(".%1.tmp%2-%3")
                .arg(info.fileName())
                .arg(::getpid())
                .arg(attempt));
        tmp.setFileName(tmp_path);
        if (!tmp.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            tmp.setFileName(QString());
        }
    }
    if (!tmp.isOpen()) return false;
    const qint64 written = tmp.write(payload);
    if (written != payload.size()) {
        tmp.close();
        QFile::remove(tmp_path);
        return false;
    }
    tmp.flush();
#ifdef _WIN32
    ::_commit(static_cast<int>(tmp.handle()));
#else
    ::fsync(static_cast<int>(tmp.handle()));
#endif
    tmp.close();
    std::error_code ec;
    std::filesystem::rename(tmp_path.toStdString(), path.toStdString(), ec);
    if (ec) {
        // Replace-existing fallback (Windows rename does not overwrite).
        QFile::remove(path);
        ec.clear();
        std::filesystem::rename(tmp_path.toStdString(), path.toStdString(),
                                ec);
        if (ec) {
            QFile::remove(tmp_path);
            return false;
        }
    }
    return true;
}

}  // namespace

PlayerSettings PlayerSettings::validated() const {
    PlayerSettings out;
    out.volume = clamp_int(volume, 0, 200);
    out.muted = muted;
    double r = rate;
    if (!std::isfinite(r)) r = 1.0;
    out.rate = std::max(0.25, std::min(4.0, r));
    QString device = audio_device;
    if (device.isEmpty() || !text_ok(device)) device.clear();
    out.audio_device = device;

    QStringList folders;
    bool folders_ok = watched_folders.size() <= kMaxWatchedFolders;
    for (const QString& raw : watched_folders) {
        const QString value = expand_user(raw);
        if (!text_ok(value)) {
            folders_ok = false;
            break;
        }
        folders.append(value);
    }
    if (!folders_ok) folders.clear();
    out.watched_folders = folders;

    out.ytdlp_consent = ytdlp_consent;
    static const QStringList viz_ok = {"waveform", "off"};
    out.visualizer =
        viz_ok.contains(visualizer) ? visualizer : QStringLiteral("waveform");
    out.resume_playback = resume_playback;
    out.cache_limit_mib = clamp_int(cache_limit_mib, 0, 65536);
    QString recordings = recordings_dir;
    if (!recordings.isEmpty() && !text_ok(recordings)) recordings.clear();
    out.recordings_dir = recordings;
    out.record_split_minutes = clamp_int(record_split_minutes, 0, 24 * 60);
    QString fmt = record_format.toLower();
    static const QStringList fmts = {"mkv", "mp4", "ts", "webm",
                                     "ogg", "mp3", "flac", "wav"};
    if (!fmts.contains(fmt)) fmt = QStringLiteral("mkv");
    out.record_format = fmt;
    out.shuffle = shuffle;
    static const QStringList repeats = {"off", "all", "one"};
    out.repeat_mode = repeats.contains(repeat_mode)
                          ? repeat_mode
                          : QStringLiteral("off");
    return out;
}

SettingsStore::SettingsStore(QString settings_path)
    : path_(std::move(settings_path)) {}

SettingsStore::SettingsStore(QString settings_path, QString session_path)
    : path_(std::move(settings_path)), session_path_(std::move(session_path)) {}

PlayerSettings SettingsStore::load() const {
    PlayerSettings defaults;
    QFile f(path_);
    if (!f.open(QIODevice::ReadOnly)) return defaults.validated();
    const QByteArray raw = f.read(kMaxSettingsBytes + 1);
    f.close();
    if (raw.size() > kMaxSettingsBytes) return defaults.validated();
    QJsonParseError perr{};
    const QJsonDocument doc = QJsonDocument::fromJson(raw, &perr);
    if (perr.error != QJsonParseError::NoError || !doc.isObject())
        return defaults.validated();
    const QJsonObject root = doc.object();
    if (root.value("version").toInt(-1) != 1) return defaults.validated();
    if (!root.value("player").isObject()) return defaults.validated();
    const QJsonObject o = root.value("player").toObject();

    PlayerSettings s;
    s.volume = o.value("volume").toInt(defaults.volume);
    s.muted = o.value("muted").toBool(false);
    s.rate = o.value("rate").toDouble(defaults.rate);
    s.audio_device = o.value("audio_device").toString();
    bool list_ok = false;
    s.watched_folders = coerce_string_list(o.value("watched_folders"), &list_ok);
    if (!list_ok) s.watched_folders.clear();
    s.ytdlp_consent = o.value("ytdlp_consent").toBool(false);
    s.visualizer = o.value("visualizer").toString(defaults.visualizer);
    s.resume_playback = o.value("resume_playback").toBool(true);
    s.cache_limit_mib =
        o.value("cache_limit_mib").toInt(defaults.cache_limit_mib);
    s.recordings_dir = o.value("recordings_dir").toString();
    s.record_split_minutes =
        o.value("record_split_minutes").toInt(defaults.record_split_minutes);
    s.record_format = o.value("record_format").toString(defaults.record_format);
    s.shuffle = o.value("shuffle").toBool(false);
    s.repeat_mode = o.value("repeat_mode").toString(defaults.repeat_mode);
    return s.validated();
}

void SettingsStore::save(const PlayerSettings& settings) const {
    const PlayerSettings v = settings.validated();
    // Direct serialization in the reference dataclass field order with
    // json.dumps(indent=2, ensure_ascii=False) formatting.
    QString body;
    body += QStringLiteral("{\n"
                           "  \"version\": 1,\n"
                           "  \"player\": {\n");
    auto scalar_line = [&body](const char* key, const QString& text,
                               bool last) {
        body += QStringLiteral("    \"%1\": %2%3")
                    .arg(QString::fromLatin1(key), text,
                         last ? QStringLiteral("\n") : QStringLiteral(",\n"));
    };
    auto string_line = [&body, &scalar_line](const char* key,
                                             const QString& value, bool last) {
        QString encoded;
        py_json_escape(value, &encoded);
        scalar_line(key, encoded, last);
    };

    scalar_line("volume", QString::number(v.volume), false);
    scalar_line("muted",
                v.muted ? QStringLiteral("true") : QStringLiteral("false"),
                false);
    scalar_line("rate", py_float_text(v.rate), false);
    string_line("audio_device", v.audio_device, false);
    if (v.watched_folders.isEmpty()) {
        scalar_line("watched_folders", QStringLiteral("[]"), false);
    } else {
        body += QStringLiteral("    \"watched_folders\": [\n");
        for (int i = 0; i < v.watched_folders.size(); ++i) {
            QString encoded;
            py_json_escape(v.watched_folders.at(i), &encoded);
            body += QStringLiteral("      %1%2\n")
                        .arg(encoded,
                             i + 1 == v.watched_folders.size()
                                 ? QString()
                                 : QStringLiteral(","));
        }
        body += QStringLiteral("    ],\n");
    }
    scalar_line("ytdlp_consent",
                v.ytdlp_consent ? QStringLiteral("true")
                                : QStringLiteral("false"),
                false);
    string_line("visualizer", v.visualizer, false);
    scalar_line("resume_playback",
                v.resume_playback ? QStringLiteral("true")
                                  : QStringLiteral("false"),
                false);
    scalar_line("cache_limit_mib", QString::number(v.cache_limit_mib), false);
    string_line("recordings_dir", v.recordings_dir, false);
    scalar_line("record_split_minutes", QString::number(v.record_split_minutes),
                false);
    string_line("record_format", v.record_format, false);
    scalar_line(
        "shuffle",
        v.shuffle ? QStringLiteral("true") : QStringLiteral("false"), false);
    string_line("repeat_mode", v.repeat_mode, true);
    body += QStringLiteral("  }\n}\n");
    atomic_write(path_, body.toUtf8(), kMaxSettingsBytes);
}

SessionState SettingsStore::load_session() const {
    SessionState state;
    if (session_path_.isEmpty()) return state;
    QFile f(session_path_);
    if (!f.open(QIODevice::ReadOnly)) return state;
    const QByteArray raw = f.read(kMaxSettingsBytes + 1);
    f.close();
    if (raw.size() > kMaxSettingsBytes) return state;
    QJsonParseError perr{};
    const QJsonDocument doc = QJsonDocument::fromJson(raw, &perr);
    if (perr.error != QJsonParseError::NoError || !doc.isObject()) return state;
    const QJsonObject o = doc.object();
    for (const QJsonValue& v : o.value("playlist").toArray())
        state.playlist.append(v.toString());
    state.current = o.value("current").toString();
    state.position = std::max(0.0, o.value("position").toDouble(0.0));
    state.volume = clamp_int(o.value("volume").toInt(100), 0, 200);
    state.muted = o.value("muted").toBool(false);
    state.rate = o.value("rate").toDouble(1.0);
    const QString geometry = o.value("geometry").toString();
    if (!geometry.isEmpty()) {
        const QStringList plus_parts = geometry.split(u'+');
        if (plus_parts.size() >= 3) {
            const QStringList wh = plus_parts.at(0).split(u'x');
            if (wh.size() == 2) {
                state.width = wh.at(0).toInt();
                state.height = wh.at(1).toInt();
                state.x = plus_parts.at(1).toInt();
                state.y = plus_parts.at(2).toInt();
            }
        }
    }
    state.snapshot_dir = o.value("snapshot_dir").toString();
    state.library_dir = o.value("library_dir").toString();
    state.last_playlist = o.value("last_playlist").toString();
    return state;
}

void SettingsStore::save_session(const SessionState& s) const {
    if (session_path_.isEmpty()) return;
    QJsonObject o;
    QJsonArray playlist;
    for (const QString& item : s.playlist) playlist.append(item);
    o["playlist"] = playlist;
    o["volume"] = clamp_int(s.volume, 0, 200);
    o["muted"] = s.muted;
    o["rate"] = std::isfinite(s.rate)
                    ? std::max(0.25, std::min(4.0, s.rate))
                    : 1.0;
    o["current"] = s.current.isEmpty() ? QJsonValue(QJsonValue::Null)
                                       : QJsonValue(s.current);
    o["position"] = s.position;
    o["geometry"] =
        QStringLiteral("%1x%2+%3+%4")
            .arg(s.width)
            .arg(s.height)
            .arg(s.x)
            .arg(s.y);
    o["snapshot_dir"] = s.snapshot_dir;
    o["library_dir"] = s.library_dir;
    o["last_playlist"] = s.last_playlist;
    const QByteArray payload =
        QJsonDocument(o).toJson(QJsonDocument::Indented) + "\n";
    atomic_write(session_path_, payload, kMaxSettingsBytes);
}

QString app_config_dir() {
    // Reference (~/.config/mpcasu) Windows parity WITHOUT admin rights:
    // %APPDATA%\Lino-Codec\MPCASU via QStandardPaths. The previous
    // exe-relative ./config folder broke under Program Files (read-only for
    // normal users — the app then only ran elevated).
    QString base = QStandardPaths::writableLocation(
        QStandardPaths::AppLocalDataLocation);
    if (base.isEmpty()) base = QDir::homePath() + "/.local/share/mpcasu";
    QDir dir(base);
    if (!dir.exists()) dir.mkpath(".");

    // One-time migration from the legacy exe-relative ./config.
    const QString legacy =
        QCoreApplication::applicationDirPath() + "/config";
    const QString migrated_marker = dir.absoluteFilePath(".migrated-legacy");
    if (!QFileInfo::exists(migrated_marker) && QDir(legacy).exists()) {
        const QFileInfoList entries =
            QDir(legacy).entryInfoList(QDir::Files);
        bool copied_any = false;
        for (const QFileInfo& fi : entries) {
            // NEVER migrate lock files: a stale admin-owned mpcasu.lock in
            // the old folder made non-elevated starts report "already
            // running" although nothing was running.
            if (fi.fileName().endsWith(".lock")) continue;
            const QString target = dir.absoluteFilePath(fi.fileName());
            if (!QFileInfo::exists(target)) {
                QFile::copy(fi.absoluteFilePath(), target);
                copied_any = true;
            }
        }
        QFile marker(migrated_marker);
        marker.open(QIODevice::WriteOnly);
        marker.write(copied_any ? "1" : "0");
    }
    return dir.absolutePath();
}

}  // namespace mpcasu

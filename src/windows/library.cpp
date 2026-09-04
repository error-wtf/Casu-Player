// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Functional port of casu/library.py (see header). Storage remains a JSON
// document; the file format gains an object envelope {entries, bookmarks,
// playlists, next_bookmark_id} while legacy bare-array files still load.
#include "library.hpp"

#include "casu/media/tags.hpp"

#include <QDateTime>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <cmath>

#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif

namespace mpcasu {

namespace {

constexpr qint64 kNsPerMs = 1'000'000;

QString casefold(const QString& s) { return s.toCaseFolded(); }

QJsonObject entry_json(const LibraryEntry& e) {
    QJsonObject o;
    o["path"] = e.path;
    o["title"] = e.title;
    o["kind"] = e.kind;
    o["added_ms"] = e.added_ms;
    o["favorite"] = e.favorite;
    o["size_bytes"] = e.size_bytes;
    o["modified_ns"] = e.modified_ns;
    o["resume_seconds"] = e.resume_seconds;
    if (e.has_duration) o["duration_seconds"] = e.duration_seconds;
    o["last_played_ms"] = e.last_played_ms;
    o["last_seen_ms"] = e.last_seen_ms;
    if (!e.metadata.isEmpty()) {
        QJsonObject meta;
        for (auto it = e.metadata.constBegin(); it != e.metadata.constEnd(); ++it)
            meta.insert(it.key(), it.value());
        o["metadata"] = meta;
    }
    return o;
}

LibraryEntry entry_from_json(const QJsonObject& o) {
    LibraryEntry e;
    e.path = o.value("path").toString();
    e.title = o.value("title").toString();
    e.kind = o.value("kind").toString();
    e.added_ms = static_cast<qint64>(o.value("added_ms").toDouble(0));
    e.favorite = o.value("favorite").toBool(false);
    e.size_bytes = static_cast<qint64>(o.value("size_bytes").toDouble(0));
    e.modified_ns = static_cast<qint64>(o.value("modified_ns").toDouble(0));
    e.resume_seconds = o.value("resume_seconds").toDouble(0);
    if (!o.contains("duration_seconds") ||
        o.value("duration_seconds").isNull()) {
        e.has_duration = false;
    } else {
        e.has_duration = true;
        e.duration_seconds = o.value("duration_seconds").toDouble(0);
    }
    e.last_played_ms =
        static_cast<qint64>(o.value("last_played_ms").toDouble(0));
    e.last_seen_ms = static_cast<qint64>(o.value("last_seen_ms").toDouble(0));
    if (o.value("metadata").isObject()) {
        const QJsonObject m = o.value("metadata").toObject();
        for (auto it = m.begin(); it != m.end(); ++it)
            e.metadata.insert(it.key(), it.value().toString());
    }
    return e;
}

bool atomic_write(const QString& path, const QByteArray& payload) {
    const QFileInfo info(path);
    const QDir dir = info.absoluteDir();
    if (!dir.exists()) QDir().mkpath(dir.absolutePath());
    QFile tmp(dir.filePath(info.fileName() + QStringLiteral(".tmp")));
    if (!tmp.open(QIODevice::WriteOnly | QIODevice::Truncate)) return false;
    tmp.write(payload);
    tmp.flush();
#ifdef _WIN32
    ::_commit(static_cast<int>(tmp.handle()));
#else
    ::fsync(static_cast<int>(tmp.handle()));
#endif
    tmp.close();
    QFile::remove(path);
    std::error_code ec;
    std::filesystem::rename(tmp.fileName().toStdString(),
                            path.toStdString(), ec);
    if (ec) {
        QFile::remove(tmp.fileName());
        return false;
    }
    return true;
}

}  // namespace

QStringList media_extensions() {
    static const QStringList exts = {
        ".mp3", ".mp4", ".m4a", ".m4v", ".mov", ".mkv", ".webm", ".flac",
        ".wav", ".ogg", ".opus", ".aac", ".aiff", ".alac", ".wma", ".mpg",
        ".mpeg", ".ts", ".m2ts", ".avi", ".casu", ".mp5"};
    return exts;
}

QString library_group_key(const QString& value) {
    const QString trimmed = value.trimmed();
    return trimmed.isEmpty() ? QStringLiteral("(unknown)") : trimmed;
}

void MediaLibrary::load() {
    entries_.clear();
    bookmarks_.clear();
    playlists_.clear();
    next_bookmark_id_ = 1;
    QFile f(path_);
    if (!f.open(QIODevice::ReadOnly)) return;
    const QByteArray raw = f.read(kMaxLibraryMetadataBytes * 64);
    f.close();
    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(raw, &err);
    if (err.error != QJsonParseError::NoError) return;
    if (doc.isArray()) {
        // Legacy bare-array library file.
        for (const QJsonValue& v : doc.array()) {
            LibraryEntry e = entry_from_json(v.toObject());
            if (!e.path.isEmpty()) entries_.append(e);
        }
        return;
    }
    if (!doc.isObject()) return;
    const QJsonObject root = doc.object();
    for (const QJsonValue& v : root.value("entries").toArray())
        entries_.append(entry_from_json(v.toObject()));
    for (const QJsonValue& v : root.value("bookmarks").toArray()) {
        const QJsonObject b = v.toObject();
        MediaBookmark bm;
        bm.id = b.value("id").toInt(0);
        bm.path = b.value("media_path").toString();
        bm.position_seconds = b.value("position_seconds").toDouble(0);
        bm.label = b.value("label").toString();
        bookmarks_.append(bm);
        next_bookmark_id_ = std::max(next_bookmark_id_, bm.id + 1);
    }
    if (root.value("playlists").isObject()) {
        const QJsonObject pls = root.value("playlists").toObject();
        for (auto it = pls.begin(); it != pls.end(); ++it) {
            SavedPlaylist sp;
            sp.name = it.key();
            for (const QJsonValue& p : it.value().toArray())
                sp.paths.append(p.toString());
            playlists_.append(sp);
        }
    }
}

void MediaLibrary::save() {
    QJsonObject root;
    QJsonArray arr;
    for (const LibraryEntry& e : entries_) arr.append(entry_json(e));
    root["entries"] = arr;
    QJsonArray bms;
    for (const MediaBookmark& b : bookmarks_) {
        QJsonObject b_o;
        b_o["id"] = b.id;
        b_o["media_path"] = b.path;
        b_o["position_seconds"] = b.position_seconds;
        b_o["label"] = b.label;
        bms.append(b_o);
    }
    root["bookmarks"] = bms;
    QJsonObject pls;
    for (const SavedPlaylist& sp : playlists_) {
        QJsonArray items;
        for (const QString& p : sp.paths) items.append(p);
        pls[sp.name] = items;
    }
    root["playlists"] = pls;
    root["next_bookmark_id"] = next_bookmark_id_;
    atomic_write(path_,
                 QJsonDocument(root).toJson(QJsonDocument::Indented));
}

LibraryEntry* MediaLibrary::find(const QString& path) {
    for (LibraryEntry& e : entries_)
        if (e.path == path) return &e;
    return nullptr;
}

LibraryEntry* MediaLibrary::ensure(const QString& path) {
    if (LibraryEntry* existing = find(path)) return existing;
    add(path, QFileInfo(path).fileName());
    return find(path);
}

LibraryEntry* MediaLibrary::upsert(const QString& path,
                                   std::optional<double> duration_seconds) {
    const QFileInfo info(path);
    LibraryEntry* entry = ensure(info.absoluteFilePath());
    if (!entry) return nullptr;
    entry->size_bytes = info.size();
    entry->modified_ns = info.lastModified().toMSecsSinceEpoch() * kNsPerMs;
    entry->last_seen_ms = QDateTime::currentMSecsSinceEpoch();
    if (duration_seconds.has_value()) {
        entry->has_duration = true;
        entry->duration_seconds = *duration_seconds;
    }
    // Always probe for embedded tags (ID3/Vorbis/MP4) so that real tags
    // from inside the file replace any stale filename-derived metadata.
    if (!path.contains(QStringLiteral("://"))) {
        try {
            const auto tags = casu::media::metadata_for(
                entry->path.toStdString());
            if (!tags.empty()) {
                entry->metadata.clear();
                for (const auto& [k, v] : tags)
                    entry->metadata.insert(
                        QString::fromStdString(k),
                        QString::fromStdString(v));
            }
        } catch (const std::exception&) {
        }
    }
    // Also update duration from probe if not yet set.
    if (!entry->has_duration) {
        const auto dur_it = entry->metadata.find("duration");
        if (dur_it != entry->metadata.end()) {
            bool ok = false;
            double d = dur_it->toDouble(&ok);
            if (ok && d > 0.0) {
                entry->has_duration = true;
                entry->duration_seconds = d;
            }
        }
    }
    save();
    return entry;
}

void MediaLibrary::add(const QString& path, const QString& title) {
    for (const LibraryEntry& e : entries_)
        if (e.path == path) return;  // already present
    LibraryEntry e;
    e.path = path;
    e.title = title.isEmpty() ? QFileInfo(path).fileName() : title;
    e.added_ms = QDateTime::currentMSecsSinceEpoch();
    e.last_seen_ms = e.added_ms;
    if (path.contains(QStringLiteral("://")))
        e.kind = QStringLiteral("stream");
    else if (path.toLower().endsWith(QStringLiteral(".m3u")) ||
             path.toLower().endsWith(QStringLiteral(".pls")))
        e.kind = QStringLiteral("playlist");
    else
        e.kind = QStringLiteral("media");
    // Best-effort tag probe for fresh entries (reference upsert behavior).
    if (!path.contains(QStringLiteral("://"))) {
        try {
            const auto tags = casu::media::metadata_for(path.toStdString());
            for (const auto& [k, v] : tags)
                e.metadata.insert(QString::fromStdString(k),
                                  QString::fromStdString(v));
        } catch (const std::exception&) {
        }
    }
    entries_.append(e);
    save();
}

void MediaLibrary::remove(int index) {
    if (index < 0 || index >= entries_.size()) return;
    entries_.removeAt(index);
    save();
}

void MediaLibrary::clear() {
    entries_.clear();
    save();
}

void MediaLibrary::set_favorite(const QString& path, bool favorite) {
    if (LibraryEntry* e = find(path)) {
        e->favorite = favorite;
        save();
    }
}

int MediaLibrary::index_of(const QString& path) const {
    for (int i = 0; i < entries_.size(); ++i)
        if (entries_[i].path == path) return i;
    return -1;
}

QVector<LibraryEntry*> MediaLibrary::scan(const QStringList& roots) {
    const QStringList exts = media_extensions();
    QVector<QString> candidates;
    for (const QString& raw_root : roots) {
        const QDir base(raw_root);
        if (!base.exists()) continue;
        QDirIterator it(base.absolutePath(), QDir::Files,
                        QDirIterator::Subdirectories);
        while (it.hasNext() &&
               candidates.size() < kMaxLibraryScanFiles) {
            const QString candidate = it.next();
            const QString name = QFileInfo(candidate).fileName();
            if (name == QFileInfo(path_).fileName() ||
                name == QFileInfo(path_).fileName() + "-wal" ||
                name == QFileInfo(path_).fileName() + "-shm")
                continue;
            const QString suffix =
                u'.' + name.section(u'.', -1).toLower();
            if (!exts.contains(suffix)) continue;
            candidates.append(candidate);
        }
    }
    QVector<LibraryEntry*> found;
    for (const QString& candidate : candidates) {
        if (found.size() >= kMaxLibraryScanFiles) break;
        if (LibraryEntry* e = upsert(candidate)) found.append(e);
    }
    save();
    return found;
}

void MediaLibrary::record_progress(const QString& path, double seconds,
                                   std::optional<double> duration_seconds) {
    LibraryEntry* entry = ensure(QFileInfo(path).absoluteFilePath());
    if (!entry) return;
    double position = seconds;
    if (!std::isfinite(position)) return;  // fail-closed like ValueError
    position = std::max(0.0, position);
    const double duration =
        duration_seconds.value_or(entry->has_duration ? entry->duration_seconds
                                                      : 0.0);
    if (duration > 0.0 &&
        position >= std::max(0.0, duration - 5.0))
        position = 0.0;  // reference dur-5 resume clamp
    if (duration_seconds.has_value()) {
        entry->has_duration = true;
        entry->duration_seconds = *duration_seconds;
    }
    entry->resume_seconds = position;
    entry->last_played_ms = QDateTime::currentMSecsSinceEpoch();
    save();
}

QVector<LibraryEntry> MediaLibrary::search(const QString& query,
                                           bool favorites_only,
                                           int limit) const {
    const int maximum = std::max(1, std::min(5000, limit));
    const QString needle = query.trimmed().toCaseFolded();
    struct Scored {
        const LibraryEntry* entry;
        qint64 played;
        QString path;
    };
    QVector<Scored> matches;
    for (const LibraryEntry& e : entries_) {
        if (favorites_only && !e.favorite) continue;
        if (!needle.isEmpty()) {
            bool hit = casefold(e.path).contains(needle);
            if (!hit)
                for (auto it = e.metadata.constBegin();
                     it != e.metadata.constEnd() && !hit; ++it)
                    hit = casefold(it.value()).contains(needle);
            if (!hit) continue;
        }
        matches.append({&e, e.last_played_ms, e.path});
    }
    std::stable_sort(matches.begin(), matches.end(),
                     [](const Scored& a, const Scored& b) {
                         if (a.played != b.played)
                             return a.played > b.played;
                         return a.path < b.path;
                     });
    QVector<LibraryEntry> out;
    for (int i = 0; i < matches.size() && i < maximum; ++i)
        out.append(*matches[i].entry);
    return out;
}

int MediaLibrary::add_bookmark(const QString& path, double position_seconds,
                               const QString& label) {
    double position = position_seconds;
    if (!std::isfinite(position) || position < 0) return -1;
    QString title = label.trimmed();
    if (title.isEmpty()) {
        // Python f"{position:.1f} s" rounds half-to-even.
        const double scaled = std::nearbyint(position * 10.0) / 10.0;
        title = QString::number(scaled, 'f', 1) + QStringLiteral(" s");
    }
    if (title.contains(u'\0') || title.toUtf8().size() > 255) return -1;
    for (MediaBookmark& b : bookmarks_) {
        if (b.path == path &&
            std::abs(b.position_seconds - position) < 1e-9) {
            b.label = title;  // upsert semantics
            save();
            return b.id;
        }
    }
    MediaBookmark bookmark;
    bookmark.id = next_bookmark_id_++;
    bookmark.path = path;
    bookmark.position_seconds = position;
    bookmark.label = title;
    bookmarks_.append(bookmark);
    save();
    return bookmark.id;
}

QVector<MediaBookmark> MediaLibrary::bookmarks(const QString& path,
                                               int limit) const {
    const int maximum = std::max(1, std::min(5000, limit));
    QVector<MediaBookmark> out;
    QVector<MediaBookmark> sorted = bookmarks_;
    std::stable_sort(sorted.begin(), sorted.end(),
                     [](const MediaBookmark& a, const MediaBookmark& b) {
                         if (a.position_seconds != b.position_seconds)
                             return a.position_seconds < b.position_seconds;
                         return a.id < b.id;
                     });
    for (const MediaBookmark& b : sorted) {
        if (b.path != path) continue;
        out.append(b);
        if (out.size() >= maximum) break;
    }
    return out;
}

void MediaLibrary::remove_bookmark(int identifier) {
    for (int i = 0; i < bookmarks_.size(); ++i) {
        if (bookmarks_[i].id == identifier) {
            bookmarks_.removeAt(i);
            save();
            return;
        }
    }
}

void MediaLibrary::save_playlist(const QString& name,
                                 const QStringList& paths) {
    const QString title = name.trimmed();
    if (title.isEmpty()) return;
    if (title.contains(u'\0') || title.toUtf8().size() > kMaxPlaylistNameBytes)
        return;
    for (SavedPlaylist& sp : playlists_) {
        if (sp.name == title) {
            sp.paths = paths;
            save();
            return;
        }
    }
    SavedPlaylist sp;
    sp.name = title;
    sp.paths = paths;
    playlists_.append(sp);
    save();
}

QStringList MediaLibrary::playlist_names() const {
    QStringList names;
    for (const SavedPlaylist& sp : playlists_) names.append(sp.name);
    names.sort(Qt::CaseInsensitive);
    return names;
}

QStringList MediaLibrary::load_playlist(const QString& name) const {
    for (const SavedPlaylist& sp : playlists_)
        if (sp.name == name) return sp.paths;
    return {};
}

// --- per-media playback preferences (Linux parity, now clamped+atomic) ------

void MediaLibrary::load_prefs() const {
    if (prefs_loaded_) return;
    prefs_loaded_ = true;
    QFile f(path_ + ".prefs.json");
    if (!f.open(QIODevice::ReadOnly)) return;
    const QJsonDocument doc = QJsonDocument::fromJson(f.readAll());
    if (!doc.isObject()) return;
    const QJsonObject root = doc.object();
    for (auto it = root.begin(); it != root.end(); ++it) {
        const QJsonObject o = it.value().toObject();
        Prefs p;
        p.audio_track = o.value("audio_track").toInt(-1);
        p.video_track = o.value("video_track").toInt(-1);
        p.subtitle_track = o.value("subtitle_track").toInt(-1);
        p.audio_delay_ms = o.value("audio_delay_ms").toDouble(0.0);
        p.subtitle_delay_ms = o.value("subtitle_delay_ms").toDouble(0.0);
        prefs_.insert(it.key(), p);
    }
}

PlaybackPreferences MediaLibrary::playback_preferences(
    const QString& path) const {
    load_prefs();
    const Prefs p = prefs_.value(path);
    PlaybackPreferences out;
    out.audio_track = p.audio_track;
    out.video_track = p.video_track;
    out.subtitle_track = p.subtitle_track;
    out.audio_delay_ms = p.audio_delay_ms;
    out.subtitle_delay_ms = p.subtitle_delay_ms;
    return out;
}

void MediaLibrary::set_playback_preferences(
    const QString& path, const PlaybackPreferences& prefs) {
    load_prefs();
    Prefs p;
    p.audio_track = prefs.audio_track;
    p.video_track = prefs.video_track;
    p.subtitle_track = prefs.subtitle_track;
    // Reference clamp: delays are bounded to ±5000 ms.
    p.audio_delay_ms =
        std::max(-5000.0, std::min(5000.0, prefs.audio_delay_ms));
    p.subtitle_delay_ms =
        std::max(-5000.0, std::min(5000.0, prefs.subtitle_delay_ms));
    prefs_.insert(path, p);
    QJsonObject root;
    for (auto it = prefs_.begin(); it != prefs_.end(); ++it) {
        QJsonObject o;
        o["audio_track"] = it->audio_track;
        o["video_track"] = it->video_track;
        o["subtitle_track"] = it->subtitle_track;
        o["audio_delay_ms"] = it->audio_delay_ms;
        o["subtitle_delay_ms"] = it->subtitle_delay_ms;
        root[it.key()] = o;
    }
    atomic_write(path_ + ".prefs.json",
                 QJsonDocument(root).toJson(QJsonDocument::Indented));
}

}  // namespace mpcasu

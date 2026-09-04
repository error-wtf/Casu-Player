// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Media library — functional port of casu/library.py on the Windows JSON
// store: complete resume/identity data model (size_bytes/modified_ns/
// resume_seconds/duration_seconds/last_played/last_seen + metadata),
// bookmarks and named saved playlists, parity media-extension set with the
// 100k scan cap, case-insensitive search/grouping and ±5000 ms delay clamps
// with atomic preference writes.
#pragma once
#include <QMap>
#include <QString>
#include <QStringList>
#include <QVector>
#include <optional>

namespace mpcasu {

constexpr int kMaxLibraryScanFiles = 100'000;
constexpr qint64 kMaxLibraryMetadataBytes = 1024 * 1024;
constexpr int kMaxPlaylistNameBytes = 255;

struct LibraryEntry {
    QString path;
    QString title;
    QString kind;  // "video" | "audio" | "playlist" | "stream" | "media"
    qint64 added_ms = 0;
    bool favorite = false;
    // casu/library.py identity/resume model:
    qint64 size_bytes = 0;
    qint64 modified_ns = 0;
    double resume_seconds = 0.0;
    bool has_duration = false;
    double duration_seconds = 0.0;
    qint64 last_played_ms = 0;  // 0 == never played
    qint64 last_seen_ms = 0;
    QMap<QString, QString> metadata;
};

struct MediaBookmark {
    int id = 0;
    QString path;
    double position_seconds = 0.0;
    QString label;
};

struct SavedPlaylist {
    QString name;
    QStringList paths;
};

// Parity extension whitelist (casu/library.py MEDIA_EXTENSIONS). Deliberately
// excludes .m3u/.pls.
QStringList media_extensions();

// Grouping helper parity: trimmed value, "(unknown)" when empty; comparisons
// group by casefolded key while keeping a representative display value.
QString library_group_key(const QString& value);

// casu/library.py PlaybackPreferences: per-media track and A/V delay recall.
struct PlaybackPreferences {
    int audio_track = -1;      // -1 == None
    int video_track = -1;
    int subtitle_track = -1;
    double audio_delay_ms = 0.0;
    double subtitle_delay_ms = 0.0;
};

class MediaLibrary {
public:
    explicit MediaLibrary(QString path) : path_(std::move(path)) {}

    void load();
    void save();
    void add(const QString& path, const QString& title);
    void remove(int index);
    void clear();
    void set_favorite(const QString& path, bool favorite);
    int index_of(const QString& path) const;
    const QVector<LibraryEntry>& entries() const { return entries_; }

    PlaybackPreferences playback_preferences(const QString& path) const;
    void set_playback_preferences(const QString& path,
                                  const PlaybackPreferences& prefs);

    // --- casu/library.py surface -------------------------------------------
    // Upsert with stat()/last_seen refresh; keeps existing metadata unless it
    // is missing, then probes tags best-effort.
    LibraryEntry* upsert(const QString& path,
                         std::optional<double> duration_seconds = std::nullopt);
    // Recursive scan with the parity extension filter and 100k cap.
    QVector<LibraryEntry*> scan(const QStringList& roots);
    void record_progress(const QString& path, double seconds,
                         std::optional<double> duration_seconds = std::nullopt);
    QVector<LibraryEntry> search(const QString& query = {},
                                 bool favorites_only = false,
                                 int limit = 500) const;

    int add_bookmark(const QString& path, double position_seconds,
                     const QString& label = {});
    QVector<MediaBookmark> bookmarks(const QString& path,
                                     int limit = 500) const;
    void remove_bookmark(int identifier);

    void save_playlist(const QString& name, const QStringList& paths);
    QStringList playlist_names() const;
    QStringList load_playlist(const QString& name) const;

private:
    struct Prefs {
        int audio_track = -1;
        int video_track = -1;
        int subtitle_track = -1;
        double audio_delay_ms = 0.0;
        double subtitle_delay_ms = 0.0;
    };

    void load_prefs() const;
    void save_prefs() const;
    LibraryEntry* find(const QString& path);
    LibraryEntry* ensure(const QString& path);

    QString path_;
    QVector<LibraryEntry> entries_;
    QVector<MediaBookmark> bookmarks_;
    QVector<SavedPlaylist> playlists_;
    int next_bookmark_id_ = 1;
    mutable QMap<QString, Prefs> prefs_;
    mutable bool prefs_loaded_ = false;
};

}  // namespace mpcasu

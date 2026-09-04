// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Playlist model + M3U/M3U8/PLS parsing for MPCASU. Pragmatic port of the
// queue semantics (casu/playlist.py): ordered items, shuffle/repeat,
// next/prev, load/save M3U/PLS. Unicode/space-safe paths.
#pragma once
#include <QString>
#include <QStringList>
#include <QVector>

#include <random>
#include <string>
#include <vector>

namespace mpcasu {

struct PlaylistItem {
    QString path;   // local path or stream URL
    QString title;  // EXTINF / entry title or derived name
    bool is_url = false;
    // A playlist GROUP row: the item is a playlist file whose entries are
    // only logically part of the queue (children shown in the tree). The
    // group stays visible/movable; it is never dissolved into its entries.
    bool is_playlist = false;
};

class PlaylistModel {
public:
    void clear();
    void add(const QString& path, const QString& title = QString());
    void add_files(const QStringList& paths);
    void remove(int index);
    void remove_many(const QVector<int>& indices);
    void move(int from, int to);
    void move_many(const QVector<int>& indices, int delta);
    void reorder(const QStringList& paths);
    const QVector<PlaylistItem>& items() const { return items_; }
    int size() const { return items_.size(); }
    bool empty() const { return items_.isEmpty(); }
    int index_of(const QString& path) const;
    bool is_playlist_row(int index) const {
        return index >= 0 && index < items_.size() && items_[index].is_playlist;
    }

    int current_index() const { return current_; }
    void set_current(int index) { current_ = index; }

    // Transport logic.
    int next_index(bool automatic_end) const;
    int previous_index() const;

    bool shuffle = false;
    enum class RepeatMode { Off, All, One };
    RepeatMode repeat = RepeatMode::Off;

    // Load/save. Returns error string (empty = ok).
    static std::string load_m3u(const QString& file, PlaylistModel* out);
    static std::string load_pls(const QString& file, PlaylistModel* out);
    static std::string load_xspf(const QString& file, PlaylistModel* out);
    static std::string load_wpl(const QString& file, PlaylistModel* out);
    static std::string load_jspf(const QString& file, PlaylistModel* out);
    static std::string load_asx(const QString& file, PlaylistModel* out);
    static std::string load_rmp(const QString& file, PlaylistModel* out);
    static std::string load_ram(const QString& file, PlaylistModel* out);
    static std::string load_mpcasu_json(const QString& file, PlaylistModel* out);
    static std::string load_file(const QString& file, PlaylistModel* out);
    static std::string save_m3u(const QString& file, const PlaylistModel& model);
    static std::string save_pls(const QString& file, const PlaylistModel& model);
    static std::string save_xspf(const QString& file, const PlaylistModel& model);
    static std::string save_json(const QString& file, const PlaylistModel& model);
    // Format-preserving dispatch (extension decides; JSON payload fallback),
    // mirroring casu/playlist.py save_playlist_file.
    static std::string save_file(const QString& file, const PlaylistModel& model);
    static bool looks_like_playlist(const QString& path);

private:
    QVector<PlaylistItem> items_;
    int current_ = -1;
    mutable std::mt19937 rng_{std::random_device{}()};
};

QString display_title_for_path(const QString& path);

// ---- Pure queue-group semantics (ports of the mpcasu_qt helpers; unit-
// testable without widgets). ----------------------------------------------

// Logical playback order over the queue: each playlist GROUP contributes its
// entries (in file order) at the group's position, every other row is itself.
// Broken/unreadable playlists contribute nothing (skipped). The queue model is
// NEVER modified here — mirrors _playback_sequence().
QVector<QString> playlist_logical_sequence(const QVector<PlaylistItem>& items);

// First position in the logical playback sequence that top-level queue row
// `row` contributes (a group's first entry), or -1 — mirrors _row_to_seq().
int playlist_row_to_seq(const QVector<PlaylistItem>& items, int row);

// Row that OWNS sequence position `target`, or -1 — mirrors the owner search
// in play_next/play_previous.
int playlist_seq_owner_row(const QVector<PlaylistItem>& items, int target);

// Playlist-file paths among the rows (the "groups") — mirrors _queue_playlists().
QStringList playlist_group_paths(const QVector<PlaylistItem>& items);

// Path of the queued playlist whose entries contain `entry`, or empty —
// mirrors _containing_playlist(). Reads the playlist FILES.
QString playlist_containing_playlist(const QVector<PlaylistItem>& items,
                                     const QString& entry);

// Linux batch-add planning (add_files with covered-set + existing_only):
// playlists become ONE group row each (deduplicated), their entries are
// "covered" so media chosen in the same batch is not double-added,
// non-existent local files are skipped, input ORDER is preserved
// (playlists and loose rows stay interleaved as chosen).
struct PlaylistBatchPlan {
    QStringList rows;   // rows to append, in input order
};
PlaylistBatchPlan playlist_batch_plan(const QStringList& paths);

}  // namespace mpcasu

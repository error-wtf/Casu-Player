// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// MPCASU main window (port of mpcasu_qt/main_window.py). Layout per
// ui-style-bible.md: Sidebar(240) | Workspace (topbar 72, stage, transport
// 66) | Playlist(310). Single-player pipeline:
//   UI -> CppPlaybackController -> PlaybackBackend -> VideoSurface.
#pragma once
#include "casu/playback/controller.hpp"
#include "casu/playback/libvlc_backend.hpp"
#include "casu/playback/state.hpp"

#include "epg.hpp"
#include "library.hpp"
#include "playlist.hpp"
#include "recording.hpp"
#include "settings.hpp"
#include "web_player_tabs.hpp"
#include "youtube_proxy.hpp"

#include <QMainWindow>
#include <QEvent>
#include <QPixmap>
#include <QGridLayout>
#include <QHash>
#include <QListWidgetItem>
#include <QMap>
#include <QObject>
#include <QSet>
#include <QSplitter>
#include <QTimer>

#include <memory>

class QLabel;
class QLineEdit;
class QListWidget;
class QPushButton;
class QSlider;
class QStackedWidget;
class QTableWidget;
class QTreeWidget;
class QTreeWidgetItem;
class QComboBox;
class QCheckBox;
class QSpinBox;
class QDoubleSpinBox;
class QFrame;
class QStackedLayout;

namespace casu::playback {
class CppPlaybackController;
}

namespace mpcasu {

class VideoSurface;

// Marshals libVLC event-thread callbacks onto the GUI thread via a queued
// QMetaObject::invokeMethod functor. No Q_OBJECT (the bundled Qt is Windows
// only, so this cross build has no host moc).
class BackendEventBridge {
public:
    using State = casu::playback::PlaybackState;
    explicit BackendEventBridge(QObject* context) : context_(context) {}
    void post(State s) {
        QMetaObject::invokeMethod(context_, [this, s] {
            if (on_state) on_state(s);
        }, Qt::QueuedConnection);
    }
    std::function<void(State)> on_state;

private:
    QObject* context_ = nullptr;
};

class MainWindow final : public QMainWindow {
public:
explicit MainWindow(const QStringList& initial_files = {},
                    bool force_proxy = false, QString vout = {},
                    QString aout = {}, bool play_test = false,
                    QWidget* parent = nullptr);
    ~MainWindow() override;

    void add_files(const QStringList& paths);
    void play_selected_path(const QString& path);

    // Wine verification helpers (--play-test): report the current backend
    // state/position without exposing the controller.
    const char* playback_state_name() const {
        return casu::playback::state_name(controller_->state());
    }
    double playback_position() const { return controller_->position(); }
    double playback_duration() const { return controller_->duration(); }
    bool has_playback_backend() const { return static_cast<bool>(backend_); }

    // Test/parity helper: switch to a named page (used by --page).
    void navigate_to(const QString& page) { navigate(page); }

protected:
    void dragEnterEvent(QDragEnterEvent* event) override;
    void dragMoveEvent(QDragMoveEvent* event) override;
    void dragLeaveEvent(QDragLeaveEvent* event) override;
    void dropEvent(QDropEvent* event) override;
    void closeEvent(QCloseEvent* event) override;
    void keyPressEvent(QKeyEvent* event) override;

private:
    void build_ui();
    void build_sidebar();
    void build_playlist_pane();
    void build_player_page();
    void build_about_page();
    void build_transport();
    void build_library_page();
    void build_settings_page();
    void build_epg_page();
    void build_recording_page();
    void build_visualizer_page();
    void build_youtube_page();
    void build_web_players_page();

    void status(const QString& text);
    void toast(const QString& text);
    void navigate(const QString& page);
    void change_volume(int delta);
    void exit_fullscreen_ui();
    void open_files_dialog();
    void open_url_dialog();
    void show_media_info();

    void open_backend_and_play(const QString& source, const QString& title);
    void open_network_source(const QString& source, const QString& title);
    // Linux parity (_queue_and_play/_tag_queue_title): YouTube results go
    // INTO the queue with their title; the real title is fetched async.
    void queue_and_play(const QString& url, const QString& label);
    void tag_queue_title(const QString& url);
    void open_web_player(const QString& provider, const QString& query = {},
                         const QString& url = {});
    void play_queue_index(int index, bool automatic);
    void stop_playback();
    void handle_end();
    void apply_backend_settings();
    void update_play_button();
    void refresh_library();
    void refresh_playlist();
    void expand_playlist_group(QTreeWidgetItem* top);
    void refresh_playlist_group(const QString& path);

    // playlist pane / groups
    QVector<QString> logical_sequence() const;
    void play_seq_entry(const QString& path, int row, bool automatic);
    void move_playlist_rows(const QVector<int>& rows, int delta);
    void reselect_playlist_rows(const QStringList& paths);
    void remove_children_from_playlist(const QStringList& entries);
    void move_children_to_playlist(const QStringList& entries);

    // transport
    void toggle_playback();
    void pause();
    void resume_after_seek();
    void play_next(bool automatic = false);
    void play_previous();
    void seek_to(double seconds);
    void set_volume(int value);
    void toggle_mute();
    void cycle_rate();
    void toggle_fullscreen();
    void save_snapshot();
    void cycle_repeat();
    void on_backend_state(casu::playback::PlaybackState);
    void poll();

    // playlist pane
    void choose_files();
    void add_url();
    void load_playlist_file();
    void save_playlist_file();
    void playlist_double_clicked();
    void playlist_context_menu(const QPoint& pos);
    void merge_selection_into_playlist();

    // pages
    void on_library_add_current();
    void on_library_add_selected(QListWidgetItem* item);
    void on_library_add_playlists();
    void scan_library_folders();
    void scan_playlist_files();
    void on_playlist_group_selected(QListWidgetItem* current);
    void on_settings_save();
    void on_epg_load();
    void load_epg_source(const QString& source);
    void render_epg_cards();
    bool eventFilter(QObject* watched, QEvent* event) override;
    void on_recording_toggle();
    void on_recording_toggle_restart_after_rotate();
    void show_record_settings_dialog();
    void on_visualizer_toggle();
    void load_cover_art(const QString& source);
    void set_queue_view_filter(const QString& view);
    void request_queue_thumbnails();
    void apply_thumb(const QString& path, const QString& thumb);
    void apply_media_preferences();
    void persist_media_preferences();
    void update_stage();
    void rename_queue_entry();
    void commit_queue_rename(QTreeWidgetItem* item, QLineEdit* editor);
    void apply_viz_mode();
    void set_diagnostics(const QString& support, const QString& integrity,
                         const QString& segmented, const QString& guide);
    void update_diagnostics_guide();
    QString epg_now_next_text(const QString& source);
    QString queue_label_for(const QString& path);
    void show_fs_overlay();
    void hide_fs_overlay();
    void clamp_to_screen();
    void mouseMoveEvent(QMouseEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;
    void moveEvent(QMoveEvent* event) override;
    void showEvent(QShowEvent* event) override;
    void on_youtube_play();

    VideoSurface* surface_ = nullptr;
    QStackedLayout* stage_stack_ = nullptr;
    QFrame* stage_empty_ = nullptr;  // Linux parity "Drop media here" placeholder
    bool stage_media_active_ = false;  // media loaded -> video/viz instead of empty
    QFrame* transport_frame_ = nullptr;
    QFrame* topbar_ = nullptr;
    QFrame* sidebar_ = nullptr;
    QFrame* diagnostics_bar_ = nullptr;
    QMap<QString, QLabel*> diag_labels_;
    BackendEventBridge* bridge_ = nullptr;
    casu::playback::CppPlaybackController* controller_ = nullptr;
    std::shared_ptr<casu::playback::PlaybackBackend> backend_;
    PlaylistModel playlist_;
    MediaLibrary* library_ = nullptr;
    SettingsStore* settings_ = nullptr;
    AppSettings app_settings_;
    mpcasu::SessionState session_;
    YoutubeProxy* yt_proxy_ = nullptr;
    WebPlayerTabs* web_player_tabs_ = nullptr;
    std::atomic<int> resolve_generation_{0};
    std::atomic<int> title_generation_{0};
    bool error_latched_ = false;
    RecordingController* recorder_ = nullptr;
    QTimer* record_timer_ = nullptr;
    int record_part_ = 1;
    int record_split_minutes_ = 0;
    bool pending_rotate_ = false;
    QString record_stem_;
    mpcasu::StreamCatalog epg_;
    mpcasu::EpgGuide epg_guide_;
    QString current_source_;
    QString current_title_;
    double audio_delay_ms_ = 0.0;     // Linux parity: per-media A/V delays
    double subtitle_delay_ms_ = 0.0;
    QString output_dir_;
    QString vout_;
    QString aout_;
    bool force_proxy_ = false;
    bool paused_ = false;
    bool end_handled_ = false;
    bool advancing_ = false;
    bool clamping_ = false;
    bool play_test_mode_ = false;  // CI: no session restore / resume
    double duration_ = 0.0;
    int volume_ = 100;
    bool muted_ = false;
    double rate_ = 1.0;
    QTimer* poll_timer_ = nullptr;
    QStackedWidget* pages_ = nullptr;
    QWidget* player_page_ = nullptr;

    // player page widgets
    QLabel* topbar_title_ = nullptr;
    QLabel* time_current_ = nullptr;
    QLabel* time_total_ = nullptr;
    QSlider* seek_slider_ = nullptr;
    QPushButton* play_btn_ = nullptr;
    QPushButton* mute_btn_ = nullptr;
    QSlider* volume_slider_ = nullptr;
    QPushButton* rate_btn_ = nullptr;
    QPushButton* ab_btn_ = nullptr;
    QPushButton* repeat_btn_ = nullptr;
    QPushButton* shuffle_btn_ = nullptr;
    QPushButton* record_btn_ = nullptr;
    QPushButton* viz_btn_ = nullptr;
    QWidget* fs_overlay_ = nullptr;
    QLabel* fs_title_ = nullptr;
    QLabel* fs_time_ = nullptr;
    QPushButton* fs_play_btn_ = nullptr;
    QTimer* fs_hide_timer_ = nullptr;
    QLabel* status_label_ = nullptr;    // left: version (MPCASU 5.0.0)
    QLabel* status_center_ = nullptr;   // center: transient status messages
    QLabel* toast_label_ = nullptr;
    QLabel* drop_overlay_ = nullptr;
    QTimer* toast_timer_ = nullptr;
    QWidget* visualizer_ = nullptr;
    QPixmap* cover_pixmap_ = nullptr;  // owned cover art shown in visualizer

    // playlist pane
    QTreeWidget* playlist_view_ = nullptr;
    QComboBox* view_filter_ = nullptr;
    QLineEdit* queue_search_ = nullptr;
    QLabel* empty_hint_ = nullptr;
    QSet<QString> expanded_groups_;
    QHash<QString, QString> display_titles_;
    QHash<QString, QString> tag_titles_;  // Linux parity: cached tag titles
    QString current_played_path_;
    QString resume_source_;
    double resume_position_ = -1.0;
    bool seq_valid_ = false;
    QVector<QString> seq_;  // cached logical playback sequence
    void invalidate_seq() { seq_valid_ = false; }
    void apply_queue_filter();
    void remove_selected_rows();
    void remove_selected_rows(const QVector<int>& fixed_rows);
    QString selected_child_entry() const;
    bool play_selected_child();
    void cycle_ab_loop();
    double ab_loop_a_ = -1.0;
    double ab_loop_b_ = -1.0;

    // sidebar
    QList<QPushButton*> nav_buttons_;
    QMap<QString, QPushButton*> nav_map_;

    // library page
    QLineEdit* library_search_ = nullptr;
    QComboBox* library_mode_ = nullptr;
    QSplitter* library_splitter_ = nullptr;
    QListWidget* library_groups_ = nullptr;
    QListWidget* library_tracks_ = nullptr;
    QListWidget* library_folders_ = nullptr;
    QLabel* library_count_ = nullptr;
    QHash<QString, QString> lib_meta_;  // cached tag fields: "path|field" → value
    QMap<QString, QString> playlist_files_;  // playlist name → file path

    // settings page
    QSlider* settings_volume_ = nullptr;
    QDoubleSpinBox* settings_rate_ = nullptr;
    QCheckBox* settings_shuffle_ = nullptr;
    QComboBox* settings_repeat_ = nullptr;
    QLineEdit* settings_record_dir_ = nullptr;
    QLabel* backend_info_label_ = nullptr;
    QCheckBox* settings_muted_ = nullptr;
    QCheckBox* settings_resume_ = nullptr;
    QComboBox* settings_viz_ = nullptr;
    QSpinBox* settings_cache_ = nullptr;
    QListWidget* settings_folders_ = nullptr;
    QSpinBox* settings_split_ = nullptr;
    QComboBox* settings_format_ = nullptr;
    QCheckBox* settings_consent_ = nullptr;

    // epg page
    QLineEdit* epg_source_ = nullptr;
    QLabel* epg_status_ = nullptr;
    QGridLayout* epg_grid_ = nullptr;
    QHash<QObject*, QString> epg_card_urls_;

    // recording page
    QLabel* record_status_ = nullptr;
    QLineEdit* record_dir_ = nullptr;

    // youtube page
    QLineEdit* youtube_url_ = nullptr;
    QLabel* youtube_status_ = nullptr;
    QFrame* yt_consent_frame_ = nullptr;
    QListWidget* yt_results_ = nullptr;
    bool yt_searching_ = false;
};

}  // namespace mpcasu

// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define NOGDI
#include <windows.h>  // CREATE_NO_WINDOW: keep GUI child processes silent
#endif
#include "main_window.hpp"
#include "epg.hpp"

#include "casu/codec/tools.hpp"
#include "casu/formats.hpp"
#include "casu/json.hpp"
#include "casu/media/tags.hpp"
#include "casu/media/thumbnail.hpp"
#include "casu/native.hpp"
#include "casu/network/http.hpp"
#include "casu/network/spotify.hpp"
#include "casu/network/url.hpp"
#include "casu/network/ytdlp.hpp"
#include "casu/web/webproviders.hpp"

#include "theme.hpp"
#include "video_surface.hpp"
#include "visualizer.hpp"

#include <QDirIterator>
#include <QRegularExpression>

#include <QApplication>
#include <cmath>
#include <QIcon>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPainter>
#include <QPen>
#include <QPolygonF>
#include <QCheckBox>
#include <QCloseEvent>
#include <QColor>
#include <QComboBox>
#include <QDateTime>
#include <QDialog>
#include <QDir>
#include <QDirIterator>
#include <QDoubleSpinBox>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFileDialog>
#include <QFileInfo>
#include <QFont>
#include <QFrame>
#include <QGridLayout>
#include <QGuiApplication>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QInputDialog>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QKeyEvent>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMenu>
#include <QMessageBox>
#include <QMimeData>
#include <QPushButton>
#include <QProcess>
#include <QScrollArea>
#include <QTextBrowser>
#include <QRandomGenerator>
#include <QScreen>
#include <QSet>
#include <QSlider>
#include <QSpinBox>
#include <QSplitter>
#include <QStandardPaths>
#include <thread>
#include <QPointer>
#include <filesystem>
#include <atomic>
#include <QStackedLayout>
#include <QStackedWidget>
#include <QStatusBar>
#include <QTableWidget>
#include <QTabWidget>
#include <QTemporaryFile>
#include <QTreeWidget>
#include <QTime>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>
#include <cstdlib>
#include <set>

namespace mpcasu {
namespace {

const std::set<QString> kAudioExtensions = {
    "mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "wma", "caf", "mp2", "alac",
};

bool is_audio_ext(const QString& path) {
    QString ext = QFileInfo(path).suffix().toLower();
    return kAudioExtensions.count(ext) > 0;
}

bool is_casu_container(const QString& path) {
    QString ext = QFileInfo(path).suffix().toLower();
    return ext == "casu" || ext == "mp5";
}

bool is_network_like(const QString& value) {
    return value.contains("://") || value.startsWith("spotify:") || value.startsWith("ytdl:");
}

QString default_output_dir() {
    QString d = QDir::homePath() + "/Videos/MPCASU";
    if (QDir().mkpath(d)) return d;
    return QDir::currentPath();
}

}  // namespace

MainWindow::MainWindow(const QStringList& initial_files, bool force_proxy,
                       QString vout, QString aout, bool play_test,
                       QWidget* parent)
    : QMainWindow(parent), force_proxy_(force_proxy), vout_(std::move(vout)),
      aout_(std::move(aout)), play_test_mode_(play_test) {
    setWindowTitle(QStringLiteral("MPCASU Media Player"));
    const QString icon_path = QDir(QCoreApplication::applicationDirPath() + "/assets")
                                  .filePath("mpcasu_player_icon.png");
    if (QFileInfo::exists(icon_path))
        setWindowIcon(QIcon(icon_path));
    const QScreen* screen = QGuiApplication::primaryScreen();
    const QRect avail = screen ? screen->availableGeometry() : QRect(0, 0, 1600, 1000);
    setMinimumSize(qMin(980, avail.width()), qMin(620, avail.height()));
    resize(qMin(1360, avail.width() - 24), qMin(820, avail.height() - 24));
    setAcceptDrops(true);
    setObjectName("Root");
    setStyleSheet(application_stylesheet());

    bridge_ = new BackendEventBridge(this);
    controller_ = new casu::playback::CppPlaybackController();
    yt_proxy_ = new YoutubeProxy(this);
    recorder_ = new RecordingController(this);
    library_ = new MediaLibrary(app_config_dir() + "/library.json");
    library_->load();
    settings_ = new SettingsStore(app_config_dir() + "/settings.json",
                                  app_config_dir() + "/session.json");
    app_settings_.player = settings_->load();
    session_ = settings_->load_session();
    app_settings_.snapshot_dir = session_.snapshot_dir;
    app_settings_.library_dir = session_.library_dir;
    app_settings_.last_playlist = session_.last_playlist;
    volume_ = app_settings_.player.volume;
    muted_ = app_settings_.player.muted;
    rate_ = app_settings_.player.rate;
    playlist_.shuffle = app_settings_.player.shuffle;
    playlist_.repeat = app_settings_.player.repeat_mode == "one"
                           ? PlaylistModel::RepeatMode::One
                           : (app_settings_.player.repeat_mode == "all"
                                  ? PlaylistModel::RepeatMode::All
                                  : PlaylistModel::RepeatMode::Off);
    output_dir_ = app_settings_.player.recordings_dir.isEmpty() ? default_output_dir()
                                                     : app_settings_.player.recordings_dir;

    build_ui();

    surface_->on_double_click = [this] { toggle_fullscreen(); };
    surface_->on_click = [this] { toggle_playback(); };
    // Reference parity: mouse wheel over the video changes the volume.
    surface_->on_wheel = [this](int step) { change_volume(step * 5); };
    bridge_->on_state = [this](casu::playback::PlaybackState s) {
        on_backend_state(s);
    };
    recorder_->on_state_changed = [this] {
        const char* label = "Idle";
        switch (recorder_->state()) {
            case RecordingController::State::Starting: label = "Starting…"; break;
            case RecordingController::State::Recording: label = "Recording…"; break;
            case RecordingController::State::Stopping: label = "Stopping…"; break;
            case RecordingController::State::Failed: label = "Failed"; break;
            default: break;
        }
        record_status_->setText(QStringLiteral("Recording: %1").arg(label));
        record_btn_->setChecked(recorder_->is_recording());
    };
    recorder_->on_finished = [this](const QString& out, bool ok, const QString& detail) {
        if (pending_rotate_ && ok) {
            pending_rotate_ = false;
            ++record_part_;
            on_recording_toggle_restart_after_rotate();
            return;
        }
        pending_rotate_ = false;
        if (ok) status(QStringLiteral("Recording saved: %1").arg(out));
        else status(QStringLiteral("Recording failed: %1").arg(detail));
    };

    poll_timer_ = new QTimer(this);
    poll_timer_->setInterval(200);
    connect(poll_timer_, &QTimer::timeout, this, &MainWindow::poll);
    poll_timer_->start();

    connect(seek_slider_, &QSlider::sliderPressed, this, [this] {
        pause();  // freeze UI position while dragging (kept paused until release)
    });
    connect(seek_slider_, &QSlider::sliderReleased, this, [this] {
        seek_to(seek_slider_->value() / 1000.0);
        resume_after_seek();
    });

    // Session restore (Linux parity): WxH+X+Y geometry, last queue,
    // current source and resume position come from session.json.
    if (!play_test_mode_) {
        if (session_.width > 0 && session_.height > 0)
            resize(session_.width, session_.height);
        resume_source_ = session_.current;
        resume_position_ = session_.position;
        if (app_settings_.player.resume_playback &&
            !session_.playlist.isEmpty()) {
            add_files(session_.playlist);
            refresh_playlist();
        }
    }

    if (!initial_files.isEmpty()) {
        add_files(initial_files);
        QTimer::singleShot(300, this, [this] { play_queue_index(playlist_.current_index() < 0 ? 0 : playlist_.current_index(), false); });
    }
}

MainWindow::~MainWindow() {
    stop_playback();
    if (controller_) delete controller_;
    delete bridge_;
    delete library_;
    delete settings_;
}

// ------------------------------------------------------------------ UI

void MainWindow::build_ui() {
    auto* central = new QWidget(this);
    setCentralWidget(central);
    auto* main_layout = new QHBoxLayout(central);
    main_layout->setContentsMargins(0, 0, 0, 0);
    main_layout->setSpacing(0);

    build_sidebar();
    main_layout->addWidget(sidebar_);

    pages_ = new QStackedWidget(this);
    build_player_page();
    build_about_page();
    build_library_page();
    build_settings_page();
    build_epg_page();
    build_recording_page();
    build_visualizer_page();
    build_youtube_page();
    build_web_players_page();
    main_layout->addWidget(pages_, 1);

    build_playlist_pane();
    main_layout->addWidget(playlist_view_->parentWidget());
    apply_viz_mode();

    // Linux parity: status bar shows version | tagline | telemetry placeholder;
    // transient status messages go to the center label.
    status_label_ = new QLabel(QStringLiteral("MPCASU 7.0.0"), this);
    status_label_->setObjectName("StatusText");
    statusBar()->addWidget(status_label_);
    status_center_ = new QLabel(
        QStringLiteral("Optimized for performance and integrity"), this);
    status_center_->setObjectName("StatusText");
    statusBar()->addWidget(status_center_, 1);
    auto* status_right = new QLabel(
        QStringLiteral("CPU/RAM telemetry unavailable"), this);
    status_right->setObjectName("StatusText");
    statusBar()->addPermanentWidget(status_right);
    statusBar()->setSizeGripEnabled(false);}


// Font-independent sidebar nav icons: the Unicode glyphs (▶ ▣ ▤ ≡ ▦ …) are
// missing from some systems' UI fonts and render as tofu boxes (user-
// reported), so every icon is drawn with QPainter — identical on every OS
// and matching the Linux reference (mpcasu_qt Sidebar.ICON_DRAWERS).
QIcon nav_icon(const QString& name, const QColor& color, const QColor& active,
               int size = 18) {
    QIcon icon;
    const std::pair<QIcon::State, QColor> variants[] = {
        {QIcon::Off, color}, {QIcon::On, active}};
    for (const auto& [state, tint] : variants) {
        QPixmap pm(size, size);
        pm.fill(Qt::transparent);
        QPainter p(&pm);
        p.setRenderHint(QPainter::Antialiasing);
        QPen pen(tint, 1.5);
        pen.setCapStyle(Qt::RoundCap);
        p.setPen(pen);
        const QRectF r(2.5, 2.5, size - 5.0, size - 5.0);
        const QPointF c = r.center();
        const qreal w = r.width(), h = r.height();
        if (name == QLatin1String("NOW PLAYING")) {
            QPolygonF tri;
            tri << r.topLeft() << QPointF(r.left(), r.bottom())
                << QPointF(r.right(), c.y());
            p.drawPolygon(tri);
        } else if (name == QLatin1String("LIBRARY")) {
            p.drawRect(r);
            p.drawRect(r.adjusted(w * 0.3, h * 0.3, -w * 0.3, -h * 0.3));
        } else if (name == QLatin1String("WEB & STREAMS")) {
            p.drawEllipse(r);
            p.drawLine(QPointF(r.left(), c.y()), QPointF(r.right(), c.y()));
            p.drawEllipse(r.adjusted(w * 0.28, 0, -w * 0.28, 0));
        } else if (name == QLatin1String("PLAYLISTS")) {
            for (const qreal f : {0.1, 0.5, 0.9})
                p.drawLine(QPointF(r.left(), r.top() + h * f),
                           QPointF(r.right(), r.top() + h * f));
        } else if (name == QLatin1String("IPTV / EPG")) {
            p.drawRect(r.adjusted(0, 0, -w / 2 - 1, -h / 2 - 1));
            p.drawRect(r.adjusted(w / 2 + 1, 0, 0, -h / 2 - 1));
            p.drawRect(r.adjusted(0, h / 2 + 1, -w / 2 - 1, 0));
            p.drawRect(r.adjusted(w / 2 + 1, h / 2 + 1, 0, 0));
        } else if (name == QLatin1String("YOUTUBE")) {
            p.drawRoundedRect(r, 3, 3);
            QPolygonF tri;
            tri << QPointF(c.x() - w * 0.12, r.top() + h * 0.28)
                << QPointF(c.x() - w * 0.12, r.bottom() - h * 0.28)
                << QPointF(r.right() - w * 0.22, c.y());
            p.drawPolygon(tri);
        } else if (name == QLatin1String("SPOTIFY")) {
            p.drawEllipse(r);
            const qreal insets[] = {0.22, 0.16, 0.10};
            for (int i = 0; i < 3; ++i)
                p.drawArc(QRectF(r.left() + w * insets[i],
                                 r.top() + h * (0.3 + 0.22 * i),
                                 w * (1 - 2 * insets[i]), h * 0.16),
                          20 * 16, 140 * 16);
        } else if (name == QLatin1String("CASU FILES")) {
            QPolygonF outer, inner;
            outer << (c + QPointF(0, -h / 2)) << (c + QPointF(w / 2, 0))
                  << (c + QPointF(0, h / 2)) << (c + QPointF(-w / 2, 0));
            inner << (c + QPointF(0, -h * 0.18)) << (c + QPointF(w * 0.18, 0))
                  << (c + QPointF(0, h * 0.18)) << (c + QPointF(-w * 0.18, 0));
            p.drawPolygon(outer);
            p.drawPolygon(inner);
        } else if (name == QLatin1String("HEARTHIS")) {
            p.drawLine(QPointF(r.left() + 1, r.bottom() - 1),
                       QPointF(r.right() - 1, r.top() + 1));
            QPolygonF head;
            head << QPointF(r.right() - w * 0.45, r.top() + 1)
                 << QPointF(r.right() - 1, r.top() + 1)
                 << QPointF(r.right() - 1, r.top() + h * 0.45);
            p.drawPolyline(head);
        } else if (name == QLatin1String("TIDAL")) {
            for (int i = 0; i < 2; ++i) {
                QPolygonF wave;
                wave << QPointF(r.left(), r.top() + h * (0.2 + 0.3 * i))
                     << QPointF(c.x(), r.top() + h * (0.05 + 0.3 * i))
                     << QPointF(r.right(), r.top() + h * (0.2 + 0.3 * i))
                     << QPointF(r.right(), r.top() + h * (0.5 + 0.3 * i));
                p.drawPolyline(wave);
            }
        } else if (name == QLatin1String("NETFLIX")) {
            p.drawLine(QPointF(r.left() + 2, r.top()),
                       QPointF(r.left() + 2, r.bottom()));
            p.drawLine(QPointF(r.right() - 2, r.top()),
                       QPointF(r.right() - 2, r.bottom()));
            p.drawLine(QPointF(r.left() + 2, r.top()),
                       QPointF(r.right() - 2, r.bottom()));
        } else if (name == QLatin1String("BROWSE")) {
            p.drawEllipse(r);
            p.drawPie(r, 40 * 16, 100 * 16);
            p.drawPie(r, 220 * 16, 100 * 16);
        } else if (name == QLatin1String("OPTIONS")) {
            p.drawEllipse(r.adjusted(w * 0.22, h * 0.22, -w * 0.22, -h * 0.22));
            for (int i = 0; i < 8; ++i) {
                const qreal a = i * M_PI / 4.0;
                p.drawLine(c + QPointF((w / 2 - 1) * std::cos(a),
                                       (h / 2 - 1) * std::sin(a)),
                           c + QPointF((w / 2 - 1) * 0.55 * std::cos(a),
                                       (h / 2 - 1) * 0.55 * std::sin(a)));
            }
        } else if (name == QLatin1String("ABOUT")) {
            p.drawEllipse(r);
            p.drawPoint(c + QPointF(0, -h * 0.22));
            p.drawLine(c, QPointF(c.x(), r.bottom() - h * 0.2));
        }
        p.end();
        icon.addPixmap(pm, QIcon::Normal, state);
        icon.addPixmap(pm, QIcon::Selected, state);
    }
    return icon;
}

void MainWindow::build_sidebar() {
    sidebar_ = new QFrame(this);
    sidebar_->setObjectName("Sidebar");
    sidebar_->setFixedWidth(metrics().sidebar_width);
    auto* sidebar_layout = new QVBoxLayout(sidebar_);
    sidebar_layout->setContentsMargins(12, 16, 12, 12);
    sidebar_layout->setSpacing(4);

    // The nav body lives in its own widget inside a scroll area: 13 nav
    // rows + section headers need ~800 px. In a non-fullscreen window
    // (620 px minimum) a plain layout would COMPRESS every row below its
    // natural height and clip the label text halfway (user-reported).
    // Scrolling keeps each row fully readable at any window height. The
    // brand block stays pinned above it, the status footer below it.

    // Brand logo (exe-relative assets, mirrors the Linux sidebar header).
    auto* logo = new QLabel(sidebar_);
    logo->setObjectName("BrandName");
    const QString logo_path = QDir(QCoreApplication::applicationDirPath() + "/assets")
                                  .filePath("mpcasu_player_logo_cropped.png");
    if (QFileInfo::exists(logo_path)) {
        QPixmap pix(logo_path);
        if (!pix.isNull()) {
            logo->setPixmap(pix.scaledToWidth(180, Qt::SmoothTransformation));
            logo->setFixedHeight(logo->pixmap().height());
        } else {
            logo->setText(QStringLiteral("MPCASU"));
        }
    } else {
        logo->setText(QStringLiteral("MPCASU"));
    }
    logo->setCursor(Qt::PointingHandCursor);
    sidebar_layout->addWidget(logo);
    auto* sub = new QLabel(QStringLiteral("MEDIA · CASU"), sidebar_);
    sub->setObjectName("BrandSub");
    sidebar_layout->addWidget(sub);
    sidebar_layout->addSpacing(12);

    auto* body = new QWidget(sidebar_);
    auto* layout = new QVBoxLayout(body);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(4);

    auto add_section = [&](const QString& label) {
        auto* section = new QLabel(label, sidebar_);
        section->setObjectName("SidebarSection");
        layout->addWidget(section);
    };
    auto add_nav = [&](const QString& name, const QString& icon) {
        Q_UNUSED(icon);  // glyphs replaced by drawn nav_icon() (font-safe)
        auto* btn = new QPushButton(name, sidebar_);
        btn->setObjectName("NavItem");
        btn->setCheckable(true);
        btn->setToolTip(name);
        btn->setCursor(Qt::PointingHandCursor);
        btn->setIcon(nav_icon(name, QColor(QStringLiteral("#8a93a0")),
                              QColor(QStringLiteral("#ff1e2d"))));
        btn->setIconSize(QSize(18, 18));
        layout->addWidget(btn);
        nav_buttons_.append(btn);
        nav_map_[name] = btn;
        connect(btn, &QPushButton::clicked, this,
                [this, name] { navigate(name); });
    };

    // Linux parity (main_window.py nav_items/NAV_ICONS): identical sections,
    // entries and icons.
    add_section(QStringLiteral("LIBRARY"));
    add_nav(QStringLiteral("NOW PLAYING"), QStringLiteral("▶"));
    add_nav(QStringLiteral("LIBRARY"), QStringLiteral("▣"));
    add_nav(QStringLiteral("WEB & STREAMS"), QStringLiteral("▤"));
    add_nav(QStringLiteral("PLAYLISTS"), QStringLiteral("≡"));
    add_nav(QStringLiteral("IPTV / EPG"), QStringLiteral("▦"));
    add_section(QStringLiteral("SEARCH"));
    add_nav(QStringLiteral("YOUTUBE"), QStringLiteral("▷"));
    add_section(QStringLiteral("CASU"));
    add_nav(QStringLiteral("CASU FILES"), QStringLiteral("◈"));
    add_section(QStringLiteral("WEB PLAYERS"));
    add_nav(QStringLiteral("SPOTIFY"), QStringLiteral("♪"));
    add_nav(QStringLiteral("HEARTHIS"), QStringLiteral("↗"));
    add_nav(QStringLiteral("TIDAL"), QStringLiteral("≋"));
    add_nav(QStringLiteral("NETFLIX"), QStringLiteral("▣"));
    add_nav(QStringLiteral("BROWSE"), QStringLiteral("◎"));
    add_section(QStringLiteral("SYSTEM"));
    add_nav(QStringLiteral("OPTIONS"), QStringLiteral("⚙"));
    add_nav(QStringLiteral("ABOUT"), QStringLiteral("ⓘ"));
    layout->addStretch();

    auto* nav_scroll = new QScrollArea(sidebar_);
    nav_scroll->setObjectName("SidebarScroll");
    nav_scroll->setWidgetResizable(true);
    nav_scroll->setFrameShape(QFrame::NoFrame);
    nav_scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    nav_scroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    nav_scroll->setWidget(body);
    sidebar_layout->addWidget(nav_scroll);

    auto* backend = new QLabel(QStringLiteral("libVLC backend"), sidebar_);
    backend->setObjectName("StatusText");
    sidebar_layout->addWidget(backend);
    // Reference Sidebar footer: version label pinned to the bottom.
    auto* sidebar_version = new QLabel(QStringLiteral("MPCASU 7.0.0"), sidebar_);
    sidebar_version->setObjectName("NowPlayingMeta");
    sidebar_version->setContentsMargins(16, 8, 16, 8);
    sidebar_version->setAlignment(Qt::AlignLeft | Qt::AlignBottom);
    sidebar_layout->addWidget(sidebar_version);
}

void MainWindow::build_about_page() {
    auto* page = new QWidget(this);
    page->setObjectName("Page");
    auto* wrap = new QVBoxLayout(page);
    wrap->setContentsMargins(24, 24, 24, 24);
    auto* panel = new QFrame(page);
    panel->setObjectName("PagePanel");
    panel->setMaximumWidth(560);
    auto* col = new QVBoxLayout(panel);
    col->setContentsMargins(28, 28, 28, 28);
    col->setSpacing(10);

    auto* icon = new QLabel(panel);
    icon->setAlignment(Qt::AlignCenter);
    const QString icon_path = QDir(QCoreApplication::applicationDirPath() + "/assets")
                                  .filePath("mpcasu_player_icon.png");
    if (QFileInfo::exists(icon_path)) {
        QPixmap pix(icon_path);
        if (!pix.isNull())
            icon->setPixmap(pix.scaledToWidth(96, Qt::SmoothTransformation));
    }
    col->addWidget(icon);

    auto* name = new QLabel(QStringLiteral("MPCASU Media Player"), panel);
    name->setObjectName("BrandName");
    name->setAlignment(Qt::AlignCenter);
    col->addWidget(name);
    auto* version = new QLabel(QStringLiteral("Version 7.0.0 · Windows port (MinGW-w64 x64 + Qt 6)"), panel);
    version->setObjectName("BrandSub");
    version->setAlignment(Qt::AlignCenter);
    col->addWidget(version);

    auto* divider = new QFrame(panel);
    divider->setFrameShape(QFrame::HLine);
    divider->setStyleSheet(QStringLiteral("color: %1;").arg(mpcasu::palette().line));
    col->addWidget(divider);

    auto* license = new QLabel(
        QStringLiteral("License: LicenseRef-CASU-AntiCapitalist-1.4 — free for "
                       "personal and non-commercial use. Built on Qt 6, libVLC, "
                       "ffmpeg, yt-dlp and zstd."),
        panel);
    license->setObjectName("StatusText");
    license->setWordWrap(true);
    col->addWidget(license);
    auto* parity = new QLabel(
        QStringLiteral("Feature parity target: the Linux reference player "
                       "(mpcasu_qt/main_window.py). Playlist groups, queue "
                       "semantics and the web players behave identically."),
        panel);
    parity->setObjectName("StatusText");
    parity->setWordWrap(true);
    col->addWidget(parity);
    col->addStretch();

    wrap->addWidget(panel);
    wrap->addStretch();
    pages_->addWidget(page);
}

namespace {
// Minimal flow layout (Qt example semantics): children keep their size
// hint and wrap to the next row when the width runs out — used for the
// diagnostics cards so nothing is ever clipped on narrow windows.
class FlowLayout : public QLayout {
public:
    FlowLayout(QWidget* parent, int h_space, int v_space)
        : QLayout(parent), h_space_(h_space), v_space_(v_space) {}
    ~FlowLayout() override {
        while (QLayoutItem* item = takeAt(0)) delete item;
    }
    void addItem(QLayoutItem* item) override { items_.append(item); }
    Qt::Orientations expandingDirections() const override { return {}; }
    bool hasHeightForWidth() const override { return true; }
    int heightForWidth(int width) const override {
        return do_layout(QRect(0, 0, width, 0), true);
    }
    int count() const override { return items_.size(); }
    QLayoutItem* itemAt(int index) const override {
        return items_.value(index);
    }
    QSize minimumSize() const override {
        QSize size;
        for (QLayoutItem* item : items_) size = size.expandedTo(item->minimumSize());
        const QMargins margins = contentsMargins();
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom());
        return size;
    }
    void setGeometry(const QRect& rect) override {
        QLayout::setGeometry(rect);
        do_layout(rect, false);
    }
    QSize sizeHint() const override { return minimumSize(); }
    QLayoutItem* takeAt(int index) override {
        return index >= 0 && index < items_.size() ? items_.takeAt(index)
                                                   : nullptr;
    }

private:
    int do_layout(const QRect& rect, bool test_only) const {
        const QMargins margins = contentsMargins();
        const QRect effective = rect.adjusted(margins.left(), margins.top(),
                                             -margins.right(), -margins.bottom());
        int x = effective.x(), y = effective.y(), row_height = 0;
        for (QLayoutItem* item : items_) {
            const int next_x = x + item->sizeHint().width() + h_space_;
            if (next_x - effective.left() > effective.width() &&
                row_height > 0) {
                x = effective.x();
                y = y + row_height + v_space_;
                row_height = 0;
            }
            if (!test_only)
                item->setGeometry(QRect(QPoint(x, y), item->sizeHint()));
            x = next_x;
            row_height = qMax(row_height, item->sizeHint().height());
        }
        return y + row_height - rect.y() + margins.bottom();
    }
    QList<QLayoutItem*> items_;
    int h_space_;
    int v_space_;
};
}  // namespace




void MainWindow::build_player_page() {
    player_page_ = new QWidget(this);
    auto* col = new QVBoxLayout(player_page_);
    col->setContentsMargins(0, 0, 0, 0);
    col->setSpacing(0);

    // TopBar (Linux parity): ‹ back | NOW PLAYING + dynamic title | stretch |
    // Search queue… | ☰ nav toggle | ☷ queue toggle.
    topbar_ = new QFrame(player_page_);
    topbar_->setObjectName("TopBar");
    topbar_->setFixedHeight(metrics().topbar_height);
    auto* tb = new QHBoxLayout(topbar_);
    tb->setContentsMargins(10, 0, 10, 0);
    tb->setSpacing(8);
    auto* back_btn = new QPushButton(QStringLiteral("‹"), topbar_);
    back_btn->setObjectName("IconButton");
    back_btn->setFixedSize(40, 40);
    back_btn->setToolTip(QStringLiteral("Back to Now Playing"));
    connect(back_btn, &QPushButton::clicked, this,
            [this] { navigate(QStringLiteral("NOW PLAYING")); });
    tb->addWidget(back_btn);
    auto* heading = new QLabel(QStringLiteral("NOW PLAYING"), topbar_);
    heading->setObjectName("NowPlayingTitle");
    tb->addWidget(heading);
    topbar_title_ = new QLabel(QStringLiteral("No media loaded"), topbar_);
    topbar_title_->setObjectName("NowPlayingMeta");
    topbar_title_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    tb->addWidget(topbar_title_, 1);
    tb->addStretch();
    queue_search_ = new QLineEdit(topbar_);
    queue_search_->setPlaceholderText(QStringLiteral("Search queue…"));
    queue_search_->setFixedWidth(220);
    queue_search_->setFixedHeight(34);
    queue_search_->setClearButtonEnabled(true);
    connect(queue_search_, &QLineEdit::textChanged,
            this, [this] { apply_queue_filter(); });
    tb->addWidget(queue_search_);
    auto* nav_toggle = new QPushButton(QStringLiteral("☰"), topbar_);
    nav_toggle->setObjectName("IconButton");
    nav_toggle->setFixedSize(40, 40);
    nav_toggle->setToolTip(QStringLiteral("Toggle navigation"));
    connect(nav_toggle, &QPushButton::clicked, this,
            [this] { sidebar_->setVisible(!sidebar_->isVisible()); });
    tb->addWidget(nav_toggle);
    auto* queue_toggle = new QPushButton(QStringLiteral("☷"), topbar_);
    queue_toggle->setObjectName("IconButton");
    queue_toggle->setFixedSize(40, 40);
    queue_toggle->setToolTip(QStringLiteral("Toggle playlist panel"));
    connect(queue_toggle, &QPushButton::clicked, this, [this] {
        playlist_view_->parentWidget()->setVisible(
            !playlist_view_->parentWidget()->isVisible());
    });
    tb->addWidget(queue_toggle);
    col->addWidget(topbar_);

    // Stage: video surface + visualizer switch.
    auto* stage = new QWidget(player_page_);
    stage_stack_ = new QStackedLayout(stage);
    stage_stack_->setContentsMargins(0, 0, 0, 0);
    surface_ = new VideoSurface(stage);
    visualizer_ = new VisualizerWidget(stage);
    // Linux parity (main_window.py _empty_hint): centered "Drop media here"
    // placeholder with icon + radial gradient, shown while no media plays.
    stage_empty_ = new QFrame(stage);
    stage_empty_->setStyleSheet(QStringLiteral(
        "background: qradialgradient(cx:0.5, cy:0.5, radius:0.9, "
        "stop:0 #291014, stop:1 #0b0d10); border: none;"));
    auto* eh_layout = new QVBoxLayout(stage_empty_);
    eh_layout->setContentsMargins(24, 24, 24, 24);
    eh_layout->setSpacing(6);
    eh_layout->addStretch();
    auto* eh_icon = new QLabel(stage_empty_);
    const QString icon_path = QCoreApplication::applicationDirPath() + QStringLiteral(
        "/../assets/web_casu_icon.png");
    if (QFileInfo::exists(icon_path)) {
        QPixmap pix(icon_path);
        if (!pix.isNull()) eh_icon->setPixmap(
            pix.scaledToWidth(72, Qt::SmoothTransformation));
    }
    eh_icon->setAlignment(Qt::AlignCenter);
    eh_icon->setStyleSheet(QStringLiteral("background: transparent;"));
    eh_layout->addWidget(eh_icon);
    auto* eh_title = new QLabel(QStringLiteral("Drop media here"), stage_empty_);
    eh_title->setObjectName("NowPlayingTitle");
    eh_title->setStyleSheet(QStringLiteral("background: transparent; font-size: 18px;"));
    eh_title->setAlignment(Qt::AlignCenter);
    eh_layout->addWidget(eh_title);
    auto* eh_meta = new QLabel(QStringLiteral(
        "Audio, video, CASU, playlists and streams — "
        "\u201CChoose files\u201D in the playlist panel, or drag & drop"), stage_empty_);
    eh_meta->setObjectName("NowPlayingMeta");
    eh_meta->setStyleSheet(QStringLiteral("background: transparent;"));
    eh_meta->setAlignment(Qt::AlignCenter);
    eh_meta->setWordWrap(true);
    eh_layout->addWidget(eh_meta);
    eh_layout->addStretch();
    stage_stack_->addWidget(surface_);
    stage_stack_->addWidget(visualizer_);
    stage_stack_->addWidget(stage_empty_);
    stage_stack_->setCurrentIndex(2);  // empty until media plays
    col->addWidget(stage, 1);

    // Toast overlay (web #toast): transient message above the stage.
    toast_label_ = new QLabel(stage);
    toast_label_->setObjectName("Toast");
    toast_label_->setAttribute(Qt::WA_TransparentForMouseEvents);
    toast_label_->hide();
    toast_timer_ = new QTimer(this);
    toast_timer_->setSingleShot(true);
    connect(toast_timer_, &QTimer::timeout, this, [this] { toast_label_->hide(); });

    // Drop overlay (Linux parity): red "DROP TO PLAY / ADD TO QUEUE" banner
    // shown while dragging files/URLs over the stage.
    drop_overlay_ = new QLabel(QStringLiteral("DROP TO PLAY / ADD TO QUEUE"), player_page_);
    drop_overlay_->setAlignment(Qt::AlignCenter);
    drop_overlay_->setStyleSheet(QStringLiteral(
        "background: #07090bcc; border: 2px solid #ff1e2d; border-radius: 10px;"
        " color: #ff1e2d; font-size: 16px; font-weight: 800;"));
    drop_overlay_->hide();

    build_transport();
    col->addWidget(transport_frame_);

    // Diagnostics bar (Linux parity): four status cards above the stage.
    // The cards WRAP to a second row on narrow windows: at the 980 px
    // minimum the player column is ~360 px wide and four fixed cards
    // clipped their label text halfway (user-reported "Schriften nicht
    // ganz sichtbar" in non-fullscreen windows).
    diagnostics_bar_ = new QFrame(player_page_);
    diagnostics_bar_->setObjectName("Panel");
    auto* diag_layout = new FlowLayout(diagnostics_bar_, 8, 6);
    diag_layout->setContentsMargins(12, 8, 12, 8);
    const std::vector<std::pair<const char*, const char*>> kDiagCards = {
        {"SEGMENTED PLAYBACK", "unavailable"},
        {"LIVE GUIDE", "no EPG loaded"},
        {"INTEGRITY MODE", "unavailable"},
        {"CASU SUPPORT", "Legacy backend"},
    };
    for (const auto& [title, def] : kDiagCards) {
        auto* card = new QFrame(diagnostics_bar_);
        card->setObjectName("Panel");
        auto* cl = new QVBoxLayout(card);
        cl->setContentsMargins(12, 8, 12, 8);
        cl->setSpacing(3);
        auto* t = new QLabel(QString::fromLatin1(title), card);
        t->setObjectName("PanelTitle");
        cl->addWidget(t);
        auto* v = new QLabel(QString::fromLatin1(def), card);
        v->setObjectName("PanelValue");
        cl->addWidget(v);
        diag_layout->addWidget(card);
        diag_labels_.insert(QString::fromLatin1(title), v);
    }
    col->addWidget(diagnostics_bar_);
    pages_->addWidget(player_page_);
}

// ------------------------------------------------------------------ transport

namespace {
class QueueTree : public QTreeWidget {
public:
    using QTreeWidget::QTreeWidget;
    std::function<void()> on_delete_key;
    std::function<void(const QStringList&)> on_reordered;
protected:
    void keyPressEvent(QKeyEvent* event) override {
        // Delete/Backspace removes the selected queue rows (Linux parity).
        if ((event->key() == Qt::Key_Delete || event->key() == Qt::Key_Backspace) &&
            on_delete_key) {
            on_delete_key();
            event->accept();
            return;
        }
        QTreeWidget::keyPressEvent(event);
    }
    void dropEvent(QDropEvent* event) override {
        // Drag-reorder of top-level queue rows (Linux QueueTree parity).
        QStringList before;
        for (int i = 0; i < topLevelItemCount(); ++i)
            before.append(topLevelItem(i)->data(0, Qt::UserRole).toString());
        QTreeWidget::dropEvent(event);
        if (event->isAccepted() && on_reordered && topLevelItemCount() > 0) {
            QStringList after;
            for (int i = 0; i < topLevelItemCount(); ++i)
                after.append(topLevelItem(i)->data(0, Qt::UserRole).toString());
            if (after != before) on_reordered(after);
        }
    }
};

QPushButton* make_transport_button(const QString& text, QWidget* parent, const QString& tooltip) {
    auto* b = new QPushButton(text, parent);
    b->setObjectName("TransportButton");
    b->setToolTip(tooltip);
    return b;
}

QString provider_status_text() {
    // Linux parity: live capability check of the external providers.
    auto has = [](const QString& name) {
        return !QStandardPaths::findExecutable(name).isEmpty();
    };
    QStringList lines;
    lines << QStringLiteral("libVLC (legacy playback): bundled");
#ifdef CASU_HAS_LIBAV
    lines << QStringLiteral("FFmpeg/libav (convert/analysis): bundled");
#else
    lines << QStringLiteral("FFmpeg (convert/analysis): %1").arg(
        has(QStringLiteral("ffmpeg")) ? QStringLiteral("✓") : QStringLiteral("✗ missing"));
#endif
    lines << QStringLiteral("yt-dlp (YouTube provider): %1").arg(
        has(QStringLiteral("yt-dlp")) ? QStringLiteral("✓") : QStringLiteral("✗ missing"));
    const bool has_deno = has(QStringLiteral("deno"));
    lines << QStringLiteral("spotDL (Spotify provider): %1").arg(
        has(QStringLiteral("spotdl")) ? QStringLiteral("✓")
                                      : QStringLiteral("✗ not installed"));
    lines << QStringLiteral("Deno (optional spotDL helper): %1").arg(
        has_deno ? QStringLiteral("✓") : QStringLiteral("– optional"));
    return lines.join(QStringLiteral("\n"));
}

QString queue_badge_for(const QString& path) {
    // Linux parity (main_window.py _badge_for): short type badge per entry.
    const QString low = path.toLower();
    if (low.startsWith(QStringLiteral("http://")) ||
        low.startsWith(QStringLiteral("https://"))) {
        if (low.contains(QStringLiteral("youtube.com")) ||
            low.contains(QStringLiteral("youtu.be")))
            return QStringLiteral("YT");
        return QStringLiteral("STREAM");
    }
    if (low.startsWith(QStringLiteral("rtsp://"))) return QStringLiteral("RTSP");
    if (low.startsWith(QStringLiteral("rtmp://"))) return QStringLiteral("RTMP");
    if (low.startsWith(QStringLiteral("udp://")) ||
        low.startsWith(QStringLiteral("srt://")))
        return QStringLiteral("UDP");
    const QString ext = QFileInfo(path).suffix().toLower();
    static const QStringList audio_exts = {
        "mp3", "flac", "wav", "aac", "ogg", "opus", "m4a", "wma",
        "aiff", "alac", "ape", "wv", "tta", "dts", "mpc", "voc", "au"};
    static const QStringList video_exts = {
        "mp4", "mkv", "webm", "avi", "mov", "m4v", "flv", "wmv",
        "mpeg", "mpg", "m2ts", "mts", "ts", "vob", "ogv", "3gp",
        "divx", "rm", "rmvb", "mxf", "asf"};
    if (audio_exts.contains(ext)) return ext.toUpper();
    if (video_exts.contains(ext)) return ext.toUpper();
    if (ext == "m3u" || ext == "m3u8" || ext == "pls" || ext == "wpl" ||
        ext == "xspf" || ext == "jspf" || ext == "asx" || ext == "wmx" ||
        ext == "wvx" || ext == "rmp" || ext == "ram")
        return QStringLiteral("PLAYLIST");
    if (ext == "casu" || ext == "mp5") return QStringLiteral("CASU");
    return QStringLiteral("MEDIA");
}
}  // namespace

void MainWindow::build_transport() {
    auto* frame = new QFrame(player_page_);
    frame->setObjectName("Panel");
    auto* layout = new QVBoxLayout(frame);
    layout->setContentsMargins(14, 6, 14, 8);
    layout->setSpacing(4);

    seek_slider_ = new QSlider(Qt::Horizontal, frame);
    seek_slider_->setRange(0, 0);
    seek_slider_->setCursor(Qt::PointingHandCursor);
    layout->addWidget(seek_slider_);

    auto* time_row = new QHBoxLayout();
    time_current_ = new QLabel(QStringLiteral("00:00"), frame);
    time_current_->setObjectName("TimeLabel");
    time_total_ = new QLabel(QStringLiteral("00:00"), frame);
    time_total_->setObjectName("TimeLabel");
    time_row->addWidget(time_current_);
    time_row->addStretch();
    time_row->addWidget(time_total_);
    layout->addLayout(time_row);

    auto* controls = new QHBoxLayout();
    controls->setSpacing(6);

    shuffle_btn_ = make_transport_button(QStringLiteral("⤨"), frame, QStringLiteral("Shuffle"));
    shuffle_btn_->setCheckable(true);
    shuffle_btn_->setChecked(playlist_.shuffle);
    shuffle_btn_->setProperty("on", playlist_.shuffle ? QStringLiteral("true")
                                                      : QStringLiteral("false"));
    connect(shuffle_btn_, &QPushButton::toggled, this, [this](bool on) {
        playlist_.shuffle = on;
        app_settings_.player.shuffle = on;
        settings_->save(app_settings_.player);
        // Linux parity: highlight the active state via TransportButton[on=true].
        shuffle_btn_->setProperty("on", on ? QStringLiteral("true") : QStringLiteral("false"));
        shuffle_btn_->style()->unpolish(shuffle_btn_);
        shuffle_btn_->style()->polish(shuffle_btn_);
        status(on ? QStringLiteral("Shuffle on") : QStringLiteral("Shuffle off"));
    });
    controls->addWidget(shuffle_btn_);

    auto* prev_btn = make_transport_button(QStringLiteral("«"), frame, QStringLiteral("Previous"));
    connect(prev_btn, &QPushButton::clicked, this, &MainWindow::play_previous);
    controls->addWidget(prev_btn);

    play_btn_ = make_transport_button(QStringLiteral("▶"), frame, QStringLiteral("Play / Pause"));
    play_btn_->setObjectName("PlayButton");
    play_btn_->setFixedSize(metrics().play_button, metrics().play_button);
    connect(play_btn_, &QPushButton::clicked, this, &MainWindow::toggle_playback);
    controls->addWidget(play_btn_);

    auto* next_btn = make_transport_button(QStringLiteral("»"), frame, QStringLiteral("Next"));
    connect(next_btn, &QPushButton::clicked, this, [this] { play_next(false); });
    controls->addWidget(next_btn);

    repeat_btn_ = make_transport_button(
        QStringLiteral("↻"), frame, QStringLiteral("Repeat off / all / one"));
    connect(repeat_btn_, &QPushButton::clicked, this, &MainWindow::cycle_repeat);
    controls->addWidget(repeat_btn_);

    auto* snapshot_btn = make_transport_button(QStringLiteral("▧"), frame, QStringLiteral("Snapshot"));
    connect(snapshot_btn, &QPushButton::clicked, this, &MainWindow::save_snapshot);
    controls->addWidget(snapshot_btn);

    ab_btn_ = make_transport_button(QStringLiteral("A–B"), frame, QStringLiteral("A–B loop (set A, set B, clear)"));
    ab_btn_->setCheckable(true);
    connect(ab_btn_, &QPushButton::clicked, this, &MainWindow::cycle_ab_loop);
    controls->addWidget(ab_btn_);

    rate_btn_ = make_transport_button(QStringLiteral("1×"), frame, QStringLiteral("Playback speed"));
    connect(rate_btn_, &QPushButton::clicked, this, &MainWindow::cycle_rate);
    controls->addWidget(rate_btn_);

    viz_btn_ = make_transport_button(QStringLiteral("〰"), frame, QStringLiteral("Visualizer"));
    viz_btn_->setCheckable(true);
    connect(viz_btn_, &QPushButton::clicked, this, &MainWindow::on_visualizer_toggle);
    controls->addWidget(viz_btn_);

    record_btn_ = make_transport_button(QStringLiteral("●"), frame, QStringLiteral("Record"));
    record_btn_->setCheckable(true);
    connect(record_btn_, &QPushButton::clicked, this, &MainWindow::on_recording_toggle);
    controls->addWidget(record_btn_);

    controls->addStretch();

    mute_btn_ = new QPushButton(muted_ ? QStringLiteral("×") : QStringLiteral("♪"), frame);
    mute_btn_->setObjectName("IconButton");
    mute_btn_->setFixedSize(32, 32);
    mute_btn_->setToolTip(QStringLiteral("Mute / Unmute"));
    connect(mute_btn_, &QPushButton::clicked, this, &MainWindow::toggle_mute);
    controls->addWidget(mute_btn_);

    volume_slider_ = new QSlider(Qt::Horizontal, frame);
    volume_slider_->setObjectName("VolumeSlider");
    volume_slider_->setRange(0, 200);
    volume_slider_->setValue(volume_);
    volume_slider_->setFixedWidth(100);
    connect(volume_slider_, &QSlider::valueChanged, this, &MainWindow::set_volume);
    controls->addWidget(volume_slider_);

    auto* fullscreen_btn = make_transport_button(QStringLiteral("□"), frame, QStringLiteral("Fullscreen (F)"));
    connect(fullscreen_btn, &QPushButton::clicked, this, &MainWindow::toggle_fullscreen);
    controls->addWidget(fullscreen_btn);

    auto* more_btn = make_transport_button(QStringLiteral("⋯"), frame, QStringLiteral("More controls"));
    auto* more_menu = new QMenu(frame);
    more_menu->addAction(QStringLiteral("■ Stop"), this, &MainWindow::stop_playback);
    more_menu->addAction(QStringLiteral("Media info (Ctrl+I)"), this, &MainWindow::show_media_info);
    more_menu->addAction(QStringLiteral("Rec-Settings…"), this, &MainWindow::show_record_settings_dialog);
    more_menu->addAction(QStringLiteral("‹ Rewind 10s"), this, [this] { seek_to(qMax(0.0, controller_->position() - 10.0)); });
    more_menu->addAction(QStringLiteral("› Forward 10s"), this, [this] { seek_to(controller_->position() + 10.0); });
    more_menu->addAction(QStringLiteral("Open file…"), this, &MainWindow::choose_files);
    more_menu->addAction(QStringLiteral("Add URL…"), this, &MainWindow::add_url);

    // Linux parity: track / chapter / device / delay / frame-step controls.
    auto* chapters_menu = more_menu->addMenu(QStringLiteral("Chapters"));
    connect(chapters_menu, &QMenu::aboutToShow, this, [this, chapters_menu] {
        chapters_menu->clear();
        if (!backend_) return;
        const auto chapters = backend_->chapters();
        if (chapters.empty()) {
            chapters_menu->addAction(QStringLiteral("(no chapters)"));
            return;
        }
        for (const auto& ch : chapters)
            chapters_menu->addAction(QString::fromStdString(ch.name), this,
                                     [this, ch] {
                try { backend_->set_chapter(ch.index); }
                catch (const casu::playback::PlaybackError& e) {
                    status(QString::fromStdString(e.what()));
                }
            });
    });
    auto* tracks_menu = more_menu->addMenu(QStringLiteral("Tracks"));
    connect(tracks_menu, &QMenu::aboutToShow, this, [this, tracks_menu] {
        tracks_menu->clear();
        if (!backend_) { tracks_menu->addAction(QStringLiteral("(no media)")); return; }
        auto add_group = [this, tracks_menu](const QString& title,
                                             const std::vector<casu::playback::TrackInfo>& descs,
                                             int current, auto setter) {
            auto* sub = tracks_menu->addMenu(title);
            if (descs.empty()) { sub->addAction(QStringLiteral("(none)")); return; }
            for (const auto& t : descs) {
                QAction* act = sub->addAction(
                    QString::fromStdString(t.name), this, [this, setter, t] {
                        try { setter(t.id); }
                        catch (const casu::playback::PlaybackError& e) {
                            status(QString::fromStdString(e.what()));
                        }
                    });
                act->setCheckable(true);
                act->setChecked(t.id == current);
            }
        };
        add_group(QStringLiteral("Audio"),
                  backend_->audio_track_descriptions(), backend_->audio_track(),
                  [this](int id) { backend_->set_audio_track(id); persist_media_preferences(); });
        add_group(QStringLiteral("Video"),
                  backend_->video_track_descriptions(), backend_->video_track(),
                  [this](int id) { backend_->set_video_track(id); persist_media_preferences(); });
        add_group(QStringLiteral("Subtitles"),
                  backend_->subtitle_track_descriptions(), backend_->subtitle_track(),
                  [this](int id) { backend_->set_subtitle_track(id); persist_media_preferences(); });
    });
    auto* devices_menu = more_menu->addMenu(QStringLiteral("Audio device"));
    connect(devices_menu, &QMenu::aboutToShow, this, [this, devices_menu] {
        devices_menu->clear();
        if (!backend_) { devices_menu->addAction(QStringLiteral("(no media)")); return; }
        const auto devices = backend_->audio_devices();
        if (devices.empty()) { devices_menu->addAction(QStringLiteral("(system default)")); return; }
        for (const auto& d : devices)
            devices_menu->addAction(QString::fromStdString(d.name), this,
                                    [this, d] {
                try { backend_->set_audio_device(d.name); }
                catch (const casu::playback::PlaybackError& e) {
                    status(QString::fromStdString(e.what()));
                }
            });
    });
    more_menu->addAction(QStringLiteral("Load subtitle file…"), this, [this] {
        if (!backend_) { status(QStringLiteral("No active media backend")); return; }
        const QString file = QFileDialog::getOpenFileName(
            this, QStringLiteral("Load subtitle"), QDir::homePath(),
            QStringLiteral("Subtitles (*.srt *.ass *.ssa *.sub *.vtt);;All files (*)"));
        if (file.isEmpty()) return;
        try {
            if (backend_->load_subtitle_file(file.toStdString()))
                status(QStringLiteral("External subtitle loaded · %1").arg(QFileInfo(file).fileName()));
            else
                status(QStringLiteral("Could not load subtitle: %1").arg(QFileInfo(file).fileName()));
        } catch (const casu::playback::PlaybackError& e) {
            status(QString::fromStdString(e.what()));
        }
    });
    more_menu->addAction(QStringLiteral("A/V delays…"), this, [this] {
        if (!backend_) { status(QStringLiteral("No active media backend")); return; }
        QDialog dlg(this);
        dlg.setWindowTitle(QStringLiteral("Audio / subtitle delay"));
        auto* layout = new QVBoxLayout(&dlg);
        layout->setSpacing(10);
        auto* audio_row = new QHBoxLayout();
        audio_row->addWidget(new QLabel(QStringLiteral("Audio delay (ms)")));
        auto* audio_spin = new QSpinBox();
        audio_spin->setRange(-10000, 10000);
        audio_spin->setValue(0);
        audio_row->addWidget(audio_spin);
        layout->addLayout(audio_row);
        auto* sub_row = new QHBoxLayout();
        sub_row->addWidget(new QLabel(QStringLiteral("Subtitle delay (ms)")));
        auto* sub_spin = new QSpinBox();
        sub_spin->setRange(-10000, 10000);
        sub_spin->setValue(0);
        sub_row->addWidget(sub_spin);
        layout->addLayout(sub_row);
        auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
        connect(buttons, &QDialogButtonBox::accepted, &dlg, &QDialog::accept);
        connect(buttons, &QDialogButtonBox::rejected, &dlg, &QDialog::reject);
        layout->addWidget(buttons);
        if (dlg.exec() != QDialog::Accepted) return;
        try {
            backend_->set_audio_delay(audio_spin->value());
            backend_->set_subtitle_delay(sub_spin->value());
            audio_delay_ms_ = audio_spin->value();
            subtitle_delay_ms_ = sub_spin->value();
            persist_media_preferences();  // Linux parity: remember per media
            status(QStringLiteral("Delays set: audio %1 ms, subtitle %2 ms")
                       .arg(audio_spin->value()).arg(sub_spin->value()));
        } catch (const casu::playback::PlaybackError& e) {
            status(QString::fromStdString(e.what()));
        }
    });
    more_menu->addAction(QStringLiteral("Frame step"), this, [this] {
        if (!backend_) { status(QStringLiteral("No active media backend")); return; }
        try {
            paused_ = true;
            backend_->next_frame();
            update_play_button();
            status(QStringLiteral("Advanced one decoded frame"));
        } catch (const casu::playback::PlaybackError& e) {
            status(QString::fromStdString(e.what()));
        }
    });
    more_btn->setMenu(more_menu);
    controls->addWidget(more_btn);

    layout->addLayout(controls);
    frame->setObjectName("Panel");
    transport_frame_ = frame;

    // Fullscreen overlay (Linux parity): floating transport bar that appears
    // in fullscreen on mouse move and auto-hides after 3 s.
    fs_overlay_ = new QWidget(this);
    fs_overlay_->setObjectName("FsOverlay");
    fs_overlay_->setAttribute(Qt::WA_StyledBackground, true);
    // Reference styling: translucent dark fill, hairline border, r8.
    fs_overlay_->setStyleSheet(QStringLiteral(
        "background: #07090bdd; border: 1px solid #252a30; "
        "border-radius: 8px;"));
    auto* fs_layout = new QHBoxLayout(fs_overlay_);
    fs_layout->setContentsMargins(10, 6, 10, 6);
    fs_layout->setSpacing(6);
    fs_title_ = new QLabel(QStringLiteral(""), fs_overlay_);
    fs_title_->setStyleSheet(QStringLiteral("color: #e8ecf1;"));
    fs_layout->addWidget(fs_title_, 1);
    fs_play_btn_ = new QPushButton(QStringLiteral("| |"), fs_overlay_);
    fs_play_btn_->setObjectName("PlayButton");
    fs_play_btn_->setFixedSize(40, 28);
    connect(fs_play_btn_, &QPushButton::clicked, this, &MainWindow::toggle_playback);
    fs_layout->addWidget(fs_play_btn_);
    fs_time_ = new QLabel(QStringLiteral("00:00 / 00:00"), fs_overlay_);
    fs_time_->setStyleSheet(QStringLiteral("color: #e8ecf1;"));
    fs_layout->addWidget(fs_time_);
    auto* fs_exit = new QPushButton(QStringLiteral("✕"), fs_overlay_);
    fs_exit->setObjectName("IconButton");
    fs_exit->setFixedSize(28, 28);
    fs_exit->setToolTip(QStringLiteral("Exit fullscreen (F)"));
    connect(fs_exit, &QPushButton::clicked, this, &MainWindow::exit_fullscreen_ui);
    fs_layout->addWidget(fs_exit);
    fs_overlay_->hide();
    fs_hide_timer_ = new QTimer(this);
    fs_hide_timer_->setSingleShot(true);
    fs_hide_timer_->setInterval(3000);
    connect(fs_hide_timer_, &QTimer::timeout, this, &MainWindow::hide_fs_overlay);
}

// ------------------------------------------------------------------ playlist pane

void MainWindow::remove_selected_rows(const QVector<int>& fixed_rows) {
    QVector<int> rows;
    if (!fixed_rows.isEmpty()) {
        rows = fixed_rows;
    } else {
        for (auto* it : playlist_view_->selectedItems())
            if (it->parent() == nullptr) rows.append(playlist_view_->indexOfTopLevelItem(it));
    }
    if (rows.isEmpty()) return;
    // Linux parity (_on_playlist_remove): rows that were marked but not
    // removed stay marked; clearing everything stops playback and resets the
    // now-playing caption.
    const bool clears_all = rows.size() >= playlist_.size();
    const auto& items = playlist_.items();
    QSet<QString> marked;
    for (auto* it : playlist_view_->selectedItems()) {
        if (it->parent() != nullptr) continue;
        const int row = playlist_view_->indexOfTopLevelItem(it);
        if (row >= 0 && row < items.size()) marked.insert(items[row].path);
    }
    QSet<int> removed;
    for (int row : rows)
        if (row >= 0 && row < items.size()) removed.insert(row);
    QStringList keep;
    for (int i = 0; i < items.size(); ++i)
        if (marked.contains(items[i].path) && !removed.contains(i))
            keep << items[i].path;
    playlist_.remove_many(rows);
    invalidate_seq();
    refresh_playlist();
    if (clears_all) {
        stop_playback();
        status(QStringLiteral("Playlist cleared"));
    } else if (!keep.isEmpty()) {
        reselect_playlist_rows(keep);
    }
}

void MainWindow::remove_selected_rows() {
    remove_selected_rows(QVector<int>());
}

void MainWindow::rename_queue_entry() {
    if (!playlist_view_) return;
    auto* item = playlist_view_->currentItem();
    if (!item) { status(QStringLiteral("Select a queue entry to rename.")); return; }
    if (item->parent()) item = item->parent();
    const int row = playlist_view_->indexOfTopLevelItem(item);
    if (row < 0 || row >= playlist_.items().size()) return;
    const QString current = item->text(0);
    auto* editor = new QLineEdit(playlist_view_);
    editor->setText(current);
    editor->setObjectName("IconButton");
    playlist_view_->setItemWidget(item, 0, editor);
    const QString path = item->data(0, Qt::UserRole).toString();
    connect(editor, &QLineEdit::returnPressed, this,
            [this, item, editor] { commit_queue_rename(item, editor); });
    connect(editor, &QLineEdit::editingFinished, this,
            [this, item, editor] { commit_queue_rename(item, editor); });
    editor->setFocus();
    editor->selectAll();
}

void MainWindow::commit_queue_rename(QTreeWidgetItem* item, QLineEdit* editor) {
    if (!item || !editor) return;
    const QString text = editor->text().trimmed();
    playlist_view_->removeItemWidget(item, 0);
    editor->deleteLater();
    if (!text.isEmpty()) {
        const QString path = item->data(0, Qt::UserRole).toString();
        if (!path.isEmpty()) display_titles_.insert(path, text);
        item->setText(0, text);
        status(QStringLiteral("Renamed queue entry"));
    }
}

void MainWindow::apply_queue_filter() {
    if (!playlist_view_ || !view_filter_) return;
    const int idx = view_filter_->currentIndex();
    const QString view = idx == 0 ? QStringLiteral("all")
                         : idx == 1 ? QStringLiteral("files")
                         : idx == 2 ? QStringLiteral("streams")
                         : idx == 3 ? QStringLiteral("playlists")
                         : idx == 4 ? QStringLiteral("casu")
                         : idx == 5 ? QStringLiteral("youtube")
                                    : QStringLiteral("spotify");
    const QString needle = queue_search_ ? queue_search_->text().toLower() : QString();
    int visible = 0;
    for (int i = 0; i < playlist_view_->topLevelItemCount(); ++i) {
        QTreeWidgetItem* item = playlist_view_->topLevelItem(i);
        const QString path = item->data(0, Qt::UserRole).toString();
        const QString low = path.toLower();
        const bool is_url = low.startsWith(QStringLiteral("http://")) ||
                            low.startsWith(QStringLiteral("https://")) ||
                            low.startsWith(QStringLiteral("rtsp://")) ||
                            low.startsWith(QStringLiteral("rtmp://"));
        const bool is_playlist = playlist_.is_playlist_row(i);
        bool show = (view == "all") ||
                    (view == "playlists" && is_playlist) ||
                    (view == "files" && !is_url && !is_playlist) ||
                    (view == "casu" && (low.endsWith(".casu") || low.endsWith(".mp5"))) ||
                    (view == "youtube" && (low.contains("youtube.com") || low.contains("youtu.be"))) ||
                    (view == "spotify" && low.contains("spotify.com")) ||
                    (view == "streams" && is_url && !is_playlist);
        if (show && !needle.isEmpty()) {
            show = item->text(0).toLower().contains(needle) ||
                   QFileInfo(path).fileName().toLower().contains(needle);
            if (show && item->childCount() > 0) {
                // A playlist group matches when any child matches.
                show = false;
                for (int k = 0; k < item->childCount(); ++k)
                    if (item->child(k)->text(0).toLower().contains(needle)) { show = true; break; }
            }
        }
        item->setHidden(!show);
        if (show) ++visible;
    }
    if (empty_hint_) empty_hint_->setVisible(visible == 0);
}

void MainWindow::set_queue_view_filter(const QString& view) {
    if (!view_filter_) return;
    static const QStringList keys = {
        QStringLiteral("all"), QStringLiteral("local"), QStringLiteral("streams"),
        QStringLiteral("playlists"), QStringLiteral("casu"), QStringLiteral("youtube"),
        QStringLiteral("spotify"),
    };
    const int idx = keys.indexOf(view.toLower());
    if (idx >= 0 && view_filter_->currentIndex() != idx) {
        view_filter_->setCurrentIndex(idx);  // triggers apply_queue_filter()
    } else if (idx >= 0) {
        apply_queue_filter();
    }
}

void MainWindow::build_playlist_pane() {
    auto* pane = new QFrame(this);
    pane->setObjectName("PlaylistPane");
    pane->setFixedWidth(metrics().right_panel_width);
    auto* layout = new QVBoxLayout(pane);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(0);

    // Linux parity (PlaylistPane header): title + sub + view combo + actions.
    auto* header = new QFrame(pane);
    header->setObjectName("TopBar");
    auto* header_layout = new QVBoxLayout(header);
    header_layout->setContentsMargins(12, 14, 12, 8);
    auto* title = new QLabel(QStringLiteral("PLAYLIST"), header);
    title->setObjectName("NowPlayingTitle");
    title->setStyleSheet(QStringLiteral("font-size: 14px; background: transparent;"));
    header_layout->addWidget(title);
    auto* sub = new QLabel(QStringLiteral("Queue · expandable · drag to reorder"), header);
    sub->setObjectName("NowPlayingMeta");
    header_layout->addWidget(sub);
    view_filter_ = new QComboBox(header);
    view_filter_->setObjectName("IconButton");
    view_filter_->addItems({"All items", "Local files", "Streams / IPTV", "Playlists",
                            "CASU", "YouTube", "Spotify"});
    connect(view_filter_, &QComboBox::currentIndexChanged,
            this, [this] { apply_queue_filter(); });
    header_layout->addWidget(view_filter_);
    auto* actions = new QHBoxLayout();
    actions->setSpacing(6);
    auto* choose_btn = new QPushButton(QStringLiteral("Choose files"), header);
    choose_btn->setObjectName("PrimaryButton");
    choose_btn->setToolTip(QStringLiteral("Add media files to the queue (Ctrl+O)"));
    connect(choose_btn, &QPushButton::clicked, this, &MainWindow::choose_files);
    actions->addWidget(choose_btn, 1);
    auto* url_btn = new QPushButton(QStringLiteral("Add URL"), header);
    url_btn->setObjectName("IconButton");
    url_btn->setToolTip(QStringLiteral("Add a network stream URL (Ctrl+L)"));
    connect(url_btn, &QPushButton::clicked, this, &MainWindow::add_url);
    actions->addWidget(url_btn);
    header_layout->addLayout(actions);
    layout->addWidget(header);

    playlist_view_ = new QueueTree(pane);
    playlist_view_->setHeaderHidden(true);
    playlist_view_->setColumnCount(2);
    playlist_view_->setSelectionMode(QAbstractItemView::ExtendedSelection);
    playlist_view_->setContextMenuPolicy(Qt::CustomContextMenu);
    playlist_view_->setDragEnabled(true);
    playlist_view_->setAcceptDrops(true);
    playlist_view_->setDragDropMode(QAbstractItemView::InternalMove);
    playlist_view_->setDropIndicatorShown(true);
    static_cast<QueueTree*>(playlist_view_)->on_delete_key = [this] { remove_selected_rows(); };
    static_cast<QueueTree*>(playlist_view_)->on_reordered =
        [this](const QStringList& order) {
            playlist_.reorder(order);
            invalidate_seq();
            refresh_playlist();
            status(QStringLiteral("Queue reordered"));
        };
    connect(playlist_view_, &QTreeWidget::itemDoubleClicked, this,
            &MainWindow::playlist_double_clicked);
    connect(playlist_view_, &QTreeWidget::itemClicked, this, [this](QTreeWidgetItem* item, int) {
        // Single click: only toggle expand for playlist groups — play requires double-click.
        if (item->parent() == nullptr) {
            const int row = playlist_view_->indexOfTopLevelItem(item);
            if (row < 0) return;
            if (playlist_.is_playlist_row(row)) {
                item->setExpanded(!item->isExpanded());
            }
        }
    });
    empty_hint_ = new QLabel(QStringLiteral("No media queued — add files, a URL or load a playlist."), pane);
    empty_hint_->setObjectName("StatusText");
    empty_hint_->setWordWrap(true);
    empty_hint_->setAlignment(Qt::AlignCenter);
    layout->addWidget(empty_hint_);
    connect(playlist_view_, &QTreeWidget::itemExpanded, this, [this](QTreeWidgetItem* item) {
        if (item->parent() == nullptr) {
            expanded_groups_.insert(item->data(0, Qt::UserRole).toString());
            expand_playlist_group(item);
        }
    });
    connect(playlist_view_, &QTreeWidget::itemCollapsed, this, [this](QTreeWidgetItem* item) {
        if (item->parent() == nullptr)
            expanded_groups_.remove(item->data(0, Qt::UserRole).toString());
    });
    connect(playlist_view_, &QTreeWidget::customContextMenuRequested, this,
            &MainWindow::playlist_context_menu);
    layout->addWidget(playlist_view_, 1);

    auto* tools = new QHBoxLayout();
    auto* up_btn = new QPushButton(QStringLiteral("↑"), pane);
    auto* down_btn = new QPushButton(QStringLiteral("↓"), pane);
    auto* remove_btn = new QPushButton(QStringLiteral("×"), pane);
    auto* rename_btn = new QPushButton(QStringLiteral("✎"), pane);
    auto* load_btn = new QPushButton(QStringLiteral("Load"), pane);
    auto* save_btn = new QPushButton(QStringLiteral("Save"), pane);
    for (auto* b : {up_btn, down_btn, remove_btn, rename_btn, load_btn, save_btn}) {
        b->setObjectName("IconButton");
        tools->addWidget(b);
    }
    connect(up_btn, &QPushButton::clicked, this, [this] {
        QVector<int> rows;
        for (auto* it : playlist_view_->selectedItems())
            if (it->parent() == nullptr) rows.append(playlist_view_->indexOfTopLevelItem(it));
        move_playlist_rows(rows, -1);
    });
    connect(down_btn, &QPushButton::clicked, this, [this] {
        QVector<int> rows;
        for (auto* it : playlist_view_->selectedItems())
            if (it->parent() == nullptr) rows.append(playlist_view_->indexOfTopLevelItem(it));
        move_playlist_rows(rows, 1);
    });
    connect(remove_btn, &QPushButton::clicked, this, [this] {
        remove_selected_rows();
    });
    rename_btn->setToolTip(QStringLiteral("Rename the selected queue entry"));
    connect(rename_btn, &QPushButton::clicked, this, &MainWindow::rename_queue_entry);
    connect(load_btn, &QPushButton::clicked, this, &MainWindow::load_playlist_file);
    connect(save_btn, &QPushButton::clicked, this, &MainWindow::save_playlist_file);
    layout->addLayout(tools);

    // Linux parity (PlaylistPane footer): shuffle + repeat buttons.
    auto* footer = new QFrame(pane);
    auto* footer_layout = new QHBoxLayout(footer);
    footer_layout->setContentsMargins(12, 4, 12, 12);
    auto* shuffle_label = new QPushButton(QStringLiteral("Shuffle off"), footer);
    shuffle_label->setObjectName("IconButton");
    shuffle_label->setCheckable(true);
    connect(shuffle_label, &QPushButton::clicked, this, [this, shuffle_label] {
        playlist_.shuffle = !playlist_.shuffle;
        shuffle_label->setText(playlist_.shuffle ? QStringLiteral("Shuffle on")
                                                 : QStringLiteral("Shuffle off"));
        status(playlist_.shuffle ? QStringLiteral("Shuffle on")
                                 : QStringLiteral("Shuffle off"));
    });
    footer_layout->addWidget(shuffle_label);
    auto* repeat_label = new QPushButton(QStringLiteral("Repeat off"), footer);
    repeat_label->setObjectName("IconButton");
    connect(repeat_label, &QPushButton::clicked, this, [this, repeat_label] {
        using R = PlaylistModel::RepeatMode;
        if (playlist_.repeat == R::Off) playlist_.repeat = R::All;
        else if (playlist_.repeat == R::All) playlist_.repeat = R::One;
        else playlist_.repeat = R::Off;
        repeat_label->setText(playlist_.repeat == R::All ? QStringLiteral("Repeat all")
                                : playlist_.repeat == R::One ? QStringLiteral("Repeat one")
                                                             : QStringLiteral("Repeat off"));
        status(repeat_label->text());
    });
    footer_layout->addWidget(repeat_label);
    footer_layout->addStretch();
    layout->addWidget(footer);
}

// ------------------------------------------------------------------ other pages

void MainWindow::build_library_page() {
    // Linux parity (LibraryPage): search + artist/album/genre/favorites modes.
    auto* page = new QWidget(this);
    auto* layout = new QVBoxLayout(page);
    layout->setContentsMargins(20, 18, 20, 16);
    layout->setSpacing(10);
    auto* title = new QLabel(QStringLiteral("LIBRARY"), page);
    title->setObjectName("NowPlayingTitle");
    layout->addWidget(title);

    auto* top = new QHBoxLayout();
    library_search_ = new QLineEdit(page);
    library_search_->setObjectName("IconButton");
    library_search_->setPlaceholderText(
        QStringLiteral("Search library · title, artist, album, genre…"));
    connect(library_search_, &QLineEdit::textChanged, this,
            [this](const QString&) { refresh_library(); });
    top->addWidget(library_search_, 1);

    library_mode_ = new QComboBox(page);
    library_mode_->setObjectName("IconButton");
    library_mode_->addItem(QStringLiteral("All Tracks"), QStringLiteral("all"));
    library_mode_->addItem(QStringLiteral("Artists"), QStringLiteral("artists"));
    library_mode_->addItem(QStringLiteral("Albums"), QStringLiteral("albums"));
    library_mode_->addItem(QStringLiteral("Genres"), QStringLiteral("genres"));
    library_mode_->addItem(QStringLiteral("Favorites"), QStringLiteral("favorites"));
    library_mode_->addItem(QStringLiteral("Playlists"), QStringLiteral("playlists"));
    connect(library_mode_, &QComboBox::currentIndexChanged, this,
            [this](int) { refresh_library(); });
    top->addWidget(library_mode_);

    auto* refresh_btn = new QPushButton(QStringLiteral("Refresh"), page);
    refresh_btn->setObjectName("IconButton");
    connect(refresh_btn, &QPushButton::clicked, this, [this] { refresh_library(); });
    top->addWidget(refresh_btn);
    layout->addLayout(top);

    auto* split = new QSplitter(Qt::Horizontal, page);
    library_splitter_ = split;
    library_groups_ = new QListWidget(split);
    library_groups_->setObjectName("QueueTree");
    connect(library_groups_, &QListWidget::currentItemChanged, this,
            [this](QListWidgetItem* current, QListWidgetItem*) {
                if (!current) return;
                const QString mode = library_mode_->currentData().toString();
                if (mode == "playlists") on_playlist_group_selected(current);
                else refresh_library();
            });
    split->addWidget(library_groups_);
    library_tracks_ = new QListWidget(split);
    library_tracks_->setObjectName("QueueTree");
    library_tracks_->setSelectionMode(QAbstractItemView::ExtendedSelection);
    connect(library_tracks_, &QListWidget::itemDoubleClicked, this,
            [this](QListWidgetItem* item) { on_library_add_selected(item); });
    library_tracks_->setContextMenuPolicy(Qt::CustomContextMenu);
    connect(library_tracks_, &QListWidget::customContextMenuRequested, this,
            [this](const QPoint& pos) {
                QListWidgetItem* item = library_tracks_->itemAt(pos);
                if (!item) return;
                QList<QListWidgetItem*> sel = library_tracks_->selectedItems();
                if (sel.isEmpty()) sel.append(item);
                QStringList paths;
                for (auto* s : sel) {
                    const QString p = s->data(Qt::UserRole).toString();
                    if (!p.isEmpty()) paths.append(p);
                }
                if (paths.isEmpty()) return;
                QMenu menu(library_tracks_);
                if (paths.size() == 1) {
                    const bool fav = library_->index_of(paths[0]) >= 0 &&
                                     library_->entries()[library_->index_of(paths[0])].favorite;
                    QAction* fav_act = menu.addAction(fav ? QStringLiteral("★ Unmark favorite")
                                                       : QStringLiteral("☆ Mark as favorite"));
                    QAction* add_act = menu.addAction(QStringLiteral("Add to queue"));
                    QAction* chosen = menu.exec(library_tracks_->viewport()->mapToGlobal(pos));
                    if (chosen == fav_act) {
                        library_->set_favorite(paths[0], !fav);
                        refresh_library();
                    } else if (chosen == add_act) {
                        add_files(paths);
                        status(QStringLiteral("Added to queue: %1").arg(QFileInfo(paths[0]).fileName()));
                    }
                } else {
                    bool any_fav = false;
                    for (const QString& p : paths)
                        if (int idx = library_->index_of(p); idx >= 0)
                            if (library_->entries()[idx].favorite) { any_fav = true; break; }
                    QAction* fav_act = menu.addAction(any_fav
                        ? QStringLiteral("★ Unmark favorite (%1)").arg(paths.size())
                        : QStringLiteral("☆ Mark as favorite (%1)").arg(paths.size()));
                    QAction* add_act = menu.addAction(QStringLiteral("Add to queue (%1)").arg(paths.size()));
                    QAction* chosen = menu.exec(library_tracks_->viewport()->mapToGlobal(pos));
                    if (chosen == fav_act) {
                        for (const QString& p : paths) library_->set_favorite(p, !any_fav);
                        refresh_library();
                    } else if (chosen == add_act) {
                        add_files(paths);
                        status(QStringLiteral("Added %1 tracks to queue").arg(paths.size()));
                    }
                }
            });
    split->addWidget(library_tracks_);
    split->setStretchFactor(0, 2);
    split->setStretchFactor(1, 5);
    split->setSizes({260, 720});
    layout->addWidget(split, 1);

    auto* bottom = new QHBoxLayout();
    library_count_ = new QLabel(QStringLiteral(""), page);
    library_count_->setObjectName("NowPlayingMeta");
    bottom->addWidget(library_count_);
    bottom->addStretch();
    auto* add_btn = new QPushButton(QStringLiteral("Add to queue"), page);
    add_btn->setObjectName("PrimaryButton");
    connect(add_btn, &QPushButton::clicked, this,
            [this] { on_library_add_selected(library_tracks_->currentItem()); });
    bottom->addWidget(add_btn);
    layout->addLayout(bottom);

    auto* folders_label = new QLabel(QStringLiteral("WATCHED FOLDERS"), page);
    folders_label->setObjectName("SidebarSection");
    layout->addWidget(folders_label);
    library_folders_ = new QListWidget(page);
    library_folders_->setObjectName("QueueTree");
    library_folders_->setMaximumHeight(110);
    for (const QString& folder : app_settings_.player.watched_folders)
        library_folders_->addItem(folder);
    layout->addWidget(library_folders_);
    auto* folder_row = new QHBoxLayout();
    auto* add_folder_btn = new QPushButton(QStringLiteral("Add folder…"), page);
    add_folder_btn->setObjectName("IconButton");
    connect(add_folder_btn, &QPushButton::clicked, this, [this] {
        const QString folder = QFileDialog::getExistingDirectory(
            this, QStringLiteral("Add library folder"));
        if (folder.isEmpty()) return;
        for (const QString& f : app_settings_.player.watched_folders)
            if (f == folder) return;
        app_settings_.player.watched_folders.append(folder);
        settings_->save(app_settings_.player);
        library_folders_->addItem(folder);
    });
    folder_row->addWidget(add_folder_btn);
    auto* remove_folder_btn = new QPushButton(QStringLiteral("Remove selected"), page);
    remove_folder_btn->setObjectName("IconButton");
    connect(remove_folder_btn, &QPushButton::clicked, this, [this] {
        const int row = library_folders_->currentRow();
        if (row < 0) return;
        app_settings_.player.watched_folders.removeAt(row);
        settings_->save(app_settings_.player);
        delete library_folders_->takeItem(row);
    });
    folder_row->addWidget(remove_folder_btn);
    auto* scan_btn = new QPushButton(QStringLiteral("Scan now"), page);
    scan_btn->setObjectName("IconButton");
    connect(scan_btn, &QPushButton::clicked, this, [this] { scan_library_folders(); });
    folder_row->addWidget(scan_btn);
    folder_row->addStretch();
    layout->addLayout(folder_row);
    refresh_library();
    pages_->addWidget(page);
}

void MainWindow::on_library_add_selected(QListWidgetItem* item) {
    QList<QListWidgetItem*> sel = library_tracks_->selectedItems();
    if (sel.isEmpty() && item) sel.append(item);
    if (sel.isEmpty()) return;
    QStringList paths;
    for (auto* s : sel) {
        const QString p = s->data(Qt::UserRole).toString();
        if (!p.isEmpty()) paths.append(p);
    }
    if (paths.isEmpty()) return;
    add_files(paths);
    if (paths.size() == 1)
        status(QStringLiteral("Added to queue: %1").arg(QFileInfo(paths[0]).fileName()));
    else
        status(QStringLiteral("Added %1 tracks to queue").arg(paths.size()));
}

void MainWindow::scan_library_folders() {
    int added = 0;
    for (const QString& folder : app_settings_.player.watched_folders) {
        QDirIterator it(folder, {"*.mp3", "*.flac", "*.wav", "*.ogg", "*.opus",
                                 "*.m4a", "*.aac", "*.mp4", "*.mkv", "*.webm",
                                 "*.avi", "*.mov", "*.m3u", "*.m3u8", "*.pls"},
                        QDir::Files, QDirIterator::Subdirectories);
        while (it.hasNext()) {
            const QString file = it.next();
            if (library_->index_of(file) >= 0) continue;
            QString title;
            try {
                const auto tags = casu::media::metadata_for(file.toStdString());
                title = QString::fromStdString(tags.at("title"));
            } catch (const std::out_of_range&) {}
            library_->add(file, title);
            ++added;
        }
    }
    library_->save();
    refresh_library();
    status(added > 0 ? QStringLiteral("Library scan: %1 new item(s)").arg(added)
                     : QStringLiteral("Library scan: nothing new"));
}

void MainWindow::build_settings_page() {
    auto* page = new QWidget(this);
    auto* outer = new QVBoxLayout(page);
    outer->setContentsMargins(20, 18, 20, 18);
    auto* title = new QLabel(QStringLiteral("OPTIONS"), page);
    title->setObjectName("NowPlayingTitle");
    outer->addWidget(title);

    auto* scroll = new QScrollArea(page);
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    auto* content = new QWidget(scroll);
    content->setStyleSheet(QStringLiteral("background: transparent;"));
    auto* layout = new QVBoxLayout(content);
    layout->setContentsMargins(4, 4, 4, 4);
    layout->setSpacing(14);

    auto add_section = [layout](const QString& text) {
        auto* label = new QLabel(text);
        label->setObjectName("SidebarSection");
        layout->addWidget(label);
    };

    add_section(QStringLiteral("PLAYBACK"));
    auto* row = new QHBoxLayout();
    row->addWidget(new QLabel(QStringLiteral("Volume")));
    settings_volume_ = new QSlider(Qt::Horizontal, content);
    settings_volume_->setRange(0, 200);
    settings_volume_->setValue(volume_);
    row->addWidget(settings_volume_, 1);
    layout->addLayout(row);
    auto* row2 = new QHBoxLayout();
    row2->addWidget(new QLabel(QStringLiteral("Rate")));
    settings_rate_ = new QDoubleSpinBox(content);
    settings_rate_->setRange(0.25, 4.0);
    settings_rate_->setSingleStep(0.25);
    settings_rate_->setValue(rate_);
    row2->addWidget(settings_rate_);
    row2->addStretch();
    layout->addLayout(row2);
    auto* row3 = new QHBoxLayout();
    row3->addWidget(new QLabel(QStringLiteral("Shuffle")));
    settings_shuffle_ = new QCheckBox(content);
    settings_shuffle_->setChecked(playlist_.shuffle);
    row3->addWidget(settings_shuffle_);
    row3->addSpacing(18);
    row3->addWidget(new QLabel(QStringLiteral("Repeat")));
    settings_repeat_ = new QComboBox(content);
    settings_repeat_->addItems({"off", "all", "one"});
    settings_repeat_->setCurrentText(app_settings_.player.repeat_mode);
    row3->addWidget(settings_repeat_);
    row3->addStretch();
    layout->addLayout(row3);
    auto* row4 = new QHBoxLayout();
    settings_muted_ = new QCheckBox(QStringLiteral("Muted"), content);
    settings_muted_->setChecked(app_settings_.player.muted);
    row4->addWidget(settings_muted_);
    row4->addSpacing(18);
    settings_resume_ = new QCheckBox(QStringLiteral("Resume playback on startup"), content);
    settings_resume_->setChecked(app_settings_.player.resume_playback);
    row4->addWidget(settings_resume_);
    row4->addStretch();
    layout->addLayout(row4);

    add_section(QStringLiteral("VISUALIZER"));
    auto* viz_row = new QHBoxLayout();
    settings_viz_ = new QComboBox(content);
    settings_viz_->addItem(QStringLiteral("Waveform"), QStringLiteral("waveform"));
    settings_viz_->addItem(QStringLiteral("Off"), QStringLiteral("off"));
    const int viz_index = settings_viz_->findData(app_settings_.player.visualizer);
    settings_viz_->setCurrentIndex(qMax(0, viz_index));
    viz_row->addWidget(settings_viz_);
    viz_row->addStretch();
    layout->addLayout(viz_row);

    add_section(QStringLiteral("CACHE"));
    auto* cache_row = new QHBoxLayout();
    settings_cache_ = new QSpinBox(content);
    settings_cache_->setRange(64, 8192);
    settings_cache_->setSuffix(QStringLiteral(" MiB"));
    settings_cache_->setValue(app_settings_.player.cache_limit_mib);
    cache_row->addWidget(settings_cache_);
    auto* clear_cache_btn = new QPushButton(QStringLiteral("Clear yt-dlp temp cache"), content);
    clear_cache_btn->setObjectName("IconButton");
    connect(clear_cache_btn, &QPushButton::clicked, this, [this] {
        QDir cache_dir(QDir::tempPath() + QStringLiteral("/yt-dlp"));
        if (cache_dir.exists()) {
            cache_dir.removeRecursively();
            toast(QStringLiteral("Cleared %1").arg(cache_dir.absolutePath()));
        } else {
            toast(QStringLiteral("No yt-dlp cache found"));
        }
    });
    cache_row->addWidget(clear_cache_btn);
    cache_row->addStretch();
    layout->addLayout(cache_row);

    add_section(QStringLiteral("LIBRARY FOLDERS"));
    auto* folders_hint = new QLabel(
        QStringLiteral("Folders whose audio/video files are indexed into the "
                       "library (tags and file names are read for "
                       "album/track/artist/genre)."),
        content);
    folders_hint->setObjectName("NowPlayingMeta");
    folders_hint->setWordWrap(true);
    layout->addWidget(folders_hint);
    settings_folders_ = new QListWidget(content);
    settings_folders_->setObjectName("QueueTree");
    settings_folders_->setMinimumHeight(110);
    settings_folders_->setMaximumHeight(180);
    for (const QString& folder : app_settings_.player.watched_folders)
        settings_folders_->addItem(folder);
    layout->addWidget(settings_folders_);
    auto* folder_row = new QHBoxLayout();
    auto* add_folder_btn = new QPushButton(QStringLiteral("Add folder…"), content);
    connect(add_folder_btn, &QPushButton::clicked, this, [this] {
        const QString folder = QFileDialog::getExistingDirectory(this);
        if (folder.isEmpty()) return;
        for (int i = 0; i < settings_folders_->count(); ++i)
            if (settings_folders_->item(i)->text() == folder) return;
        settings_folders_->addItem(folder);
        settings_folders_->setCurrentRow(settings_folders_->count() - 1);
    });
    folder_row->addWidget(add_folder_btn);
    auto* remove_folder_btn = new QPushButton(QStringLiteral("Remove selected"), content);
    connect(remove_folder_btn, &QPushButton::clicked, this, [this] {
        const int row = settings_folders_->currentRow();
        if (row >= 0) delete settings_folders_->takeItem(row);
    });
    folder_row->addWidget(remove_folder_btn);
    folder_row->addStretch();
    layout->addLayout(folder_row);

    add_section(QStringLiteral("RECORDINGS & SNAPSHOTS"));
    auto* rec_row = new QHBoxLayout();
    settings_record_dir_ = new QLineEdit(output_dir_, content);
    settings_record_dir_->setPlaceholderText(
        QStringLiteral("Default folder for recordings and snapshots"));
    rec_row->addWidget(settings_record_dir_, 1);
    auto* rec_btn = new QPushButton(QStringLiteral("Choose folder…"), content);
    connect(rec_btn, &QPushButton::clicked, this, [this] {
        const QString folder = QFileDialog::getExistingDirectory(this);
        if (!folder.isEmpty()) settings_record_dir_->setText(folder);
    });
    rec_row->addWidget(rec_btn);
    layout->addLayout(rec_row);
    auto* split_row = new QHBoxLayout();
    split_row->addWidget(new QLabel(QStringLiteral("Split recordings every")));
    settings_split_ = new QSpinBox(content);
    settings_split_->setRange(0, 24 * 60);
    settings_split_->setSuffix(QStringLiteral(" min"));
    settings_split_->setSpecialValueText(QStringLiteral("no splitting"));
    settings_split_->setValue(app_settings_.player.record_split_minutes);
    split_row->addWidget(settings_split_);
    split_row->addSpacing(12);
    split_row->addWidget(new QLabel(QStringLiteral("Format")));
    settings_format_ = new QComboBox(content);
    for (const QString& fmt : {"mkv", "mp4", "ts", "webm", "ogg", "mp3", "flac", "wav"})
        settings_format_->addItem(fmt);
    const int fmt_index = settings_format_->findText(app_settings_.player.record_format);
    settings_format_->setCurrentIndex(qMax(0, fmt_index));
    split_row->addWidget(settings_format_);
    split_row->addStretch();
    layout->addLayout(split_row);

    add_section(QStringLiteral("LEGAL"));
    settings_consent_ = new QCheckBox(
        QStringLiteral("I understand that YouTube uses yt-dlp and Spotify uses "
                       "spotDL (personal use only)"),
        content);
    settings_consent_->setChecked(app_settings_.player.ytdlp_consent);
    layout->addWidget(settings_consent_);

    add_section(QStringLiteral("PROVIDERS"));
    auto* providers = new QLabel(content);
    providers->setObjectName("NowPlayingMeta");
    providers->setWordWrap(true);
    providers->setText(provider_status_text());
    layout->addWidget(providers);
    backend_info_label_ = providers;

    auto* apply_row = new QHBoxLayout();
    apply_row->addStretch();
    auto* apply_btn = new QPushButton(QStringLiteral("Apply"), content);
    apply_btn->setObjectName("PrimaryButton");
    connect(apply_btn, &QPushButton::clicked, this, &MainWindow::on_settings_save);
    apply_row->addWidget(apply_btn);
    layout->addLayout(apply_row);
    layout->addStretch();

    scroll->setWidget(content);
    outer->addWidget(scroll, 1);
    pages_->addWidget(page);
}

void MainWindow::build_epg_page() {
    // Linux parity (EpgPage): M3U catalog + XMLTV guide, web-style channel cards.
    auto* page = new QWidget(this);
    auto* outer = new QVBoxLayout(page);
    outer->setContentsMargins(20, 18, 20, 16);
    outer->setSpacing(10);
    auto* title = new QLabel(QStringLiteral("EPG / IPTV"), page);
    title->setObjectName("NowPlayingTitle");
    outer->addWidget(title);

    auto* source_row = new QHBoxLayout();
    epg_source_ = new QLineEdit(page);
    epg_source_->setPlaceholderText(QStringLiteral("M3U / XMLTV path or http(s) URL…"));
    source_row->addWidget(epg_source_, 1);
    auto* load_file_btn = new QPushButton(QStringLiteral("Load file"), page);
    load_file_btn->setObjectName("IconButton");
    connect(load_file_btn, &QPushButton::clicked, this, &MainWindow::on_epg_load);
    source_row->addWidget(load_file_btn);
    auto* load_url_btn = new QPushButton(QStringLiteral("Load URL"), page);
    load_url_btn->setObjectName("IconButton");
    connect(load_url_btn, &QPushButton::clicked, this, [this] {
        load_epg_source(epg_source_->text().trimmed());
    });
    source_row->addWidget(load_url_btn);
    outer->addLayout(source_row);

    epg_status_ = new QLabel(
        QStringLiteral("Load an Extended-M3U playlist (and optional XMLTV guide) to browse channels."),
        page);
    epg_status_->setObjectName("NowPlayingMeta");
    outer->addWidget(epg_status_);

    auto* scroll = new QScrollArea(page);
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOn);
    auto* grid_host = new QWidget(scroll);
    grid_host->setStyleSheet(QStringLiteral("background: transparent;"));
    epg_grid_ = new QGridLayout(grid_host);
    epg_grid_->setSpacing(8);
    scroll->setWidget(grid_host);
    outer->addWidget(scroll, 1);
    pages_->addWidget(page);
}

void MainWindow::on_epg_load() {
    QString file = QFileDialog::getOpenFileName(
        this, QStringLiteral("Load playlist / guide"), QDir::homePath(),
        QStringLiteral("Playlists & guides (*.m3u *.m3u8 *.pls *.xspf *.wpl *.asx *.xml *.xmltv);;All files (*)"));
    if (file.isEmpty()) return;
    load_epg_source(file);
}

void MainWindow::load_epg_source(const QString& source) {
    if (source.isEmpty()) return;
    const QString lower = source.toLower();
    // XMLTV guide.
    if (lower.endsWith(QStringLiteral(".xml")) || lower.endsWith(QStringLiteral(".xmltv"))) {
        QByteArray data;
        QString err;
        if (lower.startsWith(QStringLiteral("http://")) ||
            lower.startsWith(QStringLiteral("https://"))) {
            const casu::network::HttpResponse res =
                casu::network::HttpClient().get(source.toStdString(), 30000);
            if (!res.error.empty()) {
                epg_status_->setText(QStringLiteral("Guide fetch failed: %1")
                                         .arg(QString::fromStdString(res.error)));
                return;
            }
            data = QByteArray(reinterpret_cast<const char*>(res.body.data()),
                              static_cast<int>(res.body.size()));
        } else {
            QFile f(source);
            if (!f.open(QIODevice::ReadOnly)) {
                epg_status_->setText(QStringLiteral("Could not read %1").arg(source));
                return;
            }
            data = f.readAll();
        }
        err = parse_xmltv(data, &epg_, &epg_guide_);
        if (!err.isEmpty()) {
            epg_status_->setText(QStringLiteral("EPG error: %1").arg(err));
            return;
        }
        epg_status_->setText(QStringLiteral("Guide loaded: %1 programmes").arg(epg_guide_.programmes.size()));
        update_diagnostics_guide();
        render_epg_cards();
        return;
    }
    // M3U / playlist catalog (local or fetched over HTTP).
    mpcasu::StreamCatalog catalog;
    if (lower.startsWith(QStringLiteral("http://")) ||
        lower.startsWith(QStringLiteral("https://"))) {
        const casu::network::HttpResponse res =
            casu::network::HttpClient().get(source.toStdString(), 30000);
        if (!res.error.empty()) {
            epg_status_->setText(QStringLiteral("Fetch failed: %1").arg(QString::fromStdString(res.error)));
            return;
        }
        const QString parse_err =
            mpcasu::parse_m3u(QByteArray(reinterpret_cast<const char*>(res.body.data()),
                                         static_cast<int>(res.body.size())),
                              QString(), &catalog);
        if (!parse_err.isEmpty()) {
            epg_status_->setText(QStringLiteral("EPG error: %1").arg(parse_err));
            return;
        }
    } else {
        const QString parse_err = mpcasu::load_m3u_file(source, &catalog);
        if (!parse_err.isEmpty()) {
            epg_status_->setText(QStringLiteral("EPG error: %1").arg(parse_err));
            return;
        }
    }
    if (catalog.channels.isEmpty()) {
        epg_status_->setText(QStringLiteral("No channels found in %1").arg(source));
        return;
    }
    epg_ = catalog;
    epg_status_->setText(QStringLiteral("%1 channels loaded").arg(catalog.channels.size()));
    update_diagnostics_guide();
    render_epg_cards();
}

void MainWindow::render_epg_cards() {
    if (!epg_grid_) return;
    while (QLayoutItem* item = epg_grid_->takeAt(0)) {
        if (QWidget* w = item->widget()) w->deleteLater();
        delete item;
    }
    const qint64 now_ms = QDateTime::currentMSecsSinceEpoch();
    for (int index = 0; index < epg_.channels.size(); ++index) {
        const mpcasu::StreamChannel& ch = epg_.channels[index];
        auto* card = new QFrame(epg_grid_->parentWidget());
        card->setObjectName("EpgChannel");
        card->setCursor(Qt::PointingHandCursor);
        auto* cl = new QVBoxLayout(card);
        cl->setContentsMargins(12, 10, 12, 10);
        auto* name = new QLabel(ch.name, card);
        name->setObjectName("NowPlayingTitle");
        name->setStyleSheet(QStringLiteral("font-size: 13px;"));
        name->setWordWrap(true);
        cl->addWidget(name);
        QString now_text;
        if (!epg_guide_.programmes.isEmpty()) {
            const QString key =
                ch.epg_id.isEmpty() ? ch.name : ch.epg_id;
            const mpcasu::Programme* active = nullptr;
            const mpcasu::Programme* upcoming = nullptr;
            epg_guide_.now_next(key, now_ms, &active, &upcoming);
            if (active) now_text = active->title;
        }
        if (now_text.isEmpty()) now_text = ch.group;
        auto* meta = new QLabel(now_text, card);
        meta->setObjectName("NowPlayingMeta");
        meta->setWordWrap(true);
        cl->addWidget(meta);
        const QString url = ch.url;
        card->installEventFilter(this);
        epg_card_urls_[card] = url;
        epg_grid_->addWidget(card, index / 3, index % 3);
    }
}

bool MainWindow::eventFilter(QObject* watched, QEvent* event) {
    if (event->type() == QEvent::MouseButtonRelease && epg_card_urls_.contains(watched)) {
        open_network_source(epg_card_urls_.value(watched), epg_card_urls_.value(watched));
        return true;
    }
    return QMainWindow::eventFilter(watched, event);
}

void MainWindow::build_recording_page() {
    auto* scroll_host = new QScrollArea(this);
    scroll_host->setWidgetResizable(true);
    scroll_host->setFrameShape(QFrame::NoFrame);
    auto* page = new QWidget(scroll_host);
    scroll_host->setWidget(page);
    auto* layout = new QVBoxLayout(page);
    layout->setContentsMargins(24, 24, 24, 24);
    layout->setSpacing(12);
    auto* title = new QLabel(QStringLiteral("RECORDING"), page);
    title->setObjectName("NowPlayingTitle");
    layout->addWidget(title);

    auto* hint = new QLabel(QStringLiteral("Records the current source with ffmpeg "
                                           "(-c copy) into the output directory."), page);
    hint->setObjectName("NowPlayingMeta");
    hint->setWordWrap(true);
    layout->addWidget(hint);

    auto* row = new QHBoxLayout();
    row->addWidget(new QLabel(QStringLiteral("Output dir"), page));
    record_dir_ = new QLineEdit(output_dir_, page);
    row->addWidget(record_dir_, 1);
    auto* browse = new QPushButton(QStringLiteral("…"), page);
    browse->setObjectName("IconButton");
    connect(browse, &QPushButton::clicked, this, [this] {
        QString dir = QFileDialog::getExistingDirectory(this, QStringLiteral("Recording folder"), output_dir_);
        if (!dir.isEmpty()) { record_dir_->setText(dir); output_dir_ = dir; }
    });
    row->addWidget(browse);
    layout->addLayout(row);

    record_status_ = new QLabel(QStringLiteral("Recording: Idle"), page);
    record_status_->setObjectName("NowPlayingMeta");
    layout->addWidget(record_status_);

    auto* toggle = new QPushButton(QStringLiteral("Start / Stop recording"), page);
    toggle->setObjectName("PlayButton");
    connect(toggle, &QPushButton::clicked, this, &MainWindow::on_recording_toggle);
    layout->addWidget(toggle, 0, Qt::AlignLeft);
    layout->addStretch();
    pages_->addWidget(scroll_host);
}

void MainWindow::build_visualizer_page() {
    auto* page = new QWidget(this);
    auto* layout = new QVBoxLayout(page);
    layout->setContentsMargins(20, 20, 20, 20);
    layout->setSpacing(10);
    auto* title = new QLabel(QStringLiteral("VISUALIZER"), page);
    title->setObjectName("NowPlayingTitle");
    layout->addWidget(title);
    auto* hint = new QLabel(QStringLiteral(
        "Lightweight waveform · bounded resolution · 30 FPS"), page);
    hint->setObjectName("NowPlayingMeta");
    hint->setWordWrap(true);
    layout->addWidget(hint);
    auto* page_viz = new VisualizerWidget(page);
    page_viz->set_playing(false);
    layout->addWidget(page_viz, 1);
    pages_->addWidget(page);
}

void MainWindow::build_youtube_page() {
    auto* page = new QWidget(this);
    auto* layout = new QVBoxLayout(page);
    layout->setContentsMargins(20, 20, 20, 20);
    layout->setSpacing(12);
    auto* title = new QLabel(QStringLiteral("YOUTUBE / NETWORK"), page);
    title->setObjectName("NowPlayingTitle");
    layout->addWidget(title);

    auto* hint = new QLabel(QStringLiteral(
        "Enter a YouTube URL, a search term or a stream URL. YouTube is "
        "resolved via yt-dlp and streamed through the loopback Range/206 "
        "transport."), page);
    hint->setObjectName("NowPlayingMeta");
    hint->setWordWrap(true);
    layout->addWidget(hint);

    // Consent gate (Linux parity): legal notice before yt-dlp features.
    yt_consent_frame_ = new QFrame(page);
    yt_consent_frame_->setObjectName("Panel");
    auto* consent_layout = new QVBoxLayout(yt_consent_frame_);
    consent_layout->setContentsMargins(14, 12, 14, 12);
    consent_layout->setSpacing(8);
    auto* notice = new QLabel(
        QStringLiteral("Legal notice — YouTube search/playback uses yt-dlp "
                       "(GNU GPL); Spotify uses spotDL: Spotify metadata "
                       "matched on YouTube (metadata → match → YouTube audio "
                       "source). Stream URLs are resolved temporarily and "
                       "never stored or redistributed. Personal use only."),
        yt_consent_frame_);
    notice->setObjectName("NowPlayingMeta");
    notice->setWordWrap(true);
    consent_layout->addWidget(notice);
    auto* accept_btn = new QPushButton(
        QStringLiteral("Accept and enable yt-dlp features"), yt_consent_frame_);
    accept_btn->setObjectName("PrimaryButton");
    connect(accept_btn, &QPushButton::clicked, this, [this] {
        app_settings_.player.ytdlp_consent = true;
        settings_->save(app_settings_.player);
        if (yt_consent_frame_) yt_consent_frame_->hide();
        status(QStringLiteral("yt-dlp features enabled"));
    });
    consent_layout->addWidget(accept_btn, 0, Qt::AlignLeft);
    yt_consent_frame_->setVisible(!app_settings_.player.ytdlp_consent);
    layout->addWidget(yt_consent_frame_);

    youtube_url_ = new QLineEdit(page);
    youtube_url_->setPlaceholderText(QStringLiteral("https://www.youtube.com/watch?v=…  or  search term"));
    youtube_url_->setClearButtonEnabled(true);
    connect(youtube_url_, &QLineEdit::returnPressed, this, &MainWindow::on_youtube_play);
    layout->addWidget(youtube_url_);

    auto* row = new QHBoxLayout();
    auto* play = new QPushButton(QStringLiteral("Play / search"), page);
    play->setObjectName("PlayButton");
    connect(play, &QPushButton::clicked, this, &MainWindow::on_youtube_play);
    row->addWidget(play);
    layout->addLayout(row);

    yt_results_ = new QListWidget(page);
    yt_results_->setObjectName("QueueTree");
    yt_results_->setIconSize(QSize(120, 68));
    yt_results_->setSpacing(4);
    connect(yt_results_, &QListWidget::itemDoubleClicked, this, [this](QListWidgetItem* item) {
        if (!item) return;
        const QString url = item->data(Qt::UserRole).toString();
        if (url.isEmpty()) return;
        // Linux parity: search results enter the QUEUE with their title
        // (never a raw-URL "Now Playing"), then play.
        const QString title = item->data(Qt::UserRole + 1).toString();
        queue_and_play(url, title);
    });
    layout->addWidget(yt_results_, 1);

    youtube_status_ = new QLabel(QStringLiteral("Idle"), page);
    youtube_status_->setObjectName("NowPlayingMeta");
    youtube_status_->setWordWrap(true);
    layout->addWidget(youtube_status_);
    auto* yt_scroll = new QScrollArea(this);
    yt_scroll->setWidgetResizable(true);
    yt_scroll->setFrameShape(QFrame::NoFrame);
    yt_scroll->setWidget(page);
    pages_->addWidget(yt_scroll);
}

void MainWindow::build_web_players_page() {
    auto* page = new QWidget(this);
    auto* layout = new QVBoxLayout(page);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(0);
    web_player_tabs_ = new WebPlayerTabs(page);
    layout->addWidget(web_player_tabs_, 1);
    pages_->addWidget(page);
}

void MainWindow::open_web_player(const QString& provider, const QString& query,
                                 const QString& url) {
    if (!web_player_tabs_) return;
    web_player_tabs_->open(provider, query, url);
    navigate(QStringLiteral("WEB PLAYERS"));
    // Mirror the reference toast "X geöffnet im eingebetteten Browser".
    QString label = provider.toUpper();
    for (const auto& spec : casu::web::web_players())
        if (QString::fromStdString(spec.key) == provider)
            label = QString::fromStdString(spec.label);
    if (provider == QLatin1String("browse")) label = QStringLiteral("BROWSE");
    status(QStringLiteral("%1 geöffnet im eingebetteten Browser").arg(label));
}

void MainWindow::navigate(const QString& page) {
    static const QMap<QString, int> pages = {
        {"NOW PLAYING", 0}, {"ABOUT", 1}, {"LIBRARY", 2}, {"SETTINGS", 3},
        {"EPG", 4}, {"RECORDING", 5}, {"VISUALIZER", 6}, {"YOUTUBE", 7},
        {"WEB PLAYERS", 8},
    };
    // Sidebar entries that are not stacked pages mirror the reference
    // _navigate(): they redirect to a page + view/queue filter.
    QString target = page;
    if (page == QStringLiteral("WEB & STREAMS")) target = QStringLiteral("YOUTUBE");
    else if (page == QStringLiteral("PLAYLISTS") ||
             page == QStringLiteral("CASU FILES")) {
        target = QStringLiteral("NOW PLAYING");
    } else if (page == QStringLiteral("SPOTIFY") ||
               page == QStringLiteral("HEARTHIS") ||
               page == QStringLiteral("TIDAL") ||
               page == QStringLiteral("NETFLIX") ||
               page == QStringLiteral("BROWSE")) {
        open_web_player(page.toLower());
        for (QPushButton* b : nav_buttons_) b->setChecked(false);
        if (QPushButton* b = nav_map_.value(page)) b->setChecked(true);
        return;
    } else if (page == QStringLiteral("OPTIONS")) {
        target = QStringLiteral("SETTINGS");
    }
    int idx = pages.value(target, 0);
    pages_->setCurrentIndex(idx);
    if (target == QStringLiteral("LIBRARY")) refresh_library();
    if (target == QStringLiteral("SETTINGS") && backend_info_label_)
        backend_info_label_->setText(provider_status_text());
    if (target == QStringLiteral("YOUTUBE")) set_queue_view_filter(
        page == QStringLiteral("WEB & STREAMS")
            ? QStringLiteral("streams") : QString());
    if (target == QStringLiteral("NOW PLAYING") &&
        (page == QStringLiteral("PLAYLISTS") ||
         page == QStringLiteral("CASU FILES"))) {
        set_queue_view_filter(page == QStringLiteral("PLAYLISTS")
                                  ? QStringLiteral("playlists")
                                  : QStringLiteral("casu"));
    }
    for (QPushButton* b : nav_buttons_) b->setChecked(false);
    if (QPushButton* b = nav_map_.value(page)) b->setChecked(true);
    if (page == QStringLiteral("YOUTUBE") && youtube_url_ && youtube_status_) {
        youtube_status_->setText(QStringLiteral("Enter a YouTube URL (resolved via yt-dlp) or a "
                                                "local file path (loopback transport test)."));
    }
}

// ------------------------------------------------------------------ status/toast

void MainWindow::show_media_info() {
    // Media info dialog (mirror of the Linux show_media_info): ffprobe
    // metadata + manifest basics in a readable text browser.
    const QString path = current_played_path_.isEmpty()
                             ? (playlist_.items().isEmpty() ? QString()
                                                            : playlist_.items().first().path)
                             : current_played_path_;
    QString text;
    if (path.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("Media info"),
                                 QStringLiteral("No media loaded."));
        return;
    }
    text += QStringLiteral("<b>%1</b>\n\n").arg(path.toHtmlEscaped());
    // Linux parity: native CASU containers show their manifest (no ffprobe).
    if (QFileInfo::exists(path)) {
        const casu::CasuKind kind = casu::detect_casu_kind(path.toStdString());
        if (kind != casu::CasuKind::None && kind != casu::CasuKind::Sidecar) {
            bool native_v2 = kind == casu::CasuKind::Casunat2;
            QString format_name;
            QString duration;
            QVector<QPair<QString, QString>> streams;
            QStringList metadata_keys;
            try {
                if (native_v2) {
                    QFile f(path);
                    if (f.open(QIODevice::ReadOnly)) {
                        const QByteArray head = f.read(20);
                        const casu::casunat2::Header h = casu::casunat2::parse_header(
                            reinterpret_cast<const uint8_t*>(head.constData()),
                            static_cast<std::size_t>(head.size()));
                        if (h.manifest_length > 0 && h.manifest_length <= 64ULL * 1024 * 1024) {
                            const QByteArray manifest_bytes = f.read(
                                static_cast<qint64>(qBound<uint64_t>(
                                    0, h.manifest_length, 64ULL * 1024 * 1024)));
                            const casu::JsonValue manifest = casu::parse_json(
                                manifest_bytes.constData(),
                                static_cast<std::size_t>(manifest_bytes.size()));
                            format_name = QStringLiteral("CASUNAT2 segmented media");
                            if (const casu::JsonValue* d = manifest.find("duration"))
                                duration = QString::number(d->as_double(), 'f', 2);
                            if (const casu::JsonValue* md = manifest.find("metadata");
                                md && md->is_object())
                                for (const auto& [key, val] : md->as_object().items)
                                    if (val.is_string())
                                        metadata_keys.append(QString::fromStdString(key));
                            if (const casu::JsonValue* s = manifest.find("streams");
                                s && s->is_array())
                                for (const casu::JsonValue& item : s->as_array().items) {
                                    if (!item.is_object()) continue;
                                    QString type;
                                    if (const casu::JsonValue* t = item.find("type"))
                                        type = QString::fromStdString(t->as_string());
                                    streams.append({type,
                                                    QStringLiteral("casu-%1").arg(type)});
                                }
                        }
                    }
                } else {
                    const casu::casunat1::Container c =
                        casu::casunat1::read_native(path.toStdString(), false);
                    format_name = QStringLiteral("CASU native container");
                    if (const casu::JsonValue* src = c.manifest.find("source");
                        src && src->is_object())
                        if (const casu::JsonValue* d = src->find("duration_s"))
                            duration = QString::number(d->as_double(), 'f', 2);
                    if (const casu::JsonValue* s = c.manifest.find("streams");
                        s && s->is_array())
                        for (const casu::JsonValue& item : s->as_array().items) {
                            if (!item.is_object()) continue;
                            QString type;
                            if (const casu::JsonValue* t = item.find("type"))
                                type = QString::fromStdString(t->as_string());
                            streams.append({type, QStringLiteral("casu-%1").arg(type)});
                        }
                }
            } catch (const std::exception& e) {
                text += QStringLiteral("CASU: manifest read failed — %1\n")
                            .arg(QString::fromStdString(e.what()));
            }
            text += QStringLiteral("CASU: %1\n").arg(
                native_v2 ? QStringLiteral("verified native CASUNAT2")
                          : QStringLiteral("verified CASUNAT1 compatibility envelope"));
            text += QStringLiteral("Container: %1\n").arg(format_name);
            text += QStringLiteral("Duration: %1 s\n")
                        .arg(duration.isEmpty() ? QStringLiteral("unknown") : duration);
            text += QStringLiteral("Size: %1 bytes\n").arg(QFileInfo(path).size());
            for (const QString& key : metadata_keys)
                text += QStringLiteral("%1: (see manifest)\n").arg(key);
            text += QStringLiteral("\nStreams:\n");
            for (const auto& [type, codec] : streams)
                text += QStringLiteral("  %1 · %2\n").arg(type, codec);
            QDialog dialog(this);
            dialog.setWindowTitle(QStringLiteral("Media info"));
            dialog.setObjectName("Panel");
            dialog.resize(520, 420);
            auto* layout = new QVBoxLayout(&dialog);
            auto* browser = new QTextBrowser(&dialog);
            browser->setObjectName("PagePanel");
            browser->setText(text);
            layout->addWidget(browser);
            dialog.exec();
            return;
        }
    }
    const QString ffprobe = qEnvironmentVariable("CASU_FFPROBE");
    if (!ffprobe.isEmpty() && QFileInfo::exists(ffprobe) && QFileInfo::exists(path)) {
        QProcess probe;
#ifdef Q_OS_WIN
        // GUI app: never flash a console window for ffprobe.
        probe.setCreateProcessArgumentsModifier(
            [](QProcess::CreateProcessArguments* a) { a->flags |= CREATE_NO_WINDOW; });
#endif
        probe.start(ffprobe, {"-v", "quiet", "-print_format", "json",
                              "-show_format", "-show_streams", path});
        if (probe.waitForFinished(15000)) {
            const QByteArray out = probe.readAllStandardOutput();
            QJsonParseError err;
            const QJsonDocument doc = QJsonDocument::fromJson(out, &err);
            if (err.error == QJsonParseError::NoError && doc.isObject()) {
                const QJsonObject root = doc.object();
                const QJsonObject format = root.value("format").toObject();
                const QString duration = format.value("duration").toString();
                if (!duration.isEmpty())
                    text += QStringLiteral("Duration: %1\n").arg(format_duration(duration.toDouble()));
                if (format.contains("bit_rate"))
                    text += QStringLiteral("Bitrate: %1 kbit/s\n")
                                .arg(format.value("bit_rate").toDouble() / 1000.0);
                if (format.contains("format_name"))
                    text += QStringLiteral("Format: %1\n").arg(format.value("format_name").toString());
                if (format.contains("tags")) {
                    const QJsonObject tags = format.value("tags").toObject();
                    for (const QString& key : {"title", "artist", "album"}) {
                        if (tags.contains(key))
                            text += QStringLiteral("%1: %2\n")
                                        .arg(key, tags.value(key).toString().toHtmlEscaped());
                    }
                }
                text += QStringLiteral("\nStreams:\n");
                const QJsonArray streams = root.value("streams").toArray();
                for (const QJsonValue& sv : streams) {
                    const QJsonObject s = sv.toObject();
                    const QString type = s.value("codec_type").toString();
                    const QString codec = s.value("codec_name").toString();
                    QString line = QStringLiteral("  %1 · %2").arg(type, codec);
                    if (type == "video" && s.contains("width")) {
                        line += QStringLiteral(" · %1×%2").arg(s.value("width").toInt())
                                                          .arg(s.value("height").toInt());
                    }
                    if (type == "audio" && s.contains("sample_rate"))
                        line += QStringLiteral(" · %1 Hz").arg(s.value("sample_rate").toInt());
                    text += line + "\n";
                }
            } else {
                text += QStringLiteral("ffprobe parse failed: %1\n").arg(err.errorString());
            }
        } else {
            text += QStringLiteral("ffprobe failed (%1)\n").arg(probe.errorString());
        }
    } else {
        text += QStringLiteral("(ffprobe not available for this source)\n");
    }
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("Media info"));
    dialog.setObjectName("Panel");
    dialog.resize(520, 420);
    auto* layout = new QVBoxLayout(&dialog);
    auto* browser = new QTextBrowser(&dialog);
    browser->setObjectName("PagePanel");
    browser->setText(text);
    layout->addWidget(browser);
    dialog.exec();
}

void MainWindow::status(const QString& text) {
    if (status_center_) status_center_->setText(text);  // Linux parity: center slot
}

void MainWindow::toast(const QString& text) {
    status(text);
    if (!toast_label_ || !toast_label_->parentWidget()) return;
    toast_label_->setText(text);
    toast_label_->setWordWrap(true);
    toast_label_->adjustSize();
    const QWidget* stage = toast_label_->parentWidget();
    const int width = qMin(toast_label_->width(), qMax(240, stage->width() - 32));
    toast_label_->setFixedWidth(width);
    toast_label_->adjustSize();
    toast_label_->move(qMax(16, (stage->width() - toast_label_->width()) / 2),
                       qMax(8, stage->height() - toast_label_->height() - 18));
    toast_label_->show();
    toast_label_->raise();
    toast_timer_->start(2600);
}

// ------------------------------------------------------------------ playback core

void MainWindow::stop_playback() {
    // Tear the loopback transport down FIRST (verified v5.0.0 order):
    // killing the server unblocks any pending libVLC input reads so the
    // synchronous player teardown cannot wait on a live socket.
    if (yt_proxy_) yt_proxy_->stop();
    // Invalidate an in-flight yt-dlp worker.  A late result must never start
    // playback again after Stop or a source switch.
    ++resolve_generation_;
    if (recorder_->is_recording()) recorder_->stop();
    if (backend_) {
        controller_->stop();
        controller_->close();
    }
    backend_.reset();
    controller_->poll();
    set_diagnostics(QStringLiteral("Legacy backend"), QStringLiteral("unavailable"),
                    QStringLiteral("unavailable"), QString());
    if (surface_) {
        surface_->set_video_active(false);
        surface_->clear();
    }
    paused_ = false;
    end_handled_ = false;
    duration_ = 0.0;
    seek_slider_->setRange(0, 0);
    seek_slider_->setValue(0);
    time_current_->setText(QStringLiteral("00:00"));
    time_total_->setText(QStringLiteral("00:00"));
    if (visualizer_) static_cast<VisualizerWidget*>(visualizer_)->set_playing(false);
    stage_media_active_ = false;  // back to the "Drop media here" placeholder
    update_stage();
    update_play_button();
    status(QStringLiteral("Stopped"));
}

void MainWindow::open_backend_and_play(const QString& source, const QString& title) {
    end_handled_ = false;
    current_source_ = source;
    current_title_ = title.isEmpty() ? display_title_for_path(source) : title;
    topbar_title_->setText(current_title_);
    // Linux parity: Now-Playing caption shows the EPG now/next line for URLs.
    if (source.startsWith(QStringLiteral("http://")) ||
        source.startsWith(QStringLiteral("https://"))) {
        const QString epg_line = epg_now_next_text(source);
        if (!epg_line.isEmpty() && epg_line != QStringLiteral("no EPG loaded") &&
            epg_line != QStringLiteral("EPG loaded"))
            topbar_title_->setText(current_title_ + QStringLiteral("\n") + epg_line);
    }
    ab_loop_a_ = -1.0;
    ab_loop_b_ = -1.0;
    if (ab_btn_) { ab_btn_->setChecked(false); }
    // Resume playback at the saved position (Linux parity).
    if (!resume_source_.isEmpty() && resume_source_ == source && resume_position_ > 5.0) {
        // Reference record_progress clamp: a resume point within the last
        // 5 seconds of the duration restarts from the beginning.
        const double dur = duration_;
        if (dur > 0.0 && resume_position_ >= std::max(0.0, dur - 5.0)) {
            resume_position_ = -1.0;
            return;
        }
        try { backend_->seek(resume_position_); } catch (const casu::playback::PlaybackError&) {}
        controller_->seek(resume_position_);
        status(QStringLiteral("Resumed %1 at %2 s")
                   .arg(QFileInfo(source).fileName())
                   .arg(resume_position_, 0, 'f', 1));
        resume_source_.clear();
        resume_position_ = -1.0;
    }

    bool audio = !is_network_like(source) && is_audio_ext(source);
    surface_->set_video_active(!audio);
    if (visualizer_) {
        auto* viz = static_cast<VisualizerWidget*>(visualizer_);
        if (is_network_like(source)) viz->set_stream_url(source);
        else viz->set_audio_file(source);
        viz->set_position_provider(
            [this] { return controller_ ? controller_->position() : 0.0; });
        viz->set_playing(true);
    }
    if (audio) surface_->clear();
    stage_media_active_ = true;
    // Linux parity: audio-only media shows the visualizer stage, video the surface.
    if (viz_btn_) viz_btn_->setChecked(audio);
    update_stage();
    load_cover_art(source);

    // Diagnostics bar (Linux parity): describe container capabilities.
    QString diag_support = QStringLiteral("Legacy backend");
    QString diag_integrity = QStringLiteral("unavailable");
    QString diag_segmented = QStringLiteral("unavailable");
    if (is_network_like(source)) {
        diag_support = QStringLiteral("Legacy network backend");
    } else if (is_casu_container(source)) {
        switch (casu::detect_casu_kind(source.toStdString())) {
            case casu::CasuKind::Mp5:
                diag_support = QStringLiteral("MP5 enhanced container + libVLC");
                diag_integrity = QStringLiteral("SHA-256 verified attachment");
                break;
            case casu::CasuKind::Casunat1:
                diag_support = QStringLiteral("CASUNAT1 container + libVLC");
                diag_integrity = QStringLiteral("verified source manifest");
                break;
            case casu::CasuKind::Sidecar:
                diag_support = QStringLiteral("CASUNAT1 + CASUNAT2");
                diag_integrity = QStringLiteral("CASUNAT1 envelope verified on load");
                break;
            case casu::CasuKind::Casunat2:
                diag_support = QStringLiteral("CASUNAT2 native key-state/tile/PCM");
                break;
            default:
                break;
        }
        diag_segmented = QStringLiteral("no segment data");
    }
    set_diagnostics(diag_support, diag_integrity, diag_segmented, QString());
    update_diagnostics_guide();

    std::vector<std::string> runtime_options;
    if (!vout_.isEmpty())
        runtime_options.push_back(("--vout=" + vout_).toStdString());
    if (!aout_.isEmpty())
        runtime_options.push_back(("--aout=" + aout_).toStdString());
    auto backend = std::make_shared<casu::playback::LibVLCBackend>(
        surface_->native_handle(), std::move(runtime_options));
    backend->on_event = [this](casu::playback::PlaybackState s) { bridge_->post(s); };
    backend_ = backend;
    try {
        if (!is_network_like(source) && is_casu_container(source))
            backend->open_casu(source.toStdString());
        else
            backend->open_source(source.toStdString());
        controller_->attach(backend, source.toStdString());
        controller_->play();
        apply_backend_settings();
        apply_media_preferences();  // Linux parity: recall tracks + A/V delays
        // Reference start-status: "{name} · {state} · {vlc-version}".
        const QString vlc_version = QString::fromStdString(
            backend_ ? backend_->version_string() : std::string());
        status(QStringLiteral("%1 · Playing · %2")
                   .arg(QFileInfo(source).fileName(),
                        vlc_version.isEmpty() ? QStringLiteral("libVLC")
                                              : vlc_version));
    } catch (const casu::playback::PlaybackError& e) {
        surface_->set_video_active(false);
        backend_ = nullptr;
        controller_->close();
        if (yt_proxy_) yt_proxy_->stop();
        status(QStringLiteral("Playback error: %1").arg(QString::fromStdString(e.what())));
    } catch (const casu::CasuError& e) {
        surface_->set_video_active(false);
        backend_ = nullptr;
        controller_->close();
        if (yt_proxy_) yt_proxy_->stop();
        status(QStringLiteral("CASU error: %1").arg(QString::fromStdString(e.what())));
    }
    update_play_button();
}

void MainWindow::open_network_source(const QString& source, const QString& title) {
    // Provider URLs (Spotify/Hearthis/Tidal/Netflix) open the official web
    // player in the embedded browser — never linked out, never a second
    // player. Mirrors main_window.py _play_network_source.
    const std::string provider = casu::web::provider_for_url(source.toStdString());
    if (!provider.empty()) {
        open_web_player(QString::fromStdString(provider), QString(), source);
        return;
    }
    stop_playback();  // stop old session incl. any old proxy (order matters)
    QString effective = source;
    if (casu::network::is_youtube_url(source.toStdString())) {
        // Reference consent gate: playback-time yt-dlp resolution requires
        // the user's consent, exactly like the search path.
        if (!app_settings_.player.ytdlp_consent) {
            youtube_status_->setText(
                QStringLiteral("YouTube requires consent (Options → yt-dlp)"));
            status(QStringLiteral(
                "yt-dlp consent required — enable it in Options first"));
            return;
        }
        // Reference parity: yt-dlp resolution runs on a worker thread with a
        // generation guard — the GUI stays responsive and a newer request
        // invalidates the stale result instead of racing it.
        youtube_status_->setText(QStringLiteral("Resolving YouTube via yt-dlp…"));
        const int generation = ++resolve_generation_;
        QPointer<MainWindow> guard(this);
        std::thread([guard, generation, source, title] {
            std::string resolved;
            std::string error_text;
            try {
                resolved =
                    casu::network::YtDlp().resolve(source.toStdString(), 45000);
            } catch (const std::exception& e) {
                error_text = e.what();
            }
            QMetaObject::invokeMethod(QCoreApplication::instance(),
                                      [guard, generation, source, title,
                                       resolved, error_text] {
                if (!guard) return;
                if (generation != guard->resolve_generation_)
                    return;  // stale result — a newer request superseded it
                if (!error_text.empty()) {
                    guard->youtube_status_->setText(
                        QStringLiteral("YouTube resolve failed: %1")
                            .arg(QString::fromStdString(error_text)));
                    guard->status(QStringLiteral(
                        "YouTube resolve failed: %1")
                        .arg(QString::fromStdString(error_text)));
                    return;
                }
                QString err;
                if (!guard->yt_proxy_->start_remote(
                        QString::fromStdString(resolved),
                        [source] {
                            return QString::fromStdString(
                                casu::network::YtDlp().resolve(
                                    source.toStdString(), 45000));
                        },
                        &err)) {
                    guard->status(QStringLiteral("Transport error: %1").arg(err));
                    return;
                }
                guard->youtube_status_->setText(
                    QStringLiteral("Loopback transport on port %1")
                        .arg(guard->yt_proxy_->port()));
                guard->open_backend_and_play(guard->yt_proxy_->media_url(),
                                             title);
            });
        }).detach();
        return;
    } else if (QFileInfo::exists(source) && force_proxy_) {
        // Loopback transport test: serve a local file over the proxy.
        QString err;
        if (!yt_proxy_->start_local(source, &err)) {
            status(QStringLiteral("Transport error: %1").arg(err));
            return;
        }
        effective = yt_proxy_->media_url();
    }
    open_backend_and_play(effective, title);
}

void MainWindow::queue_and_play(const QString& url, const QString& label) {
    // Linux parity (_queue_and_play): the URL lands IN the queue, the row
    // shows the passed label (or the fetched title), then playback starts.
    if (!label.isEmpty()) display_titles_.insert(url, label);
    int row = playlist_.index_of(url);
    if (row < 0) {
        playlist_.add(url, label);
        row = playlist_.index_of(url);
        refresh_playlist();
    }
    if (row < 0) {  // defensive: model refused the row
        open_network_source(url, label);
        return;
    }
    play_queue_index(row, false);
    if (label.isEmpty()) tag_queue_title(url);  // fetch the real title
}

void MainWindow::tag_queue_title(const QString& url) {
    // Linux parity (_tag_queue_title): background yt-dlp --print %(title)s,
    // then rewrite the queue row + NOW PLAYING. A newer request invalidates
    // a stale one (generation guard like the resolver).
    if (!casu::network::is_youtube_url(url.toStdString())) return;
    const int generation = ++title_generation_;
    QPointer<MainWindow> guard(this);
    std::thread([guard, generation, url] {
        std::string title;
        std::string uploader;
        try {
            const auto t = casu::network::YtDlp().title(url.toStdString(), 30000);
            title = t.first;
            uploader = t.second;
        } catch (const std::exception&) {
            return;  // keep the existing label
        }
        if (title.empty()) return;
        std::string label = title;
        if (!uploader.empty()) label += " — " + uploader;
        const QString q_label = QString::fromStdString(label);
        QMetaObject::invokeMethod(QCoreApplication::instance(),
                                  [guard, generation, url, q_label] {
            if (!guard) return;
            if (generation != guard->title_generation_) return;
            guard->display_titles_.insert(url, q_label);
            guard->refresh_playlist();
            // NOW PLAYING follows when the tagged row is the current source.
            if (guard->current_source_ == url) {
                guard->current_title_ = q_label;
                guard->topbar_title_->setText(q_label);
            }
        });
    }).detach();
}

void MainWindow::apply_backend_settings() {
    if (!backend_) return;
    try {
        backend_->set_volume(volume_);
        backend_->set_mute(muted_);
        if (rate_ != 1.0) backend_->set_rate(rate_);
    } catch (const casu::playback::PlaybackError&) {
        // settings are best-effort; playback continues
    }
}

QString MainWindow::selected_child_entry() const {
    if (!playlist_view_) return QString();
    // Linux parity (PlaylistPane.selected_child): a selected CHILD of an
    // expanded group wins over the top-level selection.
    for (auto* it : playlist_view_->selectedItems()) {
        if (it->parent() != nullptr) {
            const QString entry = it->data(0, Qt::UserRole).toString();
            if (!entry.isEmpty()) return entry;
        }
    }
    return QString();
}

bool MainWindow::play_selected_child() {
    const QString child = selected_child_entry();
    if (child.isEmpty()) return false;
    // Owning group row (queue position) for highlighting/current bookkeeping.
    int row = -1;
    for (int i = 0; i < playlist_.size(); ++i) {
        const PlaylistItem& item = playlist_.items()[i];
        if (!item.is_playlist || !QFileInfo::exists(item.path)) continue;
        PlaylistModel tmp;
        if (!PlaylistModel::load_file(item.path, &tmp).empty()) continue;
        if (tmp.index_of(child) >= 0) { row = i; break; }
    }
    if (row < 0) row = playlist_.index_of(child);
    // Linux parity (_play_playlist_entry): URLs resolve externally, missing
    // files toast without touching playback.
    if (is_network_like(child)) {
        play_seq_entry(child, row < 0 ? 0 : row, false);
        return true;
    }
    if (!QFileInfo::exists(child)) {
        toast(QStringLiteral("Local file not found: %1").arg(QFileInfo(child).fileName()));
        return true;
    }
    play_seq_entry(child, row < 0 ? 0 : row, false);
    return true;
}

void MainWindow::toggle_playback() {
    if (!backend_) {
        if (playlist_.empty() && selected_child_entry().isEmpty()) {
            status(QStringLiteral("Add a media file first."));
            return;
        }
        // Linux parity (play_selected): a selected playlist child plays
        // through the group-resolution path, before any top-level row.
        if (play_selected_child()) return;
        int idx = playlist_.current_index() < 0 ? 0 : playlist_.current_index();
        play_queue_index(idx, false);
        return;
    }
    if (controller_->state() == casu::playback::PlaybackState::PAUSED) {
        controller_->pause_or_resume();
        paused_ = false;
    } else if (controller_->state() == casu::playback::PlaybackState::PLAYING) {
        controller_->pause_or_resume();
        paused_ = true;
    } else {
        controller_->play();
        paused_ = false;
    }
    update_play_button();
    if (visualizer_) static_cast<VisualizerWidget*>(visualizer_)->set_playing(!paused_);
}

void MainWindow::pause() {
    if (!backend_) return;
    if (controller_->state() == casu::playback::PlaybackState::PLAYING) {
        controller_->pause_or_resume();
        paused_ = true;
        update_play_button();
    }
}

void MainWindow::resume_after_seek() {
    if (!backend_) return;
    if (paused_ && controller_->state() == casu::playback::PlaybackState::PAUSED) {
        controller_->pause_or_resume();
        paused_ = false;
        update_play_button();
    }
}

void MainWindow::play_queue_index(int index, bool automatic) {
    if (index < 0 || index >= playlist_.size()) return;
    navigate("NOW PLAYING");
    // A playlist GROUP row plays its first entry; the group itself stays in
    // the queue (visible, movable) — it is never dissolved into its entries.
    QString path;
    if (playlist_.is_playlist_row(index)) {
        const QVector<QString> seq = mpcasu::playlist_logical_sequence(playlist_.items());
        const int pos = mpcasu::playlist_row_to_seq(playlist_.items(), index);
        if (pos < 0 || pos >= seq.size()) {
            status(QStringLiteral("Playlist is empty"));
            return;
        }
        path = seq[pos];
    } else {
        path = playlist_.items()[index].path;
    }
    play_seq_entry(path, index, automatic);
}

void MainWindow::play_seq_entry(const QString& path, int row, bool automatic) {
    if (row < 0 || row >= playlist_.size()) return;
    playlist_.set_current(row);
    current_played_path_ = path;
    refresh_playlist();
    const PlaylistItem& item = playlist_.items()[row];
    if (item.is_url || is_network_like(path)) {
        open_network_source(path, item.title);
    } else {
        stop_playback();
        open_backend_and_play(path, item.title);
    }
}

void MainWindow::play_selected_path(const QString& path) {
    int idx = playlist_.index_of(path);
    if (idx >= 0) { play_queue_index(idx, false); return; }
    stop_playback();
    open_backend_and_play(path, display_title_for_path(path));
}

QVector<QString> MainWindow::logical_sequence() const {
    return mpcasu::playlist_logical_sequence(playlist_.items());
}

void MainWindow::play_next(bool automatic) {
    if (playlist_.empty()) { stop_playback(); return; }
    if (automatic && playlist_.repeat == PlaylistModel::RepeatMode::One &&
        !current_played_path_.isEmpty() && backend_) {
        // Replay the current track.
        end_handled_ = false;
        try { controller_->play(); controller_->seek(0.0); } catch (const casu::playback::PlaybackError&) {}
        paused_ = false;
        update_play_button();
        return;
    }
    if (!seq_valid_) { seq_ = logical_sequence(); seq_valid_ = true; }
    if (seq_.isEmpty()) return;
    int pos = seq_.indexOf(current_played_path_);
    // Linux parity: when the current entry is not part of the sequence
    // (e.g. after queue changes), fall back to the SEQUENCE position of the
    // current row — never use the raw row index as a sequence index.
    if (pos < 0) {
        const int row = playlist_.current_index();
        const int mapped = playlist_row_to_seq(playlist_.items(), row);
        pos = mapped >= 0 ? mapped : 0;
    }
    if (playlist_.shuffle && seq_.size() > 1) {
        int target;
        do { target = QRandomGenerator::global()->bounded(seq_.size()); } while (target == pos);
        // Map the sequence position back to its OWNING row (Linux
        // _play_playlist_entry highlights what actually plays).
        const int row = playlist_seq_owner_row(playlist_.items(), target);
        play_seq_entry(seq_[target], row < 0 ? 0 : row, automatic);
        return;
    }
    int target = pos + 1;
    if (target >= seq_.size()) {
        if (playlist_.repeat == PlaylistModel::RepeatMode::All) target = 0;
        else { stop_playback(); status(QStringLiteral("End of playlist")); return; }
    }
    const int row = playlist_seq_owner_row(playlist_.items(), target);
    if (row < 0) { stop_playback(); return; }
    play_seq_entry(seq_[target], row, automatic);
}

void MainWindow::play_previous() {
    if (playlist_.empty()) return;
    if (!seq_valid_) { seq_ = logical_sequence(); seq_valid_ = true; }
    if (seq_.isEmpty()) return;
    int pos = seq_.indexOf(current_played_path_);
    if (pos < 0) {
        const int row = playlist_.current_index();
        const int mapped = playlist_row_to_seq(playlist_.items(), row);
        pos = mapped >= 0 ? mapped : 0;
    }
    int target = pos - 1;
    if (target < 0 && playlist_.repeat == PlaylistModel::RepeatMode::All) target = seq_.size() - 1;
    if (target < 0) { status(QStringLiteral("Beginning of playlist")); return; }
    const int row = playlist_seq_owner_row(playlist_.items(), target);
    if (row < 0) return;
    play_seq_entry(seq_[target], row, false);
}

void MainWindow::handle_end() {
    if (end_handled_ || advancing_ || !backend_) return;
    end_handled_ = true;
    advancing_ = true;
    play_next(true);
    advancing_ = false;
}

void MainWindow::seek_to(double seconds) {
    if (!backend_ || seconds < 0) return;
    try {
        controller_->seek(seconds);
        int ms = qBound(0, static_cast<int>(seconds * 1000.0), 0x7fffffff);
        seek_slider_->setValue(ms);
        time_current_->setText(format_duration(seconds));
    } catch (const casu::playback::PlaybackError& e) {
        status(QStringLiteral("Cannot seek — %1").arg(QString::fromStdString(e.what())));
    }
}

void MainWindow::set_volume(int value) {
    volume_ = qBound(0, value, 200);
    if (volume_slider_ && volume_slider_->value() != volume_) volume_slider_->setValue(volume_);
    if (backend_) {
        try { backend_->set_volume(volume_); } catch (const casu::playback::PlaybackError&) {}
    }
    // Linux parity: live values survive a restart (saved on every change).
    if (app_settings_.player.volume != volume_) {
        app_settings_.player.volume = volume_;
        settings_->save(app_settings_.player);
    }
}

void MainWindow::toggle_mute() {
    muted_ = !muted_;
    if (backend_) {
        try { backend_->set_mute(muted_); } catch (const casu::playback::PlaybackError&) {}
    }
    mute_btn_->setText(muted_ ? QStringLiteral("×") : QStringLiteral("♪"));
    status(muted_ ? QStringLiteral("Muted")
                  : QStringLiteral("Volume %1%").arg(volume_));
    if (app_settings_.player.muted != muted_) {
        app_settings_.player.muted = muted_;
        settings_->save(app_settings_.player);
    }
}

void MainWindow::cycle_rate() {
    const double rates[] = {0.5, 1.0, 1.25, 1.5, 2.0};
    double next = 1.0;
    for (double r : rates) {
        if (qAbs(r - rate_) < 0.01) { next = r; break; }
    }
    int i = 0;
    for (; i < 5; ++i) if (qAbs(rates[i] - next) < 0.01) break;
    next = rates[(i + 1) % 5];
    rate_ = next;
    rate_btn_->setText(QString("%1×").arg(rate_, 0, 'g', 3));
    if (backend_) {
        try { backend_->set_rate(rate_); } catch (const casu::playback::PlaybackError&) {}
    }
    if (qAbs(app_settings_.player.rate - rate_) > 0.001) {
        app_settings_.player.rate = rate_;
        settings_->save(app_settings_.player);
    }
    status(QStringLiteral("Playback rate %1× (applies on next media)")
               .arg(rate_, 0, 'g', 3));
}

void MainWindow::toggle_fullscreen() {
    if (isFullScreen()) {
        showNormal();
        exit_fullscreen_ui();
        return;
    }
    // Linux parity: hide chrome, floating transport overlay instead.
    if (sidebar_) sidebar_->hide();
    if (topbar_) topbar_->hide();
    if (transport_frame_) transport_frame_->hide();
    if (diagnostics_bar_) diagnostics_bar_->hide();
    statusBar()->hide();
    showFullScreen();
    show_fs_overlay();
}

void MainWindow::exit_fullscreen_ui() {
    hide_fs_overlay();
    if (sidebar_) sidebar_->show();
    if (topbar_) topbar_->show();
    if (transport_frame_) transport_frame_->show();
    if (diagnostics_bar_) diagnostics_bar_->show();
    statusBar()->show();
}

void MainWindow::show_fs_overlay() {
    if (!fs_overlay_ || !isFullScreen()) return;
    if (fs_title_ && !current_title_.isEmpty()) fs_title_->setText(current_title_);
    if (fs_play_btn_) {
        const casu::playback::PlaybackState st =
            controller_ ? controller_->state() : casu::playback::PlaybackState::STOPPED;
        fs_play_btn_->setText(st == casu::playback::PlaybackState::PLAYING && !paused_
                                  ? QStringLiteral("| |")
                                  : QStringLiteral("▶"));
    }
    if (fs_time_ && controller_) {
        const double pos = controller_->position();
        const double dur = controller_->duration() > 0.0 ? controller_->duration() : duration_;
        fs_time_->setText(QStringLiteral("%1 / %2").arg(format_duration(pos), format_duration(dur)));
    }
    const int w = player_page_ ? player_page_->width() : width();
    const int h = player_page_ ? player_page_->height() : height();
    const QPoint origin = player_page_ ? player_page_->mapTo(this, QPoint(0, 0)) : QPoint(0, 0);
    fs_overlay_->setGeometry(origin.x() + 24, origin.y() + h - 52, w - 48, 40);
    fs_overlay_->raise();
    fs_overlay_->show();
    if (fs_hide_timer_) fs_hide_timer_->start();
}

void MainWindow::hide_fs_overlay() {
    if (fs_hide_timer_) fs_hide_timer_->stop();
    if (fs_overlay_) fs_overlay_->hide();
}

void MainWindow::mouseMoveEvent(QMouseEvent* event) {
    if (isFullScreen()) show_fs_overlay();
    QMainWindow::mouseMoveEvent(event);
}

void MainWindow::resizeEvent(QResizeEvent* event) {
    QMainWindow::resizeEvent(event);
    if (isFullScreen()) show_fs_overlay();
    if (toast_label_ && toast_label_->isVisible()) {
        const QWidget* stage = toast_label_->parentWidget();
        const int width = qMin(toast_label_->width(), qMax(240, stage->width() - 32));
        toast_label_->setFixedWidth(width);
        toast_label_->adjustSize();
        toast_label_->move(qMax(16, (stage->width() - toast_label_->width()) / 2),
                           qMax(8, stage->height() - toast_label_->height() - 18));
    }
}

void MainWindow::moveEvent(QMoveEvent* event) {
    QMainWindow::moveEvent(event);
    clamp_to_screen();
}

void MainWindow::showEvent(QShowEvent* event) {
    QMainWindow::showEvent(event);
    clamp_to_screen();
}

void MainWindow::clamp_to_screen() {
    if (isFullScreen() || clamping_) return;
    clamping_ = true;
    QScreen* screen = QGuiApplication::primaryScreen();
    if (screen) {
        const QRect avail = screen->availableGeometry();
        QRect geo = geometry();
        geo.setWidth(qMin(geo.width(), avail.width()));
        geo.setHeight(qMin(geo.height(), avail.height()));
        const int max_x = avail.left() + qMax(0, avail.width() - geo.width());
        const int max_y = avail.top() + qMax(0, avail.height() - geo.height());
        geo.moveLeft(qBound(avail.left(), geo.left(), max_x));
        geo.moveTop(qBound(avail.top(), geo.top(), max_y));
        if (geo != geometry()) setGeometry(geo);
    }
    clamping_ = false;
}

void MainWindow::change_volume(int delta) {
    set_volume(volume_ + delta);
    toast(QStringLiteral("Volume %1%").arg(volume_));
}

void MainWindow::open_files_dialog() {
    const QStringList files = QFileDialog::getOpenFileNames(
        this, QStringLiteral("Open media"), QDir::homePath(),
        QStringLiteral("Media (*.mp4 *.mkv *.webm *.avi *.mov *.mp3 *.flac *.wav *.ogg *.m4a *.aac "
                       "*.opus *.casu *.mp5 *.m3u *.m3u8 *.pls);;All files (*.*)"));
    if (files.isEmpty()) return;
    add_files(files);
    if (playlist_.current_index() >= 0)
        play_queue_index(playlist_.current_index(), false);
}

void MainWindow::open_url_dialog() {
    bool ok = false;
    QString url = QInputDialog::getText(this, QStringLiteral("Open URL"),
                                        QStringLiteral("Stream URL or YouTube link"),
                                        QLineEdit::Normal, QString(), &ok);
    if (!ok || url.trimmed().isEmpty()) return;
    const QString trimmed = url.trimmed();
    if (casu::network::is_youtube_url(trimmed.toStdString())) {
        // Linux parity: YouTube links enter the queue and get their real
        // title resolved (never a raw URL as "Now Playing").
        queue_and_play(trimmed, QString());
        return;
    }
    add_files({trimmed});
    if (playlist_.current_index() >= 0)
        play_queue_index(playlist_.current_index(), false);
}

void MainWindow::save_snapshot() {
    if (!backend_) { status(QStringLiteral("No media loaded")); return; }
    QString dir = app_settings_.snapshot_dir.isEmpty() ? output_dir_ : app_settings_.snapshot_dir;
    QDir().mkpath(dir);
    const QString stamp =
        QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss"));
    const QString name = QStringLiteral("snapshot-%1.png").arg(stamp);
    try {
        backend_->snapshot((dir + "/" + name).toStdString());
        toast(QStringLiteral("Snapshot saved · %1").arg(name));
        status(QStringLiteral("Snapshot saved: %1/%2").arg(dir, name));
    } catch (const casu::playback::PlaybackError& e) {
        status(QStringLiteral("Snapshot failed: %1").arg(QString::fromStdString(e.what())));
    }
}

void MainWindow::cycle_repeat() {
    using R = PlaylistModel::RepeatMode;
    if (playlist_.repeat == R::Off) playlist_.repeat = R::All;
    else if (playlist_.repeat == R::All) playlist_.repeat = R::One;
    else playlist_.repeat = R::Off;
    repeat_btn_->setText(playlist_.repeat == R::Off ? QStringLiteral("↻")
                         : playlist_.repeat == R::One ? QStringLiteral("↻1")
                                                      : QStringLiteral("↻∞"));
    app_settings_.player.repeat_mode = playlist_.repeat == R::Off ? "off"
                           : playlist_.repeat == R::One ? "one" : "all";
    settings_->save(app_settings_.player);
    status(QStringLiteral("Repeat %1").arg(app_settings_.player.repeat_mode));
}

void MainWindow::on_backend_state(casu::playback::PlaybackState s) {
    switch (s) {
        case casu::playback::PlaybackState::ENDED:
            handle_end();
            break;
        case casu::playback::PlaybackState::ERROR:
            status(QStringLiteral("Playback error detected"));
            surface_->set_video_active(false);
            set_diagnostics(QStringLiteral("backend error; inspect media information/logs"),
                            QString(), QString(), QString());
            break;
        case casu::playback::PlaybackState::PLAYING:
            paused_ = false;
            update_play_button();
            break;
        case casu::playback::PlaybackState::PAUSED:
            paused_ = true;
            update_play_button();
            break;
        default:
            break;
    }
}

void MainWindow::cycle_ab_loop() {
    // Reference cycle_ab_loop: exact toast texts/semantics; "B before A" is
    // an ERROR message and does NOT move the A point.
    if (!controller_) return;
    const double position = controller_->position();
    if (ab_loop_a_ < 0.0) {
        ab_loop_a_ = position;
        ab_loop_b_ = -1.0;
        toast(QStringLiteral("A point set at %1s")
                  .arg(ab_loop_a_, 0, 'f', 1));
        return;
    }
    if (ab_loop_b_ < 0.0) {
        if (position <= ab_loop_a_) {
            toast(QStringLiteral("B point must be after A point"));
            return;
        }
        ab_loop_b_ = position;
        toast(QStringLiteral("A–B loop active · %1s – %2s")
                  .arg(ab_loop_a_, 0, 'f', 1)
                  .arg(ab_loop_b_, 0, 'f', 1));
        return;
    }
    ab_loop_a_ = -1.0;
    ab_loop_b_ = -1.0;
    toast(QStringLiteral("A–B loop off"));
}

void MainWindow::poll() {
    if (!backend_) return;
    controller_->poll();
    const double pos = controller_->position();
    const double dur = controller_->duration();
    if (dur > 0.0 && qAbs(duration_ - dur) > 0.5) {
        duration_ = dur;
        seek_slider_->setRange(0, static_cast<int>(dur * 1000.0));
    }
    if (ab_loop_b_ > 0.0 && pos >= ab_loop_b_ && !paused_) {
        try { backend_->seek(ab_loop_a_); } catch (const casu::playback::PlaybackError&) {}
        controller_->seek(ab_loop_a_);
    }
    if (!seek_slider_->isSliderDown()) {
        int ms = qBound(0, static_cast<int>(pos * 1000.0), 0x7fffffff);
        seek_slider_->setValue(ms);
        time_current_->setText(format_duration(pos));
        // Live streams (network source, no finite duration) are labelled
        // LIVE instead of a meaningless 00:00 — same as the web players
        // and the Linux Qt player.
        time_total_->setText(duration_ > 0.0
                                 ? format_duration(duration_)
                                 : (current_source_.contains(
                                        QStringLiteral("://"))
                                        ? QStringLiteral("LIVE")
                                        : format_duration(duration_)));
    }
    if (fs_overlay_ && fs_overlay_->isVisible() && fs_time_) {
        const double dur = controller_->duration() > 0.0 ? controller_->duration() : duration_;
        const QString total = duration_ > 0.0
                                  ? format_duration(dur)
                                  : (current_source_.contains(
                                         QStringLiteral("://"))
                                         ? QStringLiteral("LIVE")
                                         : format_duration(dur));
        fs_time_->setText(QStringLiteral("%1 / %2").arg(format_duration(pos), total));
    }
    if (duration_ > 0.0 && pos >= duration_ - 0.25 && !paused_) handle_end();
    const casu::playback::PlaybackState st = controller_->state();
    if (st == casu::playback::PlaybackState::ERROR) {
        if (!error_latched_) {
            error_latched_ = true;
            // Reference parity: KEEP the failed pipeline so the diagnostics
            // stay inspectable instead of tearing it down silently.
            QString detail;
            if (backend_)
                detail = QString::fromStdString(backend_->last_error_detail());
            status(detail.isEmpty()
                       ? QStringLiteral("Playback error detected")
                       : QStringLiteral("Playback error: %1").arg(detail));
            set_diagnostics(QStringLiteral("Legacy backend"),
                            QStringLiteral("ERROR"), QStringLiteral("n/a"),
                            detail.isEmpty() ? QStringLiteral("see status")
                                             : detail);
        }
        return;  // do not run the rest of poll against a dead pipeline
    }
    error_latched_ = false;
}

void MainWindow::update_play_button() {
    if (!play_btn_) return;
    const casu::playback::PlaybackState st = controller_->state();
    play_btn_->setText(st == casu::playback::PlaybackState::PLAYING && !paused_
                           ? QStringLiteral("| |")
                           : QStringLiteral("▶"));
    if (fs_play_btn_)
        fs_play_btn_->setText(st == casu::playback::PlaybackState::PLAYING && !paused_
                                  ? QStringLiteral("| |")
                                  : QStringLiteral("▶"));
}

// ------------------------------------------------------------------ playlist UI actions

void MainWindow::choose_files() {
    const QStringList files = QFileDialog::getOpenFileNames(
        this, QStringLiteral("Choose media"), QDir::homePath(),
        QStringLiteral("Media (*.mp4 *.mkv *.webm *.avi *.mov *.mp3 *.flac *.wav *.ogg *.m4a *.aac "
                       "*.opus *.casu *.mp5 *.m3u *.m3u8 *.pls);;All files (*.*)"));
    if (files.isEmpty()) return;
    add_files(files);
}

void MainWindow::add_url() {
    bool ok = false;
    QString url = QInputDialog::getText(this, QStringLiteral("Add URL"),
                                        QStringLiteral("Stream URL or YouTube link"),
                                        QLineEdit::Normal, QString(), &ok);
    if (!ok || url.trimmed().isEmpty()) return;
    const QString trimmed = url.trimmed();
    add_files({trimmed});
    // Linux parity: queued YouTube links get their real title async.
    if (casu::network::is_youtube_url(trimmed.toStdString()))
        tag_queue_title(trimmed);
}

void MainWindow::load_playlist_file() {
    QString file = QFileDialog::getOpenFileName(this, QStringLiteral("Load playlist"),
                                                QDir::homePath(),
                                                QStringLiteral("Playlists (*.m3u *.m3u8 *.pls)"));
    if (file.isEmpty()) return;
    PlaylistModel tmp;
    std::string err = PlaylistModel::load_file(file, &tmp);
    if (!err.empty()) { status(QStringLiteral("Playlist error: %1").arg(QString::fromStdString(err))); return; }
    // The playlist joins the queue as ONE visible group row (entries stay in
    // their playlist, the group stays movable).
    if (playlist_.index_of(file) < 0) playlist_.add(file);
    app_settings_.last_playlist = file;
    settings_->save(app_settings_.player);
    invalidate_seq();
    refresh_playlist();
    status(QStringLiteral("Added %1 as playlist group · %2 entry/ies").arg(QFileInfo(file).fileName()).arg(tmp.size()));
}

void MainWindow::save_playlist_file() {
    if (playlist_.empty()) return;
    if (playlist_.empty()) {
        toast(QStringLiteral("The queue is empty — nothing to save."));
        return;
    }
    QString file = QFileDialog::getSaveFileName(this, QStringLiteral("Save playlist"),
                                                QDir::homePath() + "/queue.m3u",
                                                QStringLiteral("M3U (*.m3u *.m3u8);;PLS (*.pls);;XSPF (*.xspf);;MPCASU JSON (*.json)"));
    if (file.isEmpty()) return;
    if (!file.contains(QLatin1Char('.'))) file += QStringLiteral(".m3u");
    // Save the queue as ONE flat playlist: playlist groups are resolved into
    // their entries so the file contains real media/URLs, never references.
    PlaylistModel flat;
    for (const PlaylistItem& item : playlist_.items()) {
        if (item.is_playlist) {
            PlaylistModel tmp;
            if (PlaylistModel::load_file(item.path, &tmp).empty()) {
                for (const PlaylistItem& entry : tmp.items()) flat.add(entry.path, entry.title);
                continue;
            }
        }
        flat.add(item.path, item.title);
    }
    std::string err = PlaylistModel::save_file(file, flat);
    if (!err.empty()) status(QStringLiteral("Playlist error: %1").arg(QString::fromStdString(err)));
    else status(QStringLiteral("Playlist saved"));
}

void MainWindow::playlist_double_clicked() {
    QTreeWidgetItem* item = playlist_view_->currentItem();
    if (!item) return;
    if (item->parent() == nullptr) {
        int row = playlist_view_->indexOfTopLevelItem(item);
        if (row < 0) return;
        if (playlist_.is_playlist_row(row)) {
            item->setExpanded(!item->isExpanded());
            return;
        }
        play_queue_index(row, false);
        return;
    }
    // A playlist child: play exactly that entry; the group stays visible and
    // playback continues through the logical sequence afterwards.
    navigate("NOW PLAYING");
    int row = playlist_view_->indexOfTopLevelItem(item->parent());
    if (row >= 0 && !item->data(0, Qt::UserRole).toString().isEmpty())
        play_seq_entry(item->data(0, Qt::UserRole).toString(), row, false);
}

void MainWindow::playlist_context_menu(const QPoint& pos) {
    QMenu menu(this);
    QTreeWidgetItem* item = playlist_view_->itemAt(pos);
    if (!item) {
        menu.addAction(QStringLiteral("Clear queue"), this, [this] {
            playlist_.clear();
            invalidate_seq();
            refresh_playlist();
            // Linux parity (_on_playlist_remove with no indices): playback
            // stops and the now-playing state resets.
            stop_playback();
            status(QStringLiteral("Playlist cleared"));
        });
        menu.exec(playlist_view_->viewport()->mapToGlobal(pos));
        return;
    }

    QTreeWidgetItem* top = item->parent() ? item->parent() : item;
    const bool child = item->parent() != nullptr;
    QVector<int> rows;
    for (auto* it : playlist_view_->selectedItems())
        if (it->parent() == nullptr) rows.append(playlist_view_->indexOfTopLevelItem(it));
    if (rows.isEmpty()) rows.append(playlist_view_->indexOfTopLevelItem(top));

    const int count = rows.size();
    QString play_label = count == 1 ? QStringLiteral("Play") :
                                      QStringLiteral("Play (%1 items)").arg(count);
    // Linux parity (_on_queue_child_play): "Play" on a selected CHILD plays
    // exactly that entry — never the group's first entry.
    QStringList child_entries;
    for (auto* it : playlist_view_->selectedItems())
        if (it->parent() != nullptr && !it->data(0, Qt::UserRole).toString().isEmpty())
            child_entries.append(it->data(0, Qt::UserRole).toString());
    if (!child_entries.isEmpty()) {
        menu.addAction(play_label, this, [this, child_entries] {
            for (const QString& entry : child_entries) {
                int row = -1;
                for (int i = 0; i < playlist_.size(); ++i) {
                    const PlaylistItem& pl = playlist_.items()[i];
                    if (!pl.is_playlist || !QFileInfo::exists(pl.path)) continue;
                    PlaylistModel tmp;
                    if (!PlaylistModel::load_file(pl.path, &tmp).empty()) continue;
                    if (tmp.index_of(entry) >= 0) { row = i; break; }
                }
                play_seq_entry(entry, row < 0 ? 0 : row, false);
                return;  // single entry starts playback; queue continues in sequence
            }
        });
    } else {
        menu.addAction(play_label, this, [this, rows] {
            play_queue_index(rows.first(), false);
        });
    }

    if (count == 1 && !child && playlist_.is_playlist_row(rows.first())) {
        QTreeWidgetItem* g = playlist_view_->topLevelItem(rows.first());
        if (g->isExpanded())
            menu.addAction(QStringLiteral("Collapse"), this, [g] { g->setExpanded(false); });
        else
            menu.addAction(QStringLiteral("Expand"), this, [g] { g->setExpanded(true); });
    }

    // Merge/save the selection into a playlist ("sort in"). Whole playlist
    // groups expand into their entries; children are used directly.
    QString merge_label = count == 1 ? QStringLiteral("Save selection to playlist…")
                                     : QStringLiteral("Save %1 items to playlist…").arg(count);
    menu.addAction(merge_label, this, &MainWindow::merge_selection_into_playlist);

    if (child) {
        // Children can be taken OUT of their playlist ("sort out").
        QStringList entries;
        for (auto* it : playlist_view_->selectedItems())
            if (it->parent() != nullptr && !it->data(0, Qt::UserRole).toString().isEmpty())
                entries.append(it->data(0, Qt::UserRole).toString());
        if (entries.isEmpty()) entries.append(item->data(0, Qt::UserRole).toString());
        QString move_label = entries.size() == 1 ? QStringLiteral("Move to playlist…")
                                                 : QStringLiteral("Move %1 items to playlist…").arg(entries.size());
        menu.addAction(move_label, this, [this, entries] { move_children_to_playlist(entries); });
        QString out_label = entries.size() == 1 ? QStringLiteral("Remove from playlist")
                                                : QStringLiteral("Remove %1 items from playlist").arg(entries.size());
        menu.addAction(out_label, this, [this, entries] { remove_children_from_playlist(entries); });
    }

    menu.addSeparator();
    {
        QStringList queue_paths;
        for (auto* it : playlist_view_->selectedItems()) {
            const QString p = it->data(0, Qt::UserRole).toString();
            if (!p.isEmpty() && !p.startsWith(QStringLiteral("http")))
                queue_paths.append(p);
        }
        if (!queue_paths.isEmpty()) {
            bool any_fav = false;
            for (const QString& p : queue_paths)
                if (int idx = library_->index_of(p); idx >= 0)
                    if (library_->entries()[idx].favorite) { any_fav = true; break; }
            QString fav_label = queue_paths.size() == 1
                ? (any_fav ? QStringLiteral("★ Remove favorite")
                           : QStringLiteral("☆ Mark as favorite"))
                : (any_fav ? QStringLiteral("★ Remove favorite (%1)")
                           : QStringLiteral("☆ Mark as favorite (%1)"))
                  .arg(queue_paths.size());
            menu.addAction(fav_label, this, [this, queue_paths, any_fav] {
                for (const QString& p : queue_paths)
                    library_->set_favorite(p, !any_fav);
                refresh_library();
                status(QStringLiteral("★ Favorites updated"));
            });
            menu.addSeparator();
        }
    }
    menu.addAction(QStringLiteral("Move up"), this, [this, rows] { move_playlist_rows(rows, -1); });
    menu.addAction(QStringLiteral("Move down"), this, [this, rows] { move_playlist_rows(rows, 1); });
    QString remove_label = count == 1 ? QStringLiteral("Remove selected")
                                      : QStringLiteral("Remove (%1 items)").arg(count);
    // Linux parity (_on_playlist_remove): rows that were marked but not
    // removed stay marked (persistent selection).
    menu.addAction(remove_label, this, [this, rows] {
        remove_selected_rows(rows);
    });
    menu.exec(playlist_view_->viewport()->mapToGlobal(pos));
}

void MainWindow::move_playlist_rows(const QVector<int>& rows, int delta) {
    if (rows.isEmpty()) return;
    const auto& items = playlist_.items();
    QStringList saved;
    for (int row : rows)
        if (row >= 0 && row < items.size())
            saved << items[row].path;
    playlist_.move_many(rows, delta);
    invalidate_seq();
    refresh_playlist();
    if (!saved.isEmpty()) reselect_playlist_rows(saved);
}

void MainWindow::reselect_playlist_rows(const QStringList& paths) {
    // Persistent marking: re-select the same rows after the re-render, so
    // repeated moves/removes work without re-marking.
    if (!playlist_view_ || paths.isEmpty()) return;
    const QSet<QString> want(paths.begin(), paths.end());
    playlist_view_->clearSelection();
    for (int i = 0; i < playlist_view_->topLevelItemCount(); ++i) {
        auto* item = playlist_view_->topLevelItem(i);
        if (want.contains(item->data(0, Qt::UserRole).toString()))
            item->setSelected(true);
    }
}

void MainWindow::expand_playlist_group(QTreeWidgetItem* top) {
    const QString source = top->data(0, Qt::UserRole).toString();
    if (top->childCount() && top->child(0)->data(0, Qt::UserRole).isValid() &&
        !top->child(0)->data(0, Qt::UserRole).toString().isEmpty())
        return;
    while (top->childCount()) top->removeChild(top->child(0));
    PlaylistModel tmp;
    if (!PlaylistModel::load_file(source, &tmp).empty() || tmp.empty()) {
        auto* err = new QTreeWidgetItem(QStringList{QStringLiteral("(empty playlist)")});
        top->addChild(err);
        return;
    }
    for (const PlaylistItem& entry : tmp.items()) {
        auto* child = new QTreeWidgetItem();
        child->setText(0, entry.title.isEmpty() ? queue_label_for(entry.path)
                                                : entry.title);
        child->setData(0, Qt::UserRole, entry.path);
        child->setToolTip(0, entry.path);
        child->setText(1, queue_badge_for(entry.path));
        child->setTextAlignment(1, Qt::AlignRight | Qt::AlignVCenter);
        child->setForeground(1, QColor(mpcasu::palette().muted));
        QFont badge_font = child->font(0);
        badge_font.setPointSizeF(qMax(7.0, badge_font.pointSizeF() - 1.0));
        child->setFont(1, badge_font);
        top->addChild(child);
    }
}

void MainWindow::refresh_playlist_group(const QString& path) {
    if (!playlist_view_) return;
    for (int i = 0; i < playlist_view_->topLevelItemCount(); ++i) {
        QTreeWidgetItem* top = playlist_view_->topLevelItem(i);
        if (top->data(0, Qt::UserRole).toString() != path) continue;
        const bool expanded = top->isExpanded();
        while (top->childCount()) top->removeChild(top->child(0));
        expand_playlist_group(top);
        top->setExpanded(expanded);
        return;
    }
}

void MainWindow::merge_selection_into_playlist() {
    // Collect the selected media paths / URLs (deduplicated). Playlist files
    // (groups) expand into their entries; children are used directly — so
    // whole playlists, loose files/URLs and single playlist entries can all
    // be sorted into a playlist.
    QStringList entries;
    for (auto* it : playlist_view_->selectedItems()) {
        const QString path = it->data(0, Qt::UserRole).toString();
        if (path.isEmpty()) continue;
        if (it->parent() == nullptr && PlaylistModel::looks_like_playlist(path) && QFileInfo::exists(path)) {
            PlaylistModel tmp;
            if (PlaylistModel::load_file(path, &tmp).empty()) {
                for (const PlaylistItem& item : tmp.items())
                    if (!entries.contains(item.path)) entries.append(item.path);
                continue;
            }
        }
        if (!entries.contains(path)) entries.append(path);
    }
    if (entries.isEmpty()) { status(QStringLiteral("Nothing to merge: no playable item selected.")); return; }

    // Choose target: extend an existing playlist (last used) or create a new one.
    QString target = app_settings_.last_playlist;
    if (!target.isEmpty() && QFileInfo::exists(target)) {
        QMessageBox box(this);
        box.setWindowTitle(QStringLiteral("Merge into playlist"));
        box.setText(QStringLiteral("Append %1 item(s) to the existing playlist\n%2 ?")
                        .arg(entries.size()).arg(QFileInfo(target).fileName()));
        QPushButton* yes = box.addButton(QStringLiteral("Merge"), QMessageBox::AcceptRole);
        box.addButton(QStringLiteral("New playlist…"), QMessageBox::ActionRole);
        box.addButton(QMessageBox::Cancel);
        box.exec();
        if (box.clickedButton() == yes) {
            // fall through to merge into `target`
        } else if (box.clickedButton()->text() == QStringLiteral("New playlist…")) {
            target = QFileDialog::getSaveFileName(this, QStringLiteral("New playlist"),
                                                  QDir::homePath() + "/queue.m3u",
                                                  QStringLiteral("M3U (*.m3u);;PLS (*.pls)"));
            if (target.isEmpty()) return;
        } else {
            return;  // cancel
        }
    } else {
        target = QFileDialog::getSaveFileName(this, QStringLiteral("Save playlist"),
                                              QDir::homePath() + "/queue.m3u",
                                              QStringLiteral("M3U (*.m3u);;PLS (*.pls)"));
        if (target.isEmpty()) return;
    }

    // Merge: load existing playlist (if any), append selected entries (dedup),
    // then save back in the original format.
    PlaylistModel merged;
    std::string err;
    if (QFileInfo::exists(target)) {
        err = PlaylistModel::load_file(target, &merged);
        if (!err.empty()) { status(QStringLiteral("Could not read playlist: %1").arg(QString::fromStdString(err))); return; }
    }
    int added = 0;
    for (const QString& entry : entries) {
        if (merged.index_of(entry) < 0) { merged.add(entry); ++added; }
    }
    err = PlaylistModel::save_file(target, merged);
    if (!err.empty()) { status(QStringLiteral("Could not save playlist: %1").arg(QString::fromStdString(err))); return; }
    app_settings_.last_playlist = target;
    settings_->save(app_settings_.player);
    status(QStringLiteral("Added %1 item(s) to %2").arg(added).arg(QFileInfo(target).fileName()));
    toast(QStringLiteral("Playlist updated · %1").arg(QFileInfo(target).fileName()));
    // Linux parity: the logical playback sequence must reflect the new
    // playlist contents, and an already-queued group refreshes in place.
    invalidate_seq();
    if (playlist_.index_of(target) >= 0) refresh_playlist_group(target);
}

void MainWindow::remove_children_from_playlist(const QStringList& entries) {
    if (entries.isEmpty()) return;
    // Children can be sorted OUT of their playlist: for every playlist group
    // in the queue that contains one of the entries, the entry is removed
    // from the playlist file and the group is refreshed.
    int removed = 0;
    for (int i = 0; i < playlist_.items().size(); ++i) {
        const PlaylistItem& item = playlist_.items()[i];
        if (!item.is_playlist || !QFileInfo::exists(item.path)) continue;
        PlaylistModel tmp;
        if (!PlaylistModel::load_file(item.path, &tmp).empty()) continue;
        QVector<int> to_remove;
        for (int k = 0; k < tmp.items().size(); ++k)
            if (entries.contains(tmp.items()[k].path)) to_remove.append(k);
        if (to_remove.isEmpty()) continue;
        const int before = tmp.size();
        tmp.remove_many(to_remove);
        std::string err = PlaylistModel::save_file(item.path, tmp);
        if (!err.empty()) { status(QStringLiteral("Could not save playlist: %1").arg(QString::fromStdString(err))); return; }
        removed += before - tmp.size();
        refresh_playlist_group(item.path);
    }
    if (removed == 0) { status(QStringLiteral("Selected items are not inside any queued playlist.")); return; }
    invalidate_seq();
    status(QStringLiteral("Removed %1 item(s) from their playlist(s)").arg(removed));
}

void MainWindow::move_children_to_playlist(const QStringList& entries) {
    if (entries.isEmpty()) return;
    // Target candidates: every playlist GROUP in the queue except the group
    // the selected children belong to.
    QStringList candidates;
    QTreeWidgetItem* parent = nullptr;
    for (auto* it : playlist_view_->selectedItems()) {
        if (it->parent()) { parent = it->parent(); break; }
    }
    const QString own = parent ? parent->data(0, Qt::UserRole).toString() : QString();
    for (const PlaylistItem& item : playlist_.items()) {
        if (item.is_playlist && item.path != own && QFileInfo::exists(item.path))
            candidates.append(item.path);
    }
    QString target;
    if (!candidates.isEmpty()) {
        QInputDialog dlg(this);
        dlg.setWindowTitle(QStringLiteral("Move to playlist"));
        dlg.setLabelText(QStringLiteral("Move %1 item(s) into:").arg(entries.size()));
        dlg.setComboBoxItems(candidates);
        if (dlg.exec() != QDialog::Accepted) return;
        target = dlg.textValue();
    } else {
        target = QFileDialog::getSaveFileName(this, QStringLiteral("New target playlist"),
                                              QDir::homePath() + "/queue.m3u",
                                              QStringLiteral("M3U (*.m3u *.m3u8);;PLS (*.pls);;XSPF (*.xspf);;MPCASU JSON (*.json)"));
        if (target.isEmpty()) return;
    }

    // Linux parity (_on_child_move_to_playlist): remove the children from
    // their SOURCE playlists FIRST (never from the chosen target), then append
    // them to the target. Removing after adding would wipe them out of the
    // target again and lose the entries entirely.
    int removed_total = 0;
    QStringList touched;
    for (int i = 0; i < playlist_.items().size(); ++i) {
        const PlaylistItem& item = playlist_.items()[i];
        if (!item.is_playlist || !QFileInfo::exists(item.path)) continue;
        if (item.path == target) continue;  // never strip the target again
        PlaylistModel tmp;
        if (!PlaylistModel::load_file(item.path, &tmp).empty()) continue;
        QVector<int> to_remove;
        for (int k = 0; k < tmp.items().size(); ++k)
            if (entries.contains(tmp.items()[k].path)) to_remove.append(k);
        if (to_remove.isEmpty()) continue;
        const int before = tmp.size();
        tmp.remove_many(to_remove);
        std::string err = PlaylistModel::save_file(item.path, tmp);
        if (!err.empty()) { status(QStringLiteral("Could not update %1: %2").arg(QFileInfo(item.path).fileName()).arg(QString::fromStdString(err))); continue; }
        removed_total += before - tmp.size();
        touched.append(item.path);
    }

    PlaylistModel tmp;
    std::string err;
    if (QFileInfo::exists(target)) {
        err = PlaylistModel::load_file(target, &tmp);
        if (!err.empty()) { status(QStringLiteral("Could not read playlist: %1").arg(QString::fromStdString(err))); return; }
    }
    int added = 0;
    for (const QString& entry : entries) {
        if (tmp.index_of(entry) < 0) { tmp.add(entry); ++added; }
    }
    err = PlaylistModel::save_file(target, tmp);
    if (!err.empty()) { status(QStringLiteral("Could not save playlist: %1").arg(QString::fromStdString(err))); return; }
    app_settings_.last_playlist = target;
    settings_->save(app_settings_.player);
    for (const QString& path : touched) refresh_playlist_group(path);
    refresh_playlist_group(target);
    invalidate_seq();
    status(QStringLiteral("Moved %1 item(s) to %2").arg(added).arg(QFileInfo(target).fileName()));
}

void MainWindow::refresh_playlist() {
    if (!playlist_view_) return;
    playlist_view_->clear();
    for (int i = 0; i < playlist_.items().size(); ++i) {
        const PlaylistItem& item = playlist_.items()[i];
        QString label = item.title.isEmpty() ? item.path : item.title;
        if (display_titles_.contains(item.path)) label = display_titles_.value(item.path);
        // Linux parity (_label_for): tag title "title — artist" fallback.
        else if (!item.is_playlist) label = queue_label_for(item.path);
        if (item.is_playlist) {
            // Playlist groups stay visible as rows; a placeholder child gives
            // the expand arrow. Children are loaded lazily on expand.
            label = QStringLiteral("[Playlist] ") + label;
        }
        if (i == playlist_.current_index()) label = QStringLiteral("▶ ") + label;
        auto* it = new QTreeWidgetItem(QStringList{label});
        it->setData(0, Qt::UserRole, item.path);
        it->setToolTip(0, item.path);
        // Linux parity: right-aligned type badge in the second column.
        it->setText(1, queue_badge_for(item.path));
        it->setTextAlignment(1, Qt::AlignRight | Qt::AlignVCenter);
        it->setForeground(1, QColor(mpcasu::palette().muted));
        QFont badge_font = it->font(0);
        badge_font.setPointSizeF(qMax(7.0, badge_font.pointSizeF() - 1.0));
        it->setFont(1, badge_font);
        if (item.is_playlist) {
            auto* placeholder = new QTreeWidgetItem(QStringList{QStringLiteral("…")});
            it->addChild(placeholder);
            if (expanded_groups_.contains(item.path)) {
                it->setExpanded(true);
                expand_playlist_group(it);
            }
        }
        playlist_view_->addTopLevelItem(it);
    }
    if (playlist_.current_index() >= 0)
        playlist_view_->setCurrentItem(playlist_view_->topLevelItem(playlist_.current_index()));
    apply_queue_filter();
    request_queue_thumbnails();  // Linux parity: video rows get cached thumbnails
}

// Linux parity (_request_thumbnails): background-extract 320x180 PPM
// thumbnails (cached) for local video rows and set them as row icons.
void MainWindow::request_queue_thumbnails() {
    static const char* kVideoExts[] = {".mp4", ".mkv", ".webm", ".mov", ".avi"};
    QStringList jobs;
    for (int i = 0; i < playlist_view_->topLevelItemCount(); ++i) {
        QTreeWidgetItem* it = playlist_view_->topLevelItem(i);
        const QString path = it->data(0, Qt::UserRole).toString();
        if (path.isEmpty() || path.startsWith(QStringLiteral("http://")) ||
            path.startsWith(QStringLiteral("https://")) ||
            path.startsWith(QStringLiteral("rtsp://")) ||
            path.startsWith(QStringLiteral("rtmp://")))
            continue;
        const QString lower = path.toLower();
        bool video = false;
        for (const char* ext : kVideoExts)
            if (lower.endsWith(QLatin1String(ext))) { video = true; break; }
        if (video) jobs.append(path);
    }
    if (jobs.isEmpty()) return;
    const QString cache_dir = QStringLiteral("%1/.cache/mpcasu/thumbnails").arg(QDir::homePath());
    QDir().mkpath(cache_dir);
    QPointer<MainWindow> guard(this);
    std::thread([guard, jobs, cache_dir]() {
        for (const QString& path : jobs) {
            std::string thumb =
                casu::media::thumbnail_for(path.toStdString(), cache_dir.toStdString());
            if (thumb.empty()) continue;
            const QString t = QString::fromStdString(thumb);
            QMetaObject::invokeMethod(qApp, [guard, path, t] {
                if (guard) guard->apply_thumb(path, t);
            }, Qt::QueuedConnection);
        }
    }).detach();
}

void MainWindow::apply_thumb(const QString& path, const QString& thumb) {
    QPixmap pix(thumb);
    if (pix.isNull()) return;
    const QPixmap scaled = pix.scaled(metrics().thumbnail_width, metrics().thumbnail_height,
                                      Qt::KeepAspectRatioByExpanding,
                                      Qt::SmoothTransformation);
    for (int i = 0; i < playlist_view_->topLevelItemCount(); ++i) {
        QTreeWidgetItem* it = playlist_view_->topLevelItem(i);
        if (it->data(0, Qt::UserRole).toString() == path) it->setIcon(0, QIcon(scaled));
    }
}

// Linux parity (_apply_media_preferences): recall the stored audio/video/
// subtitle tracks and A/V delays for the current file once playback starts.
void MainWindow::apply_media_preferences() {
    if (!backend_ || current_source_.isEmpty() ||
        current_source_.contains(QStringLiteral("://")))
        return;
    if (!QFileInfo::exists(current_source_)) return;
    const mpcasu::PlaybackPreferences prefs =
        library_->playback_preferences(current_source_);
    try {
        if (prefs.audio_track >= 0) backend_->set_audio_track(prefs.audio_track);
        if (prefs.video_track >= 0) backend_->set_video_track(prefs.video_track);
        if (prefs.subtitle_track >= 0) backend_->set_subtitle_track(prefs.subtitle_track);
        audio_delay_ms_ = prefs.audio_delay_ms;
        subtitle_delay_ms_ = prefs.subtitle_delay_ms;
        backend_->set_audio_delay(audio_delay_ms_);
        backend_->set_subtitle_delay(subtitle_delay_ms_);
    } catch (const std::exception&) {
        // preferences are best-effort; playback continues untouched
    }
}

// Linux parity (_persist_media_preferences): store current tracks + delays
// for the playing file so reopening it restores the same experience.
void MainWindow::persist_media_preferences() {
    if (!backend_ || current_source_.isEmpty() ||
        current_source_.contains(QStringLiteral("://")))
        return;
    if (!QFileInfo::exists(current_source_)) return;
    try {
        mpcasu::PlaybackPreferences prefs;
        prefs.audio_track = backend_->audio_track();
        prefs.video_track = backend_->video_track();
        prefs.subtitle_track = backend_->subtitle_track();
        prefs.audio_delay_ms = audio_delay_ms_;
        prefs.subtitle_delay_ms = subtitle_delay_ms_;
        library_->set_playback_preferences(current_source_, prefs);
    } catch (const std::exception&) {
        // ignore — persistence is optional
    }
}

void MainWindow::add_files(const QStringList& paths) {
    int added = 0;
    // Linux parity (batch add with covered-set + existing_only): playlists
    // become ONE visible group row each, entries of batch-playlists are
    // "covered" so choosing a playlist together with its own files never
    // double-loads, non-existent local files are skipped, everything is
    // deduplicated against the queue.
    const PlaylistBatchPlan plan = playlist_batch_plan(paths);
    for (const QString& path : plan.rows) {
        if (playlist_.index_of(path) < 0) {
            playlist_.add(path);
            ++added;
        }
    }
    if (added == 0) return;
    invalidate_seq();
    refresh_playlist();
}

// ------------------------------------------------------------------ page actions

void MainWindow::on_library_add_current() {
    if (current_source_.isEmpty()) { status(QStringLiteral("Nothing playing yet.")); return; }
    library_->add(current_source_, current_title_);
    refresh_library();
    status(QStringLiteral("Added to library: %1").arg(current_title_));
}

void MainWindow::refresh_library() {
    // Linux parity (LibraryPage._refresh): modes + search + group navigation.
    if (!library_tracks_) return;
    library_tracks_->clear();
    const QString query = library_search_->text().trimmed().toLower();
    const QString mode = library_mode_->currentData().toString();
    const bool use_groups = (mode == "artists" || mode == "albums" ||
                             mode == "genres" || mode == "playlists");
    library_groups_->setVisible(use_groups);
    auto meta_of = [this](const QString& path, const QString& key) -> QString {
        const QString cache_key = path + QLatin1Char('|') + key;
        auto it = lib_meta_.constFind(cache_key);
        if (it != lib_meta_.constEnd()) return it.value();
        QString value;
        try {
            const auto tags = casu::media::metadata_for(path.toStdString());
            const auto found = tags.find(key.toStdString());
            if (found != tags.end()) value = QString::fromStdString(found->second);
        } catch (const std::exception&) {}
        lib_meta_.insert(cache_key, value);
        return value;
    };
    QVector<const LibraryEntry*> entries;
    if (mode == "favorites") {
        for (const LibraryEntry& e : library_->entries())
            if (e.favorite) entries.append(&e);
    } else {
        for (const LibraryEntry& e : library_->entries()) entries.append(&e);
    }
    auto matches = [&](const LibraryEntry& e) {
        if (query.isEmpty()) return true;
        const QString hay = QStringLiteral("%1 %2 %3 %4 %5 %6")
                                .arg(e.title, meta_of(e.path, "title"),
                                     meta_of(e.path, "artist"),
                                     meta_of(e.path, "album"),
                                     meta_of(e.path, "genre"),
                                     QFileInfo(e.path).fileName())
                                .toLower();
        return hay.contains(query);
    };
    if (mode == "all") {
        library_groups_->setEnabled(false);
        library_groups_->clear();
        int shown = 0;
        for (const LibraryEntry* e : entries) {
            if (!matches(*e)) continue;
            QString text = e->title.isEmpty() ? QFileInfo(e->path).fileName() : e->title;
            QStringList details;
            for (const QString& k : {"artist", "album", "genre"}) {
                const QString v = meta_of(e->path, k).trimmed();
                if (!v.isEmpty()) details << v;
            }
            if (!details.isEmpty()) text += QStringLiteral("\n") + details.join(QStringLiteral(" · "));
            auto* item = new QListWidgetItem((e->favorite ? QStringLiteral("★ ") : QString()) + text);
            item->setData(Qt::UserRole, e->path);
            library_tracks_->addItem(item);
            ++shown;
        }
        library_count_->setText(QStringLiteral("%1 tracks").arg(shown));
        return;
    }
    if (mode == "playlists") {
        scan_playlist_files();
        return;
    }
    // Group modes: artists / albums / genres.
    const QString field = mode == "artists" ? QStringLiteral("artist")
                          : mode == "albums" ? QStringLiteral("album")
                                             : QStringLiteral("genre");
    library_groups_->setEnabled(true);
    library_groups_->clear();
    // Linux parity: case-insensitive grouping with an "(unknown)" bucket.
    QMap<QString, QString> groups_by_key;  // casefold -> representative value
    for (const LibraryEntry* e : entries) {
        const QString key =
            mpcasu::library_group_key(meta_of(e->path, field));
        if (!groups_by_key.contains(key.toCaseFolded()))
            groups_by_key.insert(key.toCaseFolded(), key);
    }
    QStringList groups = groups_by_key.values();
    groups.sort(Qt::CaseInsensitive);
    library_groups_->addItems(groups);
    const QString selected = library_groups_->currentItem()
                                 ? library_groups_->currentItem()->text()
                                 : (groups.isEmpty() ? QString() : groups.first());
    if (!groups.isEmpty() && !library_groups_->currentItem())
        library_groups_->setCurrentRow(0);
    int shown = 0;
    if (!selected.isEmpty()) {
        for (const LibraryEntry* e : entries) {
            if (!matches(*e)) continue;
            const QString group_value =
                mpcasu::library_group_key(meta_of(e->path, field));
            if (group_value.compare(selected, Qt::CaseInsensitive) != 0) continue;
            QString text = e->title.isEmpty() ? QFileInfo(e->path).fileName() : e->title;
            auto* item = new QListWidgetItem((e->favorite ? QStringLiteral("★ ") : QString()) + text);
            item->setData(Qt::UserRole, e->path);
            library_tracks_->addItem(item);
            ++shown;
        }
    }
    library_count_->setText(groups.isEmpty() ? QStringLiteral("No groups found")
                                             : QStringLiteral("%1 tracks").arg(shown));
}

void MainWindow::scan_playlist_files() {
    library_groups_->blockSignals(true);
    library_groups_->clear();
    playlist_files_.clear();
    const QStringList exts = {".m3u", ".m3u8", ".pls", ".xspf", ".cue"};
    QStringList folders = app_settings_.player.watched_folders;
    if (folders.isEmpty()) folders << QDir::homePath();
    for (const QString& folder : folders) {
        QDir dir(folder);
        if (!dir.exists()) continue;
        QDirIterator it(dir.absolutePath(), QStringList(), QDir::Files,
                        QDirIterator::Subdirectories);
        while (it.hasNext()) {
            const QString fp = it.next();
            const QString ext = QFileInfo(fp).suffix().toLower();
            if (exts.contains(QLatin1Char('.') + ext)) {
                const QString name = QFileInfo(fp).baseName();
                playlist_files_[name] = fp;
                library_groups_->addItem(name);
            }
        }
    }
    library_groups_->blockSignals(false);
    library_groups_->setEnabled(true);
    if (library_groups_->count() > 0) {
        library_groups_->setCurrentRow(0);
        on_playlist_group_selected(library_groups_->item(0));
    } else {
        library_count_->setText(QStringLiteral("No playlist files found"));
    }
}

static QStringList parse_playlist_file(const QString& path) {
    QStringList entries;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) return entries;
    const QString text = QString::fromUtf8(f.readAll());
    const QString ext = QFileInfo(path).suffix().toLower();
    const QStringList lines = text.split(QLatin1Char('\n'));
    if (ext == "m3u" || ext == "m3u8") {
        for (const QString& line : lines) {
            const QString trimmed = line.trimmed();
            if (!trimmed.isEmpty() && !trimmed.startsWith('#'))
                entries.append(trimmed);
        }
    } else if (ext == "pls") {
        for (const QString& line : lines) {
            const QString trimmed = line.trimmed();
            if (trimmed.toLower().startsWith("file")) {
                const int eq = trimmed.indexOf('=');
                if (eq >= 0) {
                    QString val = trimmed.mid(eq + 1).trimmed();
                    if (val.startsWith('"') && val.endsWith('"'))
                        val = val.mid(1, val.length() - 2);
                    entries.append(val);
                }
            }
        }
    } else if (ext == "xspf") {
        QRegularExpression re("<location>(.*?)</location>",
                              QRegularExpression::CaseInsensitiveOption);
        QRegularExpressionMatchIterator it = re.globalMatch(text);
        while (it.hasNext()) {
            entries.append(it.next().captured(1).trimmed());
        }
    } else if (ext == "cue") {
        for (const QString& line : lines) {
            const QString trimmed = line.trimmed();
            if (trimmed.toUpper().startsWith("FILE ")) {
                const QStringList parts = trimmed.split(QLatin1Char(' '));
                if (parts.size() >= 2) {
                    QString fname = parts.mid(1).join(QLatin1Char(' '));
                    // Remove trailing type: "FILE "name" AUDIO"
                    const int lastQuote = fname.lastIndexOf('"');
                    if (lastQuote > 0) fname = fname.left(lastQuote);
                    if (fname.startsWith('"')) fname = fname.mid(1);
                    entries.append(fname);
                }
            }
        }
    }
    return entries;
}

void MainWindow::on_playlist_group_selected(QListWidgetItem* current) {
    if (!current) return;
    const QString name = current->text();
    const QString plPath = playlist_files_.value(name);
    if (plPath.isEmpty() || !QFileInfo::exists(plPath)) return;
    library_tracks_->clear();
    const QStringList entries = parse_playlist_file(plPath);
    int shown = 0;
    for (const QString& entry : entries) {
        const QString resolved = QFileInfo(entry).absoluteFilePath();
        const QString text = QFileInfo(resolved).fileName();
        auto* item = new QListWidgetItem(text);
        item->setData(Qt::UserRole, resolved);
        library_tracks_->addItem(item);
        ++shown;
    }
    library_count_->setText(QStringLiteral("%1 tracks").arg(shown));
}

void MainWindow::on_settings_save() {
    app_settings_.player.volume = settings_volume_->value();
    app_settings_.player.rate = settings_rate_->value();
    app_settings_.player.shuffle = settings_shuffle_->isChecked();
    app_settings_.player.repeat_mode = settings_repeat_->currentText();
    app_settings_.player.recordings_dir = settings_record_dir_->text().trimmed();
    app_settings_.player.muted = settings_muted_->isChecked();
    app_settings_.player.resume_playback = settings_resume_->isChecked();
    app_settings_.player.visualizer = settings_viz_->currentData().toString();
    app_settings_.player.cache_limit_mib = settings_cache_->value();
    app_settings_.player.watched_folders.clear();
    for (int i = 0; i < settings_folders_->count(); ++i)
        app_settings_.player.watched_folders.append(settings_folders_->item(i)->text());
    app_settings_.player.record_split_minutes = settings_split_->value();
    app_settings_.player.record_format = settings_format_->currentText();
    app_settings_.player.ytdlp_consent = settings_consent_->isChecked();
    settings_->save(app_settings_.player);
    volume_ = app_settings_.player.volume;
    rate_ = app_settings_.player.rate;
    playlist_.shuffle = app_settings_.player.shuffle;
    playlist_.repeat = app_settings_.player.repeat_mode == "one"
                           ? PlaylistModel::RepeatMode::One
                           : (app_settings_.player.repeat_mode == "all"
                                  ? PlaylistModel::RepeatMode::All
                                  : PlaylistModel::RepeatMode::Off);
    output_dir_ = app_settings_.player.recordings_dir;
    if (record_dir_) record_dir_->setText(output_dir_);
    if (volume_slider_) volume_slider_->setValue(volume_);
    if (mute_btn_) mute_btn_->setChecked(app_settings_.player.muted);
    shuffle_btn_->setChecked(playlist_.shuffle);
    if (playlist_.repeat == PlaylistModel::RepeatMode::Off) repeat_btn_->setText(QStringLiteral("↻"));
    else if (playlist_.repeat == PlaylistModel::RepeatMode::One) repeat_btn_->setText(QStringLiteral("↻1"));
    else repeat_btn_->setText(QStringLiteral("↻∞"));
    apply_viz_mode();
    status(QStringLiteral("Settings saved"));
}

void MainWindow::show_record_settings_dialog() {
    // Linux parity: quick-access dialog for recording folder/format/split.
    QDialog dlg(this);
    dlg.setWindowTitle(QStringLiteral("Recording settings"));
    auto* layout = new QVBoxLayout(&dlg);
    layout->setSpacing(10);

    auto* folder_row = new QHBoxLayout();
    folder_row->addWidget(new QLabel(QStringLiteral("Speicherort")));
    auto* folder_entry = new QLineEdit(output_dir_);
    folder_entry->setObjectName("IconButton");
    folder_row->addWidget(folder_entry, 1);
    auto* folder_btn = new QPushButton(QStringLiteral("…"));
    folder_btn->setObjectName("IconButton");
    connect(folder_btn, &QPushButton::clicked, this, [&dlg, folder_entry] {
        const QString dir = QFileDialog::getExistingDirectory(
            &dlg, QStringLiteral("Aufnahmenordner"), folder_entry->text());
        if (!dir.isEmpty()) folder_entry->setText(dir);
    });
    folder_row->addWidget(folder_btn);
    layout->addLayout(folder_row);

    auto* format_row = new QHBoxLayout();
    format_row->addWidget(new QLabel(QStringLiteral("Format")));
    auto* format_combo = new QComboBox();
    format_combo->setObjectName("IconButton");
    for (const char* fmt : {"mkv", "mp4", "ts", "webm", "ogg", "mp3", "flac", "wav"})
        format_combo->addItem(QString::fromLatin1(fmt));
    format_combo->setCurrentText(app_settings_.player.record_format);
    format_row->addWidget(format_combo);
    format_row->addStretch();
    layout->addLayout(format_row);

    auto* split_cb = new QCheckBox(QStringLiteral("Aufzeichnung automatisch teilen"));
    split_cb->setChecked(app_settings_.player.record_split_minutes > 0);
    layout->addWidget(split_cb);
    auto* split_row = new QHBoxLayout();
    split_row->addWidget(new QLabel(QStringLiteral("Alle")));
    auto* split_spin = new QSpinBox();
    split_spin->setObjectName("IconButton");
    split_spin->setRange(1, 24 * 60);
    split_spin->setSuffix(QStringLiteral(" min"));
    split_spin->setValue(qMax(1, app_settings_.player.record_split_minutes));
    split_spin->setEnabled(split_cb->isChecked());
    connect(split_cb, &QCheckBox::toggled, split_spin, &QSpinBox::setEnabled);
    split_row->addWidget(split_spin);
    split_row->addStretch();
    layout->addLayout(split_row);

    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
    connect(buttons, &QDialogButtonBox::accepted, &dlg, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dlg, &QDialog::reject);
    layout->addWidget(buttons);

    if (dlg.exec() != QDialog::Accepted) return;
    app_settings_.player.recordings_dir = folder_entry->text().trimmed();
    app_settings_.player.record_format = format_combo->currentText();
    app_settings_.player.record_split_minutes = split_cb->isChecked() ? split_spin->value() : 0;
    settings_->save(app_settings_.player);
    output_dir_ = app_settings_.player.recordings_dir;
    if (record_dir_) record_dir_->setText(output_dir_);
    if (settings_record_dir_) settings_record_dir_->setText(output_dir_);
    toast(QStringLiteral("Recording settings gespeichert"));
}

void MainWindow::on_recording_toggle() {
    // Linux parity (main_window.py toggle_recording/_record_destination).
    if (recorder_->is_recording()) {
        recorder_->stop();
        return;
    }
    if (current_source_.isEmpty()) { status(QStringLiteral("Nothing to record.")); return; }
    const QString lower = current_source_.toLower();
    if (lower.endsWith(QStringLiteral(".casu")) ||
        lower.endsWith(QStringLiteral(".mp5"))) {
        status(QStringLiteral("CASU sources are stored already — use Export instead"));
        return;
    }
    QString dir = record_dir_ ? record_dir_->text().trimmed() : output_dir_;
    if (dir.isEmpty()) dir = output_dir_;
    QDir().mkpath(dir);
    QString fmt = app_settings_.player.record_format.toLower();
    static const QStringList formats = {"mkv", "mp4", "ts", "webm",
                                        "ogg", "mp3", "flac", "wav"};
    if (!formats.contains(fmt)) fmt = QStringLiteral("mkv");
    record_split_minutes_ = app_settings_.player.record_split_minutes;
    record_part_ = 1;
    QString stem_src = current_source_;
    if (!stem_src.contains(QStringLiteral("://")))
        stem_src = QFileInfo(stem_src).completeBaseName();
    else
        stem_src = QStringLiteral("stream");
    record_stem_ = QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss")) +
                   QStringLiteral("-") + stem_src;

    auto destination = [this, dir, fmt] {
        const QString suffix = QStringLiteral(".") + fmt;
        if (record_split_minutes_ > 0)
            return dir + QStringLiteral("/") + record_stem_ +
                   QStringLiteral("-part%1").arg(record_part_, 3, 10, QChar('0')) +
                   suffix;
        return dir + QStringLiteral("/") + record_stem_ + suffix;
    };

    QString err;
    if (!recorder_->start(current_source_, destination(), &err)) {
        status(QStringLiteral("Recording start failed: %1").arg(err));
        return;
    }
    if (record_split_minutes_ > 0) {
        if (!record_timer_) {
            record_timer_ = new QTimer(this);
            record_timer_->setSingleShot(true);
            connect(record_timer_, &QTimer::timeout, this, [this] {
                // Rotate: finalize this part, then start the next one.
                recorder_->stop();
                pending_rotate_ = true;
            });
        }
        record_timer_->start(record_split_minutes_ * 60 * 1000);
    }
    status(QStringLiteral("Recording to %1").arg(destination()));
}

// Stage routing with the Linux "Drop media here" placeholder:
// 2 = empty hint (no media), 0 = video surface, 1 = visualizer.
namespace {
bool formats_contains(const QString& fmt) {
    static const QStringList formats = {"mkv", "mp4", "ts",   "webm",
                                        "ogg", "mp3", "flac", "wav"};
    return formats.contains(fmt);
}
}  // namespace

void MainWindow::on_recording_toggle_restart_after_rotate() {
    // Continue with the next part of a split recording (Linux _rotate_recording).
    if (current_source_.isEmpty()) return;
    QString dir = record_dir_ ? record_dir_->text().trimmed() : output_dir_;
    if (dir.isEmpty()) dir = output_dir_;
    const QString suffix = QStringLiteral(".") +
                           (formats_contains(app_settings_.player.record_format)
                                ? app_settings_.player.record_format
                                : QStringLiteral("mkv"));
    const QString destination =
        dir + QStringLiteral("/") + record_stem_ +
        QStringLiteral("-part%1").arg(record_part_, 3, 10, QChar('0')) + suffix;
    QString err;
    if (!recorder_->start(current_source_, destination, &err)) {
        status(QStringLiteral("Recording rotate failed: %1").arg(err));
        return;
    }
    if (record_split_minutes_ > 0 && record_timer_)
        record_timer_->start(record_split_minutes_ * 60 * 1000);
}

void MainWindow::update_stage() {
    if (!stage_stack_) return;
    if (!stage_media_active_) { stage_stack_->setCurrentIndex(2); return; }
    // Reference parity: AUDIO-ONLY media always shows the visualizer stage;
    // the viz button additionally overlays the visualizer for video.
    const bool has_video_surface = surface_ && surface_->is_video_active();
    const bool viz_pref = viz_btn_ && viz_btn_->isChecked();
    const bool viz = viz_pref || !has_video_surface;
    stage_stack_->setCurrentIndex(viz ? 1 : 0);
}

void MainWindow::apply_viz_mode() {
    const bool on = app_settings_.player.visualizer != "off";
    // While media is open the stage choice follows the media type (audio ->
    // visualizer), so only sync the button when nothing is playing.
    if (viz_btn_ && !stage_media_active_) viz_btn_->setChecked(on);
    update_stage();
    if (visualizer_) {
        static_cast<VisualizerWidget*>(visualizer_)->set_mode(app_settings_.player.visualizer);
        static_cast<VisualizerWidget*>(visualizer_)->set_active(on);
    }
}

void MainWindow::on_visualizer_toggle() {
    update_stage();
    if (visualizer_)
        static_cast<VisualizerWidget*>(visualizer_)->set_active(
            viz_btn_ && viz_btn_->isChecked());
}

// Linux parity (main_window.py _cover_for / set_cover): extract embedded cover
// art for local media into a temp PNG (background thread), then load it on the
// UI thread and hand it to the visualizer. Clears the previous cover.
void MainWindow::load_cover_art(const QString& source) {
    if (cover_pixmap_) { delete cover_pixmap_; cover_pixmap_ = nullptr; }
    if (visualizer_) static_cast<VisualizerWidget*>(visualizer_)->set_cover(nullptr);
    if (source.isEmpty() || is_network_like(source)) return;
    if (!QFileInfo::exists(source)) return;
    const QString key = source;
    std::thread([this, key] {
        const QString tmp = QDir::tempPath() + QStringLiteral("/mpcasu_cover_") +
                            QString::number(QCoreApplication::applicationPid()) + QStringLiteral(".png");
        bool ok = casu::media::extract_cover(key.toStdString(), tmp.toStdString());
        if (!ok) {
            // Fallback: cached PPM thumbnail (reference thumbnail_for path)
            // so the cover shows even when ffmpeg cannot extract artwork.
            std::error_code ec;
            const QString cache_dir =
                QDir::homePath() + QStringLiteral("/.cache/mpcasu/thumbnails");
            const std::string ppm =
                casu::media::thumbnail_for(key.toStdString(),
                                           cache_dir.toStdString());
            ok = !ppm.empty();
            QFile ppm_file(QString::fromStdString(ppm));
            if (ok && ppm_file.open(QIODevice::ReadOnly)) {
                const QByteArray ppm_bytes = ppm_file.readAll();
                ppm_file.close();
                QFile png(tmp);
                if (png.open(QIODevice::WriteOnly)) {
                    // Wrap the raw PPM bytes in a PNG-less QPixmap load via
                    // QImageReader? QPixmap loads PPM natively — copy file.
                    png.close();
                    QFile::remove(tmp);
                    QFile::copy(QString::fromStdString(ppm), tmp);
                }
            }
        }
        QMetaObject::invokeMethod(this, [this, key, tmp, ok] {
            if (!ok || key != current_source_) return;
            QPixmap pm;
            if (!pm.load(tmp)) return;
            if (cover_pixmap_) { delete cover_pixmap_; cover_pixmap_ = nullptr; }
            cover_pixmap_ = new QPixmap(pm);
            if (visualizer_) static_cast<VisualizerWidget*>(visualizer_)->set_cover(cover_pixmap_);
            QFile::remove(tmp);
        }, Qt::QueuedConnection);
    }).detach();
}

void MainWindow::on_youtube_play() {
    QString input = youtube_url_->text().trimmed();
    if (input.isEmpty()) { status(QStringLiteral("Enter a URL, file path or search term.")); return; }
    const QString lower = input.toLower();
    const bool is_url = lower.startsWith(QStringLiteral("http://")) ||
                        lower.startsWith(QStringLiteral("https://")) ||
                        lower.startsWith(QStringLiteral("rtsp://")) ||
                        lower.startsWith(QStringLiteral("rtmp://")) ||
                        lower.startsWith(QStringLiteral("udp://")) ||
                        lower.startsWith(QStringLiteral("rtp://")) ||
                        lower.startsWith(QStringLiteral("ftp://")) ||
                        lower.startsWith(QStringLiteral("smb://"));
    // A free-form YouTube field: several videos and/or complete playlists
    // (comma/line separated) expand straight into the queue as individual
    // entries (Linux parity), so shuffle/repeat act per-video.
    const QStringList tokens = input.split(QRegularExpression(QStringLiteral("[\r\n,;]+")),
                                           Qt::SkipEmptyParts);
    QStringList multiTokens;
    for (const QString& t : tokens) {
        const QString s = t.trimmed();
        if (!s.isEmpty()) multiTokens.append(s);
    }
    bool single_youtube_nolist = true;
    if (multiTokens.size() == 1) {
        const QString only = multiTokens.first();
        if (!casu::network::is_youtube_url(only.toStdString()) ||
            only.contains(QStringLiteral("list="))) {
            single_youtube_nolist = false;
        }
    } else if (!multiTokens.isEmpty()) {
        single_youtube_nolist = false;
    }
    if (!single_youtube_nolist && !multiTokens.isEmpty()) {
        youtube_status_->setText(QStringLiteral("Expanding YouTube into the queue…"));
        const QStringList frame = multiTokens;
        std::thread([this, frame] {
            try {
                QStringList urls;
                for (const QString& token : frame) {
                    if (!casu::network::is_youtube_url(token.toStdString())) {
                        // Non-YouTube tokens are ignored for the YouTube field.
                        continue;
                    }
                    if (token.contains(QStringLiteral("list="))) {
                        const auto found = casu::network::YtDlp().expand_playlist(
                            token.toStdString(), 100, 60000);
                        for (const auto& r : found)
                            urls.append(QString::fromStdString(r.url));
                    } else {
                        urls.append(token);
                    }
                }
                QMetaObject::invokeMethod(this, [this, urls] {
                    if (urls.isEmpty()) {
                        youtube_status_->setText(
                            QStringLiteral("No YouTube videos recognised."));
                        return;
                    }
                    add_files(urls);
                    youtube_status_->setText(
                        QStringLiteral("Queued %1 videos").arg(urls.size()));
                    status(QStringLiteral("Added %1 videos to the queue").arg(urls.size()));
                }, Qt::QueuedConnection);
            } catch (const std::exception& e) {
                QMetaObject::invokeMethod(this, [this, e] {
                    youtube_status_->setText(QStringLiteral("YouTube expand failed: %1")
                                                 .arg(QString::fromStdString(e.what())));
                }, Qt::QueuedConnection);
            }
        }).detach();
        return;
    }
    // Linux parity (_expand_spotify_url): Spotify URLs expand via spotDL
    // metadata into playable result rows.
    if (casu::network::is_spotify_url(input.toStdString())) {
        youtube_status_->setText(QStringLiteral("Expanding Spotify playlist via spotDL…"));
        std::thread([this, input] {
            try {
                const auto found =
                    casu::network::expand_spotify(input.toStdString(), 100, 90000);
                QMetaObject::invokeMethod(this, [this, found] {
                    yt_results_->clear();
                    for (const auto& r : found) {
                        const QString label = QStringLiteral("%1 — %2")
                                                  .arg(QString::fromStdString(r.title),
                                                       QString::fromStdString(r.artist));
                        auto* item = new QListWidgetItem(label, yt_results_);
                        item->setData(Qt::UserRole,
                                      QString::fromStdString(r.url));
                        item->setData(Qt::UserRole + 1,
                                      QString::fromStdString(r.title));
                        yt_results_->addItem(item);
                    }
                    youtube_status_->setText(
                        QStringLiteral("Spotify expanded: %1 entries").arg(found.size()));
                    status(QStringLiteral("Added %1 Spotify entries").arg(found.size()));
                }, Qt::QueuedConnection);
            } catch (const std::exception& e) {
                QMetaObject::invokeMethod(this, [this, e] {
                    youtube_status_->setText(QStringLiteral("Spotify expand failed: %1")
                                                 .arg(QString::fromStdString(e.what())));
                }, Qt::QueuedConnection);
            }
        }).detach();
        return;
    }
    if (is_url || QFileInfo::exists(input)) {
        if (casu::network::is_youtube_url(input.toStdString())) {
            // Linux parity: typed YouTube links enter the queue + resolve
            // their title (no raw-URL "Now Playing").
            queue_and_play(input, QString());
            return;
        }
        open_network_source(input, input);
        return;
    }
    // Anything else is a search term (yt-dlp ytsearch).
    if (!app_settings_.player.ytdlp_consent) {
        youtube_status_->setText(
            QStringLiteral("YouTube search needs the yt-dlp consent above."));
        return;
    }
    if (yt_searching_) return;
    yt_searching_ = true;
    youtube_status_->setText(QStringLiteral("Searching YouTube via yt-dlp…"));
    std::thread([this, input] {
        try {
            const auto found = casu::network::YtDlp().search(input.toStdString(), 12, 45000);
            QMetaObject::invokeMethod(this, [this, found] {
                yt_searching_ = false;
                yt_results_->clear();
                for (const auto& r : found) {
                    const QString title = QString::fromStdString(r.title);
                    const QString uploader = QString::fromStdString(r.uploader);
                    const QString dur = r.has_duration && r.duration >= 0
                                            ? QStringLiteral("%1:%2")
                                                  .arg(static_cast<int>(r.duration) / 60)
                                                  .arg(static_cast<int>(r.duration) % 60, 2, 10,
                                                       QLatin1Char('0'))
                                            : QStringLiteral("live");
                    QString displayTitle = title;
                    if (displayTitle.length() > 70) displayTitle = displayTitle.left(67) + QStringLiteral("…");
                    auto* item = new QListWidgetItem(
                        QStringLiteral("  %1\n  %2  ·  %3  ▶")
                            .arg(displayTitle, uploader.isEmpty() ? QStringLiteral("unknown") : uploader, dur));
                    item->setData(Qt::UserRole, QString::fromStdString(r.url));
                    item->setData(Qt::UserRole + 1, title);
                    item->setSizeHint(QSize(0, 76));
                    yt_results_->addItem(item);
                }
                // Load thumbnails for YouTube results in background
                struct ThumbJob { int row; std::string url; };
                QVector<ThumbJob> thumbs;
                for (int i = 0; i < found.size(); ++i) {
                    if (!found[i].thumbnail.empty())
                        thumbs.append({i, found[i].thumbnail});
                }
                if (!thumbs.isEmpty()) {
                    auto* list = yt_results_;
                    std::thread([list, thumbs] {
                        for (const auto& job : thumbs) {
                            try {
                                QNetworkAccessManager mgr;
                                QNetworkRequest req(QUrl(QString::fromStdString(job.url)));
                                req.setRawHeader("User-Agent", "MPCASU/1.0");
                                auto* reply = mgr.get(req);
                                QEventLoop loop;
                                QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
                                QTimer::singleShot(8000, &loop, &QEventLoop::quit);
                                loop.exec();
                                QByteArray data = reply->readAll();
                                reply->deleteLater();
                                if (data.isEmpty()) continue;
                                QPixmap px;
                                if (px.loadFromData(data)) {
                                    QIcon icon(px.scaled(88, 50, Qt::KeepAspectRatioByExpanding,
                                                        Qt::SmoothTransformation));
                                    QMetaObject::invokeMethod(list, [list, row = job.row, icon] {
                                        if (auto* it = list->item(row))
                                            it->setIcon(icon);
                                    }, Qt::QueuedConnection);
                                }
                            } catch (...) { continue; }
                        }
                    }).detach();
                }
                youtube_status_->setText(
                    found.empty() ? QStringLiteral("No results.")
                                  : QStringLiteral("Double-click a result to play it."));
            }, Qt::QueuedConnection);
        } catch (const std::exception& e) {
            QMetaObject::invokeMethod(this, [this, e] {
                yt_searching_ = false;
                youtube_status_->setText(QStringLiteral("Search failed: %1")
                                             .arg(QString::fromStdString(e.what())));
            }, Qt::QueuedConnection);
        }
    }).detach();
}

void MainWindow::set_diagnostics(const QString& support, const QString& integrity,
                                 const QString& segmented, const QString& guide) {
    if (diag_labels_.isEmpty()) return;
    if (!support.isNull() && diag_labels_.contains(QStringLiteral("CASU SUPPORT")))
        diag_labels_[QStringLiteral("CASU SUPPORT")]->setText(support);
    if (!integrity.isNull() && diag_labels_.contains(QStringLiteral("INTEGRITY MODE")))
        diag_labels_[QStringLiteral("INTEGRITY MODE")]->setText(integrity);
    if (!segmented.isNull() && diag_labels_.contains(QStringLiteral("SEGMENTED PLAYBACK")))
        diag_labels_[QStringLiteral("SEGMENTED PLAYBACK")]->setText(segmented);
    if (!guide.isNull() && diag_labels_.contains(QStringLiteral("LIVE GUIDE")))
        diag_labels_[QStringLiteral("LIVE GUIDE")]->setText(guide);
}

void MainWindow::update_diagnostics_guide() {
    // Linux parity: first catalog channel's now/next schedule preview.
    if (epg_.channels.isEmpty()) {
        set_diagnostics(QString(), QString(), QString(), QStringLiteral("no EPG loaded"));
        return;
    }
    const mpcasu::StreamChannel& channel = epg_.channels.first();
    const QString key = channel.epg_id.isEmpty() ? channel.name : channel.epg_id;
    const qint64 now_ms = QDateTime::currentMSecsSinceEpoch();
    if (!epg_guide_.programmes.isEmpty()) {
        const QVector<mpcasu::Programme> picks =
            epg_guide_.schedule(key, now_ms, 3);
        if (!picks.isEmpty()) {
            QStringList parts;
            for (const mpcasu::Programme& p : picks)
                parts << QStringLiteral("%1 · %2")
                             .arg(QDateTime::fromMSecsSinceEpoch(p.start_ms).toString("HH:mm"), p.title);
            set_diagnostics(QString(), QString(), QString(), parts.join(QStringLiteral("  |  ")));
            return;
        }
    }
    set_diagnostics(QString(), QString(), QString(),
                    channel.name.isEmpty() ? QStringLiteral("EPG loaded") : channel.name);
}

QString MainWindow::epg_now_next_text(const QString& source) {
    // Linux parity (main_window.py _epg_now_next): channel · now: title line.
    if (epg_.channels.isEmpty())
        return QStringLiteral("no EPG loaded");
    const qint64 now_ms = QDateTime::currentMSecsSinceEpoch();
    for (const mpcasu::StreamChannel& c : epg_.channels) {
        if (c.url != source) continue;
        if (!epg_guide_.programmes.isEmpty()) {
            const QString key = c.epg_id.isEmpty() ? c.name : c.epg_id;
            const mpcasu::Programme* active = nullptr;
            const mpcasu::Programme* upcoming = nullptr;
            epg_guide_.now_next(key, now_ms, &active, &upcoming);
            if (active)
                return QStringLiteral("%1 · now: %2").arg(c.name, active->title);
        }
        return c.name;
    }
    return QStringLiteral("EPG loaded");
}

QString MainWindow::queue_label_for(const QString& path) {
    // Linux parity (main_window.py _label_for): display name for a queue row.
    const QString text = path;
    if (text.startsWith(QStringLiteral("http://")) ||
        text.startsWith(QStringLiteral("https://")) ||
        text.startsWith(QStringLiteral("rtsp://")) ||
        text.startsWith(QStringLiteral("rtmp://")) ||
        text.startsWith(QStringLiteral("udp://")) ||
        text.startsWith(QStringLiteral("rtp://")) ||
        text.startsWith(QStringLiteral("spotify:")) ||
        text.startsWith(QStringLiteral("ytdl:")))
        return text;
    auto it = tag_titles_.constFind(text);
    if (it == tag_titles_.constEnd()) {
        // "title — artist" from media tags, else an empty string.
        QString value;
        try {
            const auto tags = casu::media::metadata_for(text.toStdString());
            const QString title = QString::fromStdString(tags.at("title")).trimmed();
            const QString artist = QString::fromStdString(tags.at("artist")).trimmed();
            if (!title.isEmpty())
                value = artist.isEmpty() ? title : QStringLiteral("%1 — %2").arg(title, artist);
        } catch (const std::out_of_range&) {
            // no title/artist key
        }
        it = tag_titles_.insert(text, value);
    }
    if (!it.value().isEmpty()) return it.value();
    return QFileInfo(text).fileName();
}

// ------------------------------------------------------------------ window events

void MainWindow::dragEnterEvent(QDragEnterEvent* event) {
    if (event->mimeData()->hasUrls()) {
        if (drop_overlay_ && surface_) {
            drop_overlay_->setGeometry(surface_->geometry());
            drop_overlay_->show();
            drop_overlay_->raise();
        }
        event->acceptProposedAction();
    }
}

void MainWindow::dragMoveEvent(QDragMoveEvent* event) {
    if (event->mimeData()->hasUrls()) event->acceptProposedAction();
}

void MainWindow::dragLeaveEvent(QDragLeaveEvent* event) {
    if (drop_overlay_) drop_overlay_->hide();
    QMainWindow::dragLeaveEvent(event);
}

void MainWindow::dropEvent(QDropEvent* event) {
    if (drop_overlay_) drop_overlay_->hide();
    QStringList paths;
    for (const QUrl& url : event->mimeData()->urls()) {
        const QString local = url.toLocalFile();
        paths << (local.isEmpty() ? url.toString() : local);
    }
    paths.removeAll(QString());
    if (paths.isEmpty()) return;
    add_files(paths);
    event->acceptProposedAction();
}

void MainWindow::keyPressEvent(QKeyEvent* event) {
    switch (event->key()) {
        case Qt::Key_Space:
            toggle_playback();
            event->accept();
            return;
        case Qt::Key_F:
            toggle_fullscreen();
            event->accept();
            return;
        case Qt::Key_Right:
            // Linux parity: arrow keys seek ±10 seconds.
            seek_to(controller_->position() + 10.0);
            event->accept();
            return;
        case Qt::Key_Left:
            seek_to(controller_->position() - 10.0);
            event->accept();
            return;
        case Qt::Key_Up:
            change_volume(5);
            event->accept();
            return;
        case Qt::Key_Down:
            change_volume(-5);
            event->accept();
            return;
        case Qt::Key_M:
            toggle_mute();
            event->accept();
            return;
        case Qt::Key_S:
            stop_playback();
            event->accept();
            return;
        case Qt::Key_Escape:
            if (isFullScreen()) {
                showNormal();          // actually LEAVE fullscreen
                exit_fullscreen_ui();
                event->accept();
                return;
            }
            // Escape returns to the player page from any sub-view (Linux).
            if (pages_ && pages_->currentIndex() != 0) {
                navigate(QStringLiteral("NOW PLAYING"));
                event->accept();
                return;
            }
            break;
        default:
            break;
    }
    if (event->modifiers() & Qt::ControlModifier) {
        switch (event->key()) {
            case Qt::Key_O:
                open_files_dialog();
                event->accept();
                return;
            case Qt::Key_L:
                // Reference parity: Ctrl+L shows the Sources view.
                navigate(QStringLiteral("YOUTUBE"));
                event->accept();
                return;
            case Qt::Key_I:
                show_media_info();
                event->accept();
                return;
            default:
                break;
        }
    }
    QMainWindow::keyPressEvent(event);
}

void MainWindow::closeEvent(QCloseEvent* event) {
    // Session restore: persist queue + resume position + geometry BEFORE the
    // backend is torn down (position must be read while still valid).
    persist_media_preferences();  // Linux parity: save per-media prefs on exit
    mpcasu::SessionState session_state;
    for (const PlaylistItem& item : playlist_.items())
        session_state.playlist.append(item.path);
    session_state.current = resume_source_;
    session_state.position =
        controller_ ? std::max(0.0, controller_->position()) : 0.0;
    session_state.volume = volume_;
    session_state.muted = muted_;
    session_state.rate = rate_;
    session_state.width = width();
    session_state.height = height();
    session_state.x = x();
    session_state.y = y();
    session_state.snapshot_dir = app_settings_.snapshot_dir;
    session_state.library_dir = app_settings_.library_dir;
    session_state.last_playlist = app_settings_.last_playlist;
    settings_->save_session(session_state);
    stop_playback();
    if (recorder_) recorder_->kill();
    if (poll_timer_) poll_timer_->stop();
    event->accept();
}

}  // namespace mpcasu

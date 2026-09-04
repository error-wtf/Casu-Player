// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// MPCASU.exe entry point (port of mpcasu_qt/app.py). QApplication +
// MainWindow, single-instance via QLockFile, clean shutdown, and the
// Wine-test flags --smoke and --proxy (used by the smoke test harness).
#include "casu/codec/tools.hpp"
#include "main_window.hpp"
#include "theme.hpp"

#include <QApplication>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLockFile>
#include <QMessageBox>
#include <QProcessEnvironment>
#include <QStringList>
#include <QTimer>

#include <cstdio>
#include <cstdlib>
#include <string>

namespace {

QString config_dir_for_log() {
    const QString dir = mpcasu::app_config_dir();
    QDir().mkpath(dir);
    return dir;
}

void log_startup(const QString& message) {
    QFile f(config_dir_for_log() + "/startup.log");
    if (!f.open(QIODevice::Append | QIODevice::Text)) return;
    f.write(QDateTime::currentDateTime().toString("yyyy-MM-dd HH:mm:ss.zzz").toUtf8());
    f.write(" ");
    f.write(message.toUtf8());
    f.write("\n");
}

}  // namespace

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("MPCASU"));
    app.setOrganizationName(QStringLiteral("Lino-Codec"));
    app.setAttribute(Qt::AA_DontCreateNativeWidgetSiblings, true);

    // Point libVLC at the bundled plugin modules (relative to the exe) when
    // the environment does not configure them (REQ-PLAYER plugin discovery).
    // Packaged layout: plugins in vlc/plugins; dev layout: plugins/ next to
    // the exe.
    const QString exe_dir = QApplication::applicationDirPath();
    if (qEnvironmentVariableIsEmpty("VLC_PLUGIN_PATH")) {
        const QStringList candidates = {exe_dir + "/vlc/plugins", exe_dir + "/plugins"};
        for (const QString& candidate : candidates) {
            if (QDir(candidate).exists()) {
                qputenv("VLC_PLUGIN_PATH", QDir::toNativeSeparators(candidate).toUtf8());
                break;
            }
        }
    }

    // Point the bundled helper wrappers (ffmpeg/ffprobe/yt-dlp) at the tools
    // folder beside the exe, so feature pages work from any working directory.
    auto set_tool_env = [&exe_dir](const char* name, const QString& exe) {
        if (qEnvironmentVariableIsEmpty(name)) {
            const QString candidate = exe_dir + "/tools/" + exe;
            if (QFileInfo::exists(candidate))
                qputenv(name, QDir::toNativeSeparators(candidate).toUtf8());
        }
    };
    set_tool_env("CASU_FFMPEG", "ffmpeg.exe");
    set_tool_env("CASU_FFPROBE", "ffprobe.exe");
    set_tool_env("CASU_YTDLP", "yt-dlp.exe");

    // --- CLI flags (Wine test harness) ---
    bool smoke = false;
    int shot_delay_ms = 1500;
    bool proxy = false;
    bool play_test = false;
    QString vout;
    QString aout;
    QString screenshot;
    QString page;
    QStringList media;
    for (int i = 1; i < argc; ++i) {
        QString arg = QString::fromLocal8Bit(argv[i]);
        if (arg == "--smoke") smoke = true;
        else if (arg == "--proxy") proxy = true;
        else if (arg == "--play-test") play_test = true;
        else if (arg == "--screenshot" && i + 1 < argc) screenshot = QString::fromLocal8Bit(argv[++i]);
        else if (arg == "--shot-delay" && i + 1 < argc) shot_delay_ms = QString::fromLocal8Bit(argv[++i]).toInt();
        else if (arg == "--page" && i + 1 < argc) page = QString::fromLocal8Bit(argv[++i]);
        else if (arg == "--vout" && i + 1 < argc) vout = QString::fromLocal8Bit(argv[++i]);
        else if (arg == "--aout" && i + 1 < argc) aout = QString::fromLocal8Bit(argv[++i]);
        else if (!arg.startsWith("--")) media << arg;    }

    // --- single instance (QLockFile in the app config dir) ---
    const QString lock_path = mpcasu::app_config_dir() + "/mpcasu.lock";
    // A leftover lock from an elevated run must not block normal starts;
    // QLockFile detects live owners by PID, so removing a STALE file first
    // is safe.
    QFile::remove(lock_path);
    QLockFile lock(lock_path);
    lock.setStaleLockTime(30000);
    if (!lock.tryLock(0)) {
        QMessageBox::information(nullptr, QStringLiteral("MPCASU"),
                                 QStringLiteral("An MPCASU instance is already running."));
        return 0;
    }

    mpcasu::MainWindow window(media, proxy, vout, aout, play_test);
    window.setMinimumSize(960, 600);  // responsiveness floor: readable fonts
    QSize start_size = window.size();
    if (start_size.width() < 1180) start_size.setWidth(1180);
    if (start_size.height() < 720) start_size.setHeight(720);
    window.resize(start_size);
    window.show();
    if (!page.isEmpty()) window.navigate_to(page);
    log_startup(QStringLiteral("started pid=%1 exe=%2").arg(QCoreApplication::applicationPid())
                    .arg(QDir::toNativeSeparators(QCoreApplication::applicationFilePath())));

    if (smoke) {
        // Automated GUI smoke run: the window must appear and the app must
        // exit cleanly (no missing DLL, no crash). Exit code 0 signals
        // success; a marker is printed once the window is actually visible.
        QTimer* visible_timer = new QTimer(&window);
        visible_timer->setInterval(250);
        QObject::connect(visible_timer, &QTimer::timeout, &window, [&window, visible_timer] {
            if (window.isVisible()) {
                std::printf("MPCASU_WINDOW_VISIBLE=1\n");
                std::fflush(stdout);
                visible_timer->stop();
            }
        });
        visible_timer->start();
        const int run_ms = 5000;
        QTimer::singleShot(run_ms, &window, [&app, &window] {
            std::printf("MPCASU_WINDOW_VISIBLE=%d\n", window.isVisible() ? 1 : 0);
            std::fflush(stdout);
            std::printf("MPCASU_SMOKE_CLEAN_EXIT\n");
            std::fflush(stdout);
            log_startup(QStringLiteral("smoke: exiting cleanly"));
            std::printf("MPCASU_CLOSING_WINDOW\n");
            std::fflush(stdout);
            window.close();  // teardown backend/proxy so libVLC threads join
            std::printf("MPCASU_WINDOW_CLOSED\n");
            std::fflush(stdout);
            app.exit(0);
        });
    }

    if (!screenshot.isEmpty()) {
        // Visual regression helper: render the real window (stylesheet, layout)
        // to a PNG for offline inspection (used by the parity workflow).
        // --shot-delay extends the settle window (e.g. playback start for
        // visualizer/cover verification).
        QTimer::singleShot(shot_delay_ms, &window, [&app, &window, screenshot] {
            const QPixmap pix = window.grab();
            const bool ok = pix.save(screenshot, "PNG");
            std::printf("MPCASU_SCREENSHOT=%d %s\n", ok ? 1 : 0,
                        qPrintable(QDir::toNativeSeparators(screenshot)));
            std::fflush(stdout);
            window.close();
            app.exit(ok ? 0 : 1);
        });
    }

    if (play_test) {
        // Player-kernel verification: a request alone is not playback.  After
        // the settle window the backend must report PLAYING and its clock must
        // have advanced beyond zero.
        QTimer::singleShot(4000, &window, [&app, &window] {
            std::printf("MPCASU_PLAY_STATE=%s pos=%.1fs dur=%.1fs backend=%d\n",
                        window.playback_state_name(),
                        window.playback_position(),
                        window.playback_duration(),
                        window.has_playback_backend() ? 1 : 0);
            std::fflush(stdout);
            const QString st = QString::fromLatin1(window.playback_state_name());
            const bool ok = window.has_playback_backend() && st == "PLAYING" &&
                            window.playback_position() > 0.05;
            log_startup(QStringLiteral("play-test state=%1 ok=%2").arg(st).arg(ok));
            window.close();  // teardown backend so libVLC threads join
            app.exit(ok ? 0 : 1);
        });
    }

    const int rc = app.exec();
    log_startup(QStringLiteral("exit rc=%1").arg(rc));
    return rc;
}

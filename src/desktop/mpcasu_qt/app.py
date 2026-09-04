# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""MPCASU Qt entry point.

Usage:
    python3 -m mpcasu_qt.app [media_file ...]
"""
from __future__ import annotations

import getpass
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure the project root is on sys.path so casu.* imports resolve.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Frozen macOS releases carry their helper executables inside the app. Keep
# source/development launches unchanged while making ffmpeg, ffprobe and
# yt-dlp available without Homebrew or another machine-level installation.
if getattr(sys, "frozen", False) and sys.platform == "darwin":
    helpers = Path(sys.executable).resolve().parent.parent / "Helpers"
    if helpers.is_dir():
        os.environ["PATH"] = str(helpers) + os.pathsep + os.environ.get("PATH", "")

from mpcasu_qt.main_window import MainWindow
from PySide6.QtCore import QLockFile, QStandardPaths, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow

try:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    _HAVE_NETWORK = True
except ImportError:  # single-instance IPC is optional
    QLocalServer = QLocalSocket = None
    _HAVE_NETWORK = False


def _instance_id() -> str:
    if hasattr(os, "getuid"):
        return str(os.getuid())
    return getpass.getuser().replace("/", "_")


def _config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    config = base / "mpcasu"
    config.mkdir(parents=True, exist_ok=True)
    return config


def _log(message: str) -> None:
    try:
        entry = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {os.getpid()} {message}\n"
        with (_config_dir() / "startup.log").open("a", encoding="utf-8") as handle:
            handle.write(entry)
    except OSError:
        pass


def _ensure_runtime_dir() -> None:
    """Ensure a session runtime directory so audio/DBus services work.

    libVLC's PulseAudio module and Qt session services discover their socket
    via XDG_RUNTIME_DIR. A launch from a terminal or launcher that drops that
    variable would silently lose audio; pin it to the user's real runtime
    directory so every launch behaves identically.
    """
    if os.environ.get("XDG_RUNTIME_DIR"):
        return
    if hasattr(os, "getuid"):
        candidate = f"/run/user/{os.getuid()}"
        if os.path.isdir(candidate):
            os.environ["XDG_RUNTIME_DIR"] = candidate


def _try_send(name: str, payload: bytes) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(100):
        socket.write(payload)
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True
    socket.abort()
    return False


def _send_to_primary(server_name: str, paths, legacy_name: str | None = None) -> bool:
    payload = "\n".join(str(p) for p in paths).encode("utf-8")

    # The primary listens on an abstract socket. Try it, its plain form, and
    # the legacy socket so a file passed to a second launch is always
    # forwarded to the surviving instance.
    candidates = [server_name]
    if server_name.startswith("@"):
        candidates.append(server_name[1:])
    if legacy_name:
        candidates.append(legacy_name)

    for _ in range(20):
        for candidate in candidates:
            if _try_send(candidate, payload):
                return True
        time.sleep(0.05)

    return False


def _proc_starttime(pid: int) -> int | None:
    """Return the process start time (field 22 of /proc/<pid>/stat)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            fields = handle.read().decode("utf-8", "replace").split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        return None


def _older_mpcasu_peer() -> int | None:
    """Return the PID of an older, live mpcasu_qt.app process, if any.

    A new launch defers to a process that is already running (older start
    time). A simultaneous twin never makes both processes exit: only the
    newer one sees an older peer, so exactly one process may ever open a
    window.
    """
    if not os.path.isdir("/proc"):
        return None
    mine = _proc_starttime(os.getpid())
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                cmd = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        if "mpcasu_qt.app" not in cmd:
            continue
        if mine is None:
            return pid
        other = _proc_starttime(pid)
        if other is not None and other < mine:
            return pid
    return None


def _log_peer_processes() -> None:
    if not os.path.isdir("/proc"):
        _log("PEER process scan unavailable on this platform")
        return
    peers = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                cmd = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        if "mpcasu_qt.app" in cmd and int(entry) != os.getpid():
            peers.append(f"{entry}:{cmd[:80]!r}")
    if peers:
        _log(f"PEER mpcasu processes: {'; '.join(peers)}")
    else:
        _log("PEER mpcasu processes: none")


def _log_window_inventory() -> None:
    """Log every top-level widget of this process (all visible windows)."""
    from PySide6.QtWidgets import QMainWindow as _MainWindow
    tops = QApplication.topLevelWidgets()
    inventory = []
    for widget in tops:
        inventory.append(
            f"{type(widget).__name__}:{widget.windowTitle()}:visible={widget.isVisible()}"
        )
    mains = [w for w in tops if isinstance(w, _MainWindow) and w.isVisible()]
    _log(f"TOPS={'; '.join(inventory)} | visible QMainWindows={len(mains)}")


def main() -> int:
    _ensure_runtime_dir()
    if hasattr(os, "getuid") and os.getuid() == 0:
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        if "--no-sandbox" not in flags:
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --no-sandbox").strip()
    app = QApplication(sys.argv)
    app.setApplicationName("MPCASU")
    app.setOrganizationName("Lino-Codec")
    # Kept exactly where the proven v5.0.0 release build has it (after
    # QApplication). Qt documents this attribute as pre-app-only, but moving
    # it earlier flips native-widget sibling policy app-wide and regressed
    # the embedded video surface on real desktops — the verified release
    # effectively does not apply it at all.
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings, True)

    paths = [Path(arg).expanduser() for arg in sys.argv[1:]]

    ident = _instance_id()
    lock_path = str(Path(tempfile.gettempdir()) / f"mpcasu-{ident}.lock")
    socket_path = str(Path(tempfile.gettempdir()) / f"mpcasu-{ident}.sock")
    legacy_name = "mpcasu-single-instance"

    # A running player is authoritative regardless of how it was started.
    # If an older mpcasu_qt.app process exists, hand any files to it and exit,
    # so a second native window can never appear — even against a stale or
    # pre-fix instance that does not know our lock.
    peer = _older_mpcasu_peer()
    if peer is not None:
        if _send_to_primary(socket_path, paths, legacy_name):
            _log(f"secondary: forwarded to running player {peer}, exit 0")
            return 0
        _log(f"secondary: running player {peer} exists but IPC unavailable")
        print(
            f"An existing MPCASU player is already running (pid {peer}). "
            "Close it and start again to open the player window.",
            file=sys.stderr)
        return 2

    # Ownership lock lives at a machine-wide, per-user path in /tmp. It is
    # independent of XDG_RUNTIME_DIR, HOME and XDG_CONFIG_HOME, so two launches
    # (desktop icon vs terminal vs different shells) can never resolve to
    # different lock files and therefore can never become two primary
    # instances / two windows.
    lock = QLockFile(lock_path)
    lock.setStaleLockTime(30_000)

    _log(f"start user={ident} home={Path.home()} xdg_runtime={os.environ.get('XDG_RUNTIME_DIR')}")
    _log(f"lock={lock_path}")

    if not lock.tryLock(0):
        if _HAVE_NETWORK and _send_to_primary(socket_path, paths, legacy_name):
            _log("secondary: forwarded to primary, exit 0")
            return 0
        _log("secondary: primary exists but IPC unavailable, exit 2")
        print("MPCASU primary instance exists but IPC is unavailable",
              file=sys.stderr)
        return 2

    _log("primary: lock acquired")

    # We own the lock, so removing any stale IPC socket file is safe here.
    # The socket lives at a deterministic path, which keeps every secondary
    # process able to reach the primary.
    server = None
    if _HAVE_NETWORK:
        QLocalServer.removeServer(socket_path)
        server = QLocalServer(app)
        if not server.listen(socket_path):
            _log(f"primary: IPC listen failed: {server.errorString()}")
            print(f"MPCASU IPC failed: {server.errorString()}",
                  file=sys.stderr)
            lock.unlock()
            return 2

    window = MainWindow(initial=paths)

    def _clamp_window_to_screen() -> None:
        """Keep the window fully on the visible screen.

        A maximized/restored window wider or higher than the current screen
        gets clipped at its top-left corner (the sidebar logo ends up cut
        off-screen).  Bound it to the available geometry so nothing is ever
        hidden outside the display.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        geometry = window.frameGeometry()
        if geometry.width() <= available.width() and \
                geometry.height() <= available.height() and \
                available.contains(geometry.topLeft()) and \
                available.contains(geometry.bottomRight()):
            return
        bounded = geometry
        bounded.setSize(geometry.size().boundedTo(available.size()))
        bounded.moveCenter(available.center())
        window.setGeometry(bounded)

    if server is not None:
        def handle_connection():
            client = server.nextPendingConnection()
            if client is None:
                return

            if not client.waitForReadyRead(1000):
                client.disconnectFromServer()
                return

            data = bytes(client.readAll())
            client.disconnectFromServer()

            targets = [
                line.strip()
                for line in data.decode("utf-8", "replace").splitlines()
                if line.strip()
            ]

            if targets:
                window.add_files(targets)

            window.showNormal()
            window.raise_()
            window.activateWindow()

        server.newConnection.connect(handle_connection)

    window.show()
    # The window manager may place a restored/maximized window partially
    # off-screen (top-left corner, e.g. 1970x1077 at -25,-24), clipping the
    # sidebar logo.  Re-check after the WM has settled and pull it fully onto
    # the visible screen so nothing is ever cut off.
    QTimer.singleShot(600, _clamp_window_to_screen)
    _log_peer_processes()
    _log_window_inventory()
    _check_main_windows()
    # Periodic watchdog: if a second visible QMainWindow ever appears inside
    # this process, log the full inventory so the root cause is never lost.
    guard_timer = QTimer(app)
    guard_timer.setInterval(2000)
    guard_timer.timeout.connect(_check_main_windows)
    guard_timer.start()

    # Release-packaging probe: exercise the final frozen Qt application and
    # its bundled decoder without adding a user-facing test mode.  The Apple
    # packaging job supplies a generated WAV and requires observable clock
    # advancement before accepting the DMG.
    playback_smoke = os.environ.get("MPCASU_PACKAGED_PLAYBACK_SMOKE")
    if playback_smoke:
        smoke_path = Path(playback_smoke)
        smoke_started_at = time.monotonic()

        def _probe_packaged_playback() -> None:
            if window.backend is not None and window.backend.position() > 0.05:
                print("MACOS_PACKAGED_PLAYBACK_SMOKE=PASS", flush=True)
                window.stop()
                app.exit(0)
                return
            if time.monotonic() - smoke_started_at > 20.0:
                state = window.backend.state().value if window.backend is not None else "NO_BACKEND"
                position = window.backend.position() if window.backend is not None else 0.0
                detail = getattr(window, "last_playback_error", "")
                print(f"MACOS_PACKAGED_PLAYBACK_SMOKE=FAIL state={state} position={position:.3f} error={detail}",
                      file=sys.stderr, flush=True)
                app.exit(3)
                return
            QTimer.singleShot(100, _probe_packaged_playback)

        QTimer.singleShot(250, lambda: window.play_selected(smoke_path))
        QTimer.singleShot(500, _probe_packaged_playback)
    result = app.exec()
    _log(f"app.exec returned {result}")

    if server is not None:
        server.close()
    lock.unlock()
    return result


def _check_main_windows() -> None:
    """Hard guard: exactly one visible QMainWindow may ever exist."""
    from PySide6.QtWidgets import QMainWindow as _MainWindow
    tops = QApplication.topLevelWidgets()
    mains = [
        widget for widget in tops
        if isinstance(widget, _MainWindow) and widget.isVisible()
    ]
    if len(mains) != 1:
        detail = ", ".join(
            f"{type(w).__name__}:{w.windowTitle()}"
            for w in tops
        )
        _log(f"GUARD: {len(mains)} visible QMainWindows; tops={detail}")
        if len(mains) > 1:
            raise RuntimeError(
                f"MPCASU BUG: {len(mains)} visible QMainWindows: "
                + ", ".join(f"{type(w).__name__}:{w.windowTitle()}"
                            for w in mains)
            )


if __name__ == "__main__":
    raise SystemExit(main())

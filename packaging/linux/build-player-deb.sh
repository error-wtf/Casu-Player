#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/mpcasu-player" \
  "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/256x256/apps"

cp -a "$ROOT/src/desktop/." "$STAGE/usr/share/mpcasu-player/"
cp "$ROOT/assets/mpcasu_player_icon.png" "$STAGE/usr/share/icons/hicolor/256x256/apps/mpcasu-player.png"

install -m 0755 "$ROOT/packaging/linux/mpcasu-player" "$STAGE/usr/bin/mpcasu-player"
install -m 0644 "$ROOT/packaging/linux/mpcasu-player.desktop" "$STAGE/usr/share/applications/mpcasu-player.desktop"

cat > "$STAGE/DEBIAN/control" <<'EOF'
Package: mpcasu-player
Version: 7.0.0
Section: video
Priority: optional
Architecture: all
Maintainer: Lino Casu <error-wtf@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-numpy, python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, python3-pyside6.qtnetwork, python3-pyside6.qtwebenginewidgets, libvlc5, vlc-plugin-base, vlc-plugin-video-output, libpulse0, libass9, ffmpeg, yt-dlp
Description: MPCASU Player for established and read-only experimental media
 Cross-platform Qt media player without CASU creation, conversion or CLI tools.
EOF

mkdir -p "$ROOT/dist"
find "$STAGE" -exec touch -h -d '@0' {} +
dpkg-deb --build --root-owner-group "$STAGE" "$ROOT/dist/mpcasu-player_7.0.0_all.deb" >/dev/null

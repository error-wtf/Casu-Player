#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${CASU_BUILD_ROOT:?Set CASU_BUILD_ROOT to the companion codec checkout with the Windows native libraries and runtimes}"
BUILD="${CASU_WINDOWS_BUILD_DIR:-$CASU_BUILD_ROOT/win-release/build-player-browser}"
cmake -S "$CASU_BUILD_ROOT/win-release" -B "$BUILD" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=cmake/mingw64-toolchain.cmake -DCMAKE_BUILD_TYPE=Release \
  -DCASU_PLAYER_SOURCE_DIR="$ROOT/src/windows"
cmake --build "$BUILD" --target casu_mpcasu -j2

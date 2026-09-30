#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${CASU_BUILD_ROOT:?Set CASU_BUILD_ROOT to the companion codec checkout with the Android native decoder toolchain}"
# v7.8: single source of truth — repo-root VERSION file (kept in sync with the
# codec checkout's android/app/build.gradle.kts versionName).
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
test -n "$VERSION"
# The shared APK is allowed only when ALL application inputs match this public tree.
diff -qr "$ROOT/src/android/java" "$CASU_BUILD_ROOT/android/app/src/main/java"
diff -qr "$ROOT/src/android/res" "$CASU_BUILD_ROOT/android/app/src/main/res"
cmp "$ROOT/src/android/AndroidManifest.xml" "$CASU_BUILD_ROOT/android/app/src/main/AndroidManifest.xml"
grep -q "versionName = \"$VERSION\"" "$CASU_BUILD_ROOT/android/app/build.gradle.kts"
(cd "$CASU_BUILD_ROOT/android" && ./gradlew :app:assembleRelease --console=plain)
mkdir -p "$ROOT/dist"
cp "$CASU_BUILD_ROOT/android/app/build/outputs/apk/release/app-release.apk" "$ROOT/dist/MPCASU-Player-Android-${VERSION}.apk"

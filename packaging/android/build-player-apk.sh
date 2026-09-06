#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${CASU_BUILD_ROOT:?Set CASU_BUILD_ROOT to the companion codec checkout with the Android native decoder toolchain}"
# The shared APK is allowed only when ALL application inputs match this public tree.
diff -qr "$ROOT/src/android/java" "$CASU_BUILD_ROOT/android/app/src/main/java"
diff -qr "$ROOT/src/android/res" "$CASU_BUILD_ROOT/android/app/src/main/res"
cmp "$ROOT/src/android/AndroidManifest.xml" "$CASU_BUILD_ROOT/android/app/src/main/AndroidManifest.xml"
grep -q 'versionName = "7.0.0"' "$CASU_BUILD_ROOT/android/app/build.gradle.kts"
(cd "$CASU_BUILD_ROOT/android" && ./gradlew :app:assembleRelease --console=plain)
mkdir -p "$ROOT/dist"
cp "$CASU_BUILD_ROOT/android/app/build/outputs/apk/release/app-release.apk" "$ROOT/dist/MPCASU-Player-Android-7.0.0.apk"

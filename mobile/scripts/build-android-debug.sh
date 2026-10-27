#!/usr/bin/env bash
# Android debug APK without Android Studio or sudo (lane APP0).
# JDK 21 and the Android SDK live outside the repository, by default under
# ~/app-build (see mobile/README.md for how they were downloaded).
#
#   mobile/scripts/build-android-debug.sh            # opens /app/ first
#   VOINEY_APP_START_PATH=device-check.html mobile/scripts/build-android-debug.sh
#   VOINEY_APP_SERVER_URL=https://pilot.example/ mobile/scripts/build-android-debug.sh
set -euo pipefail
APP_BUILD="${APP_BUILD:-$HOME/app-build}"
export JAVA_HOME="${JAVA_HOME:-$(ls -d "$APP_BUILD"/jdk-21* | head -1)}"
export ANDROID_HOME="${ANDROID_HOME:-$APP_BUILD/android-sdk}"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$APP_BUILD/gradle-home}"
export PATH="$JAVA_HOME/bin:$PATH"
cd "$(dirname "$0")/.."
[ -d node_modules ] || npm ci
node scripts/prepare-web.mjs
npx --no-install cap sync android
cd android
./gradlew assembleDebug --no-daemon --console=plain -Dorg.gradle.jvmargs=-Xmx1536m
ls -la app/build/outputs/apk/debug/

#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export JAVA_HOME="${JAVA_HOME:-/workspace/android/jdk}"
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/workspace/android/sdk}}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$PATH"
export JARVIS_RELEASE_STORE_DIR="${JARVIS_RELEASE_STORE_DIR:-/workspace/jarvis-keystore}"

if [[ ! -x "$JAVA_HOME/bin/java" ]]; then
  echo "JAVA_HOME does not point at a JDK: $JAVA_HOME" >&2
  exit 1
fi
if [[ ! -d "$ANDROID_HOME/platforms" ]]; then
  echo "ANDROID_HOME does not look like an Android SDK: $ANDROID_HOME" >&2
  exit 1
fi

STORE="$JARVIS_RELEASE_STORE_DIR/jarvis-release.keystore"
PASS="$JARVIS_RELEASE_STORE_DIR/keystore.password"
if [[ ! -f "$STORE" || ! -f "$PASS" ]]; then
  echo "Release keystore not found under $JARVIS_RELEASE_STORE_DIR" >&2
  echo "Create jarvis-release.keystore and keystore.password (mode 0600) there." >&2
  exit 1
fi

printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$ROOT/android-app/local.properties"

cd "$ROOT/android-app"
./gradlew assembleRelease --no-daemon "$@"
SRC="$ROOT/android-app/app/build/outputs/apk/release/app-release.apk"
if [[ ! -f "$SRC" ]]; then
  echo "Gradle did not produce $SRC" >&2
  exit 1
fi

if [[ -d /workspace/out ]]; then
  cp -f "$SRC" /workspace/out/jarvis-companion-v2.2.1.apk
  echo "Wrote /workspace/out/jarvis-companion-v2.2.1.apk"
fi
cp -f "$SRC" "$ROOT/jarvis-companion-v2.2.1.apk"
echo "Wrote $ROOT/jarvis-companion-v2.2.1.apk"

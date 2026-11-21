#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export JAVA_HOME="${JAVA_HOME:-/workspace/android/jdk}"
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/workspace/android/sdk}}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$PATH"

if [[ ! -x "$JAVA_HOME/bin/java" ]]; then
  echo "JAVA_HOME does not point at a JDK: $JAVA_HOME" >&2
  exit 1
fi
if [[ ! -d "$ANDROID_HOME/platforms" ]]; then
  echo "ANDROID_HOME does not look like an Android SDK: $ANDROID_HOME" >&2
  exit 1
fi

printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$ROOT/android-app/local.properties"

KS="$ROOT/android-app/keystore/jarvis-debug.keystore"
if [[ ! -f "$KS" ]]; then
  mkdir -p "$(dirname "$KS")"
  keytool -genkeypair -keystore "$KS" -alias jarvis -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass android -keypass android \
    -dname "CN=Jarvis Companion Debug, OU=Debug, O=Jarvis Companion, C=IN" -storetype JKS
fi

cd "$ROOT/android-app"
./gradlew assembleRelease --no-daemon "$@"
SRC="$ROOT/android-app/app/build/outputs/apk/release/app-release.apk"
if [[ ! -f "$SRC" ]]; then
  echo "Gradle did not produce $SRC" >&2
  exit 1
fi

# Copy to /workspace/out when that folder exists (this build environment),
# and always keep a copy next to the repo root for local use.
if [[ -d /workspace/out ]]; then
  cp -f "$SRC" /workspace/out/jarvis-companion-v2.2.apk
  echo "Wrote /workspace/out/jarvis-companion-v2.2.apk"
fi
cp -f "$SRC" "$ROOT/jarvis-companion-v2.2.apk"
echo "Wrote $ROOT/jarvis-companion-v2.2.apk"

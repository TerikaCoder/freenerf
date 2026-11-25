#!/usr/bin/env bash
# Installs the build toolchain used by this repository into $HOME (no root).
#   - Temurin JDK 17
#   - Rust (rustup) + Android targets
#   - Android SDK command-line tools, platform 35, build-tools 35.0.0, NDK r27
#   - Gradle 9.0.0 (only used to bootstrap ./gradlew; the wrapper is committed)
#
# Usage: bash scripts/setup-toolchain.sh
set -euo pipefail

TOOLS="${TOOLS_DIR:-$HOME/.freenerf-toolchain}"
SDK="$TOOLS/android-sdk"
mkdir -p "$TOOLS"

# ---------------------------------------------------------------- JDK 17
if [ ! -x "$TOOLS/jdk/bin/java" ]; then
  echo "==> installing Temurin JDK 17"
  curl -fsSL -o "$TOOLS/jdk.tar.gz" \
    "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"
  mkdir -p "$TOOLS/jdk"
  tar -xzf "$TOOLS/jdk.tar.gz" -C "$TOOLS/jdk" --strip-components=1
  rm -f "$TOOLS/jdk.tar.gz"
fi
export JAVA_HOME="$TOOLS/jdk"
export PATH="$JAVA_HOME/bin:$PATH"

# ---------------------------------------------------------------- Rust
if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
  echo "==> installing Rust"
  curl -fsSL https://sh.rustup.rs -o "$TOOLS/rustup-init.sh"
  sh "$TOOLS/rustup-init.sh" -y --profile minimal --default-toolchain stable
fi
export PATH="$HOME/.cargo/bin:$PATH"
rustup target add aarch64-linux-android x86_64-linux-android armv7-linux-androideabi \
  >/dev/null 2>&1 || true

# ---------------------------------------------------------------- Android SDK
if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
  echo "==> installing Android command-line tools"
  mkdir -p "$SDK/cmdline-tools"
  curl -fsSL -o "$TOOLS/cmdline-tools.zip" \
    "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
  unzip -q -o "$TOOLS/cmdline-tools.zip" -d "$TOOLS/cmdline-tools-unpacked"
  rm -rf "$SDK/cmdline-tools/latest"
  mv "$TOOLS/cmdline-tools-unpacked/cmdline-tools" "$SDK/cmdline-tools/latest"
  rm -rf "$TOOLS/cmdline-tools-unpacked" "$TOOLS/cmdline-tools.zip"
fi
export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"

yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null 2>&1 || true
"$SDK/cmdline-tools/latest/bin/sdkmanager" \
  "platform-tools" "platforms;android-35" "build-tools;35.0.0" "ndk;27.2.12479018" \
  > "$TOOLS/sdkmanager.log" 2>&1

echo "==> toolchain ready"
echo "JAVA_HOME=$JAVA_HOME"
echo "ANDROID_HOME=$ANDROID_HOME"

#!/usr/bin/env bash
# Cross-compiles the FreeNerf Rust runtime for Android and copies the resulting
# shared libraries into the Android app's jniLibs directory.
#
# Usage: scripts/build-rust-android.sh [debug|release] [abi...]
#   ABIs: arm64-v8a (default), x86_64, armeabi-v7a
#
# Environment:
#   ANDROID_NDK_HOME / ANDROID_NDK_ROOT / ANDROID_HOME  locate the NDK
#   MIN_SDK (default 24)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-release}"
shift || true
if [ $# -eq 0 ]; then
  ABIS=(arm64-v8a)
else
  ABIS=("$@")
fi

NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
if [ -z "$NDK" ]; then
  SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
  if [ -z "$SDK" ]; then
    echo "error: set ANDROID_NDK_HOME or ANDROID_HOME" >&2
    exit 1
  fi
  NDK="$(ls -d "$SDK"/ndk/* 2>/dev/null | sort -V | tail -1)"
fi
[ -d "$NDK" ] || { echo "error: NDK not found at $NDK" >&2; exit 1; }

case "$(uname -s)-$(uname -m)" in
  Linux-aarch64|Linux-arm64)
    HOST_TAG="linux-aarch64"
    ;;
  Linux-x86_64|Linux-amd64)
    HOST_TAG="linux-x86_64"
    ;;
  Darwin-arm64|Darwin-aarch64|Darwin-x86_64)
    HOST_TAG="darwin-x86_64"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    HOST_TAG="windows-x86_64"
    ;;
  *)
    echo "error: unsupported build host: $(uname -s)-$(uname -m)" >&2
    exit 1
    ;;
esac

TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin"

# NDK tools are bare binaries on Linux/macOS and .exe/.cmd wrappers on Windows.
tool_path() {
  local base="$1"
  for suffix in ".exe" ".cmd" ""; do
    if [ -f "$base$suffix" ]; then
      printf '%s' "$base$suffix"
      return 0
    fi
  done
  printf '%s' "$base"
}

if [ ! -f "$(tool_path "$TOOLCHAIN/clang")" ]; then
  echo "error: NDK host toolchain not found: $TOOLCHAIN" >&2
  echo "available toolchains:" >&2
  ls -1 "$NDK/toolchains/llvm/prebuilt" >&2 || true
  exit 1
fi

echo "==> host: $(uname -s)-$(uname -m)"
echo "==> NDK: $NDK"
echo "==> toolchain: $TOOLCHAIN"
MIN_SDK="${MIN_SDK:-24}"

export PATH="$HOME/.cargo/bin:$TOOLCHAIN:$PATH"

cargo_flags=()
if [ "$PROFILE" = "release" ]; then
  cargo_flags+=(--release)
fi

for abi in "${ABIS[@]}"; do
  case "$abi" in
    arm64-v8a)
      target="aarch64-linux-android"
      clang="$(tool_path "$TOOLCHAIN/aarch64-linux-android${MIN_SDK}-clang")"
      lib_dir="android/app/src/main/jniLibs/arm64-v8a"
      ;;
    x86_64)
      target="x86_64-linux-android"
      clang="$(tool_path "$TOOLCHAIN/x86_64-linux-android${MIN_SDK}-clang")"
      lib_dir="android/app/src/main/jniLibs/x86_64"
      ;;
    armeabi-v7a)
      target="armv7-linux-androideabi"
      clang="$(tool_path "$TOOLCHAIN/armv7a-linux-androideabi${MIN_SDK}-clang")"
      lib_dir="android/app/src/main/jniLibs/armeabi-v7a"
      ;;
    *)
      echo "error: unknown ABI $abi" >&2
      exit 1
      ;;
  esac

  env_suffix="${target//-/_}"
  echo "==> building $target ($PROFILE)"
  (
    cd "$ROOT/native"
    export AR_ANDROID="$(tool_path "$TOOLCHAIN/llvm-ar")"
    export "CC_${env_suffix}=$clang"
    export "AR_${env_suffix}=$AR_ANDROID"
    export "CARGO_TARGET_$(echo "$target" | tr 'a-z-' 'A-Z_')_LINKER=$clang"
    cargo build -p freenerf-android --target "$target" "${cargo_flags[@]}"
  )

  source_lib="$ROOT/native/target/$target/$PROFILE/libfreenerf_android.so"
  [ -f "$source_lib" ] || { echo "error: $source_lib was not produced" >&2; exit 1; }
  mkdir -p "$ROOT/$lib_dir"
  cp "$source_lib" "$ROOT/$lib_dir/libfreenerf.so"
  echo "==> $lib_dir/libfreenerf.so"
done

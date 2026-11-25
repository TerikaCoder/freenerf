# FreeNerf Android port — progress

Last updated: 2026-11-23

The Python CLI/TUI in `src/freenerf/` is unchanged. The Android app is a new
native client (`android/`) on top of a Rust runtime (`native/`) that implements
the same agent loop, providers, tools, permissions, sessions and tasks.

## Status

| Area | Status | Evidence |
| --- | --- | --- |
| Rust core types, config, events, cancellation | Done | `cargo test -p freenerf-core` (4) |
| Permission policy and shell safety | Done | `cargo test -p freenerf-permissions` (8) |
| Providers (OpenRouter, Gemini, FreeLLMAPI, OpenAI-compatible) + catalog | Done | `cargo test -p freenerf-providers` (13) |
| Workspace sandbox, backups, search, terminal, git allow-list | Done | `cargo test -p freenerf-workspace` (14) |
| Session store + legacy migration | Done | `cargo test -p freenerf-sessions` (6) |
| Task store and task manager | Done | `cargo test -p freenerf-tasks` (6) |
| Settings storage and Python config import | Done | `cargo test -p freenerf-storage` (5) |
| Tool registry, approval, timeout, 22 tools | Done | `cargo test -p freenerf-tools` (10) |
| Agent loop (tools, denials, cancel, retry, plain protocol, streaming, persistence) | Done | `cargo test -p freenerf-agent` (12) |
| JNI dispatcher (`nativeCall`, event sink, approval bridge) | Done | `cargo test -p freenerf-android` (4) |
| arm64 Android cross-compilation | Done | `libfreenerf.so` (ELF aarch64) in the APK |
| Compose UI: Chat, Sessions, Tasks, Settings, approvals | Done | Debug APK builds; UI unit tests pass |
| Keystore-backed API keys | Done | `SecretStore` AES-256-GCM; keys never written to Rust DBs |
| SAF workspace import/export, attachments | Done | `WorkspaceMirror` |
| Optional Termux bridge (submit-only) | Done | `TermuxBridge` + Settings action |
| Legacy config and database import | Done | Settings → Workspace; native `workspace.legacy_import` |
| Android unit tests | 8 pass | `./gradlew testDebugUnitTest` |
| Instrumented UI test | Compiles, not executed | No device/emulator in this environment |
| On-device run / real provider request | Not executed | No Android device and no API keys available here |

## Build verification

Environment used: Gradle 9.6.0 wrapper, AGP 9.5.2, Kotlin 2.2.10 (AGP built-in
Kotlin), compileSdk/targetSdk 36, minSdk 24, NDK 27.2.12479018, JDK 17.

- `scripts/build-rust-android.sh release arm64-v8a` → `libfreenerf.so`,
  `ELF 64-bit LSB shared object, ARM aarch64`.
- `./gradlew cargoBuild` runs that script; it is wired into `preBuild`, so the
  APK cannot assemble without the native library.
- `./gradlew assembleDebug` → `android/app/build/outputs/apk/debug/app-debug.apk`
  (32 MB) containing `lib/arm64-v8a/libfreenerf.so` and the exported JNI symbols
  `Java_dev_freenerf_app_NativeCore_nativeCall`, `...nativeSetBridge`,
  `...nativeClearBridge`.
- `./gradlew assembleDebugAndroidTest` builds the instrumented test APK.
- `./gradlew testDebugUnitTest` → 8 tests pass (Markdown parser, settings and
  legacy config serialization).
- `cargo test --workspace` → 82 tests pass.

Not run: instrumented tests (`connectedDebugAndroidTest`), any on-device launch,
live provider calls, Gradle lint. Python's test suite was not run because pytest
is not installed in this environment; the Python sources were not modified.

## Remaining limitations

- The default APK contains only `arm64-v8a`. Build other ABIs with
  `scripts/build-rust-android.sh release x86_64 armeabi-v7a` or
  `./gradlew assembleDebug -PrustAbis=arm64-v8a,x86_64`.
- The workspace is app-private. SAF folders must be imported and exported
  explicitly; they are not live mounts.
- Termux integration is submit-only: RUN_COMMAND cannot return stdout. The
  agent's shell tool uses `/system/bin/sh` in the app sandbox.
- The Rust shell tool is not an OS sandbox; it runs as the app's uid with the
  same policy checks as the Python version.
- Generated images and web tools require network access; Gemini still does not
  stream.
- UI was not visually inspected on a device; only compilation and JVM tests were
  verified.

## UI / feature refresh (2026-11-23)

Unverified in this environment (no Android SDK, NDK or Rust toolchain here):

- Material 3 Expressive theme with Theme Studio (mode, six accents, dynamic
  colour, AMOLED black) and animated tab transitions.
- Settings rebuilt as a tabbed centre: General, Appearance, Providers, Models,
  Permissions, Workspace, SSH, About. The Models tab loads the full catalog for
  each provider.
- Workspace picker over `/sdcard` with All files access, switching straight
  back to the chat after a choice.
- SSH replaces the Termux bridge; JSch client plus `ssh_*` agent tools through a
  new `RemoteToolExecutor` bridge in `freenerf-tools` and `freenerf-android`.
- Streaming tokens are coalesced (70 ms), auto-scroll only follows the tail when
  already at the bottom, and Markdown now renders full tables.
- Tasks tab removed; task progress stays inside the conversation.
- Base URLs start empty in Settings, with the provider default as placeholder.
- Web fetch ceiling raised from 1 MB to 4 MB in both the Python and Rust tools.

Python suite: 259 tests pass. Rust and Kotlin changes could not be compiled
here.

## UI and performance pass (2026-11-24)

Compiled on Windows with JDK 17, Android SDK 36 and the Gradle 9.6.0 wrapper.
The Rust runtime and NDK were then installed on that machine, so the native
library was rebuilt and a full debug APK assembled (see below).

- Streaming render path: `MarkdownText` now takes a `streaming` flag, parses
  only the completed prefix (splitting at the last blank line outside an open
  code fence) and draws the unfinished tail as plain text. Inline spans are
  memoised per string, so a growing answer no longer re-parses and re-lays out
  every paragraph on each token flush.
- Tables size each column from its longest cell (88–220 dp) and still draw every
  header and row inside a horizontal scroller.
- Empty assistant placeholders (tool-only steps, cancelled replies) are removed
  when the turn completes, so only real text remains in the conversation.
- Theme Studio gained a custom `#RRGGBB` accent that derives the full Material
  You palette in light, dark and AMOLED; it persists in the settings repository.
- Settings gained a Keys tab (all four providers in one place), matching the
  CLI's separate API-keys panel.
- All files access is re-checked on `ON_RESUME` in Settings → Workspace and in
  the `/sdcard` picker, so the grant is visible without reopening the screen.
- `MarkdownStreamingTest` covers the streaming splitter (4 JVM tests).

## Windows host support and APK build (2026-11-24)

Building the APK on Windows previously failed for two reasons, both fixed:

- `scripts/build-rust-android.sh` only mapped Linux/macOS hosts. It now also
  maps `MINGW*`/`MSYS*`/`CYGWIN*` to the NDK's `windows-x86_64` toolchain and
  resolves tool names through `tool_path`, which prefers `.exe`, then `.cmd`,
  then the bare name (the versioned `aarch64-linux-android24-clang` on Windows is
  a Unix shell script; `.cmd` is the runnable one).
- The Gradle `cargoBuild` task called `bash` from PATH, which on Windows is
  usually the WSL shim in `System32` that cannot run a `C:\...` path. It now
  prefers Git Bash (`GIT_BASH`, then the standard install paths) and falls back
  to `bash` elsewhere.

Toolchain used: rustup 1.100.0 with the `aarch64-linux-android` target, Android
NDK r27c (27.2.12479018), JDK 17, compileSdk 36.

- `scripts/build-rust-android.sh release arm64-v8a` → 6.8 MB
  `android/app/src/main/jniLibs/arm64-v8a/libfreenerf.so`, ELF64 AArch64 shared
  object exporting `Java_dev_freenerf_app_NativeCore_nativeCall`,
  `...nativeSetBridge` and `...nativeClearBridge`.
- `./gradlew assembleDebug` → `android/app/build/outputs/apk/debug/app-debug.apk`
  (26 MB, `dev.freenerf.app` 1.0.0, targetSdk 36, native-code `arm64-v8a`) with
  `lib/arm64-v8a/libfreenerf.so` and `classes.dex` inside.
- `./gradlew :app:testDebugUnitTest` → 12 JVM tests pass.

Still not verified: installing/launching on a device (no device was attached),
live provider calls, the Rust test suite on this host, and any visual
inspection.

## Session-centric Material You pass (2026-11-24)

- `android/ui-mockup.html` draws the shell (Chat, empty Chat, Sessions, Settings,
  approval dialog) for review without a device.

- The shell is **Chat**, **Sessions** and **Settings**, matching the terminal
  app's split (conversation, `Ctrl+H` history, `Ctrl+P` settings). Chat is the
  live conversation for the current session; Sessions is history and opening a
  session (or "New session") switches to Chat. There is no pushed session
  screen and no back arrow inside the conversation.
- `SessionsScreen` is a Material You home surface: collapsible large top app
  bar, tonal session cards with a rounded icon tile, relative timestamps,
  `FilterChip` for closed sessions, and an extended FAB for "New session".
  Sessions are created by the first prompt instead of being pre-created empty.
- `ChatScreen` does not read as a messaging app: the instruction renders as a
  labeled `PROMPT` block on `surfaceContainerHigh`, the agent's answer is plain
  document text, and tool calls are rounded tonal cards. The composer is one
  `extraLarge` tonal container with a borderless field, a round send FAB and a
  tonal stop button. Its top bar carries a history action that jumps to the
  Sessions tab, like `Ctrl+H` in the CLI.
- Insets are owned once: the root `Scaffold` uses `WindowInsets(0)` and only the
  navigation bar reserves space, so app bars, the composer and the list no
  longer double-pad the status/navigation bars.
- Theme shapes grew to Material You rounding (8/12/16/24/32 dp), and dialogs use
  `extraLarge`.

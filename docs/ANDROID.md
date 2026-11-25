# Android app

FreeNerf ships a native Android client. The Python CLI/TUI remains the reference
implementation and is untouched; the Android app reuses the same behaviour
through a Rust runtime compiled for Android.

- `native/` — Rust workspace (agent loop, providers, tools, permissions,
  workspace, sessions, tasks, storage) plus the JNI crate `freenerf-android`.
- `android/` — Kotlin + Jetpack Compose app (Material 3 Expressive / Material
  You, Theme Studio accents, AMOLED surfaces).

## Architecture

```
Compose UI (Kotlin)
  ├── AppContainer ── Native.call(method, payloadJson) ──► JNI
  │        ▲                                             │
  │        └── onEvent(json) / onPermissionRequest(json) ─┘
  ├── SecretStore (Android Keystore, AES-256-GCM)
  ├── SettingsRepository / SettingsJson
  ├── WorkspaceMirror (SAF import/export, attachments)
  ├── StorageAccess + FileBrowserDialog (/sdcard workspace picker)
  ├── SshClient (JSch; onRemoteTool bridge for the ssh_* tools)
  └── Theme Studio (mode, accent, dynamic colour, AMOLED)
```

`NativeCore.nativeCall` is the only JNI entry point. It takes a method name and
a JSON payload and returns `{"ok":true,"data":...}` or
`{"ok":false,"error":"..."}`. The methods are:

| Method | Purpose |
| --- | --- |
| `app.info` | Version, providers, tool names (26 with SSH), permission modes |
| `config.public`, `config.default` | Validated config with keys masked |
| `config.migrate` | Parse a Python `config.json` (keys returned for Keystore import) |
| `models.list` | Provider model catalog |
| `tools.list` | Tool specifications |
| `workspace.info`, `workspace.legacy_import` | Workspace summary; legacy DB import |
| `session.*` | list/search/create/rename/delete/close/reopen/messages/activity/export/clear |
| `task.*` | list/get/delete/resumable |
| `agent.run`, `agent.cancel` | Run the agent synchronously (events streamed), cancel the active run |

The JNI layer registers a Kotlin bridge object. Agent events are pushed to
`onEvent(json)` from Rust worker threads (the thread is attached to the JVM),
and approval requests call `onPermissionRequest(json): Boolean` synchronously;
the UI shows a dialog and the Rust worker blocks until the answer or a 120 s
timeout (fail closed). Cancellation is a shared atomic flag checked by the agent
loop, providers and shell execution.

## Feature parity

- **Providers**: OpenRouter, Google Gemini, FreeLLMAPI and any OpenAI-compatible
  endpoint, with the same defaults, URLs, retries, timeouts and Gemini
  no-streaming behaviour.
- **Tools**: all 22 tools, same permission policies, approval flow, output
  limits, timeouts and cancellation. Search is implemented in-process because
  Android has no `rg`.
- **Sessions and tasks**: SQLite schema and legacy migrations are shared with the
  Rust implementation; session messages and tool activity are persisted.
- **UI**: streaming chat with Markdown and copyable code blocks, tool cards with
  expandable args/output, usage counters, cancel button, session browser with
  search/rename/close/delete, task list with plan progress, settings for
  providers, keys, models, temperature, limits, streaming, permission mode and
  system prompt, plus a blocking approval dialog.

## Security

- API keys are encrypted with an AES-256-GCM key in the Android Keystore and
  stored only as ciphertext in private preferences. They are never written to
  the Rust databases or logs, and they are excluded from Android backup.
- Tool execution is confined to the app-private workspace by the Rust workspace
  layer. Absolute paths outside the root, `..` escapes and symlink escapes are
  rejected.
- `usesCleartextTraffic` is enabled only so user-provided local endpoints work;
  the built-in providers all use HTTPS.

## Building

Requirements: JDK 17, Android SDK platform 36 and build-tools 36, NDK 27.x, Rust
with the Android targets installed.

```bash
export JAVA_HOME=/path/to/jdk17
export ANDROID_HOME=/path/to/android-sdk     # NDK is discovered under $ANDROID_HOME/ndk

# Optional: build the native library ahead of time.
scripts/build-rust-android.sh release arm64-v8a

cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

`preBuild` depends on a real `cargoBuild` task that cross-compiles the runtime
with the NDK and copies `libfreenerf.so` into `jniLibs`; the APK cannot be
produced without it. Use `-PskipRust=true` only when the library was already
built by the script. Additional ABIs:

```bash
./gradlew assembleDebug -PrustAbis=arm64-v8a,x86_64
```

## Tests

```bash
cd native && cargo test --workspace          # 82 tests
cd android && ./gradlew testDebugUnitTest    # JVM tests
cd android && ./gradlew connectedDebugAndroidTest   # needs a device/emulator
```

## Data migration from the Python CLI

- **Config**: Settings → Workspace → *Import config* reads `config.json`, maps
  provider aliases (`openai` → `openai-compatible`), moves API keys into the
  Keystore and keeps the rest of the settings.
- **Databases**: *Import databases* accepts `sessions.db` and `tasks.db`; they
  are copied into the workspace data directory and migrated in place by the Rust
  session/task stores. Existing databases are not overwritten.

## Workspace and SSH

- **Workspace**: Settings → Workspace opens a filesystem browser rooted at
  `/sdcard`. Choosing a folder (or a file, whose parent folder is used) makes it
  the workspace and returns to the chat. Shared storage needs All files access
  on Android 11+; the app-private folder is always available.
- **SSH**: Settings → SSH stores host, port, user, remote directory and either a
  password or a private key in the Keystore. The Rust runtime registers a
  remote executor at bridge setup, which adds `ssh_exec`, `ssh_read_file`,
  `ssh_write_file` and `ssh_list_files` to the agent's tool set. Calls are
  answered synchronously over JNI and still pass through the permission policy.

## Known limitations

- Shared-storage workspaces need All files access; without it the picker only
  shows app storage. SAF trees are still mirrored on demand rather than mounted.
- The SSH tools need a reachable host; duplicate the remote path locally if you
  want the agent to search both at once.
- The default APK targets `arm64-v8a`. Build x86_64 for emulators.
- The shell tool is policy-checked, not an OS sandbox.
- Gemini does not stream responses.

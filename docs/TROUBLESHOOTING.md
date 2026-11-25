# Troubleshooting

## The app will not start

**`ModuleNotFoundError: lxml`** — install it (`pip install lxml`). On Termux,
install the system libraries first:

```bash
pkg install libxml2 libxslt
pip install lxml
```

**`ModuleNotFoundError: textual`** — activate the virtual environment and run
`pip install -e .`.

**`freenerf: command not found`** — the virtual environment is not active, or
the package was not installed editable. Run `pip install -e .` and check
`python -m freenerf --version`.

## Provider problems

**`Invalid or missing API key (HTTP 401)`** — open Settings (`Ctrl+S`) → API
Keys and confirm the key belongs to the selected provider. Environment variables
override the config file; check `FREENERF_*_API_KEY`.

**`Model or endpoint not found (HTTP 404)`** — the model id may be wrong for the
provider. Open Model Explorer (`Ctrl+O`) and pick from the catalog. OpenRouter
ids usually look like `vendor/model`; Gemini ids do not include the `models/`
prefix (Freenerf strips it).

**`Missing Gemini API key` / `Missing FreeLLMAPI unified API key`** — these
providers require a key, unlike OpenRouter which can start without one.

**`Rate limited (HTTP 429)`** — Freenerf retries with backoff up to
`max_retries`. If it still fails, wait or switch models.

**Provider errors keep retrying** — only transient errors (429, 5xx, timeouts,
connection failures) retry. Reduce retries in Settings → General.

**A local OpenAI-compatible server is not detected** — set the base URL in
Settings → Provider (for example `http://127.0.0.1:1234/v1`) and put any
non-empty key in the OpenAI-compatible field if the server requires one.

## Agent behaviour

**The agent stops with `blocked`** — it hit the step limit (`max_agent_steps`) or
a repeated-tool-failure guard. Raise the step limit in Settings → General, or
give a smaller, clearer task.

**The agent says `awaiting_approval`** — an operation was denied. Approve it and
ask again, or switch to FULL mode if you trust the workspace.

**A write was rejected as outside the project** — paths must stay inside the
workspace, including `..` escapes. Change workspace if the file really lives
elsewhere.

**The agent cannot run tests** — install the test runner in the active
environment (`python -m pytest`), or tell it the exact command to use. The
`verify` tool reports the exit code and output.

**Tool activity shows `timeout`** — long commands are capped (shell default
120s, maximum 600s). Split the work or pass a larger `timeout` to `run_shell`.

## Workspace and data

**`Workspace is not writable`** — choose a directory your user can write to.
On Termux, run `termux-setup-storage` before using shared storage.

**Sessions or tasks seem to be missing** — you are probably in a different
workspace. Each workspace has its own `.freenerf/sessions.db` and
`tasks.db`. Check the workspace path in the status bar or Settings → Workspace.

**I want the data somewhere else** — set `FREENERF_DATA_DIR` and restart. New
databases are created there; existing workspace data is not moved
automatically.

**The database is locked** — only one Freenerf instance should use a workspace
at a time. Close other instances. SQLite recovers automatically after a crash.

**Config is corrupted** — invalid JSON is ignored and defaults are used. Delete
or fix `~/.config/freenerf/config.json`, then configure again.

## UI

**The task panel disappeared** — that is the default when no task is running.
Press `Ctrl+B` to show it, or send a task and it opens automatically.

**The activity panel is missing** — it is hidden by default and unavailable on
terminals narrower than 100 columns. Widen the terminal and press `Ctrl+T`.

**Ctrl+P does not open Settings** — Freenerf disables Textual's command palette
so `Ctrl+P` reaches the Settings Center. If you run inside a terminal that
intercepts the key, use `/settings`.

**Font size does not change** — Termux ignores terminal font changes from apps;
use pinch-to-zoom. XFCE terminal settings are supported.

**Colors look wrong** — choose another theme in Settings → Appearance; some
themes require a true-color terminal.

## Images

**`Image generation is not configured`** — add a FreeLLMAPI API key in Settings
→ API Keys. The image model can be changed in Settings → Model.

**The image is not shown inline** — terminals cannot render images. The reply
includes the saved path under `.freenerf/images/`; open it with your file
manager or an image viewer.

## Headless mode

**Exit code 2 with `awaiting_approval`** — the task needed permission and
`--yes` was not passed. Re-run with `--yes`, or use a permission mode that
allows the operation.

**Exit code 3** — the run failed or was blocked; read the printed status and
error. Provider failures are printed before exiting.

**Output is not script-friendly** — headless mode prints a Rich Markdown
rendering. For automation, prefer the exit code and read the session database,
or redirect output and strip formatting.

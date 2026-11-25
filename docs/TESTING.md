# Testing

## Run the suite

```bash
python -m compileall -q src tests
PYTHONPATH=src python -m pytest tests -q
```

Or install the package with dev extras and run plain `pytest`:

```bash
pip install -e ".[dev]"
pytest -q
```

The tests use temporary directories and fakes; they do not require API keys and
do not call external services. One test starts a local HTTP server on
`127.0.0.1` and speaks the OpenAI-compatible protocol to exercise the real
provider, agent loop and tool pipeline.

## What is covered

| File | Focus |
| --- | --- |
| `test_config.py` | defaults, clamping, env overrides, migration, 0600 config, masking |
| `test_workspace.py` | validation, isolated data dirs, legacy migration, switching |
| `test_permissions.py` | destructive command variants, modes, git policy, redaction |
| `test_registry.py` | schema validation, approval, denial, errors, timeout, cancellation, truncation |
| `test_agent_loop.py` | finish statuses, plans, steps, limits, retries, plain fallback, duplicate search, denials, path escape, timeouts, image tool |
| `test_providers.py` | payloads, sampling config, error mapping, SSE tool assembly, Gemini translation |
| `test_catalog.py` | provider routing, tool filtering, caching, pricing labels |
| `test_image_generation.py` | request body, parameter fallback, b64 storage, history, errors |
| `test_sessions.py` | persistence, metadata, search, close/reopen, delete cascade, export |
| `test_task_lifecycle.py` | task progress, statuses, logs, session scoping, resumability |
| `test_tui_smoke.py` | mounting, commands, modals, responsive sidebar, a full chat turn, workspace switch |
| `test_cli_headless.py` | headless session persistence, provider failure, `--config` |
| `test_end_to_end_local_server.py` | real HTTP multi-step tool calling and task completion |
| existing web tests | search/fetch parsing, limits, blocked URLs |

## Testing philosophy

- Assert observable behaviour, not private implementation details.
- Fake provider responses mirror the real wire formats.
- External HTTP is always mocked, except the localhost end-to-end test.
- Safety tests include the bypass attempts that matter: flag reordering,
  chained commands, `sudo`, home/root targets, protected directories.
- The TUI is exercised with Textual's `run_test` pilot, which mounts the real
  app and drives keys without a terminal.

## Not covered

- Visual appearance on real terminals and Android devices.
- Live provider accounts and paid endpoints.
- Image or network behaviour of third-party APIs beyond the documented shapes.
- Long-running stress tests and memory profiling.

When changing the agent loop, permission policy, workspace handling or tool
schemas, run the full suite; these areas have cross-cutting effects.

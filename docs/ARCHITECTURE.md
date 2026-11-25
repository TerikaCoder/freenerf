# Architecture

## Component map

```
freenerf/
├── cli.py                  entry point, workspace selection, headless mode
├── config.py               global config, provider defaults, validation
├── workspace.py            workspace validation, isolated data directories
├── agents/
│   ├── runtime.py          autonomous loop, context, retries, outcomes
│   ├── registry.py         tool registry, argument validation, execution
│   ├── toolset.py          built-in tool schemas + handlers
│   ├── permissions.py      modes, hard safety block, redaction
│   ├── task_manager.py     task lifecycle bridge for one agent run
│   └── task_store.py       tasks, steps and logs (SQLite)
├── llm/
│   ├── providers.py        OpenAI-compatible + Gemini, streaming, factory
│   └── catalog.py          model catalogs, caching
├── sessions/store.py       conversations (SQLite)
├── tools/                  filesystem, terminal, git, project, image, web
└── tui/                    Textual app, widgets, modal screens, fonts
```

## Execution flow

```
user prompt
   │
   ├─ TUI: persist user message, start worker, show streaming area
   ▼
Agent.chat(prompt)
   │  create task in the workspace task database (status=running)
   │  append user message to context
   ▼
loop (bounded by max_agent_steps and tool budgets)
   │  trim context to max_context_chars
   │  provider.chat(messages, tools, stream_callback)
   │      ├─ retriable ProviderError → backoff and retry
   │      └─ final ProviderError → task failed, raise to UI
   │
   ├─ no tool calls → final answer, implicit task completion
   └─ tool calls (one or many)
          │  validate JSON arguments
          │  enforce per-run budgets and duplicate-search guard
          ▼
       ToolRegistry.execute()
          │  schema validation
          │  permission decision (allow / ask / deny)
          │  approval dialog when required
          │  run handler with timeout and cancellation
          ▼
       structured ToolResult (ok / error / denied / invalid / timeout)
          │  append result to context (redacted in logs)
          │  notify task log and UI activity
          │  finish tool → record status + summary and stop
          ▼
next loop iteration
   │
   └─ limits reached → task blocked, no fake success
```

## Task lifecycle

`AgentTaskManager` owns one task per user request.

```
pending → running → completed
                  ├→ partial
                  ├→ failed
                  ├→ blocked
                  ├→ awaiting_approval
                  └→ cancelled
```

- `set_plan` replaces the plan and creates steps.
- `update_step` records status, result and error, and advances `current_step`.
- `finish` stores an explicit status and summary.
- Without an explicit `finish`, the task is `completed` only when every step is
  done or no plan exists; open steps make it `partial`; an explicit denial makes
  it `awaiting_approval`.

Task rows, step rows and logs are scoped by `session_id` and workspace. The TUI
only shows tasks for the active session, and reopening a session asks before
continuing an unfinished task.

## Context management

- Tool outputs longer than 4,000 characters are truncated first.
- Then the oldest assistant messages and their tool results are dropped until
  the conversation fits `max_context_chars`, keeping the system prompt, recent
  turns and the current user message.
- Tool outputs sent back to the model are capped at 12,000 characters; registry
  output is capped at 40,000.

## Permission enforcement

`ToolRegistry.execute` is the only execution path. It validates arguments,
calls `evaluate_permission`, awaits approval when needed, runs the handler in a
worker thread with a timeout, and shapes errors. The UI cannot skip these
steps, and the hard safety block is checked before the mode.

See [TOOLS.md](TOOLS.md) for policies and [../INSTRUCTIONS.md](../INSTRUCTIONS.md)
for the user-facing modes.

## Providers

Providers translate the shared message list into their own protocol:

- OpenAI-compatible: `tools` / `tool_calls`, SSE streaming with tool-call
  fragment assembly.
- Gemini: `functionDeclarations`, `functionCall` / `functionResponse`,
  `thoughtSignature` preservation, no streaming.

The agent only depends on:

```python
provider.chat(messages, tools=None, stream_callback=None)
# -> {"content": str|None, "tool_calls": [...], "raw_message": dict|None}
```

Providers expose capability flags (`supports_tools`, `supports_streaming`,
`supports_images`), which the agent uses for graceful fallback.

## Fallback for providers without tool calling

If `supports_tools` is false, the agent appends a documented protocol to the
system prompt:

```xml
<tool>{"name": "read_file", "args": {"path": "README.md"}}</tool>
```

It parses those blocks, executes the tool, and appends the result as a user
message (since the provider has no tool role). This keeps the agent usable, with
lower reliability, on plain completion endpoints.

## Workspace isolation

`Workspace` validates a directory and owns `<root>/.freenerf/` (or
`FREENERF_DATA_DIR`). Switching workspace:

1. validates the new directory,
2. closes the current agents/providers,
3. opens new session and task databases,
4. rebuilds the agent and tools with the new root,
5. refreshes tabs, task panel and status bar.

No sessions, tasks, images or backups are copied between workspaces. A legacy
global database is imported exactly once into the first workspace opened, then
marked as migrated.

## Concurrency and cancellation

- Provider calls and agent loops run in a Textual worker thread.
- UI updates are marshalled with `call_from_thread`.
- A shared `threading.Event` cancels the loop between steps, between tool calls,
  during retry backoff and while waiting for approval.
- Shell commands run in their own session and are killed as a process group on
  timeout.
- Tool handlers run in a small thread pool; a timeout returns control to the
  agent even if the handler ignores cancellation.

## Extension points

- **New tool** — add a schema and handler in `agents/toolset.py`; the registry
  and TUI activity view pick it up automatically.
- **New provider** — implement `chat` and capability flags in
  `llm/providers.py` and add it to `PROVIDER_INFO` and `create_provider`.
- **New screen** — subclass `ModalScreen` in `tui/screens.py` and push it from
  the app with a callback.
- **New store** — accept a database path from the workspace so isolation is
  preserved.

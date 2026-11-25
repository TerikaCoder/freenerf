# Freenerf Usage Guide

Freenerf is an autonomous AI coding agent for the terminal. It works inside a
workspace directory that you choose, plans multi-step work, uses tools, verifies
changes and reports what actually happened.

## Start

```bash
freenerf
```

First launch:

1. Choose a workspace directory (the only directory Freenerf may edit).
2. Open **Settings** (`Ctrl+P`) and add an API key for your provider.
3. Optionally open **Model Explorer** (`Ctrl+O`) to pick a model.

Without a key the interface still opens and shows a warning; you can configure
everything from Settings.

## The interface

```
 FREENERF  autonomous coding agent    workspace    provider · model   PERM NORMAL
 ┌ session tabs ──────────────────────────────────────────────┐  ┌ TASK ───────┐
 │ ● Fix tests   ● Docs   ×                     +             │  │ title       │
 └────────────────────────────────────────────────────────────┘  │ 2/5  40%    │
 ┌ conversation ──────────────────────────────────────────────┐  │ ▰▰▰▱▱       │
 │ ● You                                                      │  │ ✓ Inspect   │
 │ ...                                                        │  │ ▶ Fix       │
 │ ● Freenerf                                                 │  └─────────────┘
 │ ...                                                        │  ┌ ACTIVITY ───┐
 └────────────────────────────────────────────────────────────┘  │ ✓ read_file │
 > prompt                                                       │ ✗ pytest    │
 Enter send  / commands  Esc cancel  Ctrl+P settings ...        └─────────────┘
 status: provider · model · permission · session · workspace
```

- The **conversation** shows your messages, the agent's answers and one-line
  tool results.
- The **task panel** shows the plan, current step and progress above the
  conversation. It starts hidden, opens automatically when a task begins and
  closes a few seconds after the task finishes. `Ctrl+B` toggles it without
  deleting any task data.
- The **activity panel** lists every tool call with its outcome and duration.
  It is hidden by default and `Ctrl+T` toggles it on wide terminals.
- The compact header shows workspace, provider, model and permission mode; the
  status bar shows session, elapsed time and active task progress.

## Working with the agent

Type a goal and press `Enter`, for example:

```
Analyse this project, find the problems, improve the architecture, run the
tests, fix the failures, and summarise everything.
```

The agent will inspect files, publish a plan, edit code, run commands and
verify. While it runs:

- `Esc` cancels; unfinished tool results are recorded as cancelled.
- Operations that need approval open a dialog. `y` approves, `n`/`Esc` denies.
  Denials are reported back to the agent, which can choose another approach.
- Every tool result is visible in the activity panel; failures are red.

The agent finishes with one of:

| Status | Meaning |
| --- | --- |
| `completed` | The outcome was verified (tests or checks were run). |
| `partial` | Some work is done; some plan steps remain open. |
| `failed` | It tried and could not complete the task. |
| `blocked` | It needs you, a missing dependency or a step budget. |
| `awaiting_approval` | A required operation was denied. |
| `cancelled` | You stopped it. |

## Slash commands

| Command | Action |
| --- | --- |
| `/help` | List commands and shortcuts |
| `/new` | New session |
| `/history` | Search, open, rename, close or delete sessions |
| `/models` | Model Explorer |
| `/settings` | Settings Center |
| `/config` | Show the effective configuration (keys masked) |
| `/workspace` | Change workspace |
| `/permissions` | Show the mode, or set `denied`, `normal`, `full` |
| `/tasks` | List this session's tasks and statuses |
| `/clear` | Clear the conversation view (history is kept) |
| `/exit` | Quit |

Aliases: `/setting`, `/quit`.

## Sessions

- A session stores its messages and its own agent context.
- The first message becomes the session title; rename it in `/history`.
- Tabs show the 12 most recent open sessions; the `×` closes a session without
  deleting history. Reopen closed sessions from `/history`.
- If a session has an unfinished task, opening it asks whether to continue.
  Nothing runs without your approval.

## Tasks

Each user request creates a persistent task containing:

- title and original goal,
- the plan and each step's status, result and error,
- a chronological log of tool activity and outcomes,
- the final status and summary.

Tasks are scoped to their session and workspace. Changing workspace never runs
or copies them.

## Image generation

Ask naturally:

```
Create a logo for Freenerf: a minimal teal terminal prompt, dark background.
```

The agent calls the image tool, which uses the FreeLLMAPI images endpoint and
saves the image under the workspace's `.freenerf/images/`. The reply includes
the saved path and prompt. A FreeLLMAPI key is required.

## Web research

Ask for documentation, library comparisons or current information. The agent
uses `web_search` first and `web_fetch` for page details, treats page content as
untrusted data, and cites the URLs it used.

## Keyboard

`Ctrl+P` settings · `Ctrl+O` models · `Ctrl+N` new session · `Ctrl+H` history ·
`Ctrl+G` workspace · `Ctrl+B` task panel · `Ctrl+T` activity · `Esc` cancel ·
`Tab` complete command ·
`Ctrl+C`/`Ctrl+Q` quit.

## Settings Center

Grouped into **General** (steps, retries, context budget, streaming,
typewriter), **Appearance** (theme, density, terminal font), **Provider**
(active provider, base URLs, temperature, max tokens), **Model** (current model,
Model Explorer, image model), **API Keys**, **Permissions**, and **Workspace**
(change workspace, data directory).

Settings are saved globally (`~/.config/freenerf/config.json`). Workspace
sessions, tasks and images stay inside the workspace.

## Headless usage

```bash
freenerf --print "Run the tests and fix failures" --yes
freenerf --workspace ~/code/app --print "Explain the architecture"
freenerf --permission normal --print "Review the diff"
```

Without `--yes`, operations needing approval are denied and reported. Exit
codes: `0` completed, `2` partial/awaiting, `3` failed/blocked, `130` cancelled.

## Configuration

Config path: `~/.config/freenerf/config.json`.

Key environment variables:

```
FREENERF_PROVIDER, FREENERF_MODEL
FREENERF_OPENROUTER_API_KEY, FREENERF_GEMINI_API_KEY,
FREENERF_FREELLMAPI_API_KEY, FREENERF_OPENAI_API_KEY
FREENERF_OPENAI_BASE_URL, FREENERF_OPENROUTER_BASE_URL,
FREENERF_GEMINI_BASE_URL, FREENERF_FREELLMAPI_BASE_URL
FREENERF_WORKSPACE, FREENERF_DATA_DIR, FREENERF_PERMISSION_MODE
```

More detail: [README](README.md), [docs/WORKSPACES.md](docs/WORKSPACES.md),
[docs/PROVIDERS.md](docs/PROVIDERS.md).

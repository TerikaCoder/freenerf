# Tools

Every tool is declared in `src/freenerf/agents/toolset.py` with a JSON schema
and a handler. The registry (`agents/registry.py`) validates arguments, applies
permissions, enforces timeouts and returns a structured result.

## Catalog

| Tool | What it does | Policy | Timeout |
| --- | --- | --- | --- |
| `list_files` | List files/directories (optionally recursive) | read | 30s |
| `read_file` | Read UTF-8 text with line offset/limit | read | 30s |
| `file_info` | Size, line count, suffix, modified time | read | 60s |
| `search` | ripgrep search (`source` or `project` scope) | read | 40s |
| `project_info` | Detect languages, package manager, test/lint commands | read | 60s |
| `web_search` | Public web search with URLs | read | 40s |
| `web_fetch` | Fetch and extract a public page | read | 40s |
| `write_file` | Create/overwrite with backup + atomic write | write | 60s |
| `edit_file` | Exact snippet replacement with backup | write | 60s |
| `delete_file` | Delete a file or empty directory (backup kept) | write | 60s |
| `run_shell` | Shell command with timeout and output cap | exec | 630s |
| `verify` | Run detected/explicit test command | exec | 630s |
| `git` | Allow-listed git commands | exec | 90s |
| `generate_image` | FreeLLMAPI image generation into the workspace | image | 180s |
| `set_plan` | Publish the current plan | task | 60s |
| `update_step` | Update a plan step with evidence | task | 60s |
| `finish` | Record the final status and stop | task | 60s |
| `ssh_exec` | Run a command on the configured SSH host (app builds only) | exec | 630s |
| `ssh_read_file` | Read a remote file over SFTP | read | 60s |
| `ssh_write_file` | Create/overwrite a remote file over SFTP | write | 120s |
| `ssh_list_files` | List a remote directory | read | 60s |

The `ssh_*` tools exist only when the host registers a remote executor (the
Android app does, after an SSH host is configured), so they are not offered by
the terminal build.

Per-run limits: `web_search` 3, `web_fetch` 5, `generate_image` 4, total tool
calls 80. Repeating the same web search query is skipped.

## Permission policies

- `read` — allowed in NORMAL; blocked in DENIED.
- `task` — allowed in NORMAL (plan bookkeeping).
- `image` — allowed in NORMAL; writes only inside the workspace.
- `write` — requires approval in NORMAL.
- `exec` — requires approval in NORMAL; read-only git subcommands are allowed.

FULL allows `write`/`exec`; DENIED blocks everything. The hard safety block in
`agents/permissions.py` rejects destructive shell commands in every mode.

## Results

`ToolResult` has `kind` (`ok`, `error`, `denied`, `invalid`, `timeout`,
`cancelled`), `output`, `error`, `elapsed` and `policy`. The agent receives a
short text form:

- `ok` → output
- `denied` → `Permission denied: …`
- `invalid` → `Invalid tool arguments for <tool>: …`
- `timeout` → `Tool timed out after Ns: <tool>`
- `error` → `Tool error (<tool>): …`

## Adding a tool

1. Add a spec to `TOOL_SPECS`:

```python
{
    "type": "function",
    "function": {
        "name": "word_count",
        "description": "Count words in a workspace file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}
```

2. Add a handler:

```python
def h_word_count(agent, args):
    if agent.fs is None:
        raise ToolError("No workspace is configured")
    text = agent.fs.read_file(args["path"])
    return f"{len(text.split())} words"
```

3. Register the handler and policy:

```python
HANDLERS["word_count"] = h_word_count
POLICIES["word_count"] = "read"
TIMEOUTS["word_count"] = 30
```

That is all: the registry exposes it to every tool-capable provider, the
activity panel renders it, and permission checks apply automatically.

## Safety notes

- `write_file` and `edit_file` back up the previous file under
  `.freenerf/backups/<timestamp>/…` and write atomically.
- `delete_file` refuses non-empty directories and protects the workspace root.
- All path arguments resolve inside the workspace; `..` escapes are rejected.
- Shell commands run with your user's permissions. The safety block is not an
  OS sandbox.
- Tool output is treated as untrusted data by the agent prompt; never as
  instructions.

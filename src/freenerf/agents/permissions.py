"""Permission policy for every tool execution.

Permissions are evaluated at the tool-execution layer (see
``agents.registry``), never only in the UI. Three modes are supported:

* ``denied``  – no tool may run at all.
* ``normal``  – read-only operations run freely, writes and commands need
  explicit user approval.
* ``full``    – everything runs automatically, except the hard safety block.

The hard safety block cannot be overridden by any mode. It catches commands
whose primary effect is destroying the machine, the project history or the
user's home directory.
"""

from __future__ import annotations

import posixpath
import re
import shlex


MODES = ("denied", "normal", "full")

# Tool policies used when a tool does not declare its own.
TOOL_POLICIES = {
    "list_files": "read",
    "read_file": "read",
    "search": "read",
    "file_info": "read",
    "web_search": "read",
    "web_fetch": "read",
    "project_info": "read",
    "write_file": "write",
    "edit_file": "write",
    "delete_file": "write",
    "run_shell": "exec",
    "verify": "exec",
    "git": "exec",
    "generate_image": "image",
    "image_create": "image",
    "task_start": "exec",
    "task_status": "read",
    "task_list": "read",
    "task_stop": "exec",
    "set_plan": "task",
    "update_step": "task",
    "finish": "task",
}

READ_ONLY_GIT = {
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "rev-parse",
    "ls-files",
    "blame",
    "describe",
    "tag",
    "remote",
    "stash",
    "config",
}

DANGEROUS_TARGETS = {
    "/",
    "/*",
    "~",
    "~/",
    "$HOME",
    "${HOME}",
    "..",
    "../",
    ".",
    "./",
    "*",
    "/*",
}

PROTECTED_DIRECTORIES = {".git", ".freenerf"}

HARD_BLOCK_PATTERNS = (
    # Filesystem formatting / raw device writes.
    (r"\bmkfs(?:\.[a-z0-9]+)?\b", "filesystem formatting is never allowed"),
    (r"\bdd\b[^\n;]*\bof\s*=\s*/dev/", "writing raw disk devices is never allowed"),
    (r">\s*/dev/(?:sd|hd|nvme|mmcblk|disk)", "writing raw disk devices is never allowed"),
    # Machine control.
    (r"\b(?:shutdown|reboot|poweroff|halt|init\s+0)\b", "power management is blocked"),
    # Fork bomb.
    (r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:", "fork bombs are blocked"),
    # Piping remote content straight into a shell.
    (
        r"\b(?:curl|wget)\b[^\n;|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash)\b",
        "piping remote content into a shell is blocked",
    ),
    (
        r"\b(?:sh|bash|zsh)\b\s*(?:<|=\s*)\(?\s*(?:curl|wget)\b",
        "piping remote content into a shell is blocked",
    ),
    # Recursive chmod/chown on the filesystem root.
    (
        r"\bch(?:mod|own)\b[^\n;]*\s-R[^\n;]*\s/(?:\s|$)",
        "recursive permission changes on / are blocked",
    ),
    # Deleting everything with find.
    (
        r"\bfind\s+/(?:\s|$)[^\n;]*-delete",
        "recursive deletion from / is blocked",
    ),
)


def normalize_mode(mode) -> str:
    mode = str(mode or "normal").strip().lower()
    return mode if mode in MODES else "normal"


def _short_flag_letters(token: str) -> str:
    if token.startswith("--"):
        return ""
    if token.startswith("-") and len(token) > 1:
        return token[1:]
    return ""


COMMAND_PREFIXES = {
    "sudo", "doas", "command", "env", "nohup", "time", "exec",
    "builtin", "then", "do", "else", "xargs",
}


def _is_recursive_force_rm(segment: str) -> tuple[bool, list[str]]:
    """Return (destructive, targets) for an ``rm`` in one shell segment."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()

    # ``rm`` only counts in command position: ``echo rm -rf /`` is harmless.
    index = 0
    while index < len(tokens):
        token = tokens[index]
        base = token.rsplit("/", 1)[-1]

        if base in COMMAND_PREFIXES:
            index += 1
            continue

        if "=" in token and not token.startswith("-"):
            index += 1
            continue

        break

    if index >= len(tokens):
        return False, []

    if tokens[index].rsplit("/", 1)[-1] != "rm":
        return False, []

    recursive = False
    force = False
    targets: list[str] = []

    for token in tokens[index + 1:]:
        if token in ("--recursive",):
            recursive = True
            continue
        if token in ("--force",):
            force = True
            continue
        if token.startswith("-") and token != "-":
            letters = _short_flag_letters(token)
            if "r" in letters or "R" in letters:
                recursive = True
            if "f" in letters:
                force = True
            continue
        targets.append(token)

    return (recursive and force), targets


def _normalize_target(target: str) -> str:
    cleaned = target.strip().strip("'\"")
    if cleaned in ("$HOME", "${HOME}", "~"):
        return cleaned
    normalized = posixpath.normpath(cleaned)
    return normalized


def shell_safety_violation(command: str) -> str | None:
    """Return a reason when ``command`` hits the hard safety block."""
    command = str(command or "")
    if not command.strip():
        return None

    for pattern, reason in HARD_BLOCK_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE | re.DOTALL):
            return reason

    # Check every chained segment so ``echo ok; rm -fr /`` is still caught.
    segments = re.split(r"&&|\|\||[;|\n`]|\$\(", command)
    for segment in segments:
        destructive, targets = _is_recursive_force_rm(segment)
        if not destructive:
            continue

        for target in targets:
            normalized = _normalize_target(target)

            if normalized in DANGEROUS_TARGETS or normalized.startswith("/"):
                return "recursive deletion of a root or home path is blocked"

            if normalized.startswith(("~", "$HOME/", "${HOME}/")):
                return "recursive deletion of a home path is blocked"

            head = normalized.split("/", 1)[0]
            if head in PROTECTED_DIRECTORIES:
                return (
                    f"recursive deletion of {head}/ is blocked to protect "
                    "project history and workspace data"
                )

    return None


def redact(text: str) -> str:
    """Best-effort secret redaction for logs, UI and task records."""
    value = str(text or "")

    value = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret)"
        r"(\s*[=:]\s*)([^\s'\";]+)",
        r"\1\2[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
        "Bearer [REDACTED]",
        value,
    )
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
    value = re.sub(r"\bAIza[0-9A-Za-z_-]{10,}", "[REDACTED]", value)
    value = re.sub(
        r"(?i)([?&](?:key|api_key|token)=)[^&\s]+",
        r"\1[REDACTED]",
        value,
    )
    return value


def git_subcommand(args: dict) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return ""
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        parts = command.split()
    for part in parts:
        if part.startswith("-"):
            continue
        return part.lower()
    return ""


def evaluate_permission(mode, name, args, policy=None):
    """Return ``(decision, reason)`` where decision is allow/ask/deny."""
    mode = normalize_mode(mode)
    args = args if isinstance(args, dict) else {}
    policy = policy or TOOL_POLICIES.get(name)

    if policy is None:
        return "deny", "Tool is not registered"

    if mode == "denied":
        return "deny", "Permission mode DENIED blocks all tools"

    if name in ("run_shell", "verify", "task_start"):
        violation = shell_safety_violation(str(args.get("command", "")))
        if violation:
            return "deny", f"Blocked by the hard safety policy: {violation}"

    if name == "git":
        subcommand = git_subcommand(args)
        if subcommand in READ_ONLY_GIT:
            return "allow", f"Read-only git command ({subcommand})"
        if subcommand in ("", "--version"):
            return "allow", "Git version check"
        if mode == "full":
            return "allow", "FULL mode"
        return "ask", f"Git command '{subcommand}' modifies the repository"

    if policy in ("read", "task"):
        return "allow", "Read-only or workspace-local operation"

    if mode == "full":
        return "allow", "FULL mode"

    if policy == "image":
        return "ask", "This operation calls an image service and saves files"

    if policy == "write":
        return "ask", "This operation modifies files in the workspace"

    if policy == "exec":
        return "ask", "This operation runs commands in the workspace"

    return "deny", "Unknown tool policy"


def describe(name, args) -> str:
    """Short human description used by approval dialogs and activity rows."""
    args = args if isinstance(args, dict) else {}

    if name in ("run_shell", "verify"):
        return str(args.get("command", "")).strip()[:400]
    if name == "git":
        return f"git {args.get('command', '')}".strip()[:400]
    if name in ("read_file", "write_file", "edit_file", "delete_file", "list_files"):
        return str(args.get("path", "."))
    if name == "search":
        return f"{args.get('query', '')} ({args.get('scope', 'source')})"
    if name == "web_search":
        return str(args.get("query", ""))
    if name == "web_fetch":
        return str(args.get("url", ""))
    if name in ("generate_image", "image_create"):
        return str(args.get("prompt", ""))[:400]
    if name == "task_start":
        return str(args.get("command", ""))[:400]
    if name in ("task_status", "task_stop"):
        return str(args.get("process_id", ""))
    return ", ".join(f"{key}={value}" for key, value in args.items())[:300]

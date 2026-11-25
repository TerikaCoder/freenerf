from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


READ_ONLY = {
    "status", "diff", "log", "show", "branch", "rev-parse",
    "ls-files", "blame", "describe", "remote", "stash", "tag",
}
ALLOWED = READ_ONLY | {"add", "commit", "checkout", "restore", "config"}
FORBIDDEN = {
    "push", "reset", "clean", "filter-branch", "filter-repo", "gc",
    "reflog", "update-ref", "submodule", "clone", "worktree",
}


class GitError(Exception):
    pass


def _subcommand(command: str) -> str:
    try:
        parts = shlex.split(str(command or ""), posix=True)
    except ValueError:
        parts = str(command or "").split()

    for part in parts:
        if part.startswith("-"):
            continue
        return part.lower()
    return ""


class Git:
    """Small, allow-listed git wrapper rooted at the workspace."""

    def __init__(self, root="."):
        self.root = Path(root).resolve()

    @staticmethod
    def available() -> bool:
        return bool(shutil_which("git"))

    def run(self, command, timeout=60):
        command = str(command or "").strip()
        subcommand = _subcommand(command)

        if not subcommand:
            return "Provide a git subcommand, e.g. 'status --short'"

        if subcommand in FORBIDDEN:
            return (
                f"git {subcommand} is not available through this tool. "
                "Run it manually after review."
            )

        if subcommand not in ALLOWED:
            allowed = ", ".join(sorted(ALLOWED))
            return f"Unsupported git subcommand. Allowed: {allowed}"

        try:
            args = ["git", *shlex.split(command, posix=True)]
        except ValueError as exc:
            return f"Invalid git command: {exc}"

        environment = dict(os.environ)
        environment.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})

        try:
            result = subprocess.run(
                args,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, min(int(timeout), 300)),
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "Git command timed out"
        except OSError as exc:
            return f"Cannot run git: {exc}"

        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip()

        if len(output) > 20_000:
            output = output[:20_000] + "\n[truncated]"

        return f"Exit code: {result.returncode}\n{output}".rstrip()


def shutil_which(program: str) -> str | None:
    from shutil import which
    return which(program)

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path


MAX_OUTPUT = 20_000
MAX_TIMEOUT = 600.0


class Terminal:
    """Runs shell commands inside the workspace with a hard timeout."""

    def __init__(self, cwd=".", default_timeout=120.0):
        self.cwd = Path(cwd).resolve()
        self.default_timeout = default_timeout

    def _environment(self) -> dict:
        env = dict(os.environ)
        env.update({
            "FREENERF": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "TERM": "dumb",
        })
        return env

    @staticmethod
    def which(program: str) -> str | None:
        return shutil.which(program)

    def run(self, command, timeout=None, cwd=None):
        command = str(command or "").strip()
        if not command:
            return "No command provided"

        workdir = Path(cwd).resolve() if cwd else self.cwd

        if not workdir.is_dir():
            return f"Working directory does not exist: {workdir}"

        if not workdir.is_relative_to(self.cwd):
            return "Working directory is outside the project"

        try:
            timeout_value = float(timeout) if timeout is not None else self.default_timeout
        except (TypeError, ValueError):
            timeout_value = self.default_timeout
        timeout_value = max(1.0, min(timeout_value, MAX_TIMEOUT))

        started = time.monotonic()

        try:
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=workdir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                env=self._environment(),
                start_new_session=True,
            )
        except OSError as exc:
            return f"Cannot start command: {exc}"

        timed_out = False

        try:
            output, _ = process.communicate(timeout=timeout_value)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                process.kill()
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                output = ""

        output = output or ""
        elapsed = time.monotonic() - started

        if len(output) > MAX_OUTPUT:
            omitted = len(output) - MAX_OUTPUT
            output = (
                f"[first {omitted} characters omitted]\n"
                + output[-MAX_OUTPUT:]
            )

        if timed_out:
            header = f"Command timed out after {timeout_value:.0f}s"
        else:
            header = f"Exit code: {process.returncode}"

        return f"{header} ({elapsed:.2f}s)\n{output}".rstrip()

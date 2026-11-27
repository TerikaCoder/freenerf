from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


MAX_OUTPUT = 20_000
MAX_TIMEOUT = 600.0


@dataclass(frozen=True)
class CommandResult:
    output: str
    exit_code: int | None
    timed_out: bool
    elapsed: float

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def formatted(self) -> str:
        if self.timed_out:
            header = "Command timed out"
        elif self.exit_code is None:
            header = "Command could not be run"
        else:
            header = f"Exit code: {self.exit_code}"
        return f"{header} ({self.elapsed:.2f}s)\n{self.output}".rstrip()


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

    def run_result(self, command, timeout=None, cwd=None) -> CommandResult:
        command = str(command or "").strip()
        if not command:
            return CommandResult("No command provided", None, False, 0.0)

        workdir = Path(cwd).resolve() if cwd else self.cwd

        if not workdir.is_dir():
            return CommandResult(
                f"Working directory does not exist: {workdir}", None, False, 0.0
            )

        if not workdir.is_relative_to(self.cwd):
            return CommandResult("Working directory is outside the project", None, False, 0.0)

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
            return CommandResult(f"Cannot start command: {exc}", None, False, 0.0)

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

        return CommandResult(
            output=output,
            exit_code=None if timed_out else process.returncode,
            timed_out=timed_out,
            elapsed=elapsed,
        )

    def run(self, command, timeout=None, cwd=None):
        return self.run_result(command, timeout=timeout, cwd=cwd).formatted()

"""Workspace handling.

A workspace is the directory the agent is allowed to operate on. Every
workspace owns an isolated data directory (``.freenerf/`` by default) that
contains its sessions database, task database, generated images and logs.

Set ``FREENERF_DATA_DIR`` to keep that data outside the project directory.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_data_dir


DATA_DIR_NAME = ".freenerf"
LEGACY_DATA_DIR = Path(user_data_dir("freenerf"))


class WorkspaceError(ValueError):
    """Raised when a workspace path cannot be used safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Workspace:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def open(cls, path, create: bool = True) -> "Workspace":
        """Validate ``path`` and return a workspace rooted there."""
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate

        if not candidate.exists():
            if not create:
                raise WorkspaceError(f"Workspace does not exist: {candidate}")
            try:
                candidate.mkdir(parents=True)
            except OSError as exc:
                raise WorkspaceError(
                    f"Cannot create workspace {candidate}: {exc}"
                ) from exc

        root = candidate.resolve()

        if not root.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {root}")

        if not os.access(root, os.W_OK | os.X_OK):
            raise WorkspaceError(f"Workspace is not writable: {root}")

        return cls(root)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        override = os.getenv("FREENERF_DATA_DIR", "").strip()
        if override:
            base = Path(override).expanduser()
            if not base.is_absolute():
                base = self.root / base
            return base.resolve()
        return self.root / DATA_DIR_NAME

    @property
    def sessions_db(self) -> Path:
        return self.data_dir / "sessions.db"

    @property
    def tasks_db(self) -> Path:
        return self.data_dir / "tasks.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def metadata_file(self) -> Path:
        return self.data_dir / "workspace.json"

    @property
    def display_name(self) -> str:
        return self.root.name or str(self.root)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def ensure(self) -> "Workspace":
        """Create the data layout and import legacy global data once."""
        try:
            for directory in (
                self.data_dir,
                self.images_dir,
                self.logs_dir,
                self.backups_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(
                f"Cannot create workspace data directory: {exc}"
            ) from exc

        if not self.metadata_file.exists():
            payload = {
                "name": self.display_name,
                "root": str(self.root),
                "created_at": _now(),
            }
            try:
                self.metadata_file.write_text(
                    json.dumps(payload, indent=2), encoding="utf-8"
                )
            except OSError:
                pass

        self._migrate_legacy()
        return self

    def _migrate_legacy(self) -> bool:
        """Copy legacy global databases into this workspace, once."""
        if not LEGACY_DATA_DIR.is_dir():
            return False

        marker = LEGACY_DATA_DIR / ".migrated-to-workspace"
        if marker.exists():
            return False

        copied = False
        for name, target in (
            ("sessions.db", self.sessions_db),
            ("tasks.db", self.tasks_db),
        ):
            source = LEGACY_DATA_DIR / name
            if not source.is_file() or target.exists():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied = True
            except OSError:
                continue

        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps(
                    {"migrated_to": str(self.data_dir), "at": _now()}
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

        return copied

    # ------------------------------------------------------------------
    # Safety helpers
    # ------------------------------------------------------------------
    def is_within(self, path) -> bool:
        try:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = self.root / target
            return target.resolve().is_relative_to(self.root)
        except (OSError, ValueError):
            return False

    def relative(self, path) -> str:
        try:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = self.root / target
            return str(target.resolve().relative_to(self.root))
        except (OSError, ValueError):
            return str(path)

    def summary(self) -> dict:
        return {
            "root": str(self.root),
            "name": self.display_name,
            "data_dir": str(self.data_dir),
        }


class WorkspaceManager:
    """Resolve and switch the active workspace, persisting the choice."""

    def __init__(self, config):
        self.config = config
        self._workspace: Workspace | None = None

    def current(self) -> Workspace:
        if self._workspace is not None:
            return self._workspace

        configured = (
            os.getenv("FREENERF_WORKSPACE")
            or getattr(self.config, "workspace_root", "")
            or ""
        ).strip()

        workspace = Workspace.open(configured or Path.cwd()).ensure()
        self._workspace = workspace

        if self.config.workspace_root != str(workspace.root):
            self.config.workspace_root = str(workspace.root)
            self.config.remember_workspace(workspace.root)

        return workspace

    def switch(self, path) -> Workspace:
        workspace = Workspace.open(path).ensure()

        self._workspace = workspace
        self.config.workspace_root = str(workspace.root)
        self.config.remember_workspace(workspace.root)
        self.config.save()

        return workspace

    def recent(self) -> list[str]:
        return list(getattr(self.config, "recent_workspaces", []) or [])

from __future__ import annotations

import shutil
import subprocess
import time
import os
from datetime import datetime, timezone
from pathlib import Path


MAX_READ_BYTES = 300_000
MAX_LIST_ENTRIES = 500
BACKUP_DIR_NAME = ".freenerf"
SKIP_DIRECTORIES = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".freenerf",
}


class FileSystem:
    """Sandboxed filesystem access rooted at a workspace directory."""

    def __init__(self, root="."):
        self.root = Path(root).resolve()

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------
    def resolve(self, path, must_exist: bool = False) -> Path:
        raw = Path(str(path or ".")).expanduser()
        target = raw if raw.is_absolute() else self.root / raw
        target = target.resolve()

        if not target.is_relative_to(self.root):
            raise PermissionError("Path outside project is not allowed")

        if must_exist and not target.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")

        return target

    def relative(self, target: Path) -> str:
        try:
            return str(target.relative_to(self.root))
        except ValueError:
            return str(target)

    def _backup(self, target: Path) -> Path | None:
        """Copy the current file into .freenerf/backups before changing it."""
        if not target.is_file():
            return None

        if self.root / BACKUP_DIR_NAME in target.parents:
            return None

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        destination = (
            self.root
            / BACKUP_DIR_NAME
            / "backups"
            / stamp
            / target.relative_to(self.root)
        )

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
            return destination
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_files(self, path=".", recursive=False, max_entries=MAX_LIST_ENTRIES):
        target = self.resolve(path)

        if not target.exists():
            return "Path does not exist"

        if target.is_file():
            return f"FILE {self.relative(target)}"

        try:
            max_entries = max(1, min(int(max_entries), MAX_LIST_ENTRIES))
        except (TypeError, ValueError):
            max_entries = MAX_LIST_ENTRIES

        if recursive:
            def walk():
                for directory, directories, files in os.walk(target):
                    directories[:] = sorted(
                        name for name in directories
                        if name not in SKIP_DIRECTORIES
                    )
                    for name in sorted(directories + files):
                        yield Path(directory) / name

            iterator = walk()
        else:
            iterator = target.iterdir()

        entries = []
        truncated = False
        for item in iterator:
            relative_parts = item.relative_to(target).parts
            if any(part in SKIP_DIRECTORIES for part in relative_parts):
                continue
            entries.append(item)
            if len(entries) > max_entries:
                entries.pop()
                truncated = True
                break
        entries.sort(key=lambda item: str(item).casefold())

        if not entries:
            return "Directory is empty"

        lines = [
            ("DIR  " if item.is_dir() else "FILE ") + self.relative(item)
            for item in entries
        ]
        if truncated:
            lines.append(f"[limited to {max_entries} entries]")

        return "\n".join(lines)

    def read_file(self, path, offset=0, limit=None):
        target = self.resolve(path, must_exist=True)

        if not target.is_file():
            return "File does not exist"

        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            return (
                f"File too large ({size} bytes). Use search or read a range."
            )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "File is binary or is not valid UTF-8 text"
        except OSError as exc:
            return f"Cannot read file: {exc}"

        try:
            start = max(0, int(offset))
        except (TypeError, ValueError):
            start = 0

        if start or limit:
            lines = content.splitlines()
            end = None if limit is None else start + max(1, int(limit))
            content = "\n".join(lines[start:end])

        return content

    def file_info(self, path):
        target = self.resolve(path, must_exist=True)

        if not target.is_file():
            return "File does not exist"

        stat = target.stat()
        lines = None
        try:
            with target.open("r", encoding="utf-8", errors="ignore") as handle:
                lines = sum(1 for _ in handle)
        except OSError:
            pass

        return "\n".join([
            f"Path: {self.relative(target)}",
            f"Size: {stat.st_size} bytes",
            f"Lines: {lines if lines is not None else 'unknown'}",
            f"Suffix: {target.suffix or '(none)'}",
            f"Modified: {datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}",
        ])

    def tree(self, path=".", depth=2):
        depth = max(1, min(int(depth), 6))
        base = self.resolve(path)

        if not base.is_dir():
            return "Path is not a directory"

        lines = [self.relative(base) + "/"]
        truncated = False

        for directory, directories, files in os.walk(base):
            current = Path(directory)
            level = len(current.relative_to(base).parts)
            if level >= depth:
                directories[:] = []
                continue

            directories[:] = sorted(
                name for name in directories
                if name not in SKIP_DIRECTORIES
            )

            for name in sorted(directories + files):
                item = current / name
                item_level = level + 1
                if item_level > depth:
                    continue
                is_directory = name in directories
                lines.append(
                    "  " * item_level + name + ("/" if is_directory else "")
                )
                if len(lines) >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
            if truncated:
                break

        if truncated:
            lines.append("[truncated]")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def write_file(self, path, content):
        target = self.resolve(path)
        content = "" if content is None else str(content)

        self._backup(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".freenerf.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        except OSError as exc:
            return f"Cannot write file: {exc}"

        return (
            f"Written: {self.relative(target)} "
            f"({len(content)} characters)"
        )

    def edit_file(self, path, old, new, replace_all=False):
        target = self.resolve(path, must_exist=True)

        if not target.is_file():
            return "File does not exist"

        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            return f"Cannot read file: {exc}"

        old = str(old)
        new = "" if new is None else str(new)

        if not old:
            return "The text to replace must not be empty"

        occurrences = content.count(old)

        if occurrences == 0:
            return "Text not found; the file was not modified"

        if occurrences > 1 and not replace_all:
            return (
                f"Text occurs {occurrences} times. Provide a more specific "
                "snippet or set replace_all."
            )

        updated = (
            content.replace(old, new)
            if replace_all
            else content.replace(old, new, 1)
        )

        self._backup(target)
        try:
            target.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return f"Cannot write file: {exc}"

        return (
            f"Edited: {self.relative(target)} "
            f"({occurrences if replace_all else 1} replacement(s))"
        )

    def delete_file(self, path):
        target = self.resolve(path, must_exist=True)

        if target == self.root:
            raise PermissionError("Cannot delete the workspace root")

        relative = target.relative_to(self.root)
        if relative.parts and relative.parts[0] in (".git", ".freenerf"):
            protected = relative.parts[0]
            raise PermissionError(
                f"Cannot delete files inside {protected}/; it is protected "
                "project metadata and recovery data"
            )

        if target.is_dir() and any(target.iterdir()):
            return "Directory is not empty; refusing to delete it"

        self._backup(target)
        try:
            if target.is_dir():
                target.rmdir()
            else:
                target.unlink()
        except OSError as exc:
            return f"Cannot delete: {exc}"

        return f"Deleted: {self.relative(target)}"

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(self, query, scope="source"):
        query = str(query).strip()

        if not query:
            return "Query is empty"

        if scope not in ("source", "project"):
            return "Invalid scope. Use source or project"

        rg = shutil.which("rg")
        if not rg:
            return "ripgrep (rg) is not installed"

        excluded = [
            "!**/.git/**",
            "!**/.venv/**",
            "!**/__pycache__/**",
            "!**/node_modules/**",
            "!**/.pytest_cache/**",
            "!**/backups/**",
            f"!**/{BACKUP_DIR_NAME}/**",
            "!**/*.pyc",
            "!**/*.bak*",
            "!**/*.old",
            "!**/*.orig",
            "!**/*~",
            "!**/*.db",
        ]

        command = [
            rg,
            "--line-number",
            "--no-heading",
            "--color", "never",
            "--fixed-strings",
            "--ignore-case",
            "--hidden",
            "--max-filesize", "200K",
        ]

        for glob in excluded:
            command.extend(["--glob", glob])

        if scope == "source":
            paths = [
                "src",
                "tests",
                "README.md",
                "INSTRUCTIONS.md",
                "pyproject.toml",
                ".gitignore",
            ]
            paths = [path for path in paths if (self.root / path).exists()]
            if not paths:
                return "No source paths found"
            # "--" terminates option parsing so a query like "--version"
            # is treated as a literal pattern.
            command.extend(["--", query, *paths])
        else:
            command.extend(["--", query, "."])

        try:
            result = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )

            if result.returncode == 1:
                return "No results"

            if result.returncode != 0:
                return f"Search error: {result.stderr.strip()[:1000]}"

            lines = result.stdout.splitlines()[:80]
            return "\n".join(lines)[:12000] or "No results"

        except subprocess.TimeoutExpired:
            return "Search timed out after 20 seconds"
        except OSError as exc:
            return f"Search error: {exc}"

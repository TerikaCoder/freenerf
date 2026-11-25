from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


IGNORED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".freenerf",
    "dist", "build", "target", ".next",
}

LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C/C++",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".sh": "Shell",
    ".md": "Markdown",
}

SOURCE_LIMIT = 4000


class ProjectInspector:
    """Detects the project type, package manager and verification commands."""

    def __init__(self, root="."):
        self.root = Path(root).resolve()

    def _file_count(self) -> tuple[int, Counter]:
        counts: Counter = Counter()
        total = 0

        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            total += 1
            counts[LANGUAGES.get(path.suffix.lower(), "Other")] += 1
            if total >= SOURCE_LIMIT:
                break

        return total, counts

    def _package_manager(self) -> str:
        if (self.root / "uv.lock").exists():
            return "uv"
        if (self.root / "poetry.lock").exists():
            return "poetry"
        if (self.root / "pdm.lock").exists():
            return "pdm"
        if (self.root / "pyproject.toml").exists():
            return "pip/pyproject"
        if (self.root / "package-lock.json").exists():
            return "npm"
        if (self.root / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (self.root / "yarn.lock").exists():
            return "yarn"
        if (self.root / "bun.lockb").exists() or (self.root / "bun.lock").exists():
            return "bun"
        if (self.root / "Cargo.toml").exists():
            return "cargo"
        if (self.root / "go.mod").exists():
            return "go modules"
        return ""

    def _package_json(self) -> dict:
        path = self.root / "package.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _python_has(self, name: str) -> bool:
        for filename in ("pyproject.toml", "requirements.txt", "setup.cfg"):
            path = self.root / filename
            if path.is_file():
                try:
                    if name in path.read_text(encoding="utf-8").lower():
                        return True
                except OSError:
                    continue
        return False

    def test_command(self) -> str:
        package = self._package_json()
        scripts = package.get("scripts") or {}
        if isinstance(scripts, dict) and scripts.get("test"):
            manager = self._package_manager() or "npm"
            if manager == "yarn":
                return "yarn test"
            if manager == "pnpm":
                return "pnpm test"
            if manager == "bun":
                return "bun test"
            return "npm test"

        if self._python_has("pytest") or (self.root / "tests").is_dir():
            return "python -m pytest -q"

        if (self.root / "Cargo.toml").exists():
            return "cargo test"

        if (self.root / "go.mod").exists():
            return "go test ./..."

        makefile = self.root / "Makefile"
        if makefile.is_file():
            try:
                if "test:" in makefile.read_text(encoding="utf-8"):
                    return "make test"
            except OSError:
                pass

        return ""

    def lint_command(self) -> str:
        package = self._package_json()
        scripts = package.get("scripts") or {}
        if isinstance(scripts, dict):
            for name in ("lint", "typecheck", "check"):
                if scripts.get(name):
                    manager = self._package_manager() or "npm"
                    if manager == "yarn":
                        return f"yarn {name}"
                    if manager == "pnpm":
                        return f"pnpm {name}"
                    if manager == "bun":
                        return f"bun run {name}"
                    return f"npm run {name}"

        if self._python_has("ruff"):
            return "python -m ruff check ."
        if self._python_has("flake8"):
            return "python -m flake8"
        if (self.root / "Cargo.toml").exists():
            return "cargo clippy"
        if (self.root / "go.mod").exists():
            return "go vet ./..."

        return ""

    def entry_points(self) -> list[str]:
        candidates = [
            "main.py", "app.py", "manage.py", "src/main.py",
            "index.js", "src/index.js", "src/index.ts", "src/main.ts",
            "src/main.rs", "main.go",
        ]
        return [item for item in candidates if (self.root / item).is_file()]

    def detect(self) -> dict:
        total, counts = self._file_count()

        return {
            "root": str(self.root),
            "files": total,
            "languages": [
                {"name": name, "files": count}
                for name, count in counts.most_common(6)
                if name != "Other"
            ],
            "package_manager": self._package_manager(),
            "test_command": self.test_command(),
            "lint_command": self.lint_command(),
            "entry_points": self.entry_points(),
            "git": (self.root / ".git").exists(),
        }

    def summary(self) -> str:
        info = self.detect()

        lines = [
            f"Project root: {info['root']}",
            f"Files scanned: {info['files']}",
        ]

        if info["languages"]:
            languages = ", ".join(
                f"{item['name']} ({item['files']})"
                for item in info["languages"]
            )
            lines.append(f"Languages: {languages}")

        lines.append(f"Package manager: {info['package_manager'] or 'unknown'}")
        lines.append(f"Test command: {info['test_command'] or 'not detected'}")
        lines.append(f"Lint command: {info['lint_command'] or 'not detected'}")
        lines.append(f"Git repository: {'yes' if info['git'] else 'no'}")

        if info["entry_points"]:
            lines.append("Entry points: " + ", ".join(info["entry_points"]))

        return "\n".join(lines)

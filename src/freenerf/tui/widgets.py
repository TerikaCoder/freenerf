"""Reusable, responsive widgets for the Freenerf terminal UI."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from textual.widgets import RichLog, Static

from ..agents.permissions import describe, redact


# Freenerf's signature palette. Surface colors are supplied by Textual themes;
# these colors distinguish conversation roles and execution states.
ACCENT = "#2DD4BF"
BLUE = "#60A5FA"
VIOLET = "#C084FC"
MUTED = "#94A3B8"
WARNING = "#FBBF24"
ERROR = "#FB7185"
SUCCESS = "#4ADE80"

STATUS_STYLES = {
    "completed": SUCCESS,
    "running": BLUE,
    "pending": MUTED,
    "partial": WARNING,
    "awaiting_approval": WARNING,
    "blocked": ERROR,
    "failed": ERROR,
    "cancelled": MUTED,
    "skipped": MUTED,
}

STATUS_MARKERS = {
    "completed": "✓",
    "running": "▶",
    "pending": "○",
    "failed": "✗",
    "skipped": "–",
    "awaiting_approval": "!",
}


def _short(value, limit):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)] + "…"


class BrandHeader(Static):
    """Freenerf wordmark and current provider/model identity."""

    def show(self, workspace, provider, model, permission, compact=False):
        text = Text()
        text.append("✦ FREENERF", style=f"bold {ACCENT}")
        text.append("  /  ", style=f"dim {MUTED}")
        text.append(_short(workspace, 14 if compact else 22), style=f"bold {BLUE}")
        text.append("  ·  ", style=f"dim {MUTED}")
        text.append(_short(provider, 11), style=VIOLET)
        text.append(" / ", style=f"dim {MUTED}")
        text.append(_short(str(model or "").rsplit("/", 1)[-1], 16 if compact else 24))
        if not compact:
            text.append("  ·  ", style=f"dim {MUTED}")
            text.append(str(permission or "normal").upper(), style=WARNING)
        self.update(text)


class ContextBar(Static):
    """Secondary line for the active session and workspace context."""

    def show(self, session_title, session_id, message_count=0, compact=False):
        text = Text()
        text.append("SESSION  ", style=f"bold {ACCENT}")
        text.append(_short(session_title or "New session", 24 if compact else 44), style="bold")
        text.append("  ·  ", style=f"dim {MUTED}")
        text.append(f"{message_count} msgs", style=MUTED)
        if not compact:
            text.append("  ·  ", style=f"dim {MUTED}")
            text.append(str(session_id)[:8], style=f"dim {MUTED}")
        self.update(text)


class StatusBar(Static):
    """One-line activity, permissions and task summary."""

    def show(
        self,
        activity,
        provider,
        model,
        permission,
        session_id,
        workspace,
        elapsed=None,
        busy=False,
        task=None,
        compact=False,
    ):
        text = Text()
        if busy:
            text.append("● ", style=ACCENT)
            text.append(_short(activity, 22 if compact else 34), style=f"bold {ACCENT}")
            if elapsed is not None:
                minutes, seconds = divmod(int(elapsed), 60)
                text.append(f"  {minutes:02d}:{seconds:02d}", style=MUTED)
            text.append("  ·  Esc cancel", style=f"dim {MUTED}")
        else:
            text.append("● ", style=SUCCESS)
            text.append("Ready", style=SUCCESS)
            text.append("  ·  ", style=f"dim {MUTED}")
            text.append(str(permission or "normal").upper(), style=WARNING)

        if task:
            title = _short(task.get("title") or "", 18 if compact else 28)
            text.append("  ·  ", style=f"dim {MUTED}")
            text.append(title, style=BLUE)
            total = int(task.get("step_count") or 0)
            if total:
                text.append(f" {task.get('done_steps', 0)}/{total}", style=MUTED)
        self.update(text)


class WelcomePanel(Static):
    """An intentional empty state instead of an unexplained blank transcript."""

    def show(self, workspace, provider, model, configured=True, compact=False):
        title = Text("Build something real.", style=f"bold {ACCENT}")
        body = Text()
        if compact:
            # A bordered Panel needs several terminal rows even when its text
            # is short. On phones use one inline row so the chat keeps space.
            self.add_class("compact")
            body.append("✦ FREENERF", style=f"bold {ACCENT}")
            body.append("  ·  @file attach  ·  !shell run  ·  /commands", style=f"dim {MUTED}")
            if not configured:
                body.append("  ·  API key missing", style=WARNING)
            self.update(body)
            return

        self.remove_class("compact")
        body.append("Freenerf", style=f"bold {BLUE}")
        body.append(" explores, plans, edits and verifies code inside your workspace.\n", style="")
        body.append("Workspace  ", style=f"bold {MUTED}")
        body.append(_short(workspace, 64), style="")
        body.append("\nProvider   ", style=f"bold {MUTED}")
        body.append(f"{provider} / {model}", style="")
        body.append("\n\n", style="")
        body.append("Try: ", style=f"bold {VIOLET}")
        body.append('"Review this project and make a plan"', style="italic")
        body.append("\n@file.py  attach a project file    !command  run a shell command", style=f"dim {MUTED}")
        if not configured:
            body.append("\n\nAdd a provider API key in Settings to start chatting.", style=WARNING)
        self.update(Panel(body, title=title, border_style=ACCENT, padding=(1, 2), expand=True))


class TaskPanel(Static):
    """Compact execution card with step progress and latest tool event."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._task = None
        self._last_activity = ""

    def record_activity(self, event):
        name = str(event.get("name") or "tool")
        phase = str(event.get("phase") or "")
        if phase == "started":
            self._last_activity = f"▶ {name}"
        elif phase == "waiting_approval":
            self._last_activity = f"⏳ {name} · approval needed"
        elif phase == "finished":
            kind = str(event.get("kind") or "ok")
            marker = "✓" if kind == "ok" else "⛔" if kind == "denied" else "✗"
            self._last_activity = f"{marker} {name}"
        elif phase == "failed":
            self._last_activity = f"✗ {name}"
        if self._task is not None:
            self.show(self._task)

    def show(self, task):
        previous_id = (self._task or {}).get("id")
        self._task = task
        if not task or task.get("id") != previous_id:
            self._last_activity = ""

        if not task:
            self.update(Text("TASK  ·  No active task", style=f"dim {MUTED}"))
            return

        status = str(task.get("status") or "pending")
        style = STATUS_STYLES.get(status, MUTED)
        title = _short(task.get("title") or "Untitled", 70)
        text = Text()
        text.append("TASK  ", style=f"bold {ACCENT}")
        text.append(title, style="bold")
        text.append("  ", style="")
        text.append(status.replace("_", " ").upper(), style=f"bold {style}")

        steps = task.get("steps") or []
        if steps:
            done = sum(1 for step in steps if step.get("status") in ("completed", "skipped"))
            total = len(steps)
            ratio = done / total if total else 0
            filled = int(round(ratio * 12))
            text.append("  [", style=MUTED)
            text.append("━" * filled, style=SUCCESS)
            text.append("─" * (12 - filled), style=f"dim {MUTED}")
            text.append(f"] {done}/{total}", style=MUTED)
            text.append("\n")
            visible_steps = steps[:3]
            for position, step in enumerate(visible_steps, 1):
                step_status = str(step.get("status") or "pending")
                marker = STATUS_MARKERS.get(step_status, "○")
                text.append(f"{marker} {position}. ", style=STATUS_STYLES.get(step_status, MUTED))
                text.append(_short(step.get("title") or "", 64), style="")
                if position < len(visible_steps):
                    text.append("\n")
            if total > 3:
                text.append(f"\n+{total - 3} more steps", style=f"dim {MUTED}")
        else:
            summary = str(task.get("summary") or "").strip()
            if summary:
                text.append("\n" + _short(summary, 140), style=MUTED)

        if self._last_activity:
            text.append("\n↳ ", style=f"dim {MUTED}")
            text.append(_short(self._last_activity, 68), style=MUTED)
        self.update(Panel(text, border_style=style, padding=(0, 1), expand=True))


class ToolActivityPanel(RichLog):
    """Chronological tool activity with results and elapsed time."""

    def record(self, event):
        name = str(event.get("name") or "tool")
        phase = str(event.get("phase") or "")
        args = event.get("args") or {}
        detail = str(event.get("detail") or "")[:88]
        if not detail and isinstance(args, dict) and args:
            detail = redact(describe(name, args))[:88]
        text = Text()

        if phase == "started":
            text.append("▶ ", style=BLUE)
            text.append(name, style="bold")
            if detail:
                text.append(f"  {detail}", style=f"dim {MUTED}")
        elif phase == "waiting_approval":
            text.append("⏳ ", style=WARNING)
            text.append(name, style="bold")
            text.append("  approval required", style=WARNING)
        elif phase == "finished":
            kind = str(event.get("kind") or ("ok" if event.get("ok") else "error"))
            marker, color = {
                "ok": ("✓ ", SUCCESS),
                "denied": ("⛔ ", WARNING),
                "cancelled": ("■ ", MUTED),
            }.get(kind, ("✗ ", ERROR))
            text.append(marker, style=color)
            text.append(name, style="bold")
            if detail:
                text.append(f"  {detail}", style=f"dim {MUTED}")
            elapsed = event.get("elapsed")
            if isinstance(elapsed, (int, float)):
                text.append(f"  {elapsed:.2f}s", style=MUTED)
        elif phase == "failed":
            text.append("✗ ", style=ERROR)
            text.append(name, style="bold")
            text.append("  failed", style=ERROR)
        else:
            text.append(f"· {name}", style=MUTED)
        self.write(text)

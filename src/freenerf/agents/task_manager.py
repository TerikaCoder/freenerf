"""Task lifecycle management for agent runs.

A task is created for every user request and stored in the workspace task
database together with its plan, step results, logs and final summary.
"""

from __future__ import annotations

from .permissions import redact


FINAL_STATUSES = (
    "completed",
    "partial",
    "failed",
    "blocked",
    "cancelled",
    "awaiting_approval",
)

STEP_STATUSES = ("pending", "running", "completed", "failed", "skipped")


class TaskManagerError(ValueError):
    pass


class AgentTaskManager:
    def __init__(self, store, session_id=None, workspace="", on_change=None):
        self.store = store
        self.session_id = session_id
        self.workspace = workspace
        self.on_change = on_change
        self.task_id: str | None = None
        self.finished = False

    # ------------------------------------------------------------------
    def start(self, goal, title=None):
        # A new request supersedes an unfinished task in this session; the old
        # record stays visible in history with a truthful status.
        if self.task_id and not self.finished:
            try:
                self.store.set_status(
                    self.task_id,
                    "blocked",
                    "Superseded by a new request in this session",
                )
            except Exception:
                pass

        title = str(title or "").strip()
        if not title:
            title = " ".join(str(goal).split())[:80] or "Untitled task"

        self.task_id = self.store.create_task(
            title=title,
            description=str(goal),
            session_id=self.session_id,
            workspace=self.workspace,
        )
        self.finished = False
        self.store.set_status(self.task_id, "running")
        self.store.add_log(
            self.task_id,
            f"Task started: {redact(str(goal))[:500]}",
            level="info",
        )
        self._changed()
        return self.snapshot()

    def snapshot(self):
        if not self.task_id:
            return None

        task = self.store.get_task(self.task_id)
        if task is None:
            return None

        steps = task.get("steps") or []
        done = sum(
            1 for step in steps
            if step.get("status") in ("completed", "skipped")
        )
        task["progress"] = int(round(100 * done / len(steps))) if steps else 0
        task["done_steps"] = done
        task["step_count"] = len(steps)
        task["current_step_number"] = (
            int(task.get("current_step") or 0) + 1 if steps else 0
        )
        return task

    def set_plan(self, steps):
        self._require_task()

        normalized = []
        for index, item in enumerate(steps or []):
            if isinstance(item, str):
                normalized.append({
                    "title": item.strip() or f"Step {index + 1}",
                    "description": "",
                })
            elif isinstance(item, dict):
                title = str(item.get("title") or f"Step {index + 1}").strip()
                normalized.append({
                    "title": title or f"Step {index + 1}",
                    "description": str(item.get("description") or ""),
                })
            else:
                raise TaskManagerError(
                    "Each plan step must be text or an object with a title"
                )

        if not normalized:
            raise TaskManagerError("A plan needs at least one step")

        self.store.set_plan(self.task_id, normalized)
        self.store.set_status(self.task_id, "running")
        self.store.add_log(
            self.task_id,
            "Plan: " + " | ".join(step["title"] for step in normalized),
            level="plan",
        )
        self._changed()
        return self.snapshot()

    def update_step(self, step, status, result=None, error=None):
        self._require_task()

        if status not in STEP_STATUSES:
            raise TaskManagerError(
                "Step status must be one of: " + ", ".join(STEP_STATUSES)
            )

        try:
            position = int(step) - 1
        except (TypeError, ValueError) as exc:
            raise TaskManagerError("Step number must be an integer") from exc

        if not self.store.update_step(
            self.task_id,
            position,
            status,
            result=redact(result) if result else None,
            error=redact(error) if error else None,
        ):
            raise TaskManagerError(f"Unknown step number: {step}")

        message = f"Step {step} -> {status}"
        if result:
            message += f": {redact(str(result))[:300]}"
        if error:
            message += f" (error: {redact(str(error))[:300]})"

        self.store.add_log(
            self.task_id,
            message,
            level="error" if status == "failed" else "info",
        )
        self._changed()
        return self.snapshot()

    def log_tool(self, name, summary, ok=True):
        if not self.task_id:
            return
        self.store.add_log(
            self.task_id,
            f"Tool {name}: {redact(str(summary))[:500]}",
            level="tool" if ok else "error",
        )

    def finish(self, status, summary=""):
        self._require_task()

        if status not in FINAL_STATUSES:
            raise TaskManagerError(
                "Final status must be one of: " + ", ".join(FINAL_STATUSES)
            )

        self.store.set_status(
            self.task_id, status, summary=str(summary)[:4000]
        )
        self.store.add_log(
            self.task_id,
            f"Task finished as {status}: {redact(str(summary))[:500]}",
            level="error" if status in ("failed", "blocked") else "info",
        )
        self.finished = True
        self._changed()
        return self.snapshot()

    def finish_implicit(self, summary):
        """Finish a task when the agent answered without calling finish()."""
        task = self.snapshot()
        if task is None or self.finished:
            return task

        steps = task.get("steps") or []

        if steps and any(
            step.get("status") in ("pending", "running") for step in steps
        ):
            status = "partial"
        elif steps and any(
            step.get("status") == "failed" for step in steps
        ):
            status = "partial"
        else:
            status = "completed"

        return self.finish(status, summary)

    def cancel(self, message="Cancelled by user"):
        if not self.task_id or self.finished:
            return self.snapshot()
        return self.finish("cancelled", message)

    def block(self, message):
        if not self.task_id or self.finished:
            return self.snapshot()
        return self.finish("blocked", message)

    def fail(self, message):
        if not self.task_id or self.finished:
            return self.snapshot()
        return self.finish("failed", message)

    def resumable(self, limit=10):
        return self.store.resumable_tasks(
            limit=limit, session_id=self.session_id
        )

    def history(self, limit=20):
        return self.store.list_tasks(
            limit=limit, session_id=self.session_id
        )

    # ------------------------------------------------------------------
    def _require_task(self):
        if not self.task_id:
            raise TaskManagerError("No active task")

    def _changed(self):
        if self.on_change is None:
            return
        try:
            self.on_change(self.snapshot())
        except Exception:
            pass

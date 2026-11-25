from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_data_dir


DATA_DIR = Path(user_data_dir("freenerf"))
DEFAULT_DATABASE = DATA_DIR / "tasks.db"

TASK_STATUSES = {
    "pending",
    "running",
    "awaiting_approval",
    "completed",
    "partial",
    "failed",
    "blocked",
    "cancelled",
}
STEP_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
}
RESUMABLE_STATUSES = (
    "pending",
    "running",
    "awaiting_approval",
    "failed",
    "blocked",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    """Persistent task, step and execution-log storage."""

    def __init__(self, database=DEFAULT_DATABASE):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    plan_json TEXT NOT NULL DEFAULT '[]',
                    current_step INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(tasks)")
            }
            if "summary" not in columns:
                conn.execute(
                    "ALTER TABLE tasks "
                    "ADD COLUMN summary TEXT NOT NULL DEFAULT ''"
                )
            if "workspace" not in columns:
                conn.execute(
                    "ALTER TABLE tasks "
                    "ADD COLUMN workspace TEXT NOT NULL DEFAULT ''"
                )

            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT,
                    error TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                        ON DELETE CASCADE,
                    UNIQUE(task_id, position)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'info',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                        ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_processes (
                    task_id TEXT PRIMARY KEY,
                    process_id TEXT NOT NULL,
                    pid INTEGER,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    exit_code INTEGER,
                    output TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                        ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON tasks(status, updated_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_session
                ON tasks(session_id, updated_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_processes_id
                ON task_processes(process_id)
            """)

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    def create_task(
        self,
        title,
        description="",
        session_id=None,
        workspace="",
    ):
        task_id = uuid.uuid4().hex[:16]
        timestamp = now()

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tasks (
                    id, session_id, title, description, status,
                    workspace, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """, (
                task_id,
                session_id,
                str(title)[:200],
                str(description),
                str(workspace),
                timestamp,
                timestamp,
            ))

        return task_id

    def get_task(self, task_id, session_id=None):
        with self._connect() as conn:
            if session_id is None:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ? AND session_id = ?",
                    (task_id, session_id),
                ).fetchone()

            if row is None:
                return None

            task = dict(row)
            task["plan"] = self._load_plan(task.pop("plan_json"))

            rows = conn.execute("""
                SELECT * FROM task_steps
                WHERE task_id = ?
                ORDER BY position ASC
            """, (task_id,)).fetchall()

            task["steps"] = [dict(item) for item in rows]
            task["logs"] = self.get_logs(task_id)
            return task

    @staticmethod
    def _load_plan(value):
        try:
            plan = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        return plan if isinstance(plan, list) else []

    def list_tasks(self, limit=30, statuses=None, session_id=None):
        limit = max(1, min(int(limit), 200))

        clauses = []
        params: list = []

        if statuses:
            values = [
                status for status in statuses
                if status in TASK_STATUSES
            ]
            if not values:
                return []
            placeholders = ",".join("?" for _ in values)
            clauses.append(f"status IN ({placeholders})")
            params.extend(values)

        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM tasks
                {where}
                ORDER BY updated_at DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["plan"] = self._load_plan(item.pop("plan_json"))
            result.append(item)

        return result

    def set_status(self, task_id, status, summary=None, session_id=None):
        if status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status}")

        ownership = " AND session_id = ?" if session_id is not None else ""
        owner_params = (session_id,) if session_id is not None else ()

        with self._connect() as conn:
            if summary is None:
                cursor = conn.execute("""
                    UPDATE tasks
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                """ + ownership, (status, now(), task_id, *owner_params))
            else:
                cursor = conn.execute("""
                    UPDATE tasks
                    SET status = ?, summary = ?, updated_at = ?
                    WHERE id = ?
                """ + ownership, (
                    status, str(summary), now(), task_id, *owner_params
                ))

        return cursor.rowcount > 0

    def set_plan(self, task_id, plan, session_id=None):
        if not isinstance(plan, list):
            raise TypeError("Plan must be a list")

        with self._connect() as conn:
            if session_id is None:
                exists = conn.execute(
                    "SELECT id FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            else:
                exists = conn.execute(
                    "SELECT id FROM tasks WHERE id = ? AND session_id = ?",
                    (task_id, session_id),
                ).fetchone()

            if not exists:
                return False

            conn.execute(
                "DELETE FROM task_steps WHERE task_id = ?", (task_id,)
            )

            conn.execute("""
                UPDATE tasks
                SET plan_json = ?, current_step = 0, updated_at = ?
                WHERE id = ?
            """, (json.dumps(plan, ensure_ascii=False), now(), task_id))

            timestamp = now()
            for position, item in enumerate(plan):
                if isinstance(item, str):
                    title = item
                    description = ""
                elif isinstance(item, dict):
                    title = str(item.get("title", f"Step {position + 1}"))
                    description = str(item.get("description", ""))
                else:
                    raise TypeError("Each plan item must be text or a dict")

                conn.execute("""
                    INSERT INTO task_steps (
                        task_id, position, title, description, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    task_id, position, title, description, timestamp
                ))

        return True

    def update_step(self, task_id, position, status,
                    result=None, error=None, session_id=None):
        if status not in STEP_STATUSES:
            raise ValueError(f"Invalid step status: {status}")

        timestamp = now()

        with self._connect() as conn:
            if session_id is not None:
                owned = conn.execute(
                    "SELECT id FROM tasks WHERE id = ? AND session_id = ?",
                    (task_id, session_id),
                ).fetchone()
                if owned is None:
                    return False

            row = conn.execute("""
                SELECT status FROM task_steps
                WHERE task_id = ? AND position = ?
            """, (task_id, position)).fetchone()

            if row is None:
                return False

            started_at = timestamp if status == "running" else None
            completed_at = (
                timestamp
                if status in {"completed", "failed", "skipped"}
                else None
            )

            conn.execute("""
                UPDATE task_steps
                SET status = ?,
                    result = COALESCE(?, result),
                    error = COALESCE(?, error),
                    started_at = COALESCE(?, started_at),
                    completed_at = ?
                WHERE task_id = ? AND position = ?
            """, (
                status, result, error, started_at, completed_at,
                task_id, position
            ))

            conn.execute("""
                UPDATE tasks
                SET current_step = ?, updated_at = ?
                WHERE id = ?
            """, (position, timestamp, task_id))

        return True

    def add_log(self, task_id, message, level="info"):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO task_logs (task_id, level, message, created_at)
                VALUES (?, ?, ?, ?)
            """, (task_id, str(level), str(message), now()))

    def get_logs(self, task_id, limit=200):
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT level, message, created_at
                FROM task_logs
                WHERE task_id = ?
                ORDER BY id ASC
                LIMIT ?
            """, (task_id, max(1, int(limit)))).fetchall()

        return [dict(row) for row in rows]

    def delete_task(self, task_id):
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE id = ?", (task_id,)
            )
        return cursor.rowcount > 0

    def resumable_tasks(self, limit=30, session_id=None):
        return self.list_tasks(
            limit=limit,
            statuses=RESUMABLE_STATUSES,
            session_id=session_id,
        )

    def latest_unfinished(self, session_id):
        tasks = self.resumable_tasks(limit=1, session_id=session_id)
        return tasks[0] if tasks else None

    # ------------------------------------------------------------------
    # Background processes
    # ------------------------------------------------------------------
    @staticmethod
    def _owns_task(conn, task_id, session_id):
        if session_id is None:
            row = conn.execute(
                "SELECT id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM tasks WHERE id = ? AND session_id = ?",
                (task_id, session_id),
            ).fetchone()
        return row is not None

    def save_process(self, task_id, process_id, pid, command,
                     session_id=None):
        timestamp = now()

        with self._connect() as conn:
            if not self._owns_task(conn, task_id, session_id):
                return False

            conn.execute("""
                INSERT INTO task_processes (
                    task_id, process_id, pid, command, status,
                    exit_code, output, started_at, finished_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'running', NULL, '', ?, NULL, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    process_id = excluded.process_id,
                    pid = excluded.pid,
                    command = excluded.command,
                    status = 'running',
                    exit_code = NULL,
                    output = '',
                    started_at = excluded.started_at,
                    finished_at = NULL,
                    updated_at = excluded.updated_at
            """, (
                task_id, str(process_id), pid, str(command),
                timestamp, timestamp, timestamp,
            ))

        return True

    def get_process(self, task_id, session_id=None):
        with self._connect() as conn:
            query = """
                SELECT p.* FROM task_processes p
                JOIN tasks t ON t.id = p.task_id
                WHERE p.task_id = ?
            """
            params = [task_id]
            if session_id is not None:
                query += " AND t.session_id = ?"
                params.append(session_id)

            row = conn.execute(query, params).fetchone()
            return dict(row) if row is not None else None

    def get_process_by_id(self, process_id, session_id=None):
        with self._connect() as conn:
            query = """
                SELECT p.* FROM task_processes p
                JOIN tasks t ON t.id = p.task_id
                WHERE p.process_id = ?
            """
            params = [process_id]
            if session_id is not None:
                query += " AND t.session_id = ?"
                params.append(session_id)

            row = conn.execute(query, params).fetchone()
            return dict(row) if row is not None else None

    def update_process(self, task_id, status, exit_code=None, output=None,
                       session_id=None):
        timestamp = now()
        terminal = status not in ("running",)

        with self._connect() as conn:
            if not self._owns_task(conn, task_id, session_id):
                return False

            cursor = conn.execute("""
                UPDATE task_processes
                SET status = ?,
                    exit_code = COALESCE(?, exit_code),
                    output = COALESCE(?, output),
                    finished_at = CASE WHEN ? THEN ? ELSE finished_at END,
                    updated_at = ?
                WHERE task_id = ?
            """, (
                str(status), exit_code, output,
                1 if terminal else 0, timestamp,
                timestamp, task_id,
            ))

        return cursor.rowcount > 0

    def list_processes(self, statuses=None, limit=100, session_id=None):
        limit = max(1, min(int(limit), 500))
        clauses = []
        params: list = []

        if statuses:
            values = [str(status) for status in statuses]
            placeholders = ",".join("?" for _ in values)
            clauses.append(f"p.status IN ({placeholders})")
            params.extend(values)

        if session_id is not None:
            clauses.append("t.session_id = ?")
            params.append(session_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.* FROM task_processes p
                JOIN tasks t ON t.id = p.task_id
                {where}
                ORDER BY p.updated_at DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        return [dict(row) for row in rows]

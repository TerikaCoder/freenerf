from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_data_dir


DATA_DIR = Path(user_data_dir("freenerf"))
DATABASE = DATA_DIR / "sessions.db"

MESSAGE_ROLES = {"system", "user", "assistant", "tool"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """SQLite-backed conversation storage.

    Pass ``workspace.sessions_db`` to keep sessions isolated per workspace.
    The module-level default is retained for backwards compatibility.
    """

    def __init__(self, database: Path | str = DATABASE):
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
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Safe migration: preserve all existing sessions and messages.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sessions)")
            }
            if "is_closed" not in columns:
                conn.execute(
                    "ALTER TABLE sessions "
                    "ADD COLUMN is_closed INTEGER NOT NULL DEFAULT 0"
                )
            if "workspace" not in columns:
                conn.execute(
                    "ALTER TABLE sessions "
                    "ADD COLUMN workspace TEXT NOT NULL DEFAULT ''"
                )

            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id)
                        REFERENCES sessions(id)
                        ON DELETE CASCADE
                )
            """)

            message_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(messages)")
            }
            if "metadata" not in message_columns:
                conn.execute(
                    "ALTER TABLE messages "
                    "ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
                )

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY(session_id)
                        REFERENCES sessions(id)
                        ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_activity_session
                ON tool_activity(session_id, id)
            """)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    def create(self, title: str = "New session", workspace: str = "") -> str:
        session_id = uuid.uuid4().hex[:12]
        timestamp = now()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions
                (id, title, created_at, updated_at, workspace)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, str(title), timestamp, timestamp, str(workspace)),
            )

        return session_id

    def get(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(
        self,
        limit: int = 30,
        include_closed: bool = False,
    ) -> list[dict]:
        with self._connect() as conn:
            query = """
                SELECT id, title, created_at, updated_at, is_closed, workspace
                FROM sessions
            """
            if not include_closed:
                query += " WHERE is_closed = 0"

            query += " ORDER BY updated_at DESC LIMIT ?"

            rows = conn.execute(
                query,
                (max(1, min(int(limit), 200)),),
            ).fetchall()

        return [dict(row) for row in rows]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        query = str(query or "").strip()
        limit = max(1, min(int(limit), 100))

        with self._connect() as conn:
            if not query:
                rows = conn.execute(
                    """
                    SELECT id, title, created_at, updated_at, is_closed
                    FROM sessions
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]

            pattern = f"%{query}%"
            rows = conn.execute(
                """
                SELECT DISTINCT s.id, s.title, s.created_at, s.updated_at,
                       s.is_closed
                FROM sessions AS s
                LEFT JOIN messages AS m ON m.session_id = s.id
                WHERE s.title LIKE ? OR m.content LIKE ?
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (pattern, pattern, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    def close_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET is_closed = 1 WHERE id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    def reopen_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET is_closed = 0 WHERE id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    def rename(self, session_id: str, title: str) -> bool:
        title = " ".join(str(title).split()) or "New session"
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE sessions
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (title[:120], now(), session_id),
            )
        return cursor.rowcount > 0

    def delete(self, session_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        if role not in MESSAGE_ROLES:
            raise ValueError(f"Invalid message role: {role}")

        try:
            metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        except (TypeError, ValueError):
            metadata_json = "{}"

        with self._connect() as conn:
            exists = conn.execute(
                "SELECT id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()

            if not exists:
                raise ValueError("Session does not exist")

            timestamp = now()
            conn.execute(
                """
                INSERT INTO messages
                (session_id, role, content, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, str(content), timestamp, metadata_json),
            )

            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (timestamp, session_id),
            )

    def get_messages(self, session_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        messages = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (TypeError, ValueError):
                item["metadata"] = {}
            messages.append(item)

        return messages

    def clear_messages(self, session_id: str) -> int:
        """Delete a session's transcript and return the number of rows removed."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session_id,),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now(), session_id),
            )
        return int(cursor.rowcount)

    def message_count(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # Tool activity
    # ------------------------------------------------------------------
    def add_activity(
        self,
        session_id: str,
        event: dict,
        limit: int = 160,
    ) -> None:
        """Persist one already-redacted tool activity event for a session."""
        if not isinstance(event, dict):
            raise TypeError("Activity event must be a dictionary")
        try:
            payload = json.dumps(event, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = "{}"
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not exists:
                raise ValueError("Session does not exist")
            conn.execute(
                "INSERT INTO tool_activity (session_id, created_at, event_json) "
                "VALUES (?, ?, ?)",
                (session_id, now(), payload),
            )
            bounded = max(1, min(int(limit), 1000))
            conn.execute(
                """
                DELETE FROM tool_activity
                WHERE session_id = ? AND id NOT IN (
                    SELECT id FROM tool_activity
                    WHERE session_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (session_id, session_id, bounded),
            )

    def get_activity(self, session_id: str, limit: int = 160) -> list[dict]:
        bounded = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_json, created_at FROM tool_activity
                WHERE session_id = ? ORDER BY id DESC LIMIT ?
                """,
                (session_id, bounded),
            ).fetchall()
        events = []
        for row in reversed(rows):
            try:
                event = json.loads(row["event_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(event, dict):
                event["created_at"] = row["created_at"]
                events.append(event)
        return events

    def clear_activity(self, session_id: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tool_activity WHERE session_id = ?",
                (session_id,),
            )
        return int(cursor.rowcount)

    def export_session(self, session_id: str) -> dict:
        session = self.get(session_id)
        if not session:
            raise ValueError("Session does not exist")

        return {
            "session": session,
            "messages": self.get_messages(session_id),
            "activity": self.get_activity(session_id),
        }

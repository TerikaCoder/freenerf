//! SQLite-backed conversation storage.
//!
//! The schema is byte-compatible with the Python stores (`sessions`,
//! `messages`, `tool_activity`), so an existing `.freenerf/sessions.db` can be
//! opened directly. Missing columns are added in place, never recreated.

use std::path::{Path, PathBuf};

use freenerf_core::{now_rfc3339, Error, Result};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const MESSAGE_ROLES: &[&str] = &["system", "user", "assistant", "tool"];
pub const SCHEMA_VERSION: i64 = 2;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: String,
    pub title: String,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default)]
    pub is_closed: i64,
    #[serde(default)]
    pub workspace: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredMessage {
    pub role: String,
    pub content: String,
    pub created_at: String,
    #[serde(default)]
    pub metadata: Value,
}

impl StoredMessage {
    /// Convert to the agent's message type. Tool activity metadata is kept by
    /// the UI, not fed back into the model.
    pub fn to_message(&self) -> freenerf_core::Message {
        freenerf_core::Message {
            role: self.role.clone(),
            content: Some(self.content.clone()),
            tool_calls: Vec::new(),
            tool_call_id: None,
            gemini_parts: None,
        }
    }
}

pub struct SessionStore {
    database: PathBuf,
}

impl SessionStore {
    pub fn open(database: impl AsRef<Path>) -> Result<Self> {
        let database = database.as_ref().to_path_buf();
        if let Some(parent) = database.parent() {
            std::fs::create_dir_all(parent).map_err(|e| {
                Error::database(format!("cannot create session database directory: {e}"))
            })?;
        }
        let store = Self { database };
        store.migrate()?;
        Ok(store)
    }

    pub fn path(&self) -> &Path {
        &self.database
    }

    fn connect(&self) -> Result<Connection> {
        let conn = Connection::open(&self.database)
            .map_err(|e| Error::database(format!("cannot open sessions.db: {e}")))?;
        conn.pragma_update(None, "foreign_keys", "ON")
            .map_err(|e| Error::database(e.to_string()))?;
        conn.busy_timeout(std::time::Duration::from_secs(5))
            .map_err(|e| Error::database(e.to_string()))?;
        Ok(conn)
    }

    fn migrate(&self) -> Result<()> {
        let conn = self.connect()?;

        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            "#,
        )
        .map_err(|e| Error::database(e))?;

        // Legacy databases may lack newer columns; add them without touching
        // existing rows (same migration the Python store performs).
        let columns = table_columns(&conn, "sessions")?;
        if !columns.contains(&"is_closed".to_string()) {
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN is_closed INTEGER NOT NULL DEFAULT 0",
                [],
            )
            .map_err(|e| Error::database(e))?;
        }
        if !columns.contains(&"workspace".to_string()) {
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN workspace TEXT NOT NULL DEFAULT ''",
                [],
            )
            .map_err(|e| Error::database(e))?;
        }

        let message_columns = table_columns(&conn, "messages")?;
        if !message_columns.contains(&"metadata".to_string()) {
            conn.execute(
                "ALTER TABLE messages ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'",
                [],
            )
            .map_err(|e| Error::database(e))?;
        }

        conn.execute_batch(
            r#"
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
            CREATE TABLE IF NOT EXISTS tool_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_json TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_activity_session ON tool_activity(session_id, id);
            "#,
        )
        .map_err(|e| Error::database(e))?;

        conn.pragma_update(None, "user_version", SCHEMA_VERSION)
            .map_err(|e| Error::database(e))?;
        Ok(())
    }

    // ------------------------------------------------------------------
    // Sessions
    // ------------------------------------------------------------------
    pub fn create(&self, title: &str, workspace: &str) -> Result<String> {
        let session_id = uuid::Uuid::new_v4().simple().to_string()[..12].to_string();
        let timestamp = now_rfc3339();
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, workspace) \
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![session_id, title, timestamp, timestamp, workspace],
        )
        .map_err(|e| Error::database(e))?;
        Ok(session_id)
    }

    fn row_to_session(row: &Row<'_>) -> rusqlite::Result<Session> {
        Ok(Session {
            id: row.get("id")?,
            title: row.get("title")?,
            created_at: row.get("created_at")?,
            updated_at: row.get("updated_at")?,
            is_closed: row.get("is_closed").unwrap_or(0),
            workspace: row.get("workspace").unwrap_or_default(),
        })
    }

    pub fn get(&self, session_id: &str) -> Result<Option<Session>> {
        let conn = self.connect()?;
        conn.query_row(
            "SELECT id, title, created_at, updated_at, is_closed, workspace \
             FROM sessions WHERE id = ?1",
            params![session_id],
            Self::row_to_session,
        )
        .optional()
        .map_err(|e| Error::database(e))
    }

    pub fn list(&self, limit: u32, include_closed: bool) -> Result<Vec<Session>> {
        let conn = self.connect()?;
        let limit = limit.clamp(1, 200);
        let mut query = String::from(
            "SELECT id, title, created_at, updated_at, is_closed, workspace FROM sessions",
        );
        if !include_closed {
            query.push_str(" WHERE is_closed = 0");
        }
        query.push_str(" ORDER BY updated_at DESC LIMIT ?1");

        let mut statement = conn.prepare(&query).map_err(|e| Error::database(e))?;
        let rows = statement
            .query_map(params![limit], Self::row_to_session)
            .map_err(|e| Error::database(e))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn search(&self, query: &str, limit: u32) -> Result<Vec<Session>> {
        let query = query.trim();
        let limit = limit.clamp(1, 100);
        let conn = self.connect()?;

        if query.is_empty() {
            let mut statement = conn
                .prepare(
                    "SELECT id, title, created_at, updated_at, is_closed, workspace \
                     FROM sessions ORDER BY updated_at DESC LIMIT ?1",
                )
                .map_err(|e| Error::database(e))?;
            let rows = statement
                .query_map(params![limit], Self::row_to_session)
                .map_err(|e| Error::database(e))?;
            return Ok(rows.filter_map(|r| r.ok()).collect());
        }

        let pattern = format!("%{query}%");
        let mut statement = conn
            .prepare(
                "SELECT DISTINCT s.id, s.title, s.created_at, s.updated_at, s.is_closed, \
                        s.workspace \
                 FROM sessions AS s LEFT JOIN messages AS m ON m.session_id = s.id \
                 WHERE s.title LIKE ?1 OR m.content LIKE ?1 \
                 ORDER BY s.updated_at DESC LIMIT ?2",
            )
            .map_err(|e| Error::database(e))?;
        let rows = statement
            .query_map(params![pattern, limit], Self::row_to_session)
            .map_err(|e| Error::database(e))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn close_session(&self, session_id: &str) -> Result<bool> {
        let conn = self.connect()?;
        let changed = conn
            .execute(
                "UPDATE sessions SET is_closed = 1 WHERE id = ?1",
                params![session_id],
            )
            .map_err(|e| Error::database(e))?;
        Ok(changed > 0)
    }

    pub fn reopen_session(&self, session_id: &str) -> Result<bool> {
        let conn = self.connect()?;
        let changed = conn
            .execute(
                "UPDATE sessions SET is_closed = 0 WHERE id = ?1",
                params![session_id],
            )
            .map_err(|e| Error::database(e))?;
        Ok(changed > 0)
    }

    pub fn rename(&self, session_id: &str, title: &str) -> Result<bool> {
        let title = title.split_whitespace().collect::<Vec<_>>().join(" ");
        let title = if title.is_empty() { "New session".to_string() } else { title };
        let title: String = title.chars().take(120).collect();
        let conn = self.connect()?;
        let changed = conn
            .execute(
                "UPDATE sessions SET title = ?1, updated_at = ?2 WHERE id = ?3",
                params![title, now_rfc3339(), session_id],
            )
            .map_err(|e| Error::database(e))?;
        Ok(changed > 0)
    }

    pub fn delete(&self, session_id: &str) -> Result<bool> {
        let conn = self.connect()?;
        let changed = conn
            .execute("DELETE FROM sessions WHERE id = ?1", params![session_id])
            .map_err(|e| Error::database(e))?;
        Ok(changed > 0)
    }

    // ------------------------------------------------------------------
    // Messages
    // ------------------------------------------------------------------
    pub fn add_message(
        &self,
        session_id: &str,
        role: &str,
        content: &str,
        metadata: Option<&Value>,
    ) -> Result<()> {
        if !MESSAGE_ROLES.contains(&role) {
            return Err(Error::Other(format!("Invalid message role: {role}")));
        }

        let metadata_json = metadata
            .map(|value| value.to_string())
            .unwrap_or_else(|| "{}".into());

        let conn = self.connect()?;
        let exists: Option<String> = conn
            .query_row(
                "SELECT id FROM sessions WHERE id = ?1",
                params![session_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| Error::database(e))?;
        if exists.is_none() {
            return Err(Error::Other("Session does not exist".into()));
        }

        let timestamp = now_rfc3339();
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, metadata) \
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![session_id, role, content, timestamp, metadata_json],
        )
        .map_err(|e| Error::database(e))?;

        conn.execute(
            "UPDATE sessions SET updated_at = ?1 WHERE id = ?2",
            params![timestamp, session_id],
        )
        .map_err(|e| Error::database(e))?;

        Ok(())
    }

    pub fn get_messages(&self, session_id: &str) -> Result<Vec<StoredMessage>> {
        let conn = self.connect()?;
        let mut statement = conn
            .prepare(
                "SELECT role, content, created_at, metadata FROM messages \
                 WHERE session_id = ?1 ORDER BY id ASC",
            )
            .map_err(|e| Error::database(e))?;

        let rows = statement
            .query_map(params![session_id], |row| {
                let metadata: String = row.get("metadata")?;
                Ok(StoredMessage {
                    role: row.get("role")?,
                    content: row.get("content")?,
                    created_at: row.get("created_at")?,
                    metadata: serde_json::from_str(&metadata).unwrap_or(Value::Object(Default::default())),
                })
            })
            .map_err(|e| Error::database(e))?;

        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn clear_messages(&self, session_id: &str) -> Result<usize> {
        let conn = self.connect()?;
        let removed = conn
            .execute(
                "DELETE FROM messages WHERE session_id = ?1",
                params![session_id],
            )
            .map_err(|e| Error::database(e))?;
        conn.execute(
            "UPDATE sessions SET updated_at = ?1 WHERE id = ?2",
            params![now_rfc3339(), session_id],
        )
        .map_err(|e| Error::database(e))?;
        Ok(removed)
    }

    pub fn message_count(&self, session_id: &str) -> Result<u64> {
        let conn = self.connect()?;
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?1",
                params![session_id],
                |row| row.get(0),
            )
            .map_err(|e| Error::database(e))?;
        Ok(count.max(0) as u64)
    }

    // ------------------------------------------------------------------
    // Tool activity
    // ------------------------------------------------------------------
    pub fn add_activity(&self, session_id: &str, event: &Value, limit: u32) -> Result<()> {
        let payload = event.to_string();
        let conn = self.connect()?;
        let exists: Option<String> = conn
            .query_row(
                "SELECT id FROM sessions WHERE id = ?1",
                params![session_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| Error::database(e))?;
        if exists.is_none() {
            return Err(Error::Other("Session does not exist".into()));
        }

        conn.execute(
            "INSERT INTO tool_activity (session_id, created_at, event_json) VALUES (?1, ?2, ?3)",
            params![session_id, now_rfc3339(), payload],
        )
        .map_err(|e| Error::database(e))?;

        let bounded = limit.clamp(1, 1000);
        conn.execute(
            "DELETE FROM tool_activity WHERE session_id = ?1 AND id NOT IN (\
                SELECT id FROM tool_activity WHERE session_id = ?1 ORDER BY id DESC LIMIT ?2)",
            params![session_id, bounded],
        )
        .map_err(|e| Error::database(e))?;
        Ok(())
    }

    pub fn get_activity(&self, session_id: &str, limit: u32) -> Result<Vec<Value>> {
        let conn = self.connect()?;
        let mut statement = conn
            .prepare(
                "SELECT event_json, created_at FROM tool_activity \
                 WHERE session_id = ?1 ORDER BY id DESC LIMIT ?2",
            )
            .map_err(|e| Error::database(e))?;
        let bounded = limit.clamp(1, 1000);
        let rows = statement
            .query_map(params![session_id, bounded], |row| {
                let payload: String = row.get(0)?;
                let created: String = row.get(1)?;
                Ok((payload, created))
            })
            .map_err(|e| Error::database(e))?;

        let mut events: Vec<Value> = Vec::new();
        for (payload, created) in rows.filter_map(|r| r.ok()) {
            if let Ok(mut value) = serde_json::from_str::<Value>(&payload) {
                if let Some(object) = value.as_object_mut() {
                    object.insert("created_at".into(), Value::String(created));
                }
                events.push(value);
            }
        }
        events.reverse();
        Ok(events)
    }

    pub fn clear_activity(&self, session_id: &str) -> Result<usize> {
        let conn = self.connect()?;
        let removed = conn
            .execute(
                "DELETE FROM tool_activity WHERE session_id = ?1",
                params![session_id],
            )
            .map_err(|e| Error::database(e))?;
        Ok(removed)
    }

    pub fn export_session(&self, session_id: &str) -> Result<Value> {
        let session = self
            .get(session_id)?
            .ok_or_else(|| Error::Other("Session does not exist".into()))?;
        Ok(serde_json::json!({
            "session": session,
            "messages": self.get_messages(session_id)?,
            "activity": self.get_activity(session_id, 200)?,
        }))
    }
}

fn table_columns(conn: &Connection, table: &str) -> Result<Vec<String>> {
    let mut statement = conn
        .prepare(&format!("PRAGMA table_info({table})"))
        .map_err(|e| Error::database(e))?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>("name"))
        .map_err(|e| Error::database(e))?;
    Ok(rows.filter_map(|r| r.ok()).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn store() -> (tempfile::TempDir, SessionStore) {
        let dir = tempfile::tempdir().unwrap();
        let store = SessionStore::open(dir.path().join("sessions.db")).unwrap();
        (dir, store)
    }

    #[test]
    fn creates_lists_renames_and_deletes_sessions() {
        let (_dir, store) = store();
        let id = store.create("First session", "/tmp/ws").unwrap();
        assert_eq!(store.list(30, false).unwrap().len(), 1);
        assert!(store.rename(&id, "  Renamed   session ").unwrap());
        assert_eq!(store.get(&id).unwrap().unwrap().title, "Renamed session");
        assert!(store.close_session(&id).unwrap());
        assert!(store.list(30, false).unwrap().is_empty());
        assert_eq!(store.list(30, true).unwrap().len(), 1);
        assert!(store.reopen_session(&id).unwrap());
        assert!(store.delete(&id).unwrap());
        assert!(store.get(&id).unwrap().is_none());
    }

    #[test]
    fn stores_messages_with_metadata_and_searches_them() {
        let (_dir, store) = store();
        let id = store.create("Chat", "").unwrap();
        store
            .add_message(&id, "user", "how do I parse this?", Some(&serde_json::json!({"x": 1})))
            .unwrap();
        store.add_message(&id, "assistant", "use serde", None).unwrap();

        let messages = store.get_messages(&id).unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].metadata["x"], 1);
        assert_eq!(messages[0].to_message().text(), "how do I parse this?");
        assert_eq!(store.message_count(&id).unwrap(), 2);

        let found = store.search("serde", 10).unwrap();
        assert_eq!(found[0].id, id);

        assert_eq!(store.clear_messages(&id).unwrap(), 2);
        assert_eq!(store.message_count(&id).unwrap(), 0);
    }

    #[test]
    fn rejects_unknown_roles_and_sessions() {
        let (_dir, store) = store();
        let id = store.create("Chat", "").unwrap();
        assert!(store.add_message(&id, "hacker", "x", None).is_err());
        assert!(store.add_message("missing", "user", "x", None).is_err());
    }

    #[test]
    fn activity_is_bounded_and_returned_oldest_first() {
        let (_dir, store) = store();
        let id = store.create("Chat", "").unwrap();
        for index in 0..5 {
            store
                .add_activity(&id, &serde_json::json!({"index": index}), 3)
                .unwrap();
        }
        let events = store.get_activity(&id, 10).unwrap();
        assert_eq!(events.len(), 3);
        assert_eq!(events[0]["index"], 2);
        assert_eq!(events[2]["index"], 4);
        assert!(events[0]["created_at"].is_string());
        assert_eq!(store.clear_activity(&id).unwrap(), 3);
    }

    #[test]
    fn migrates_legacy_python_schema_without_data_loss() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sessions.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, \
                 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);\
                 CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, \
                 session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, \
                 created_at TEXT NOT NULL);\
                 INSERT INTO sessions VALUES ('legacy1', 'Old chat', '2024-01-01', '2024-01-02');\
                 INSERT INTO messages (session_id, role, content, created_at) \
                 VALUES ('legacy1', 'user', 'kept', '2024-01-01');",
            )
            .unwrap();
        }

        let store = SessionStore::open(&path).unwrap();
        let session = store.get("legacy1").unwrap().unwrap();
        assert_eq!(session.title, "Old chat");
        assert_eq!(session.is_closed, 0);
        assert_eq!(store.get_messages("legacy1").unwrap()[0].content, "kept");

        store.close_session("legacy1").unwrap();
        assert_eq!(store.list(10, false).unwrap().len(), 0);
    }

    #[test]
    fn export_contains_session_messages_and_activity() {
        let (_dir, store) = store();
        let id = store.create("Export", "").unwrap();
        store.add_message(&id, "user", "hi", None).unwrap();
        store.add_activity(&id, &serde_json::json!({"phase": "started"}), 10).unwrap();
        let exported = store.export_session(&id).unwrap();
        assert_eq!(exported["session"]["id"], id);
        assert_eq!(exported["messages"].as_array().unwrap().len(), 1);
        assert_eq!(exported["activity"].as_array().unwrap().len(), 1);
    }
}

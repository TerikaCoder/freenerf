//! SQLite task, step, log and background-process storage.
//!
//! Tables and columns match `agents/task_store.py` so existing
//! `.freenerf/tasks.db` files can be read without conversion.

use std::path::{Path, PathBuf};

use freenerf_core::{now_rfc3339, Error, Result};
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde_json::{json, Value};

pub const TASK_STATUSES: &[&str] = &[
    "pending",
    "running",
    "awaiting_approval",
    "completed",
    "partial",
    "failed",
    "blocked",
    "cancelled",
];

pub const STEP_STATUSES: &[&str] = &["pending", "running", "completed", "failed", "skipped"];

pub const RESUMABLE_STATUSES: &[&str] =
    &["pending", "running", "awaiting_approval", "failed", "blocked"];

#[derive(Debug, Clone)]
pub struct TaskStore {
    database: PathBuf,
}

impl TaskStore {
    pub fn open(database: impl AsRef<Path>) -> Result<Self> {
        let database = database.as_ref().to_path_buf();
        if let Some(parent) = database.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::database(format!("cannot create task database dir: {e}")))?;
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
            .map_err(|e| Error::database(format!("cannot open tasks.db: {e}")))?;
        conn.pragma_update(None, "foreign_keys", "ON")
            .map_err(|e| Error::database(e))?;
        conn.busy_timeout(std::time::Duration::from_secs(5))
            .map_err(|e| Error::database(e))?;
        Ok(conn)
    }

    fn migrate(&self) -> Result<()> {
        let conn = self.connect()?;
        conn.execute_batch(
            r#"
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
            );
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
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                UNIQUE(task_id, position)
            );
            CREATE TABLE IF NOT EXISTS task_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
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
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_task_processes_id ON task_processes(process_id);
            "#,
        )
        .map_err(|e| Error::database(e))?;

        // Columns added by later Python versions.
        let columns = table_columns(&conn, "tasks")?;
        if !columns.contains(&"summary".to_string()) {
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
                [],
            )
            .map_err(|e| Error::database(e))?;
        }
        if !columns.contains(&"workspace".to_string()) {
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN workspace TEXT NOT NULL DEFAULT ''",
                [],
            )
            .map_err(|e| Error::database(e))?;
        }
        conn.pragma_update(None, "user_version", 2i64)
            .map_err(|e| Error::database(e))?;
        Ok(())
    }

    // ------------------------------------------------------------------
    // Tasks
    // ------------------------------------------------------------------
    pub fn create_task(
        &self,
        title: &str,
        description: &str,
        session_id: Option<&str>,
        workspace: &str,
    ) -> Result<String> {
        let task_id = uuid::Uuid::new_v4().simple().to_string()[..16].to_string();
        let timestamp = now_rfc3339();
        let title: String = title.chars().take(200).collect();
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO tasks (id, session_id, title, description, status, workspace, \
             created_at, updated_at) VALUES (?1, ?2, ?3, ?4, 'pending', ?5, ?6, ?6)",
            params![task_id, session_id, title, description, workspace, timestamp],
        )
        .map_err(|e| Error::database(e))?;
        Ok(task_id)
    }

    pub fn get_task(&self, task_id: &str, session_id: Option<&str>) -> Result<Option<Value>> {
        let conn = self.connect()?;
        let session_filter = session_id.map(|_| " AND session_id = ?2").unwrap_or("");
        let query = format!("SELECT * FROM tasks WHERE id = ?1{session_filter}");

        let task = if let Some(session) = session_id {
            conn.query_row(&query, params![task_id, session], task_from_row)
                .optional()
        } else {
            conn.query_row(&query, params![task_id], task_from_row)
                .optional()
        }
        .map_err(|e| Error::database(e))?;

        let Some(mut task) = task else {
            return Ok(None);
        };

        let mut statement = conn
            .prepare(
                "SELECT * FROM task_steps WHERE task_id = ?1 ORDER BY position ASC",
            )
            .map_err(|e| Error::database(e))?;
        let steps = statement
            .query_map(params![task_id], |row| {
                Ok(json!({
                    "id": row.get::<_, i64>("id")?,
                    "task_id": row.get::<_, String>("task_id")?,
                    "position": row.get::<_, i64>("position")?,
                    "title": row.get::<_, String>("title")?,
                    "description": row.get::<_, String>("description")?,
                    "status": row.get::<_, String>("status")?,
                    "result": row.get::<_, Option<String>>("result")?,
                    "error": row.get::<_, Option<String>>("error")?,
                    "started_at": row.get::<_, Option<String>>("started_at")?,
                    "completed_at": row.get::<_, Option<String>>("completed_at")?,
                    "created_at": row.get::<_, String>("created_at")?,
                }))
            })
            .map_err(|e| Error::database(e))?
            .filter_map(|r| r.ok())
            .collect::<Vec<Value>>();

        task["steps"] = Value::Array(steps);
        task["logs"] = serde_json::to_value(self.get_logs(task_id, 200)?).unwrap_or(Value::Null);
        Ok(Some(task))
    }

    pub fn list_tasks(
        &self,
        limit: u32,
        statuses: Option<&[&str]>,
        session_id: Option<&str>,
    ) -> Result<Vec<Value>> {
        let limit = limit.clamp(1, 200);
        let mut clauses: Vec<String> = Vec::new();
        let mut values: Vec<String> = Vec::new();

        if let Some(statuses) = statuses {
            let valid: Vec<&str> = statuses
                .iter()
                .copied()
                .filter(|status| TASK_STATUSES.contains(status))
                .collect();
            if valid.is_empty() {
                return Ok(Vec::new());
            }
            let placeholders = (0..valid.len())
                .map(|index| format!("?{}", values.len() + index + 1))
                .collect::<Vec<_>>()
                .join(",");
            clauses.push(format!("status IN ({placeholders})"));
            values.extend(valid.iter().map(|s| (*s).to_string()));
        }

        if let Some(session) = session_id {
            values.push(session.to_string());
            clauses.push(format!("session_id = ?{}", values.len()));
        }

        let where_clause = if clauses.is_empty() {
            String::new()
        } else {
            format!("WHERE {}", clauses.join(" AND "))
        };

        values.push(limit.to_string());
        let query = format!(
            "SELECT * FROM tasks {where_clause} ORDER BY updated_at DESC LIMIT ?{}",
            values.len()
        );

        let conn = self.connect()?;
        let mut statement = conn.prepare(&query).map_err(|e| Error::database(e))?;
        let rows = statement
            .query_map(params_from_iter(values.iter()), task_from_row)
            .map_err(|e| Error::database(e))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn set_status(
        &self,
        task_id: &str,
        status: &str,
        summary: Option<&str>,
        session_id: Option<&str>,
    ) -> Result<bool> {
        if !TASK_STATUSES.contains(&status) {
            return Err(Error::Other(format!("Invalid task status: {status}")));
        }

        let ownership = if session_id.is_some() {
            " AND session_id = ?"
        } else {
            ""
        };
        let conn = self.connect()?;
        let changed = match summary {
            Some(summary) => {
                let query = format!(
                    "UPDATE tasks SET status = ?1, summary = ?2, updated_at = ?3 \
                     WHERE id = ?4{ownership}"
                );
                if let Some(session) = session_id {
                    conn.execute(&query, params![status, summary, now_rfc3339(), task_id, session])
                } else {
                    conn.execute(&query, params![status, summary, now_rfc3339(), task_id])
                }
            }
            None => {
                let query = format!(
                    "UPDATE tasks SET status = ?1, updated_at = ?2 WHERE id = ?3{ownership}"
                );
                if let Some(session) = session_id {
                    conn.execute(&query, params![status, now_rfc3339(), task_id, session])
                } else {
                    conn.execute(&query, params![status, now_rfc3339(), task_id])
                }
            }
        }
        .map_err(|e| Error::database(e))?;
        Ok(changed > 0)
    }

    pub fn set_plan(&self, task_id: &str, plan: &[Value], session_id: Option<&str>) -> Result<bool> {
        let conn = self.connect()?;
        let exists: Option<String> = if let Some(session) = session_id {
            conn.query_row(
                "SELECT id FROM tasks WHERE id = ?1 AND session_id = ?2",
                params![task_id, session],
                |row| row.get(0),
            )
            .optional()
        } else {
            conn.query_row("SELECT id FROM tasks WHERE id = ?1", params![task_id], |row| {
                row.get(0)
            })
            .optional()
        }
        .map_err(|e| Error::database(e))?;

        if exists.is_none() {
            return Ok(false);
        }

        conn.execute("DELETE FROM task_steps WHERE task_id = ?1", params![task_id])
            .map_err(|e| Error::database(e))?;
        conn.execute(
            "UPDATE tasks SET plan_json = ?1, current_step = 0, updated_at = ?2 WHERE id = ?3",
            params![
                serde_json::to_string(plan).unwrap_or_else(|_| "[]".into()),
                now_rfc3339(),
                task_id
            ],
        )
        .map_err(|e| Error::database(e))?;

        let timestamp = now_rfc3339();
        for (position, item) in plan.iter().enumerate() {
            let (title, description) = match item {
                Value::String(text) => (text.clone(), String::new()),
                Value::Object(_) => (
                    item.get("title")
                        .and_then(|v| v.as_str())
                        .unwrap_or("Step")
                        .to_string(),
                    item.get("description")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                ),
                _ => {
                    return Err(Error::Other(
                        "Each plan item must be text or an object with a title".into(),
                    ))
                }
            };
            conn.execute(
                "INSERT INTO task_steps (task_id, position, title, description, created_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![task_id, position as i64, title, description, timestamp],
            )
            .map_err(|e| Error::database(e))?;
        }

        Ok(true)
    }

    pub fn update_step(
        &self,
        task_id: &str,
        position: i64,
        status: &str,
        result: Option<&str>,
        error: Option<&str>,
        session_id: Option<&str>,
    ) -> Result<bool> {
        if !STEP_STATUSES.contains(&status) {
            return Err(Error::Other(format!("Invalid step status: {status}")));
        }

        let conn = self.connect()?;
        if let Some(session) = session_id {
            let owned: Option<String> = conn
                .query_row(
                    "SELECT id FROM tasks WHERE id = ?1 AND session_id = ?2",
                    params![task_id, session],
                    |row| row.get(0),
                )
                .optional()
                .map_err(|e| Error::database(e))?;
            if owned.is_none() {
                return Ok(false);
            }
        }

        let existing: Option<String> = conn
            .query_row(
                "SELECT status FROM task_steps WHERE task_id = ?1 AND position = ?2",
                params![task_id, position],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| Error::database(e))?;
        if existing.is_none() {
            return Ok(false);
        }

        let timestamp = now_rfc3339();
        let started_at = if status == "running" {
            Some(timestamp.clone())
        } else {
            None
        };
        let completed_at = if matches!(status, "completed" | "failed" | "skipped") {
            Some(timestamp.clone())
        } else {
            None
        };

        conn.execute(
            "UPDATE task_steps SET status = ?1, result = COALESCE(?2, result), \
             error = COALESCE(?3, error), started_at = COALESCE(?4, started_at), \
             completed_at = ?5 WHERE task_id = ?6 AND position = ?7",
            params![status, result, error, started_at, completed_at, task_id, position],
        )
        .map_err(|e| Error::database(e))?;

        conn.execute(
            "UPDATE tasks SET current_step = ?1, updated_at = ?2 WHERE id = ?3",
            params![position, timestamp, task_id],
        )
        .map_err(|e| Error::database(e))?;

        Ok(true)
    }

    pub fn add_log(&self, task_id: &str, message: &str, level: &str) -> Result<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO task_logs (task_id, level, message, created_at) VALUES (?1, ?2, ?3, ?4)",
            params![task_id, level, message, now_rfc3339()],
        )
        .map_err(|e| Error::database(e))?;
        Ok(())
    }

    pub fn get_logs(&self, task_id: &str, limit: u32) -> Result<Vec<Value>> {
        let conn = self.connect()?;
        let mut statement = conn
            .prepare(
                "SELECT level, message, created_at FROM task_logs \
                 WHERE task_id = ?1 ORDER BY id ASC LIMIT ?2",
            )
            .map_err(|e| Error::database(e))?;
        let rows = statement
            .query_map(params![task_id, limit.max(1)], |row| {
                Ok(json!({
                    "level": row.get::<_, String>("level")?,
                    "message": row.get::<_, String>("message")?,
                    "created_at": row.get::<_, String>("created_at")?,
                }))
            })
            .map_err(|e| Error::database(e))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn delete_task(&self, task_id: &str) -> Result<bool> {
        let conn = self.connect()?;
        let changed = conn
            .execute("DELETE FROM tasks WHERE id = ?1", params![task_id])
            .map_err(|e| Error::database(e))?;
        Ok(changed > 0)
    }

    pub fn resumable_tasks(&self, limit: u32, session_id: Option<&str>) -> Result<Vec<Value>> {
        self.list_tasks(limit, Some(RESUMABLE_STATUSES), session_id)
    }

    pub fn latest_unfinished(&self, session_id: &str) -> Result<Option<Value>> {
        Ok(self
            .resumable_tasks(1, Some(session_id))?
            .into_iter()
            .next())
    }

    // ------------------------------------------------------------------
    // Background processes
    // ------------------------------------------------------------------
    fn owns_task(&self, conn: &Connection, task_id: &str, session_id: Option<&str>) -> Result<bool> {
        let exists: Option<String> = if let Some(session) = session_id {
            conn.query_row(
                "SELECT id FROM tasks WHERE id = ?1 AND session_id = ?2",
                params![task_id, session],
                |row| row.get(0),
            )
            .optional()
        } else {
            conn.query_row("SELECT id FROM tasks WHERE id = ?1", params![task_id], |row| {
                row.get(0)
            })
            .optional()
        }
        .map_err(|e| Error::database(e))?;
        Ok(exists.is_some())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn save_process(
        &self,
        task_id: &str,
        process_id: &str,
        pid: Option<u32>,
        command: &str,
        session_id: Option<&str>,
    ) -> Result<bool> {
        let timestamp = now_rfc3339();
        let conn = self.connect()?;
        if !self.owns_task(&conn, task_id, session_id)? {
            return Ok(false);
        }

        conn.execute(
            "INSERT INTO task_processes (task_id, process_id, pid, command, status, exit_code, \
             output, started_at, finished_at, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, 'running', NULL, '', ?5, NULL, ?5, ?5) \
             ON CONFLICT(task_id) DO UPDATE SET process_id = excluded.process_id, \
             pid = excluded.pid, command = excluded.command, status = 'running', \
             exit_code = NULL, output = '', started_at = excluded.started_at, \
             finished_at = NULL, updated_at = excluded.updated_at",
            params![task_id, process_id, pid, command, timestamp],
        )
        .map_err(|e| Error::database(e))?;
        Ok(true)
    }

    pub fn get_process(
        &self,
        task_id: &str,
        session_id: Option<&str>,
    ) -> Result<Option<Value>> {
        let conn = self.connect()?;
        let query = if session_id.is_some() {
            "SELECT p.* FROM task_processes p JOIN tasks t ON t.id = p.task_id \
             WHERE p.task_id = ?1 AND t.session_id = ?2"
        } else {
            "SELECT p.* FROM task_processes p JOIN tasks t ON t.id = p.task_id \
             WHERE p.task_id = ?1"
        };
        let process = if let Some(session) = session_id {
            conn.query_row(query, params![task_id, session], process_from_row)
                .optional()
        } else {
            conn.query_row(query, params![task_id], process_from_row)
                .optional()
        }
        .map_err(|e| Error::database(e))?;
        Ok(process)
    }

    pub fn get_process_by_id(
        &self,
        process_id: &str,
        session_id: Option<&str>,
    ) -> Result<Option<Value>> {
        let conn = self.connect()?;
        let query = if session_id.is_some() {
            "SELECT p.* FROM task_processes p JOIN tasks t ON t.id = p.task_id \
             WHERE p.process_id = ?1 AND t.session_id = ?2"
        } else {
            "SELECT p.* FROM task_processes p JOIN tasks t ON t.id = p.task_id \
             WHERE p.process_id = ?1"
        };
        let process = if let Some(session) = session_id {
            conn.query_row(query, params![process_id, session], process_from_row)
                .optional()
        } else {
            conn.query_row(query, params![process_id], process_from_row)
                .optional()
        }
        .map_err(|e| Error::database(e))?;
        Ok(process)
    }

    pub fn update_process(
        &self,
        task_id: &str,
        status: &str,
        exit_code: Option<i64>,
        output: Option<&str>,
        session_id: Option<&str>,
    ) -> Result<bool> {
        let timestamp = now_rfc3339();
        let terminal = status != "running";
        let conn = self.connect()?;
        if !self.owns_task(&conn, task_id, session_id)? {
            return Ok(false);
        }
        let changed = conn
            .execute(
                "UPDATE task_processes SET status = ?1, exit_code = COALESCE(?2, exit_code), \
                 output = COALESCE(?3, output), \
                 finished_at = CASE WHEN ?4 THEN ?5 ELSE finished_at END, updated_at = ?5 \
                 WHERE task_id = ?6",
                params![status, exit_code, output, terminal as i64, timestamp, task_id],
            )
            .map_err(|e| Error::database(e))?;
        Ok(changed > 0)
    }

    pub fn list_processes(
        &self,
        statuses: Option<&[&str]>,
        limit: u32,
        session_id: Option<&str>,
    ) -> Result<Vec<Value>> {
        let limit = limit.clamp(1, 500);
        let mut clauses: Vec<String> = Vec::new();
        let mut values: Vec<String> = Vec::new();

        if let Some(statuses) = statuses {
            if !statuses.is_empty() {
                let placeholders = (0..statuses.len())
                    .map(|index| format!("?{}", values.len() + index + 1))
                    .collect::<Vec<_>>()
                    .join(",");
                clauses.push(format!("p.status IN ({placeholders})"));
                values.extend(statuses.iter().map(|s| (*s).to_string()));
            }
        }
        if let Some(session) = session_id {
            values.push(session.to_string());
            clauses.push(format!("t.session_id = ?{}", values.len()));
        }

        let where_clause = if clauses.is_empty() {
            String::new()
        } else {
            format!("WHERE {}", clauses.join(" AND "))
        };
        values.push(limit.to_string());

        let query = format!(
            "SELECT p.* FROM task_processes p JOIN tasks t ON t.id = p.task_id \
             {where_clause} ORDER BY p.updated_at DESC LIMIT ?{}",
            values.len()
        );

        let conn = self.connect()?;
        let mut statement = conn.prepare(&query).map_err(|e| Error::database(e))?;
        let rows = statement
            .query_map(params_from_iter(values.iter()), process_from_row)
            .map_err(|e| Error::database(e))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }
}

fn task_from_row(row: &Row<'_>) -> rusqlite::Result<Value> {
    let plan_json: String = row.get("plan_json")?;
    let plan: Value = serde_json::from_str(&plan_json).unwrap_or_else(|_| json!([]));
    let workspace: String = row.get("workspace").unwrap_or_default();
    Ok(json!({
        "id": row.get::<_, String>("id")?,
        "session_id": row.get::<_, Option<String>>("session_id")?,
        "title": row.get::<_, String>("title")?,
        "description": row.get::<_, String>("description")?,
        "status": row.get::<_, String>("status")?,
        "plan": plan,
        "current_step": row.get::<_, i64>("current_step")?,
        "summary": row.get::<_, String>("summary").unwrap_or_default(),
        "workspace": workspace,
        "created_at": row.get::<_, String>("created_at")?,
        "updated_at": row.get::<_, String>("updated_at")?,
    }))
}

fn process_from_row(row: &Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "task_id": row.get::<_, String>("task_id")?,
        "process_id": row.get::<_, String>("process_id")?,
        "pid": row.get::<_, Option<i64>>("pid")?,
        "command": row.get::<_, String>("command")?,
        "status": row.get::<_, String>("status")?,
        "exit_code": row.get::<_, Option<i64>>("exit_code")?,
        "output": row.get::<_, String>("output").unwrap_or_default(),
        "started_at": row.get::<_, String>("started_at")?,
        "finished_at": row.get::<_, Option<String>>("finished_at")?,
        "created_at": row.get::<_, String>("created_at")?,
        "updated_at": row.get::<_, String>("updated_at")?,
    }))
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

    fn store() -> (tempfile::TempDir, TaskStore) {
        let dir = tempfile::tempdir().unwrap();
        let store = TaskStore::open(dir.path().join("tasks.db")).unwrap();
        (dir, store)
    }

    #[test]
    fn creates_tasks_with_plans_and_progress() {
        let (_dir, store) = store();
        let id = store
            .create_task("Do it", "long goal", Some("s1"), "/ws")
            .unwrap();
        assert!(store
            .set_plan(
                &id,
                &[
                    json!({"title": "Inspect", "description": "read files"}),
                    json!("Implement"),
                ],
                Some("s1"),
            )
            .unwrap());

        store.update_step(&id, 0, "completed", Some("ok"), None, Some("s1")).unwrap();
        let task = store.get_task(&id, Some("s1")).unwrap().unwrap();
        assert_eq!(task["steps"].as_array().unwrap().len(), 2);
        assert_eq!(task["steps"][0]["status"], "completed");
        assert_eq!(task["steps"][1]["title"], "Implement");
        assert_eq!(task["plan"][0]["title"], "Inspect");
        assert_eq!(task["workspace"], "/ws");
    }

    #[test]
    fn enforces_session_ownership() {
        let (_dir, store) = store();
        let id = store.create_task("t", "", Some("s1"), "").unwrap();
        assert!(store.get_task(&id, Some("other")).unwrap().is_none());
        assert!(!store.set_status(&id, "running", None, Some("other")).unwrap());
        assert!(store.set_status(&id, "running", None, Some("s1")).unwrap());
    }

    #[test]
    fn validates_status_vocabulary() {
        let (_dir, store) = store();
        let id = store.create_task("t", "", None, "").unwrap();
        assert!(store.set_status(&id, "nope", None, None).is_err());
        assert!(store.update_step(&id, 0, "nope", None, None, None).is_err());
    }

    #[test]
    fn logs_and_lists_tasks_by_status_and_session() {
        let (_dir, store) = store();
        let first = store.create_task("first", "", Some("s1"), "").unwrap();
        let second = store.create_task("second", "", Some("s2"), "").unwrap();
        store.add_log(&first, "started", "info").unwrap();
        store.set_status(&first, "failed", Some("boom"), None).unwrap();

        let logs = store.get_logs(&first, 10).unwrap();
        assert_eq!(logs[0]["message"], "started");

        let failed = store.list_tasks(10, Some(&["failed"]), None).unwrap();
        assert_eq!(failed.len(), 1);
        assert_eq!(failed[0]["summary"], "boom");
        assert_eq!(store.resumable_tasks(10, Some("s2")).unwrap()[0]["id"], second);
        assert!(store.latest_unfinished("s2").unwrap().is_some());
        // `failed` is resumable in the original store, so s1 still has one.
        assert_eq!(store.latest_unfinished("s1").unwrap().unwrap()["id"], first);
    }

    #[test]
    fn stores_background_processes() {
        let (_dir, store) = store();
        let id = store.create_task("bg", "", Some("s1"), "").unwrap();
        assert!(store
            .save_process(&id, "proc1", Some(42), "sleep 10", Some("s1"))
            .unwrap());
        let process = store.get_process_by_id("proc1", Some("s1")).unwrap().unwrap();
        assert_eq!(process["pid"], 42);
        assert_eq!(process["status"], "running");

        assert!(store
            .update_process(&id, "failed", Some(1), Some("err"), Some("s1"))
            .unwrap());
        let process = store.get_process(&id, Some("s1")).unwrap().unwrap();
        assert_eq!(process["status"], "failed");
        assert_eq!(process["exit_code"], 1);
        assert!(!store.list_processes(Some(&["running"]), 10, None).unwrap().iter().any(|p| p["process_id"] == "proc1"));
    }

    #[test]
    fn reads_legacy_task_database() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tasks.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE tasks (id TEXT PRIMARY KEY, session_id TEXT, title TEXT NOT NULL, \
                 description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', \
                 plan_json TEXT NOT NULL DEFAULT '[]', current_step INTEGER NOT NULL DEFAULT 0, \
                 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);\
                 INSERT INTO tasks (id, session_id, title, description, created_at, updated_at) \
                 VALUES ('old', 's1', 'Legacy', 'goal', '2024-01-01', '2024-01-02');",
            )
            .unwrap();
        }
        let store = TaskStore::open(&path).unwrap();
        let task = store.get_task("old", None).unwrap().unwrap();
        assert_eq!(task["title"], "Legacy");
        assert_eq!(task["summary"], "");
        assert_eq!(task["steps"].as_array().unwrap().len(), 0);
    }
}

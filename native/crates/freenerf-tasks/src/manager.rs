//! Task lifecycle for one agent run.
//!
//! Mirrors `agents/task_manager.py`: every user request creates a task, plan
//! steps carry evidence, and the final status is explicit (`completed`,
//! `partial`, `failed`, `blocked`, `cancelled` or `awaiting_approval`).

use std::sync::{Arc, Mutex};

use freenerf_core::{Error, Result};
use freenerf_permissions::redact;
use serde_json::{json, Value};

use crate::store::{TaskStore, STEP_STATUSES};

pub const FINAL_STATUSES: &[&str] = &[
    "completed",
    "partial",
    "failed",
    "blocked",
    "cancelled",
    "awaiting_approval",
];

type ChangeHook = Arc<dyn Fn(Option<Value>) + Send + Sync>;

#[derive(Default)]
struct ManagerState {
    task_id: Option<String>,
    finished: bool,
}

pub struct AgentTaskManager {
    store: Arc<TaskStore>,
    session_id: Option<String>,
    workspace: String,
    on_change: Option<ChangeHook>,
    state: Mutex<ManagerState>,
}

impl AgentTaskManager {
    pub fn new(
        store: Arc<TaskStore>,
        session_id: Option<String>,
        workspace: String,
        on_change: Option<ChangeHook>,
    ) -> Self {
        Self {
            store,
            session_id,
            workspace,
            on_change,
            state: Mutex::new(ManagerState::default()),
        }
    }

    pub fn store(&self) -> &Arc<TaskStore> {
        &self.store
    }

    pub fn session_id(&self) -> Option<&str> {
        self.session_id.as_deref()
    }

    pub fn task_id(&self) -> Option<String> {
        self.state.lock().ok().and_then(|state| state.task_id.clone())
    }

    fn lock_error() -> Error {
        Error::Other("task manager lock poisoned".into())
    }

    // ------------------------------------------------------------------
    // Lifecycle
    // ------------------------------------------------------------------
    pub fn start(&self, goal: &str, title: Option<&str>) -> Result<Option<Value>> {
        {
            let mut state = self.state.lock().map_err(|_| Self::lock_error())?;
            if let Some(task_id) = state.task_id.clone() {
                if !state.finished {
                    let _ = self.store.set_status(
                        &task_id,
                        "blocked",
                        Some("Superseded by a new request in this session"),
                        self.session_id.as_deref(),
                    );
                }
            }

            let title = title
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| {
                    let collapsed = goal.split_whitespace().collect::<Vec<_>>().join(" ");
                    collapsed.chars().take(80).collect()
                });
            let title = if title.trim().is_empty() {
                "Untitled task".to_string()
            } else {
                title
            };

            let task_id = self.store.create_task(
                &title,
                goal,
                self.session_id.as_deref(),
                &self.workspace,
            )?;
            self.store.set_status(&task_id, "running", None, None)?;
            self.store.add_log(
                &task_id,
                &format!("Task started: {}", redact(goal).chars().take(500).collect::<String>()),
                "info",
            )?;

            state.task_id = Some(task_id);
            state.finished = false;
        }

        self.changed();
        self.snapshot()
    }

    pub fn snapshot(&self) -> Result<Option<Value>> {
        let task_id = match self.state.lock().map_err(|_| Self::lock_error())?.task_id.clone() {
            Some(id) => id,
            None => return Ok(None),
        };

        let Some(mut task) = self.store.get_task(&task_id, self.session_id.as_deref())? else {
            return Ok(None);
        };

        let steps = task["steps"].as_array().cloned().unwrap_or_default();
        let done = steps
            .iter()
            .filter(|step| {
                matches!(
                    step["status"].as_str(),
                    Some("completed") | Some("skipped")
                )
            })
            .count();

        let progress = if steps.is_empty() {
            0
        } else {
            (100.0 * done as f64 / steps.len() as f64).round() as i64
        };

        task["progress"] = json!(progress);
        task["done_steps"] = json!(done);
        task["step_count"] = json!(steps.len());
        task["current_step_number"] = json!(if steps.is_empty() {
            0
        } else {
            task["current_step"].as_i64().unwrap_or(0) + 1
        });

        Ok(Some(task))
    }

    pub fn set_plan(&self, steps: &[Value]) -> Result<Value> {
        let task_id = self.require_task()?;

        let mut normalized: Vec<Value> = Vec::new();
        for (index, item) in steps.iter().enumerate() {
            match item {
                Value::String(text) => {
                    let title = text.trim();
                    normalized.push(json!({
                        "title": if title.is_empty() { format!("Step {}", index + 1) } else { title.to_string() },
                        "description": "",
                    }));
                }
                Value::Object(_) => {
                    let title = item
                        .get("title")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .trim()
                        .to_string();
                    normalized.push(json!({
                        "title": if title.is_empty() { format!("Step {}", index + 1) } else { title },
                        "description": item.get("description").and_then(|v| v.as_str()).unwrap_or(""),
                    }));
                }
                _ => {
                    return Err(Error::Tool(
                        "Each plan step must be text or an object with a title".into(),
                    ))
                }
            }
        }

        if normalized.is_empty() {
            return Err(Error::Tool("A plan needs at least one step".into()));
        }

        if !self
            .store
            .set_plan(&task_id, &normalized, self.session_id.as_deref())?
        {
            return Err(Error::Other("No active task".into()));
        }
        self.store
            .set_status(&task_id, "running", None, self.session_id.as_deref())?;

        let titles = normalized
            .iter()
            .map(|item| item["title"].as_str().unwrap_or("").to_string())
            .collect::<Vec<_>>()
            .join(" | ");
        self.store.add_log(&task_id, &format!("Plan: {titles}"), "plan")?;

        self.changed();
        self.snapshot()?.ok_or_else(|| Error::Other("No active task".into()))
    }

    pub fn update_step(
        &self,
        step: i64,
        status: &str,
        result: Option<&str>,
        error: Option<&str>,
    ) -> Result<Value> {
        let task_id = self.require_task()?;

        if !STEP_STATUSES.contains(&status) {
            return Err(Error::Tool(format!(
                "Step status must be one of: {}",
                STEP_STATUSES.join(", ")
            )));
        }

        let position = step - 1;
        let redacted_result = result.map(redact);
        let redacted_error = error.map(redact);

        if !self.store.update_step(
            &task_id,
            position,
            status,
            redacted_result.as_deref(),
            redacted_error.as_deref(),
            self.session_id.as_deref(),
        )? {
            return Err(Error::Tool(format!("Unknown step number: {step}")));
        }

        let mut message = format!("Step {step} -> {status}");
        if let Some(result) = &redacted_result {
            if !result.is_empty() {
                message.push_str(&format!(": {}", result.chars().take(300).collect::<String>()));
            }
        }
        if let Some(error) = &redacted_error {
            if !error.is_empty() {
                message.push_str(&format!(
                    " (error: {})",
                    error.chars().take(300).collect::<String>()
                ));
            }
        }

        self.store.add_log(
            &task_id,
            &message,
            if status == "failed" { "error" } else { "info" },
        )?;

        self.changed();
        self.snapshot()?.ok_or_else(|| Error::Other("No active task".into()))
    }

    pub fn log_tool(&self, name: &str, summary: &str, ok: bool) {
        let Ok(state) = self.state.lock() else {
            return;
        };
        let Some(task_id) = state.task_id.clone() else {
            return;
        };
        let message = format!(
            "Tool {name}: {}",
            redact(summary).chars().take(500).collect::<String>()
        );
        let _ = self.store.add_log(
            &task_id,
            &message,
            if ok { "tool" } else { "error" },
        );
    }

    pub fn finish(&self, status: &str, summary: &str) -> Result<Value> {
        let task_id = self.require_task()?;

        if !FINAL_STATUSES.contains(&status) {
            return Err(Error::Tool(format!(
                "Final status must be one of: {}",
                FINAL_STATUSES.join(", ")
            )));
        }

        let summary: String = summary.chars().take(4000).collect();
        self.store
            .set_status(&task_id, status, Some(&summary), self.session_id.as_deref())?;
        self.store.add_log(
            &task_id,
            &format!(
                "Task finished as {status}: {}",
                redact(&summary).chars().take(500).collect::<String>()
            ),
            if matches!(status, "failed" | "blocked") {
                "error"
            } else {
                "info"
            },
        )?;

        if let Ok(mut state) = self.state.lock() {
            state.finished = true;
        }

        self.changed();
        self.snapshot()?.ok_or_else(|| Error::Other("No active task".into()))
    }

    /// Finish a task when the model answered without calling `finish`.
    pub fn finish_implicit(&self, summary: &str) -> Result<Option<Value>> {
        let Some(task) = self.snapshot()? else {
            return Ok(None);
        };
        if self
            .state
            .lock()
            .map(|state| state.finished)
            .unwrap_or(false)
        {
            return Ok(Some(task));
        }

        let steps = task["steps"].as_array().cloned().unwrap_or_default();
        let status = if steps.iter().any(|step| {
            matches!(step["status"].as_str(), Some("pending") | Some("running"))
        }) {
            "partial"
        } else if steps
            .iter()
            .any(|step| step["status"].as_str() == Some("failed"))
        {
            "partial"
        } else {
            "completed"
        };

        Ok(Some(self.finish(status, summary)?))
    }

    pub fn cancel(&self, message: &str) -> Result<Option<Value>> {
        self.finish_if_active("cancelled", message)
    }

    pub fn block(&self, message: &str) -> Result<Option<Value>> {
        self.finish_if_active("blocked", message)
    }

    pub fn fail(&self, message: &str) -> Result<Option<Value>> {
        self.finish_if_active("failed", message)
    }

    fn finish_if_active(&self, status: &str, message: &str) -> Result<Option<Value>> {
        let state = self.state.lock().map_err(|_| Self::lock_error())?;
        if state.task_id.is_none() || state.finished {
            drop(state);
            return self.snapshot();
        }
        drop(state);
        Ok(Some(self.finish(status, message)?))
    }

    pub fn resumable(&self, limit: u32) -> Result<Vec<Value>> {
        self.store
            .resumable_tasks(limit, self.session_id.as_deref())
    }

    pub fn history(&self, limit: u32) -> Result<Vec<Value>> {
        self.store.list_tasks(limit, None, self.session_id.as_deref())
    }

    fn require_task(&self) -> Result<String> {
        self.state
            .lock()
            .map_err(|_| Self::lock_error())?
            .task_id
            .clone()
            .ok_or_else(|| Error::Tool("No active task".into()))
    }

    fn changed(&self) {
        if let Some(hook) = &self.on_change {
            let snapshot = self.snapshot().ok().flatten();
            hook(snapshot);
        }
    }
}

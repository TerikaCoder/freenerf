//! Task persistence and lifecycle management.
//!
//! Schema and status vocabulary match `agents/task_store.py` and
//! `agents/task_manager.py`: a task is created per user request and keeps its
//! plan, step evidence, logs and background processes.

pub mod manager;
pub mod store;

pub use manager::{AgentTaskManager, FINAL_STATUSES};
pub use store::{TaskStore, RESUMABLE_STATUSES, STEP_STATUSES, TASK_STATUSES};

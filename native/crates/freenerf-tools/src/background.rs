//! Background shell processes (`task_start`, `task_status`, `task_list`,
//! `task_stop`), persisted in the task database like the Python manager.

use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use freenerf_core::Result;
use freenerf_tasks::TaskStore;
use serde_json::{json, Value};

struct BgTask {
    process_id: String,
    task_id: Option<String>,
    command: String,
    pid: u32,
    child: Arc<Mutex<Child>>,
    output: Arc<Mutex<VecDeque<String>>>,
    status: Arc<Mutex<String>>,
    exit_code: Arc<Mutex<Option<i32>>>,
    started: Instant,
    finished: Arc<Mutex<Option<Instant>>>,
}

pub struct BackgroundTasks {
    cwd: PathBuf,
    max_tasks: usize,
    output_limit: usize,
    store: Option<Arc<TaskStore>>,
    session_id: Option<String>,
    tasks: Mutex<HashMap<String, Arc<BgTask>>>,
}

impl BackgroundTasks {
    pub fn new(
        cwd: PathBuf,
        store: Option<Arc<TaskStore>>,
        session_id: Option<String>,
    ) -> Self {
        let manager = Self {
            cwd,
            max_tasks: 8,
            output_limit: 12_000,
            store,
            session_id,
            tasks: Mutex::new(HashMap::new()),
        };
        manager.recover_unverified();
        manager
    }

    /// Processes persisted as running cannot be verified after a restart and
    /// are marked `unverified` (never attached to or signalled).
    fn recover_unverified(&self) {
        let Some(store) = &self.store else {
            return;
        };
        let Ok(processes) = store.list_processes(
            Some(&["running"]),
            100,
            self.session_id.as_deref(),
        ) else {
            return;
        };
        for process in processes {
            if let Some(task_id) = process["task_id"].as_str() {
                let _ = store.update_process(
                    task_id,
                    "unverified",
                    None,
                    None,
                    self.session_id.as_deref(),
                );
            }
        }
    }

    fn output_text(&self, task: &BgTask) -> String {
        let output = task.output.lock().ok();
        let text = output
            .map(|lines| lines.iter().cloned().collect::<String>())
            .unwrap_or_default();
        let char_count = text.chars().count();
        if char_count > self.output_limit {
            text.chars().skip(char_count - self.output_limit).collect()
        } else {
            text
        }
    }

    fn persist(&self, task: &BgTask) {
        let Some(store) = &self.store else {
            return;
        };
        let Some(task_id) = &task.task_id else {
            return;
        };
        let status = task
            .status
            .lock()
            .map(|s| s.clone())
            .unwrap_or_else(|_| "error".into());
        let exit_code = task.exit_code.lock().ok().and_then(|code| *code);
        let output = self.output_text(task);
        let _ = store.update_process(
            task_id,
            &status,
            exit_code.map(|code| code as i64),
            Some(&output),
            self.session_id.as_deref(),
        );
    }

    fn refresh(&self, task: &BgTask) {
        let mut finished_code = None;
        if let Ok(mut child) = task.child.lock() {
            if let Ok(Some(status)) = child.try_wait() {
                finished_code = Some(status.code());
            }
        }
        if let Some(code) = finished_code {
            let already = task
                .status
                .lock()
                .map(|status| status.as_str() != "running")
                .unwrap_or(true);
            if !already {
                if let Ok(mut exit) = task.exit_code.lock() {
                    *exit = code;
                }
                if let Ok(mut status) = task.status.lock() {
                    *status = if code == Some(0) {
                        "completed".into()
                    } else {
                        "failed".into()
                    };
                }
                if let Ok(mut finished) = task.finished.lock() {
                    if finished.is_none() {
                        *finished = Some(Instant::now());
                    }
                }
                self.persist(task);
            }
        }
    }

    pub fn start(&self, command: &str) -> Result<String> {
        let mut tasks = self
            .tasks
            .lock()
            .map_err(|_| freenerf_core::Error::Tool("background task lock poisoned".into()))?;

        let active = tasks
            .values()
            .filter(|task| {
                task.status
                    .lock()
                    .map(|status| *status == "running")
                    .unwrap_or(false)
            })
            .count();
        if active >= self.max_tasks {
            return Ok("Task limit reached".into());
        }

        let mut task_id: Option<String> = None;
        if let Some(store) = &self.store {
            let id = store.create_task(
                command,
                &format!("Shell process: {command}"),
                self.session_id.as_deref(),
                &self.cwd.to_string_lossy(),
            )?;
            task_id = Some(id);
        }

        let shell = if PathBuf::from("/system/bin/sh").exists() {
            "/system/bin/sh"
        } else {
            "sh"
        };

        let mut child = Command::new(shell)
            .arg("-c")
            .arg(command)
            .current_dir(&self.cwd)
            .env("FREENERF", "1")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("TERM", "dumb")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| freenerf_core::Error::Tool(format!("Cannot start task: {e}")))?;

        let process_id = uuid::Uuid::new_v4().simple().to_string()[..8].to_string();
        let pid = child.id();

        let output = Arc::new(Mutex::new(VecDeque::new()));
        let output_size = Arc::new(Mutex::new(0usize));
        let status = Arc::new(Mutex::new("running".to_string()));
        let exit_code = Arc::new(Mutex::new(None));
        let finished = Arc::new(Mutex::new(None));
        let limit = self.output_limit;

        if let Some(stdout) = child.stdout.take() {
            let output = Arc::clone(&output);
            let output_size = Arc::clone(&output_size);
            std::thread::spawn(move || {
                let reader = BufReader::new(stdout);
                for line in reader.lines().map_while(|line| line.ok()) {
                    let entry = format!("{line}\n");
                    if let Ok(mut buffer) = output.lock() {
                        if let Ok(mut size) = output_size.lock() {
                            *size += entry.len();
                            buffer.push_back(entry);
                            while *size > limit {
                                if let Some(removed) = buffer.pop_front() {
                                    *size = size.saturating_sub(removed.len());
                                } else {
                                    break;
                                }
                            }
                        }
                    }
                }
            });
        }

        if let Some(stderr) = child.stderr.take() {
            let output = Arc::clone(&output);
            let output_size = Arc::clone(&output_size);
            std::thread::spawn(move || {
                let reader = BufReader::new(stderr);
                for line in reader.lines().map_while(|line| line.ok()) {
                    let entry = format!("{line}\n");
                    if let Ok(mut buffer) = output.lock() {
                        if let Ok(mut size) = output_size.lock() {
                            *size += entry.len();
                            buffer.push_back(entry);
                            while *size > limit {
                                if let Some(removed) = buffer.pop_front() {
                                    *size = size.saturating_sub(removed.len());
                                } else {
                                    break;
                                }
                            }
                        }
                    }
                }
            });
        }

        let task = Arc::new(BgTask {
            process_id: process_id.clone(),
            task_id: task_id.clone(),
            command: command.to_string(),
            pid,
            child: Arc::new(Mutex::new(child)),
            output,
            status,
            exit_code,
            started: Instant::now(),
            finished,
        });

        if let (Some(store), Some(task_id)) = (&self.store, &task_id) {
            let _ = store.save_process(
                task_id,
                &process_id,
                Some(pid),
                command,
                self.session_id.as_deref(),
            );
        }

        tasks.insert(process_id.clone(), task);
        Ok(process_id)
    }

    pub fn status(&self, process_id: &str) -> Result<Value> {
        {
            let tasks = self
                .tasks
                .lock()
                .map_err(|_| freenerf_core::Error::Tool("background task lock poisoned".into()))?;
            if let Some(task) = tasks.get(process_id) {
                self.refresh(task);
                let status = task.status.lock().map(|s| s.clone()).unwrap_or_default();
                let exit_code = task.exit_code.lock().ok().and_then(|code| *code);
                return Ok(json!({
                    "id": task.process_id,
                    "task_id": task.task_id,
                    "command": task.command,
                    "pid": task.pid,
                    "status": status,
                    "exit_code": exit_code,
                    "output": self.output_text(task),
                    "elapsed": task.started.elapsed().as_secs_f64(),
                    "persisted": false,
                }));
            }
        }

        if let Some(store) = &self.store {
            if let Some(process) =
                store.get_process_by_id(process_id, self.session_id.as_deref())?
            {
                let mut value = process;
                value["persisted"] = json!(true);
                value["note"] = json!(
                    "Persisted process record; live process state is not verified."
                );
                return Ok(value);
            }
        }

        Ok(json!("Task not found"))
    }

    pub fn list(&self) -> Result<Vec<Value>> {
        let mut result = Vec::new();
        let mut known: Vec<String> = Vec::new();

        {
            let tasks = self
                .tasks
                .lock()
                .map_err(|_| freenerf_core::Error::Tool("background task lock poisoned".into()))?;
            for task in tasks.values() {
                self.refresh(task);
                known.push(task.process_id.clone());
                let status = task.status.lock().map(|s| s.clone()).unwrap_or_default();
                let exit_code = task.exit_code.lock().ok().and_then(|code| *code);
                let finished = task.finished.lock().ok().and_then(|value| *value);
                result.push(json!({
                    "id": task.process_id,
                    "task_id": task.task_id,
                    "command": task.command,
                    "pid": task.pid,
                    "status": status,
                    "exit_code": exit_code,
                    "started": task.started.elapsed().as_secs_f64(),
                    "finished": finished.map(|time| time.elapsed().as_secs_f64()),
                    "persisted": false,
                }));
            }
        }

        if let Some(store) = &self.store {
            for mut process in store.list_processes(
                Some(&["unverified"]),
                100,
                self.session_id.as_deref(),
            )? {
                let Some(process_id) = process["process_id"].as_str() else {
                    continue;
                };
                if known.iter().any(|id| id == process_id) {
                    continue;
                }
                process["persisted"] = json!(true);
                result.push(process);
            }
        }

        Ok(result)
    }

    pub fn stop(&self, process_id: &str) -> Result<String> {
        let task = {
            let tasks = self
                .tasks
                .lock()
                .map_err(|_| freenerf_core::Error::Tool("background task lock poisoned".into()))?;
            tasks.get(process_id).cloned()
        };
        let Some(task) = task else {
            return Ok("Task not found".into());
        };

        self.refresh(&task);
        let current = task.status.lock().map(|s| s.clone()).unwrap_or_default();
        if current != "running" {
            return Ok(format!("Task is {current}"));
        }

        if let Ok(mut child) = task.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }

        if let Ok(mut status) = task.status.lock() {
            *status = "stopped".into();
        }
        if let Ok(mut finished) = task.finished.lock() {
            *finished = Some(Instant::now());
        }
        self.persist(&task);

        Ok("Task stopped".into())
    }

    pub fn shutdown(&self) {
        if let Ok(tasks) = self.tasks.lock() {
            for task in tasks.values() {
                if let Ok(mut child) = task.child.lock() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
    }
}

impl Drop for BackgroundTasks {
    fn drop(&mut self) {
        self.shutdown();
    }
}

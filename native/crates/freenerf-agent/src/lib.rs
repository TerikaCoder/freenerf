//! Autonomous agent execution loop.
//!
//! Ported from `agents/runtime.py`: plan, execute tools, inspect results,
//! retry transient provider failures, respect tool budgets, and finish with an
//! explicit, evidence-based status. The loop never claims success by itself.

pub mod context;
pub mod prompt;

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::{Duration, Instant};

use freenerf_core::{
    AgentEvent, ApprovalHandler, Cancellation, Capabilities, Config, Error, EventSink, Message,
    PermissionMode, ProviderResponse, Result, ToolCall, ToolOutcome, Usage,
};
use freenerf_providers::{create_provider, Provider};
use freenerf_sessions::SessionStore;
use freenerf_tasks::{AgentTaskManager, TaskStore};
use freenerf_tools::background::BackgroundTasks;
use freenerf_tools::image::{ImageConfig, ImageGenerator};
use freenerf_tools::web::{WebFetch, WebSearch};
use freenerf_tools::{ToolContext, ToolRegistry};
use freenerf_workspace::{Git, ProjectInspector, ShellBackend, Terminal, Workspace};
use serde::Serialize;
use serde_json::{json, Value};
use uuid::Uuid;

pub const DEFAULT_MAX_STEPS: u32 = 25;
pub const MAX_TOTAL_TOOL_CALLS: u32 = 80;
pub const MAX_CONSECUTIVE_TOOL_ERRORS: u32 = 6;
pub const TOOL_MESSAGE_LIMIT: usize = 12_000;

pub fn tool_limit(name: &str) -> Option<u32> {
    match name {
        "web_search" => Some(3),
        "web_fetch" => Some(5),
        "generate_image" | "image_create" => Some(4),
        _ => None,
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct AgentOutcome {
    pub status: String,
    pub summary: String,
    pub steps: u32,
    pub tool_calls: u32,
}

impl AgentOutcome {
    pub fn as_json(&self) -> Value {
        json!({
            "status": self.status,
            "summary": self.summary,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
        })
    }
}

pub struct AgentOptions {
    pub workspace: Arc<Workspace>,
    pub session_id: String,
    pub config: Config,
    pub sink: Arc<dyn EventSink>,
    pub approval: Arc<dyn ApprovalHandler>,
    pub cancel: Cancellation,
    pub session_store: Option<Arc<SessionStore>>,
    pub task_store: Option<Arc<TaskStore>>,
    pub image_api_key: Option<String>,
}

pub struct Agent {
    provider: Box<dyn Provider>,
    workspace: Arc<Workspace>,
    session_id: Option<String>,
    session_store: Option<Arc<SessionStore>>,
    task_manager: Arc<AgentTaskManager>,
    context: Arc<ToolContext>,
    registry: Arc<ToolRegistry>,
    config: Config,
    sink: Arc<dyn EventSink>,
    approval: Arc<dyn ApprovalHandler>,
    cancel: Cancellation,
    messages: Vec<Message>,
    last_outcome: Option<AgentOutcome>,
    steps_taken: u32,
    tool_call_count: u32,
    denied_count: u32,
    awaiting_approval: bool,
    changes_made: bool,
    verification_passed: bool,
    plain_mode: bool,
    summary_mode: bool,
    background: Arc<BackgroundTasks>,
}

impl Agent {
    pub fn new(options: AgentOptions) -> Result<Self> {
        let provider = create_provider(&options.config)?;
        Self::with_provider(options, provider)
    }

    pub fn with_provider(options: AgentOptions, provider: Box<dyn Provider>) -> Result<Self> {
        let mut config = options.config;
        config.validate();

        let workspace = options.workspace;
        workspace.ensure()?;

        let task_store = match options.task_store {
            Some(store) => store,
            None => Arc::new(TaskStore::open(workspace.tasks_db())?),
        };

        let sink = options.sink;
        let change_sink = Arc::clone(&sink);
        let task_manager = Arc::new(AgentTaskManager::new(
            Arc::clone(&task_store),
            Some(options.session_id.clone()),
            workspace.root().to_string_lossy().to_string(),
            Some(Arc::new(move |task: Option<Value>| {
                if let Some(task) = task {
                    change_sink.emit(AgentEvent::TaskUpdated { task });
                }
            })),
        ));

        let background = Arc::new(BackgroundTasks::new(
            workspace.root().to_path_buf(),
            Some(task_store),
            Some(options.session_id.clone()),
        ));

        let shell: Arc<dyn ShellBackend> =
            Arc::new(Terminal::new(workspace.root(), 120));

        let image_key = options
            .image_api_key
            .filter(|key| !key.trim().is_empty())
            .unwrap_or_else(|| config.api_keys.freellmapi.clone());

        let context = Arc::new(ToolContext {
            workspace: Arc::clone(&workspace),
            shell,
            git: Arc::new(Git::new(workspace.root())),
            project: Arc::new(ProjectInspector::new(workspace.root())),
            tasks: Some(Arc::clone(&task_manager)),
            background: Arc::clone(&background),
            web_search: Arc::new(WebSearch::new()),
            web_fetch: Arc::new(WebFetch::new()),
            image: Arc::new(ImageGenerator::new(ImageConfig {
                api_key: image_key,
                base_url: config.base_urls.freellmapi.clone(),
                model: config.image_model.clone(),
                output_dir: workspace.images_dir(),
            })),
            event_sink: Arc::clone(&sink),
        });

        let system = prompt::system_prompt(
            &workspace.root().to_string_lossy(),
            config.permission_mode.as_str(),
            &config.custom_prompt,
            provider.capabilities().supports_tools,
        );

        Ok(Self {
            provider,
            workspace,
            session_id: Some(options.session_id),
            session_store: options.session_store,
            task_manager,
            context,
            registry: Arc::new(ToolRegistry::builtin()),
            config,
            sink,
            approval: options.approval,
            cancel: options.cancel,
            messages: vec![Message::system(system)],
            last_outcome: None,
            steps_taken: 0,
            tool_call_count: 0,
            denied_count: 0,
            awaiting_approval: false,
            changes_made: false,
            verification_passed: false,
            plain_mode: false,
            summary_mode: false,
            background,
        })
    }

    // ------------------------------------------------------------------
    // Accessors
    // ------------------------------------------------------------------
    pub fn capabilities(&self) -> Capabilities {
        self.provider.capabilities()
    }

    pub fn messages(&self) -> &[Message] {
        &self.messages
    }

    pub fn last_outcome(&self) -> Option<&AgentOutcome> {
        self.last_outcome.as_ref()
    }

    pub fn config(&self) -> &Config {
        &self.config
    }

    pub fn cancel_handle(&self) -> Cancellation {
        self.cancel.clone()
    }

    pub fn task_manager(&self) -> &Arc<AgentTaskManager> {
        &self.task_manager
    }

    pub fn close(&self) {
        self.background.shutdown();
    }

    pub fn set_permission_mode(&mut self, mode: PermissionMode) {
        self.config.permission_mode = mode;
        self.refresh_system_prompt();
    }

    pub fn set_config(&mut self, config: Config) {
        self.config = config;
        self.config.validate();
        self.refresh_system_prompt();
    }

    /// Restore a previous conversation transcript after the system prompt.
    pub fn load_history(&mut self, stored: &[freenerf_sessions::StoredMessage]) {
        for message in stored {
            if matches!(message.role.as_str(), "user" | "assistant" | "system") {
                self.messages.push(Message {
                    role: message.role.clone(),
                    content: Some(message.content.clone()),
                    ..Default::default()
                });
            }
        }
    }

    pub fn reset_conversation(&mut self) {
        self.messages.truncate(1);
        self.last_outcome = None;
        self.steps_taken = 0;
        self.tool_call_count = 0;
        self.denied_count = 0;
        self.awaiting_approval = false;
        self.summary_mode = false;
        self.changes_made = false;
        self.verification_passed = false;
    }

    fn refresh_system_prompt(&mut self) {
        let supports_tools = self.provider.capabilities().supports_tools;
        let prompt = prompt::system_prompt(
            &self.workspace.root().to_string_lossy(),
            self.config.permission_mode.as_str(),
            &self.config.custom_prompt,
            supports_tools,
        );
        if self.messages.is_empty() {
            self.messages.push(Message::system(prompt));
        } else {
            self.messages[0] = Message::system(prompt);
        }
    }

    fn emit(&self, event: AgentEvent) {
        self.sink.emit(event);
    }

    fn status(&self, message: &str, busy: bool) {
        self.emit(AgentEvent::AgentStatusChanged {
            status: message.to_string(),
            busy,
        });
    }

    fn persist_message(&self, role: &str, content: &str, metadata: Option<Value>) {
        let (Some(store), Some(session_id)) = (&self.session_store, &self.session_id) else {
            return;
        };
        let _ = store.add_message(session_id, role, content, metadata.as_ref());
    }

    fn persist_activity(&self, event: &Value) {
        let (Some(store), Some(session_id)) = (&self.session_store, &self.session_id) else {
            return;
        };
        let _ = store.add_activity(session_id, event, 160);
    }

    // ------------------------------------------------------------------
    // Provider interaction
    // ------------------------------------------------------------------
    async fn provider_chat(&mut self, message_id: &str) -> Result<ProviderResponse> {
        self.refresh_system_prompt();
        let capabilities = self.provider.capabilities();
        self.plain_mode = !capabilities.supports_tools;

        let mut outgoing = context::trim_context(&self.messages, self.config.max_context_chars);
        if self.summary_mode {
            outgoing.push(Message::system(
                "The agent step budget is exhausted. Do not call tools. Give a concise final \
                 summary of what was done, what was verified, and what remains unfinished. Do \
                 not claim the task is complete unless the available evidence proves it.",
            ));
        }

        let tools = if capabilities.supports_tools && !self.summary_mode {
            Some(self.registry.specs())
        } else {
            None
        };

        let use_streaming = self.config.streaming && capabilities.supports_streaming;
        let sink = Arc::clone(&self.sink);
        let id = message_id.to_string();
        let on_token = move |text: &str| {
            sink.emit(AgentEvent::AssistantToken {
                message_id: id.clone(),
                text: text.to_string(),
            });
        };

        let attempts = self.config.max_retries;
        let mut last_error: Option<Error> = None;

        for attempt in 0..=attempts {
            if self.cancel.is_cancelled() {
                return Err(Error::Cancelled);
            }

            let stream_fn: Option<&(dyn Fn(&str) + Send + Sync)> =
                if use_streaming { Some(&on_token) } else { None };

            match self
                .provider
                .chat(&outgoing, tools.as_deref(), stream_fn, &self.cancel)
                .await
            {
                Ok(response) => return Ok(response),
                Err(error) => {
                    let retriable = error.is_retriable();
                    if !retriable || attempt >= attempts {
                        return Err(error);
                    }
                    let delay = 2u64.saturating_pow(attempt).min(8);
                    self.status(
                        &format!(
                            "Provider error; retrying in {delay}s ({}/{})...",
                            attempt + 1,
                            attempts
                        ),
                        true,
                    );
                    self.sleep_interruptible(delay).await?;
                    last_error = Some(error);
                }
            }
        }

        Err(last_error.unwrap_or_else(|| Error::provider("Provider request failed")))
    }

    async fn sleep_interruptible(&self, seconds: u64) -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(seconds);
        while Instant::now() < deadline {
            if self.cancel.is_cancelled() {
                return Err(Error::Cancelled);
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Main loop
    // ------------------------------------------------------------------
    pub async fn chat(&mut self, prompt: &str) -> Result<AgentOutcome> {
        self.refresh_system_prompt();
        self.last_outcome = None;
        self.awaiting_approval = false;
        self.summary_mode = false;
        self.changes_made = false;
        self.verification_passed = false;

        if let Some(task) = self.task_manager.start(prompt, None)? {
            self.emit(AgentEvent::TaskStarted { task });
        }

        self.messages.push(Message::user(prompt));
        self.persist_message("user", prompt, None);

        let limit = self.config.max_agent_steps.max(1);
        let mut tool_counts: HashMap<String, u32> = HashMap::new();
        let mut seen_queries: HashSet<String> = HashSet::new();
        let mut consecutive_errors: u32 = 0;
        let mut total_calls: u32 = 0;
        let mut steps: u32 = 0;
        let mut summary_turn = false;

        loop {
            if steps >= limit && !summary_turn {
                summary_turn = true;
                self.summary_mode = true;
            }
            if steps >= limit && summary_turn && total_calls == 0 {
                break;
            }

            steps += 1;
            self.steps_taken = steps;

            if self.cancel.is_cancelled() {
                return Ok(self.cancelled());
            }

            self.status("Thinking...", true);

            let message_id = Uuid::new_v4().simple().to_string();
            self.emit(AgentEvent::AssistantMessageStarted {
                message_id: message_id.clone(),
            });

            let response = match self.provider_chat(&message_id).await {
                Ok(response) => response,
                Err(error) if error.is_cancelled() => return Ok(self.cancelled()),
                Err(error) => {
                    let message = format!("Provider error: {error}");
                    self.fail_task(&message);
                    self.status("Provider error", false);
                    self.emit(AgentEvent::Error {
                        message: message.clone(),
                        fatal: true,
                    });
                    self.last_outcome = Some(AgentOutcome {
                        status: "failed".into(),
                        summary: message.clone(),
                        steps,
                        tool_calls: total_calls,
                    });
                    return Err(error);
                }
            };

            if self.cancel.is_cancelled() {
                return Ok(self.cancelled());
            }

            let mut content = response.content.clone().unwrap_or_default();
            let mut calls = response.tool_calls.clone();

            if let Some(usage) = response.usage {
                self.emit_usage(usage);
            }

            if self.plain_mode && !content.is_empty() {
                let (cleaned, plain_calls) = parse_plain_calls(&content);
                content = cleaned;
                calls.extend(plain_calls);
            }

            let streamed = self.config.streaming && self.provider.capabilities().supports_streaming;

            if !content.is_empty() {
                if !streamed {
                    self.emit(AgentEvent::AssistantToken {
                        message_id: message_id.clone(),
                        text: content.clone(),
                    });
                }
                self.emit(AgentEvent::AssistantMessageCompleted {
                    message_id: message_id.clone(),
                    content: content.clone(),
                });
            }

            // ----------------------------------------------------------
            // No tool calls: final answer.
            // ----------------------------------------------------------
            if calls.is_empty() {
                let mut content = content.trim().to_string();
                if content.is_empty() {
                    content = "(the model returned an empty response)".into();
                }
                self.messages.push(Message::assistant(content.clone()));

                if summary_turn {
                    return Ok(self.blocked(
                        &format!(
                            "Stopped after reaching the {limit}-step limit. The task is not \
                             verified as complete; review the summary and continue the \
                             unfinished work."
                        ),
                        steps,
                        total_calls,
                    ));
                }

                return Ok(self.finish_without_explicit_status(content, steps, total_calls));
            }

            if summary_turn {
                if !content.trim().is_empty() {
                    self.messages
                        .push(Message::assistant(content.trim().to_string()));
                }
                break;
            }

            self.messages.push(response.raw_message.clone().unwrap_or(Message {
                role: "assistant".into(),
                content: if content.is_empty() { None } else { Some(content.clone()) },
                tool_calls: calls.clone(),
                tool_call_id: None,
                gemini_parts: None,
            }));

            // ----------------------------------------------------------
            // Execute every requested tool call in order.
            // ----------------------------------------------------------
            let mut finish_result: Option<(String, String)> = None;

            for (index, call) in calls.iter().enumerate() {
                if self.cancel.is_cancelled() {
                    for remaining in &calls[index..] {
                        self.append_tool_message(&remaining.id, "Cancelled by user");
                    }
                    return Ok(self.cancelled());
                }

                let (name, args, outcome, updated_calls) = self
                    .run_tool_call(
                        call,
                        &mut tool_counts,
                        &mut seen_queries,
                        total_calls,
                    )
                    .await;
                total_calls = updated_calls;

                let text: String = outcome.to_text().chars().take(TOOL_MESSAGE_LIMIT).collect();
                self.append_tool_message(&call.id, &text);

                if outcome.kind == "denied" {
                    self.denied_count += 1;
                    if outcome.error == "User denied operation" {
                        self.awaiting_approval = true;
                    }
                }

                if matches!(outcome.kind.as_str(), "error" | "invalid" | "timeout") {
                    consecutive_errors += 1;
                } else {
                    consecutive_errors = 0;
                }

                if name == "finish" && outcome.is_ok() {
                    let status = args
                        .get("status")
                        .and_then(|v| v.as_str())
                        .unwrap_or("completed")
                        .to_string();
                    let summary = args
                        .get("summary")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .trim()
                        .to_string();
                    finish_result =
                        Some((status, if summary.is_empty() { "Task finished.".into() } else { summary }));
                    break;
                }

                if consecutive_errors >= MAX_CONSECUTIVE_TOOL_ERRORS {
                    return Ok(self.failed_repeatedly(steps, total_calls));
                }

                if self.cancel.is_cancelled() {
                    for remaining in &calls[index + 1..] {
                        self.append_tool_message(&remaining.id, "Cancelled by user");
                    }
                    return Ok(self.cancelled());
                }
            }

            if let Some((status, summary)) = finish_result {
                return Ok(self.explicit_finish(status, summary, steps, total_calls));
            }
        }

        Ok(self.blocked(
            &format!(
                "Stopped after reaching the {limit}-step limit. The task is not verified as \
                 complete; review the work so far and continue."
            ),
            steps,
            total_calls,
        ))
    }

    fn emit_usage(&self, usage: Usage) {
        self.emit(AgentEvent::UsageUpdated {
            input_tokens: usage.input_tokens,
            output_tokens: usage.output_tokens,
            total_tokens: usage.total_tokens,
        });
    }

    fn append_tool_message(&mut self, call_id: &str, text: &str) {
        if self.plain_mode {
            self.messages.push(Message::user(format!("[tool result] {text}")));
        } else {
            self.messages
                .push(Message::tool(call_id.to_string(), text.to_string()));
        }
    }

    async fn run_tool_call(
        &mut self,
        call: &ToolCall,
        tool_counts: &mut HashMap<String, u32>,
        seen_queries: &mut HashSet<String>,
        total_calls: u32,
    ) -> (String, Value, ToolOutcome, u32) {
        let name = call.name.trim().to_string();
        if name.is_empty() {
            let outcome = ToolOutcome {
                name: "unknown".into(),
                kind: "invalid".into(),
                error: "tool name is missing".into(),
                ..Default::default()
            };
            return (name, json!({}), outcome, total_calls);
        }

        let args: Value = if call.arguments.trim().is_empty() {
            json!({})
        } else {
            match serde_json::from_str::<Value>(&call.arguments) {
                Ok(value) => value,
                Err(error) => {
                    let outcome = ToolOutcome {
                        name: name.clone(),
                        kind: "invalid".into(),
                        error: format!("arguments must be a JSON object ({error})"),
                        ..Default::default()
                    };
                    self.emit_tool_failure(&call.id, &outcome);
                    return (name, json!({}), outcome, total_calls);
                }
            }
        };

        if !args.is_object() {
            let outcome = ToolOutcome {
                name: name.clone(),
                kind: "invalid".into(),
                error: "arguments must be a JSON object".into(),
                ..Default::default()
            };
            self.emit_tool_failure(&call.id, &outcome);
            return (name, json!({}), outcome, total_calls);
        }

        if let Some(limit) = tool_limit(&name) {
            let count = tool_counts.entry(name.clone()).or_insert(0);
            *count += 1;
            if *count > limit {
                let outcome = ToolOutcome {
                    name: name.clone(),
                    kind: "error".into(),
                    error: format!(
                        "Tool limit reached: {name} may be called at most {limit} times per \
                         request. Use the results you already have."
                    ),
                    ..Default::default()
                };
                self.emit_tool_failure(&call.id, &outcome);
                return (name, args, outcome, total_calls);
            }
        }

        if name == "web_search" {
            let query = args
                .get("query")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_lowercase()
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            if !query.is_empty() && seen_queries.contains(&query) {
                let outcome = ToolOutcome {
                    name: name.clone(),
                    kind: "error".into(),
                    error: "Duplicate web_search query skipped. Reuse the previous results."
                        .into(),
                    ..Default::default()
                };
                self.emit_tool_failure(&call.id, &outcome);
                return (name, args, outcome, total_calls);
            }
            if !query.is_empty() {
                seen_queries.insert(query);
            }
        }

        if total_calls >= MAX_TOTAL_TOOL_CALLS {
            let outcome = ToolOutcome {
                name: name.clone(),
                kind: "error".into(),
                error: "Tool call budget for this request is exhausted. Summarise the current \
                        state and stop."
                    .into(),
                ..Default::default()
            };
            return (name, args, outcome, total_calls);
        }

        if name == "finish"
            && args.get("status").and_then(Value::as_str) == Some("completed")
            && self.changes_made
            && !self.verification_passed
        {
            let outcome = ToolOutcome {
                name: name.clone(),
                kind: "error".into(),
                error: "Cannot mark this task completed: workspace files were changed, but no \
                        verification command has succeeded. Run verify and inspect its output, \
                        or finish with partial/failed."
                    .into(),
                ..Default::default()
            };
            self.emit_tool_failure(&call.id, &outcome);
            return (name, args, outcome, total_calls + 1);
        }

        self.status(&prompt::tool_label(&name), true);
        self.emit(AgentEvent::ToolStarted {
            call_id: call.id.clone(),
            name: name.clone(),
            args: args.clone(),
            summary: freenerf_permissions::describe(&name, &args),
            policy: freenerf_permissions::tool_policy(&name).unwrap_or("read").to_string(),
        });
        self.persist_activity(&json!({
            "name": name,
            "args": freenerf_permissions::redact(&args.to_string()),
            "phase": "started",
        }));

        let started = Instant::now();
        let outcome = self.execute_tool(&name, &args).await;
        let elapsed = started.elapsed().as_secs_f64();

        self.tool_call_count += 1;

        if outcome.is_ok() {
            self.emit(AgentEvent::ToolCompleted {
                call_id: call.id.clone(),
                name: name.clone(),
                kind: outcome.kind.clone(),
                ok: true,
                output: freenerf_permissions::redact(&outcome.output)
                    .chars()
                    .take(20_000)
                    .collect(),
                elapsed: if outcome.elapsed > 0.0 { outcome.elapsed } else { elapsed },
            });
        } else {
            self.emit_tool_failure(&call.id, &outcome);
        }

        self.persist_activity(&json!({
            "name": name,
            "args": freenerf_permissions::redact(&args.to_string()),
            "phase": "finished",
            "ok": outcome.is_ok(),
            "kind": outcome.kind,
            "result": freenerf_permissions::redact(&outcome.to_text()).chars().take(600).collect::<String>(),
            "elapsed": elapsed,
        }));

        self.task_manager.log_tool(
            &name,
            &format!("{}: {}", outcome.kind, outcome.to_text().chars().take(300).collect::<String>()),
            outcome.is_ok(),
        );

        if outcome.is_ok()
            && matches!(
                name.as_str(),
                "write_file" | "edit_file" | "delete_file" | "ssh_write_file"
            )
        {
            self.changes_made = true;
            self.verification_passed = false;
        }
        if outcome.is_ok() && name == "verify" {
            self.verification_passed = true;
        }

        (name, args, outcome, total_calls + 1)
    }

    fn emit_tool_failure(&self, call_id: &str, outcome: &ToolOutcome) {
        self.emit(AgentEvent::ToolFailed {
            call_id: call_id.to_string(),
            name: outcome.name.clone(),
            kind: outcome.kind.clone(),
            error: freenerf_permissions::redact(&outcome.to_text()),
            elapsed: outcome.elapsed,
        });
    }

    async fn execute_tool(&self, name: &str, args: &Value) -> ToolOutcome {
        let registry = Arc::clone(&self.registry);
        let context = Arc::clone(&self.context);
        let approval = Arc::clone(&self.approval);
        let cancel = self.cancel.clone();
        let mode = self.config.permission_mode;
        let name = name.to_string();
        let worker_name = name.clone();
        let args = args.clone();

        match tokio::task::spawn_blocking(move || {
            registry.execute(&context, &worker_name, &args, mode, approval.as_ref(), &cancel)
        })
        .await
        {
            Ok(outcome) => outcome,
            Err(error) => ToolOutcome {
                name: name.clone(),
                kind: "error".into(),
                error: format!("tool worker failed: {error}"),
                ..Default::default()
            },
        }
    }

    // ------------------------------------------------------------------
    // Outcomes
    // ------------------------------------------------------------------
    fn finish_without_explicit_status(
        &mut self,
        content: String,
        steps: u32,
        total_calls: u32,
    ) -> AgentOutcome {
        let status = if self.awaiting_approval {
            let _ = self.task_manager.finish("awaiting_approval", &content);
            "awaiting_approval".to_string()
        } else if self.changes_made && !self.verification_passed {
            let summary = format!(
                "Workspace files were changed, but no successful verification was recorded. \
                 {content}"
            );
            let _ = self.task_manager.finish("partial", &summary);
            "partial".to_string()
        } else {
            match self.task_manager.finish_implicit(&content) {
                Ok(Some(task)) => task["status"]
                    .as_str()
                    .unwrap_or("completed")
                    .to_string(),
                _ => "completed".to_string(),
            }
        };

        self.persist_message("assistant", &content, Some(json!({"outcome": status})));
        self.status("Finished", false);

        let outcome = AgentOutcome {
            status: outcome_status(&status),
            summary: content,
            steps,
            tool_calls: total_calls,
        };
        self.finish_events(&outcome);
        self.last_outcome = Some(outcome.clone());
        outcome
    }

    fn explicit_finish(
        &mut self,
        status: String,
        summary: String,
        steps: u32,
        total_calls: u32,
    ) -> AgentOutcome {
        self.persist_message(
            "assistant",
            &summary,
            Some(json!({"outcome": outcome_status(&status)})),
        );
        self.status(&format!("Finished ({status})"), false);

        let outcome = AgentOutcome {
            status: outcome_status(&status),
            summary,
            steps,
            tool_calls: total_calls,
        };
        self.finish_events(&outcome);
        self.last_outcome = Some(outcome.clone());
        outcome
    }

    fn finish_events(&self, outcome: &AgentOutcome) {
        let task_id = self.task_manager.task_id().unwrap_or_default();
        match outcome.status.as_str() {
            "failed" | "blocked" => {
                self.emit(AgentEvent::TaskFailed {
                    task_id,
                    error: outcome.summary.clone(),
                });
                self.emit(AgentEvent::Error {
                    message: outcome.summary.clone(),
                    fatal: false,
                });
            }
            _ => {
                self.emit(AgentEvent::TaskCompleted {
                    task_id,
                    status: outcome.status.clone(),
                    summary: outcome.summary.clone(),
                });
            }
        }
    }

    fn fail_task(&self, message: &str) {
        let task_id = self.task_manager.task_id().unwrap_or_default();
        let _ = self.task_manager.finish("failed", message);
        self.emit(AgentEvent::TaskFailed {
            task_id,
            error: message.to_string(),
        });
    }

    fn cancelled(&mut self) -> AgentOutcome {
        let _ = self.task_manager.cancel("Cancelled by user");
        self.status("Cancelled", false);
        let task_id = self.task_manager.task_id().unwrap_or_default();
        self.emit(AgentEvent::TaskCompleted {
            task_id,
            status: "cancelled".into(),
            summary: "Cancelled by user".into(),
        });

        let outcome = AgentOutcome {
            status: "cancelled".into(),
            summary: "Cancelled by user".into(),
            steps: self.steps_taken,
            tool_calls: self.tool_call_count,
        };
        self.last_outcome = Some(outcome.clone());
        outcome
    }

    fn blocked(&mut self, message: &str, steps: u32, total_calls: u32) -> AgentOutcome {
        let _ = self.task_manager.block(message);
        self.status("Blocked: maximum steps reached", false);

        let outcome = AgentOutcome {
            status: "blocked".into(),
            summary: message.to_string(),
            steps,
            tool_calls: total_calls,
        };
        self.finish_events(&outcome);
        self.last_outcome = Some(outcome.clone());
        outcome
    }

    fn failed_repeatedly(&mut self, steps: u32, total_calls: u32) -> AgentOutcome {
        let message = format!(
            "Stopped after {MAX_CONSECUTIVE_TOOL_ERRORS} consecutive tool failures. The task is \
             not complete; inspect the errors and retry with a different approach."
        );
        let _ = self.task_manager.fail(&message);
        self.status("Failed: repeated tool errors", false);

        let outcome = AgentOutcome {
            status: "failed".into(),
            summary: message,
            steps,
            tool_calls: total_calls,
        };
        self.finish_events(&outcome);
        self.last_outcome = Some(outcome.clone());
        outcome
    }
}

fn outcome_status(value: &str) -> String {
    let value = value.to_ascii_lowercase();
    if matches!(
        value.as_str(),
        "completed" | "partial" | "failed" | "blocked" | "awaiting_approval" | "cancelled"
    ) {
        value
    } else {
        "completed".into()
    }
}

/// Parse the plain-text fallback protocol `<tool>{json}</tool>`.
pub fn parse_plain_calls(content: &str) -> (String, Vec<ToolCall>) {
    let pattern = regex::Regex::new(r"(?s)<tool>\s*(.*?)\s*</tool>").expect("static regex");
    let mut calls = Vec::new();

    for (index, captures) in pattern.captures_iter(content).enumerate() {
        let Some(payload) = captures.get(1) else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(payload.as_str()) else {
            continue;
        };
        let Some(name) = value
            .get("name")
            .or_else(|| value.get("tool"))
            .and_then(|v| v.as_str())
        else {
            continue;
        };
        let args = value
            .get("args")
            .or_else(|| value.get("arguments"))
            .cloned()
            .unwrap_or_else(|| json!({}));
        calls.push(ToolCall {
            id: format!(
                "plain_{index}_{}",
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.subsec_millis())
                    .unwrap_or(0)
            ),
            name: name.to_string(),
            arguments: args.to_string(),
        });
    }

    let cleaned = pattern.replace_all(content, "").trim().to_string();
    (cleaned, calls)
}

#[cfg(test)]
mod tests;

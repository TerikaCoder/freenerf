//! Built-in agent tools.
//!
//! Every tool execution flows through [`ToolRegistry::execute`], which applies
//! schema validation, the permission policy, user approval, a timeout and
//! cancellation. The UI cannot bypass any of those steps.

pub mod background;
pub mod handlers;
pub mod image;
pub mod specs;
pub mod web;

use std::collections::HashMap;
use std::sync::{mpsc, Arc, OnceLock};
use std::time::{Duration, Instant};

use freenerf_core::{
    AgentEvent, ApprovalHandler, Cancellation, EventSink, PermissionMode, ToolOutcome,
};
use freenerf_permissions::{describe, evaluate_permission, Decision};
use freenerf_tasks::AgentTaskManager;
use freenerf_workspace::{Git, ProjectInspector, ShellBackend, Workspace};
use serde_json::Value;

use background::BackgroundTasks;
use image::ImageGenerator;
use web::{WebFetch, WebSearch};

pub const MAX_OUTPUT_CHARS: usize = 40_000;

/// Executes tools that live outside the Rust runtime (for example the Android
/// SSH client). The host registers one executor once; the `ssh_*` tools are
/// only offered to the model when an executor is present.
pub trait RemoteToolExecutor: Send + Sync {
    /// Receives a JSON request `{"op":"...", ...}` and returns the tool output.
    fn execute(&self, request: &str) -> freenerf_core::Result<String>;
}

static REMOTE_EXECUTOR: OnceLock<Arc<dyn RemoteToolExecutor>> = OnceLock::new();

/// Installs the process-wide remote executor. Later calls are ignored.
pub fn set_remote_executor(executor: Arc<dyn RemoteToolExecutor>) {
    let _ = REMOTE_EXECUTOR.set(executor);
}

pub fn remote_executor() -> Option<&'static Arc<dyn RemoteToolExecutor>> {
    REMOTE_EXECUTOR.get()
}

pub fn has_remote_executor() -> bool {
    REMOTE_EXECUTOR.get().is_some()
}

pub struct ToolContext {
    pub workspace: Arc<Workspace>,
    pub shell: Arc<dyn ShellBackend>,
    pub git: Arc<Git>,
    pub project: Arc<ProjectInspector>,
    pub tasks: Option<Arc<AgentTaskManager>>,
    pub background: Arc<BackgroundTasks>,
    pub web_search: Arc<WebSearch>,
    pub web_fetch: Arc<WebFetch>,
    pub image: Arc<ImageGenerator>,
    pub event_sink: Arc<dyn EventSink>,
}

impl ToolContext {
    pub fn require_tasks(&self) -> freenerf_core::Result<&Arc<AgentTaskManager>> {
        self.tasks
            .as_ref()
            .ok_or_else(|| freenerf_core::Error::Tool("Task tracking is not available".into()))
    }

    pub fn emit(&self, event: AgentEvent) {
        self.event_sink.emit(event);
    }
}

type Handler = Arc<dyn Fn(&ToolContext, &Value) -> freenerf_core::Result<String> + Send + Sync>;

pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub parameters: Value,
    pub policy: String,
    pub timeout: u64,
    handler: Handler,
}

pub struct ToolRegistry {
    tools: HashMap<String, ToolDefinition>,
}

impl Default for ToolRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self {
            tools: HashMap::new(),
        }
    }

    /// Registry with every tool implemented by this build.
    pub fn builtin() -> Self {
        let mut registry = Self::new();

        for spec in specs::all_specs() {
            let handler: Handler = match spec.name {
                "list_files" => Arc::new(handlers::list_files),
                "read_file" => Arc::new(handlers::read_file),
                "file_info" => Arc::new(handlers::file_info),
                "search" => Arc::new(handlers::search),
                "project_info" => Arc::new(handlers::project_info),
                "web_search" => Arc::new(handlers::web_search),
                "web_fetch" => Arc::new(handlers::web_fetch),
                "write_file" => Arc::new(handlers::write_file),
                "edit_file" => Arc::new(handlers::edit_file),
                "delete_file" => Arc::new(handlers::delete_file),
                "run_shell" => Arc::new(handlers::run_shell),
                "verify" => Arc::new(handlers::verify),
                "git" => Arc::new(handlers::git),
                "generate_image" | "image_create" => Arc::new(handlers::generate_image),
                "set_plan" => Arc::new(handlers::set_plan),
                "update_step" => Arc::new(handlers::update_step),
                "finish" => Arc::new(handlers::finish),
                "task_start" => Arc::new(handlers::task_start),
                "task_status" => Arc::new(handlers::task_status),
                "task_list" => Arc::new(handlers::task_list),
                "task_stop" => Arc::new(handlers::task_stop),
                "ssh_exec" => Arc::new(handlers::ssh_exec),
                "ssh_read_file" => Arc::new(handlers::ssh_read_file),
                "ssh_write_file" => Arc::new(handlers::ssh_write_file),
                "ssh_list_files" => Arc::new(handlers::ssh_list_files),
                _ => continue,
            };

            registry.register(ToolDefinition {
                name: spec.name.to_string(),
                description: spec.description.to_string(),
                parameters: spec.parameters.clone(),
                policy: spec.policy.to_string(),
                timeout: spec.timeout,
                handler,
            });
        }

        registry
    }

    pub fn register(&mut self, tool: ToolDefinition) -> &mut Self {
        self.tools.insert(tool.name.clone(), tool);
        self
    }

    pub fn unregister(&mut self, name: &str) {
        self.tools.remove(name);
    }

    pub fn names(&self) -> Vec<String> {
        let mut names: Vec<String> = self.tools.keys().cloned().collect();
        names.sort();
        names
    }

    pub fn specs(&self) -> Vec<Value> {
        specs::provider_specs()
    }

    pub fn len(&self) -> usize {
        self.tools.len()
    }

    pub fn is_empty(&self) -> bool {
        self.tools.is_empty()
    }

    pub fn get(&self, name: &str) -> Option<&ToolDefinition> {
        self.tools.get(name)
    }

    /// Execute one tool call with the full safety pipeline.
    pub fn execute(
        &self,
        ctx: &Arc<ToolContext>,
        name: &str,
        args: &Value,
        mode: PermissionMode,
        approval: &dyn ApprovalHandler,
        cancel: &Cancellation,
    ) -> ToolOutcome {
        let started = Instant::now();
        let elapsed = || started.elapsed().as_secs_f64();

        let Some(tool) = self.tools.get(name) else {
            return ToolOutcome {
                name: name.into(),
                kind: "denied".into(),
                error: "Tool is not registered".into(),
                elapsed: elapsed(),
                ..Default::default()
            };
        };

        if !args.is_object() {
            return ToolOutcome {
                name: name.into(),
                kind: "invalid".into(),
                error: "arguments must be a JSON object".into(),
                policy: tool.policy.clone(),
                elapsed: elapsed(),
                ..Default::default()
            };
        }

        let errors = validate_arguments(&tool.parameters, args);
        if !errors.is_empty() {
            return ToolOutcome {
                name: name.into(),
                kind: "invalid".into(),
                error: errors.join("; "),
                policy: tool.policy.clone(),
                elapsed: elapsed(),
                ..Default::default()
            };
        }

        let (decision, reason) =
            evaluate_permission(mode, name, args, Some(&tool.policy));

        if decision == Decision::Deny {
            return ToolOutcome {
                name: name.into(),
                kind: "denied".into(),
                error: reason.clone(),
                policy: tool.policy.clone(),
                permission_reason: reason,
                elapsed: elapsed(),
                ..Default::default()
            };
        }

        if decision == Decision::Ask {
            let detail = describe(name, args);
            ctx.emit(AgentEvent::PermissionRequested {
                request_id: uuid::Uuid::new_v4().simple().to_string(),
                name: name.into(),
                detail: detail.clone(),
                reason: reason.clone(),
                policy: tool.policy.clone(),
            });

            let approved = approval.approve(name, &detail, &reason, &tool.policy);

            if cancel.is_cancelled() {
                return ToolOutcome {
                    name: name.into(),
                    kind: "cancelled".into(),
                    policy: tool.policy.clone(),
                    elapsed: elapsed(),
                    ..Default::default()
                };
            }

            if !approved {
                return ToolOutcome {
                    name: name.into(),
                    kind: "denied".into(),
                    error: "User denied operation".into(),
                    policy: tool.policy.clone(),
                    permission_reason: reason,
                    elapsed: elapsed(),
                    ..Default::default()
                };
            }
        }

        let (tx, rx) = mpsc::channel();
        let handler = Arc::clone(&tool.handler);
        let context = Arc::clone(ctx);
        let arguments = args.clone();
        std::thread::spawn(move || {
            let _ = tx.send(handler(&context, &arguments));
        });

        let deadline = started + Duration::from_secs(tool.timeout.max(1));
        let result = loop {
            if cancel.is_cancelled() {
                return ToolOutcome {
                    name: name.into(),
                    kind: "cancelled".into(),
                    policy: tool.policy.clone(),
                    elapsed: elapsed(),
                    ..Default::default()
                };
            }
            match rx.recv_timeout(Duration::from_millis(50)) {
                Ok(result) => break result,
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    if Instant::now() >= deadline {
                        return ToolOutcome {
                            name: name.into(),
                            kind: "timeout".into(),
                            policy: tool.policy.clone(),
                            elapsed: elapsed(),
                            ..Default::default()
                        };
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    break Err(freenerf_core::Error::Tool(
                        "tool worker stopped unexpectedly".into(),
                    ))
                }
            }
        };

        match result {
            Ok(output) => {
                let output = cap_output(output);
                ToolOutcome {
                    name: name.into(),
                    kind: "ok".into(),
                    output,
                    policy: tool.policy.clone(),
                    permission_reason: reason,
                    elapsed: elapsed(),
                    ..Default::default()
                }
            }
            Err(freenerf_core::Error::Permission(message)) => ToolOutcome {
                name: name.into(),
                kind: "denied".into(),
                error: message,
                policy: tool.policy.clone(),
                elapsed: elapsed(),
                ..Default::default()
            },
            Err(error) => ToolOutcome {
                name: name.into(),
                kind: "error".into(),
                error: freenerf_permissions::redact(&error.to_string()),
                policy: tool.policy.clone(),
                elapsed: elapsed(),
                ..Default::default()
            },
        }
    }
}

fn cap_output(output: String) -> String {
    let length = output.chars().count();
    if length <= MAX_OUTPUT_CHARS {
        return output;
    }
    let omitted = length - MAX_OUTPUT_CHARS;
    let kept: String = output.chars().take(MAX_OUTPUT_CHARS).collect();
    format!("{kept}\n[output truncated: {omitted} characters omitted]")
}

// ----------------------------------------------------------------------
// JSON-schema validation (subset used by the tool specs)
// ----------------------------------------------------------------------
pub fn validate_arguments(schema: &Value, arguments: &Value) -> Vec<String> {
    let mut errors = Vec::new();
    let Some(arguments) = arguments.as_object() else {
        return vec!["arguments must be a JSON object".into()];
    };

    let properties = schema
        .get("properties")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let required = schema
        .get("required")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    for name in required.iter().filter_map(|v| v.as_str()) {
        if !arguments.contains_key(name) {
            errors.push(format!("missing required argument '{name}'"));
        }
    }

    for (name, value) in arguments {
        let Some(spec) = properties.get(name) else {
            if schema.get("additionalProperties").and_then(|v| v.as_bool()) == Some(false) {
                errors.push(format!("unexpected argument '{name}'"));
            }
            continue;
        };

        let expected = spec.get("type").and_then(|v| v.as_str()).unwrap_or("");

        if !expected.is_empty() && !type_matches(value, expected) {
            errors.push(format!("argument '{name}' must be of type {expected}"));
            continue;
        }

        if let Some(values) = spec.get("enum").and_then(|v| v.as_array()) {
            if !values.contains(value) {
                let allowed = values
                    .iter()
                    .map(|v| v.to_string())
                    .collect::<Vec<_>>()
                    .join(", ");
                errors.push(format!("argument '{name}' must be one of: {allowed}"));
            }
        }

        if let Some(number) = value.as_f64() {
            if !value.is_boolean() {
                if let Some(minimum) = spec.get("minimum").and_then(|v| v.as_f64()) {
                    if number < minimum {
                        errors.push(format!("argument '{name}' must be >= {minimum}"));
                    }
                }
                if let Some(maximum) = spec.get("maximum").and_then(|v| v.as_f64()) {
                    if number > maximum {
                        errors.push(format!("argument '{name}' must be <= {maximum}"));
                    }
                }
            }
        }

        if expected == "array" {
            if let (Some(values), Some(items)) = (value.as_array(), spec.get("items")) {
                if let Some(item_type) = items.get("type").and_then(|v| v.as_str()) {
                    for (position, item) in values.iter().enumerate() {
                        if !type_matches(item, item_type) {
                            errors.push(format!(
                                "argument '{name}[{position}]' must be of type {item_type}"
                            ));
                        }
                    }
                }
                // Validate nested objects in array items.
                if items.get("type").and_then(|v| v.as_str()) == Some("object") {
                    for item in values {
                        if item.is_object() {
                            errors.extend(validate_arguments(items, item));
                        }
                    }
                }
            }
        } else if expected == "object" && value.is_object() {
            errors.extend(validate_arguments(spec, value));
        }
    }

    errors
}

fn type_matches(value: &Value, expected: &str) -> bool {
    match expected {
        "string" => value.is_string(),
        "integer" => value.as_i64().is_some() || value.as_u64().is_some(),
        "number" => value.is_number(),
        "boolean" => value.is_boolean(),
        "array" => value.is_array(),
        "object" => value.is_object(),
        _ => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct TaggedSink;

    impl EventSink for TaggedSink {
        fn emit(&self, _event: AgentEvent) {}
    }

    #[test]
    fn validates_required_types_enums_and_ranges() {
        let schema = json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "count": {"type": "integer", "minimum": 1, "maximum": 4},
                "scope": {"type": "string", "enum": ["source", "project"]}
            },
            "required": ["path"]
        });

        assert!(validate_arguments(&schema, &json!({"path": "a"})).is_empty());
        assert_eq!(
            validate_arguments(&schema, &json!({}))
                .first()
                .unwrap(),
            "missing required argument 'path'"
        );
        assert!(!validate_arguments(&schema, &json!({"path": 1})).is_empty());
        assert!(!validate_arguments(&schema, &json!({"path": "a", "count": 9})).is_empty());
        assert!(!validate_arguments(&schema, &json!({"path": "a", "scope": "x"})).is_empty());
        assert!(validate_arguments(&schema, &json!({"path": "a", "offset": -1})).is_empty()
            == false);
    }

    #[test]
    fn rejects_non_object_arguments() {
        let schema = json!({"type": "object"});
        assert_eq!(
            validate_arguments(&schema, &json!("x")),
            vec!["arguments must be a JSON object"]
        );
    }

    #[test]
    fn every_spec_has_a_registered_handler() {
        let registry = ToolRegistry::builtin();
        assert_eq!(registry.len(), 22, "all documented tools must be registered");
        for spec in specs::all_specs() {
            assert!(
                registry.get(spec.name).is_some(),
                "missing handler for {}",
                spec.name
            );
        }
    }

    #[test]
    fn unknown_tool_is_denied() {
        // Execution of an unknown tool is covered by the agent tests with a
        // full context; here we assert the registry exposes only known names.
        let registry = ToolRegistry::builtin();
        assert!(registry.get("nope").is_none());
        assert!(registry.names().contains(&"run_shell".to_string()));
    }

    #[allow(dead_code)]
    fn _sink_is_send_sync(sink: Arc<TaggedSink>) -> Arc<dyn EventSink> {
        sink
    }
}

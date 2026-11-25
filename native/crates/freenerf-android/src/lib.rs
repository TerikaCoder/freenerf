//! JNI boundary between the Kotlin Android UI and the Rust runtime.
//!
//! The Kotlin side calls one generic function, `nativeCall(method, payload)`,
//! which returns a JSON envelope (`{"ok":true,"data":...}` or
//! `{"ok":false,"error":"..."}`). Agent events are pushed asynchronously to the
//! registered bridge object's `onEvent(json)` method, and permission requests
//! call `onPermissionRequest(json): Boolean` synchronously.

use std::panic::AssertUnwindSafe;
use std::sync::{Arc, Mutex, OnceLock};

use freenerf_agent::{Agent, AgentOptions};
use freenerf_core::{
    AgentEvent, ApprovalHandler, Cancellation, Config, EventSink, NullSink, PermissionMode,
    ProviderKind,
};
use freenerf_sessions::SessionStore;
use freenerf_storage;
use freenerf_tasks::TaskStore;
use freenerf_tools::ToolRegistry;
use freenerf_workspace::Workspace;
use jni::objects::{GlobalRef, JClass, JObject, JString, JValue};
use jni::{JNIEnv, JavaVM};
use serde_json::{json, Value};

static VM: OnceLock<JavaVM> = OnceLock::new();
static BRIDGE: Mutex<Option<Arc<GlobalRef>>> = Mutex::new(None);
static CANCEL: Mutex<Option<Cancellation>> = Mutex::new(None);

fn runtime() -> &'static tokio::runtime::Runtime {
    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .expect("failed to build the FreeNerf runtime")
    })
}

fn bridge() -> Option<Arc<GlobalRef>> {
    BRIDGE.lock().ok().and_then(|guard| guard.clone())
}

struct JavaSink {
    bridge: Arc<GlobalRef>,
}

impl EventSink for JavaSink {
    fn emit(&self, event: AgentEvent) {
        let Some(vm) = VM.get() else {
            return;
        };
        let Ok(payload) = serde_json::to_string(&event) else {
            return;
        };
        let Ok(mut env) = vm.attach_current_thread() else {
            return;
        };
        let Ok(text) = env.new_string(payload) else {
            return;
        };
        let _ = env.call_method(
            self.bridge.as_ref(),
            "onEvent",
            "(Ljava/lang/String;)V",
            &[JValue::Object(&text)],
        );
    }
}

/// Runs `ssh_*` tools through the host application's SSH client. The Kotlin
/// side answers synchronously with `{"ok":true,"output":"..."}` or
/// `{"ok":false,"error":"..."}`.
struct JavaRemote {
    bridge: Arc<GlobalRef>,
}

impl freenerf_tools::RemoteToolExecutor for JavaRemote {
    fn execute(&self, request: &str) -> freenerf_core::Result<String> {
        let unavailable = || {
            freenerf_core::Error::Tool("The SSH bridge is not available".into())
        };
        let Some(vm) = VM.get() else {
            return Err(unavailable());
        };
        let mut env = vm.attach_current_thread().map_err(|_| unavailable())?;
        let text = env.new_string(request).map_err(|_| unavailable())?;
        let response = env
            .call_method(
                self.bridge.as_ref(),
                "onRemoteTool",
                "(Ljava/lang/String;)Ljava/lang/String;",
                &[JValue::Object(&text)],
            )
            .map_err(|_| unavailable())?;
        let object = response.l().map_err(|_| unavailable())?;
        if object.is_null() {
            return Err(unavailable());
        }
        let java_string = JString::from(object);
        let payload: String = env
            .get_string(&java_string)
            .map_err(|_| unavailable())?
            .into();
        let parsed: Value = serde_json::from_str(&payload)
            .map_err(|error| freenerf_core::Error::Tool(format!("Malformed SSH response: {error}")))?;
        if parsed.get("ok").and_then(|value| value.as_bool()).unwrap_or(false) {
            Ok(parsed
                .get("output")
                .and_then(|value| value.as_str())
                .unwrap_or("")
                .to_string())
        } else {
            Err(freenerf_core::Error::Tool(
                parsed
                    .get("error")
                    .and_then(|value| value.as_str())
                    .unwrap_or("SSH tool failed")
                    .to_string(),
            ))
        }
    }
}

struct JavaApproval {
    bridge: Arc<GlobalRef>,
}

impl ApprovalHandler for JavaApproval {
    fn approve(&self, name: &str, detail: &str, reason: &str, policy: &str) -> bool {
        let Some(vm) = VM.get() else {
            return false;
        };
        let Ok(mut env) = vm.attach_current_thread() else {
            return false;
        };
        let payload = json!({
            "name": name,
            "detail": detail,
            "reason": reason,
            "policy": policy,
        })
        .to_string();
        let Ok(text) = env.new_string(payload) else {
            return false;
        };
        match env.call_method(
            self.bridge.as_ref(),
            "onPermissionRequest",
            "(Ljava/lang/String;)Z",
            &[JValue::Object(&text)],
        ) {
            Ok(value) => value.z().unwrap_or(false),
            Err(_) => false,
        }
    }
}

fn ok(data: Value) -> String {
    json!({"ok": true, "data": data}).to_string()
}

fn fail(message: impl Into<String>) -> String {
    json!({"ok": false, "error": message.into()}).to_string()
}

fn field<'a>(payload: &'a Value, key: &str) -> &'a str {
    payload.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

fn workspace_from(payload: &Value) -> freenerf_core::Result<Arc<Workspace>> {
    let root = field(payload, "root");
    if root.trim().is_empty() {
        return Err(freenerf_core::Error::Config(
            "No workspace path was provided".into(),
        ));
    }
    let workspace = Workspace::open(root, true)?;
    workspace.ensure()?;
    Ok(Arc::new(workspace))
}

fn config_from(payload: &Value) -> Config {
    let mut config: Config =
        serde_json::from_value(payload.get("config").cloned().unwrap_or_else(|| json!({})))
            .unwrap_or_default();
    if let Some(mode) = payload.get("permissionMode").and_then(|v| v.as_str()) {
        config.permission_mode = PermissionMode::parse(mode);
    }
    config.validate();
    config
}

fn title_from(prompt: &str) -> String {
    let collapsed = prompt.split_whitespace().collect::<Vec<_>>().join(" ");
    let title: String = collapsed.chars().take(60).collect();
    if title.is_empty() {
        "New session".into()
    } else {
        title
    }
}

// ----------------------------------------------------------------------
// Dispatcher
// ----------------------------------------------------------------------
fn dispatch(method: &str, payload_text: &str) -> String {
    let payload: Value = serde_json::from_str(payload_text).unwrap_or_else(|_| json!({}));

    match method {
        "app.info" => {
            let registry = ToolRegistry::builtin();
            ok(json!({
                "version": freenerf_core::VERSION,
                "tools": registry.names(),
                "tool_count": registry.len(),
                "providers": ProviderKind::ALL
                    .iter()
                    .map(|kind| freenerf_providers::provider_info(*kind))
                    .collect::<Vec<_>>(),
                "permission_modes": ["denied", "normal", "full"],
            }))
        }

        "config.default" | "config.public" => {
            let mut config = config_from(&payload);
            config.validate();
            ok(config.public_json())
        }

        "config.migrate" => {
            let raw = payload
                .get("json")
                .and_then(|v| v.as_str())
                .unwrap_or_default();
            // Legacy config files contain plaintext keys. Returning them to the
            // Kotlin layer lets it move them into the Android Keystore; the
            // values are never persisted by the Rust runtime.
            match serde_json::to_value(freenerf_storage::config_from_python_json(raw)) {
                Ok(value) => ok(value),
                Err(error) => fail(error.to_string()),
            }
        }

        "models.list" => {
            let provider = ProviderKind::parse(field(&payload, "provider"));
            let api_key = field(&payload, "apiKey");
            let base_url = field(&payload, "baseUrl");
            let query = field(&payload, "query");
            let tools_only = payload
                .get("toolsOnly")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            let force = payload
                .get("forceRefresh")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);

            match runtime().block_on(freenerf_providers::get_models(
                provider, api_key, base_url, query, tools_only, force,
            )) {
                Ok(models) => ok(json!(models)),
                Err(error) => fail(error.to_string()),
            }
        }

        "tools.list" => ok(json!(ToolRegistry::builtin().specs())),

        "workspace.info" => match workspace_from(&payload) {
            Ok(workspace) => {
                let sessions = SessionStore::open(workspace.sessions_db())
                    .and_then(|store| store.list(200, true))
                    .map(|sessions| sessions.len())
                    .unwrap_or(0);
                let tasks = TaskStore::open(workspace.tasks_db())
                    .and_then(|store| store.list_tasks(200, None, None))
                    .map(|tasks| tasks.len())
                    .unwrap_or(0);
                let mut summary = workspace.summary();
                summary["sessions"] = json!(sessions);
                summary["tasks"] = json!(tasks);
                summary["backups"] = json!(
                    std::fs::read_dir(workspace.backups_dir())
                        .map(|dir| dir.flatten().count())
                        .unwrap_or(0)
                );
                ok(summary)
            }
            Err(error) => fail(error.to_string()),
        },

        "workspace.legacy_import" => {
            let legacy = field(&payload, "legacyDir");
            let root = field(&payload, "root");
            if legacy.is_empty() || root.is_empty() {
                return fail("legacyDir and root are required");
            }
            let workspace = match Workspace::open(root, true) {
                Ok(workspace) => workspace,
                Err(error) => return fail(error.to_string()),
            };
            let _ = workspace.ensure();
            match freenerf_storage::import_legacy_databases(
                std::path::Path::new(legacy),
                &workspace.data_dir(),
            ) {
                Ok(imported) => ok(json!({"imported": imported})),
                Err(error) => fail(error.to_string()),
            }
        }

        // ------------------------------------------------------------------
        // Sessions
        // ------------------------------------------------------------------
        "session.list" | "session.search" | "session.create" | "session.rename"
        | "session.delete" | "session.close" | "session.reopen" | "session.messages"
        | "session.activity" | "session.export" | "session.clear" => {
            let workspace = match workspace_from(&payload) {
                Ok(workspace) => workspace,
                Err(error) => return fail(error.to_string()),
            };
            let store = match SessionStore::open(workspace.sessions_db()) {
                Ok(store) => store,
                Err(error) => return fail(error.to_string()),
            };
            let session_id = field(&payload, "sessionId");

            let result = match method {
                "session.list" => store
                    .list(
                        payload.get("limit").and_then(|v| v.as_u64()).unwrap_or(30) as u32,
                        payload
                            .get("includeClosed")
                            .and_then(|v| v.as_bool())
                            .unwrap_or(false),
                    )
                    .map(|items| json!(items)),
                "session.search" => store
                    .search(
                        field(&payload, "query"),
                        payload.get("limit").and_then(|v| v.as_u64()).unwrap_or(20) as u32,
                    )
                    .map(|items| json!(items)),
                "session.create" => store
                    .create(field(&payload, "title"), field(&payload, "workspace"))
                    .map(|id| json!({"id": id})),
                "session.rename" => store
                    .rename(session_id, field(&payload, "title"))
                    .map(|changed| json!({"changed": changed})),
                "session.delete" => store
                    .delete(session_id)
                    .map(|changed| json!({"changed": changed})),
                "session.close" => store
                    .close_session(session_id)
                    .map(|changed| json!({"changed": changed})),
                "session.reopen" => store
                    .reopen_session(session_id)
                    .map(|changed| json!({"changed": changed})),
                "session.messages" => store.get_messages(session_id).map(|items| json!(items)),
                "session.activity" => store
                    .get_activity(
                        session_id,
                        payload.get("limit").and_then(|v| v.as_u64()).unwrap_or(160) as u32,
                    )
                    .map(|items| json!(items)),
                "session.clear" => {
                    let removed = store.clear_messages(session_id).unwrap_or(0);
                    let _ = store.clear_activity(session_id);
                    Ok(json!({"removed": removed}))
                }
                _ => store.export_session(session_id),
            };

            match result {
                Ok(data) => ok(data),
                Err(error) => fail(error.to_string()),
            }
        }

        // ------------------------------------------------------------------
        // Tasks
        // ------------------------------------------------------------------
        "task.list" | "task.get" | "task.delete" | "task.resumable" => {
            let workspace = match workspace_from(&payload) {
                Ok(workspace) => workspace,
                Err(error) => return fail(error.to_string()),
            };
            let store = match TaskStore::open(workspace.tasks_db()) {
                Ok(store) => store,
                Err(error) => return fail(error.to_string()),
            };
            let session = payload
                .get("sessionId")
                .and_then(|v| v.as_str())
                .filter(|value| !value.is_empty());
            let limit = payload.get("limit").and_then(|v| v.as_u64()).unwrap_or(30) as u32;

            let result = match method {
                "task.list" => store.list_tasks(limit, None, session).map(|items| json!(items)),
                "task.get" => store
                    .get_task(field(&payload, "taskId"), session)
                    .map(|task| task.unwrap_or(Value::Null)),
                "task.delete" => store
                    .delete_task(field(&payload, "taskId"))
                    .map(|changed| json!({"changed": changed})),
                _ => store.resumable_tasks(limit, session).map(|items| json!(items)),
            };

            match result {
                Ok(data) => ok(data),
                Err(error) => fail(error.to_string()),
            }
        }

        // ------------------------------------------------------------------
        // Agent
        // ------------------------------------------------------------------
        "agent.run" => {
            let prompt = field(&payload, "prompt").to_string();
            if prompt.trim().is_empty() {
                return fail("A prompt is required");
            }

            let workspace = match workspace_from(&payload) {
                Ok(workspace) => workspace,
                Err(error) => return fail(error.to_string()),
            };
            let store = match SessionStore::open(workspace.sessions_db()) {
                Ok(store) => Arc::new(store),
                Err(error) => return fail(error.to_string()),
            };

            let mut session_id = field(&payload, "sessionId").to_string();
            if session_id.trim().is_empty() {
                match store.create(&title_from(&prompt), field(&payload, "root")) {
                    Ok(id) => session_id = id,
                    Err(error) => return fail(error.to_string()),
                }
            } else if store.get(&session_id).ok().flatten().is_none() {
                match store.create(&title_from(&prompt), field(&payload, "root")) {
                    Ok(id) => session_id = id,
                    Err(error) => return fail(error.to_string()),
                }
            }

            let config = config_from(&payload);
            let cancel = Cancellation::new();
            if let Ok(mut guard) = CANCEL.lock() {
                *guard = Some(cancel.clone());
            }

            let sink: Arc<dyn EventSink> = match bridge() {
                Some(bridge) => Arc::new(JavaSink { bridge }),
                None => Arc::new(NullSink),
            };
            let approval: Arc<dyn ApprovalHandler> = match (config.permission_mode, bridge()) {
                (PermissionMode::Full, _) => Arc::new(freenerf_core::AllowAll),
                (PermissionMode::Denied, _) => Arc::new(freenerf_core::DenyAll),
                (_, Some(bridge)) => Arc::new(JavaApproval { bridge }),
                (_, None) => Arc::new(freenerf_core::DenyAll),
            };

            let load_history = payload
                .get("loadHistory")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);

            let options = AgentOptions {
                workspace,
                session_id: session_id.clone(),
                config,
                sink,
                approval,
                cancel,
                session_store: Some(Arc::clone(&store)),
                task_store: None,
                image_api_key: payload
                    .get("imageApiKey")
                    .and_then(|v| v.as_str())
                    .map(str::to_string),
            };

            let mut agent = match Agent::new(options) {
                Ok(agent) => agent,
                Err(error) => {
                    if let Ok(mut guard) = CANCEL.lock() {
                        *guard = None;
                    }
                    return fail(error.to_string());
                }
            };

            if load_history {
                if let Ok(messages) = store.get_messages(&session_id) {
                    agent.load_history(&messages);
                }
            }

            let result = runtime().block_on(agent.chat(&prompt));

            if let Ok(mut guard) = CANCEL.lock() {
                *guard = None;
            }

            match result {
                Ok(outcome) => ok(json!({
                    "session_id": session_id,
                    "outcome": outcome.as_json(),
                })),
                Err(error) => fail(error.to_string()),
            }
        }

        "agent.cancel" => {
            if let Ok(mut guard) = CANCEL.lock() {
                if let Some(cancel) = guard.take() {
                    cancel.cancel();
                }
            }
            ok(json!({"cancelled": true}))
        }

        other => fail(format!("Unknown native method: {other}")),
    }
}

// ----------------------------------------------------------------------
// JNI entry points
// ----------------------------------------------------------------------
#[no_mangle]
pub extern "system" fn Java_dev_freenerf_app_NativeCore_nativeCall<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    method: JString<'local>,
    payload: JString<'local>,
) -> jni::sys::jstring {
    let method_name: String = env
        .get_string(&method)
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_default();
    let payload_text: String = env
        .get_string(&payload)
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_default();

    let response = std::panic::catch_unwind(AssertUnwindSafe(|| {
        dispatch(&method_name, &payload_text)
    }))
    .unwrap_or_else(|_| fail("internal panic in the native core"));

    match env.new_string(response) {
        Ok(text) => text.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "system" fn Java_dev_freenerf_app_NativeCore_nativeSetBridge(
    env: JNIEnv<'_>,
    _class: JClass<'_>,
    bridge: JObject<'_>,
) -> jni::sys::jboolean {
    if let Ok(vm) = env.get_java_vm() {
        let _ = VM.set(vm);
    }
    match env.new_global_ref(bridge) {
        Ok(reference) => {
            let reference = Arc::new(reference);
            if let Ok(mut guard) = BRIDGE.lock() {
                *guard = Some(Arc::clone(&reference));
            }
            freenerf_tools::set_remote_executor(Arc::new(JavaRemote {
                bridge: reference,
            }));
            1
        }
        Err(_) => 0,
    }
}

#[no_mangle]
pub extern "system" fn Java_dev_freenerf_app_NativeCore_nativeClearBridge(
    _env: JNIEnv<'_>,
    _class: JClass<'_>,
) {
    if let Ok(mut guard) = BRIDGE.lock() {
        *guard = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dispatcher_reports_app_info() {
        let response: Value = serde_json::from_str(&dispatch("app.info", "{}")).unwrap();
        assert_eq!(response["ok"], true);
        assert_eq!(response["data"]["tool_count"], 22);
        assert_eq!(response["data"]["providers"].as_array().unwrap().len(), 4);
    }

    #[test]
    fn unknown_methods_fail_cleanly() {
        let response: Value = serde_json::from_str(&dispatch("nope", "{}")).unwrap();
        assert_eq!(response["ok"], false);
        assert!(response["error"].as_str().unwrap().contains("Unknown native method"));
    }

    #[test]
    fn config_round_trip_masks_keys() {
        let payload = json!({
            "config": {
                "provider": "gemini",
                "api_keys": {"gemini": "AIza-secret"},
                "max_agent_steps": 7
            }
        });
        let response: Value =
            serde_json::from_str(&dispatch("config.public", &payload.to_string())).unwrap();
        assert_eq!(response["data"]["provider"], "gemini");
        assert_eq!(response["data"]["max_agent_steps"], 7);
        assert_eq!(response["data"]["api_keys"]["gemini"], "********");
    }

    #[test]
    fn missing_workspace_is_an_error() {
        let response: Value = serde_json::from_str(&dispatch(
            "workspace.info",
            &json!({"root": "/does/not/exist/anywhere"}).to_string(),
        ))
        .unwrap_or_default();
        // Either an error envelope or a successful creation is impossible for
        // an unwritable location, so the response must not claim success.
        assert_eq!(response["ok"], false);
    }
}

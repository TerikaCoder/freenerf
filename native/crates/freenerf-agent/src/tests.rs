use std::collections::VecDeque;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use freenerf_core::{
    Cancellation, Capabilities, Config, Error, Message, NullSink, PermissionMode, ProviderResponse,
    Result, ToolCall,
};
use freenerf_sessions::SessionStore;
use freenerf_workspace::Workspace;
use serde_json::json;

use super::*;

struct ScriptedProvider {
    responses: Mutex<VecDeque<ProviderResponse>>,
    capabilities: Capabilities,
    calls: AtomicUsize,
    emit_tokens: bool,
}

impl ScriptedProvider {
    fn new(responses: Vec<ProviderResponse>) -> Self {
        Self {
            responses: Mutex::new(responses.into()),
            capabilities: Capabilities {
                supports_tools: true,
                supports_streaming: false,
                supports_images: false,
            },
            calls: AtomicUsize::new(0),
            emit_tokens: false,
        }
    }

    fn plain(mut self) -> Self {
        self.capabilities.supports_tools = false;
        self
    }

    fn streaming(mut self) -> Self {
        self.capabilities.supports_streaming = true;
        self.emit_tokens = true;
        self
    }
}

#[async_trait]
impl Provider for ScriptedProvider {
    fn name(&self) -> &str {
        "scripted"
    }

    fn capabilities(&self) -> Capabilities {
        self.capabilities
    }

    async fn chat(
        &self,
        _messages: &[Message],
        _tools: Option<&[Value]>,
        on_token: Option<freenerf_providers::StreamFn<'_>>,
        _cancel: &Cancellation,
    ) -> Result<ProviderResponse> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let response = self
            .responses
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_default();
        if self.emit_tokens {
            if let (Some(text), Some(callback)) = (&response.content, on_token) {
                callback(text);
            }
        }
        Ok(response)
    }
}

/// Provider that fails with a retriable error once, then succeeds.
struct FlakyProvider {
    attempts: AtomicUsize,
}

#[async_trait]
impl Provider for FlakyProvider {
    fn name(&self) -> &str {
        "flaky"
    }

    fn capabilities(&self) -> Capabilities {
        Capabilities {
            supports_tools: true,
            supports_streaming: false,
            supports_images: false,
        }
    }

    async fn chat(
        &self,
        _messages: &[Message],
        _tools: Option<&[Value]>,
        _on_token: Option<freenerf_providers::StreamFn<'_>>,
        _cancel: &Cancellation,
    ) -> Result<ProviderResponse> {
        let attempt = self.attempts.fetch_add(1, Ordering::SeqCst);
        if attempt == 0 {
            return Err(Error::status("rate limited", 429, true));
        }
        Ok(ProviderResponse {
            content: Some("recovered".into()),
            ..Default::default()
        })
    }
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap()
}

fn agent(
    provider: Box<dyn Provider>,
    approval: Arc<dyn ApprovalHandler>,
    mode: PermissionMode,
) -> (tempfile::TempDir, Agent) {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Arc::new(Workspace::open(dir.path(), true).unwrap());
    workspace.ensure().unwrap();
    let mut config = Config::default();
    config.permission_mode = mode;
    config.max_retries = 1;

    let options = AgentOptions {
        workspace,
        session_id: "test-session".into(),
        config,
        sink: Arc::new(NullSink),
        approval,
        cancel: Cancellation::new(),
        session_store: None,
        task_store: None,
        image_api_key: None,
    };

    (dir, Agent::with_provider(options, provider).unwrap())
}

fn tool_response(name: &str, args: serde_json::Value) -> ProviderResponse {
    ProviderResponse {
        content: None,
        tool_calls: vec![ToolCall {
            id: "call_1".into(),
            name: name.into(),
            arguments: args.to_string(),
        }],
        raw_message: None,
        usage: None,
    }
}

#[test]
fn runs_a_tool_then_finishes_with_the_answer() {
    let (dir, mut agent) = agent(
        Box::new(ScriptedProvider::new(vec![
            tool_response("read_file", json!({"path": "a.txt"})),
            ProviderResponse {
                content: Some("The file says hello.".into()),
                ..Default::default()
            },
        ])),
        Arc::new(freenerf_core::AllowAll),
        PermissionMode::Normal,
    );
    std::fs::write(dir.path().join("a.txt"), "hello").unwrap();

    let rt = runtime();
    let outcome = rt.block_on(agent.chat("read a.txt")).unwrap();

    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.summary, "The file says hello.");
    assert!(agent
        .messages()
        .iter()
        .any(|m| m.role == "tool" && m.text() == "hello"));
    assert_eq!(outcome.tool_calls, 1);
}

#[test]
fn write_permission_is_enforced_and_reported() {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Arc::new(Workspace::open(dir.path(), true).unwrap());
    workspace.ensure().unwrap();

    let provider = ScriptedProvider::new(vec![
        tool_response(
            "write_file",
            json!({"path": "blocked.txt", "content": "nope"}),
        ),
        ProviderResponse {
            content: Some("I could not write the file.".into()),
            ..Default::default()
        },
    ]);

    let options = AgentOptions {
        workspace,
        session_id: "s".into(),
        config: Config::default(),
        sink: Arc::new(NullSink),
        approval: Arc::new(freenerf_core::DenyAll),
        cancel: Cancellation::new(),
        session_store: None,
        task_store: None,
        image_api_key: None,
    };
    let mut agent = Agent::with_provider(options, Box::new(provider)).unwrap();

    let rt = runtime();
    let outcome = rt.block_on(agent.chat("write a file")).unwrap();
    assert_eq!(outcome.status, "awaiting_approval");
    assert!(!dir.path().join("blocked.txt").exists());
    assert!(agent
        .messages()
        .iter()
        .any(|m| m.text().contains("Permission denied")));
}

#[test]
fn cancelled_runs_report_cancelled() {
    let (_dir, mut agent) = agent(
        Box::new(ScriptedProvider::new(vec![ProviderResponse {
            content: Some("never used".into()),
            ..Default::default()
        }])),
        Arc::new(freenerf_core::AllowAll),
        PermissionMode::Full,
    );
    agent.cancel_handle().cancel();

    let rt = runtime();
    let outcome = rt.block_on(agent.chat("do something")).unwrap();
    assert_eq!(outcome.status, "cancelled");
    assert!(outcome.summary.contains("Cancelled"));
}

#[test]
fn retries_retriable_provider_errors() {
    let (_dir, mut agent) = agent(
        Box::new(FlakyProvider {
            attempts: AtomicUsize::new(0),
        }),
        Arc::new(freenerf_core::AllowAll),
        PermissionMode::Full,
    );
    let rt = runtime();
    let outcome = rt.block_on(agent.chat("hello")).unwrap();
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.summary, "recovered");
}

#[test]
fn plain_text_tool_protocol_is_parsed_and_executed() {
    let (dir, mut agent) = agent(
        Box::new(
            ScriptedProvider::new(vec![
                ProviderResponse {
                    content: Some(
                        "<tool>{\"name\":\"read_file\",\"args\":{\"path\":\"a.txt\"}}</tool>".into(),
                    ),
                    ..Default::default()
                },
                ProviderResponse {
                    content: Some("done".into()),
                    ..Default::default()
                },
            ])
            .plain(),
        ),
        Arc::new(freenerf_core::AllowAll),
        PermissionMode::Full,
    );
    std::fs::write(dir.path().join("a.txt"), "content").unwrap();

    let rt = runtime();
    let outcome = rt.block_on(agent.chat("read it")).unwrap();
    assert_eq!(outcome.status, "completed");
    assert!(agent
        .messages()
        .iter()
        .any(|m| m.text().contains("[tool result] content")));
}

#[test]
fn streaming_tokens_are_emitted_once() {
    struct CaptureSink(Mutex<Vec<AgentEvent>>);
    impl EventSink for CaptureSink {
        fn emit(&self, event: AgentEvent) {
            self.0.lock().unwrap().push(event);
        }
    }

    let dir = tempfile::tempdir().unwrap();
    let workspace = Arc::new(Workspace::open(dir.path(), true).unwrap());
    let sink = Arc::new(CaptureSink(Mutex::new(Vec::new())));

    let options = AgentOptions {
        workspace,
        session_id: "s".into(),
        config: Config::default(),
        sink: sink.clone(),
        approval: Arc::new(freenerf_core::AllowAll),
        cancel: Cancellation::new(),
        session_store: None,
        task_store: None,
        image_api_key: None,
    };
    let mut agent = Agent::with_provider(
        options,
        Box::new(
            ScriptedProvider::new(vec![ProviderResponse {
                content: Some("streamed answer".into()),
                ..Default::default()
            }])
            .streaming(),
        ),
    )
    .unwrap();

    let rt = runtime();
    rt.block_on(agent.chat("hi")).unwrap();

    let events = sink.0.lock().unwrap();
    let tokens: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            AgentEvent::AssistantToken { text, .. } => Some(text.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(tokens, vec!["streamed answer".to_string()]);
    assert!(events
        .iter()
        .any(|event| matches!(event, AgentEvent::TaskCompleted { .. })));
}

#[test]
fn persists_user_and_assistant_messages_to_sessions() {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Arc::new(Workspace::open(dir.path(), true).unwrap());
    let store = Arc::new(SessionStore::open(workspace.sessions_db()).unwrap());
    let session_id = store.create("chat", "").unwrap();

    let options = AgentOptions {
        workspace,
        session_id: session_id.clone(),
        config: Config::default(),
        sink: Arc::new(NullSink),
        approval: Arc::new(freenerf_core::AllowAll),
        cancel: Cancellation::new(),
        session_store: Some(store.clone()),
        task_store: None,
        image_api_key: None,
    };
    let mut agent = Agent::with_provider(
        options,
        Box::new(ScriptedProvider::new(vec![ProviderResponse {
            content: Some("answer".into()),
            ..Default::default()
        }])),
    )
    .unwrap();

    let rt = runtime();
    rt.block_on(agent.chat("question")).unwrap();
    let messages = store.get_messages(&session_id).unwrap();
    assert_eq!(messages.len(), 2);
    assert_eq!(messages[0].role, "user");
    assert_eq!(messages[1].content, "answer");
    assert_eq!(messages[1].metadata["outcome"], "completed");
}

#[test]
fn parses_multiple_plain_tool_calls() {
    let (cleaned, calls) = parse_plain_calls(
        "before <tool>{\"name\":\"a\"}</tool> middle <tool>{\"tool\":\"b\",\"args\":{\"x\":1}}</tool> after",
    );
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].name, "a");
    assert_eq!(calls[1].name, "b");
    assert_eq!(cleaned, "before  middle  after");
}

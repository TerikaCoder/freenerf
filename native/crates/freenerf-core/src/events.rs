use serde::Serialize;
use serde_json::Value;

/// Structured events emitted by the Rust runtime and consumed by the Android
/// UI through a Kotlin `Flow`.
///
/// `#[serde(tag = "type")]` produces one JSON object per event, e.g.
/// `{"type":"assistant_token","message_id":"..","text":"hi"}`.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum AgentEvent {
    AssistantMessageStarted {
        message_id: String,
    },
    AssistantToken {
        message_id: String,
        text: String,
    },
    AssistantMessageCompleted {
        message_id: String,
        content: String,
    },
    ToolStarted {
        call_id: String,
        name: String,
        args: Value,
        summary: String,
        policy: String,
    },
    ToolProgress {
        call_id: String,
        name: String,
        message: String,
    },
    ToolCompleted {
        call_id: String,
        name: String,
        kind: String,
        ok: bool,
        output: String,
        elapsed: f64,
    },
    ToolFailed {
        call_id: String,
        name: String,
        kind: String,
        error: String,
        elapsed: f64,
    },
    PermissionRequested {
        request_id: String,
        name: String,
        detail: String,
        reason: String,
        policy: String,
    },
    PermissionResolved {
        request_id: String,
        approved: bool,
    },
    TaskStarted {
        task: Value,
    },
    TaskUpdated {
        task: Value,
    },
    TaskCompleted {
        task_id: String,
        status: String,
        summary: String,
    },
    TaskFailed {
        task_id: String,
        error: String,
    },
    AgentStatusChanged {
        status: String,
        busy: bool,
    },
    UsageUpdated {
        input_tokens: u64,
        output_tokens: u64,
        total_tokens: u64,
    },
    Error {
        message: String,
        fatal: bool,
    },
}

/// Sink used by the runtime to publish events. Implemented by the JNI layer.
pub trait EventSink: Send + Sync {
    fn emit(&self, event: AgentEvent);
}

/// A no-op sink for tests and headless runs.
pub struct NullSink;

impl EventSink for NullSink {
    fn emit(&self, _event: AgentEvent) {}
}

/// Optional progress callback used by streaming providers.
pub type TokenSink = Box<dyn Fn(&str) + Send + Sync>;

/// Co-operative cancellation shared by the agent loop, provider streams and
/// shell execution.
#[derive(Debug, Clone, Default)]
pub struct Cancellation {
    flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl Cancellation {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn cancel(&self) {
        self.flag.store(true, std::sync::atomic::Ordering::SeqCst);
    }

    pub fn reset(&self) {
        self.flag.store(false, std::sync::atomic::Ordering::SeqCst);
    }

    pub fn is_cancelled(&self) -> bool {
        self.flag.load(std::sync::atomic::Ordering::SeqCst)
    }
}

/// Synchronous approval hook. The Android implementation shows a dialog and
/// blocks the calling (Rust worker) thread until the user answers, which keeps
/// permission checks inside the Rust registry rather than in the UI.
pub trait ApprovalHandler: Send + Sync {
    fn approve(&self, name: &str, detail: &str, reason: &str, policy: &str) -> bool;
}

/// Denies every approval request. Used for `Denied` mode and headless runs.
pub struct DenyAll;

impl ApprovalHandler for DenyAll {
    fn approve(&self, _name: &str, _detail: &str, _reason: &str, _policy: &str) -> bool {
        false
    }
}

/// Approves every request, used by FULL mode and explicit headless `--yes`.
pub struct AllowAll;

impl ApprovalHandler for AllowAll {
    fn approve(&self, _name: &str, _detail: &str, _reason: &str, _policy: &str) -> bool {
        true
    }
}

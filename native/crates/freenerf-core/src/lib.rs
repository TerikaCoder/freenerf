//! Shared types, configuration and structured events for FreeNerf.
//!
//! This crate deliberately has no networking, filesystem or database code so
//! both the agent runtime and the Android JNI layer can depend on it.

pub mod config;
pub mod error;
pub mod events;
pub mod types;

pub use config::{ApiKeys, Config, ProviderKind, ProviderModels, ProviderUrls};
pub use error::{Error, Result};
pub use events::{
    AgentEvent, AllowAll, ApprovalHandler, Cancellation, DenyAll, EventSink, NullSink, TokenSink,
};
pub use types::{
    now_rfc3339, Capabilities, Message, PermissionMode, ProviderResponse, ToolCall, ToolOutcome,
    Usage,
};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

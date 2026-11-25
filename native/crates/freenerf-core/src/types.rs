use chrono::{SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Current UTC timestamp in the same RFC 3339 shape the Python stores use.
pub fn now_rfc3339() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Micros, true)
}

/// A message as stored in a session and handed to a provider adapter.
///
/// Providers translate this into their own wire format; the raw `gemini_parts`
/// field preserves Gemini `thoughtSignature` metadata across turns, matching the
/// Python implementation's `_gemini_parts` behaviour.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Message {
    pub role: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tool_calls: Vec<ToolCall>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gemini_parts: Option<Value>,
}

impl Message {
    pub fn system(content: impl Into<String>) -> Self {
        Self { role: "system".into(), content: Some(content.into()), ..Default::default() }
    }

    pub fn user(content: impl Into<String>) -> Self {
        Self { role: "user".into(), content: Some(content.into()), ..Default::default() }
    }

    pub fn assistant(content: impl Into<String>) -> Self {
        Self { role: "assistant".into(), content: Some(content.into()), ..Default::default() }
    }

    pub fn tool(tool_call_id: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            role: "tool".into(),
            content: Some(content.into()),
            tool_call_id: Some(tool_call_id.into()),
            ..Default::default()
        }
    }

    pub fn text(&self) -> &str {
        self.content.as_deref().unwrap_or("")
    }
}

/// A tool invocation requested by the model.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    /// Raw JSON argument string, exactly as the provider sent it.
    pub arguments: String,
}

/// Provider capability flags mirrored from `PROVIDER_INFO`.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub struct Capabilities {
    pub supports_tools: bool,
    pub supports_streaming: bool,
    pub supports_images: bool,
}

/// Normalized provider reply.
#[derive(Debug, Clone, Default)]
pub struct ProviderResponse {
    pub content: Option<String>,
    pub tool_calls: Vec<ToolCall>,
    /// Assistant message to append verbatim to history (keeps provider metadata).
    pub raw_message: Option<Message>,
    pub usage: Option<Usage>,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct Usage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub total_tokens: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PermissionMode {
    Denied,
    Normal,
    Full,
}

impl PermissionMode {
    pub fn as_str(self) -> &'static str {
        match self {
            PermissionMode::Denied => "denied",
            PermissionMode::Normal => "normal",
            PermissionMode::Full => "full",
        }
    }

    pub fn parse(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "denied" => PermissionMode::Denied,
            "full" => PermissionMode::Full,
            _ => PermissionMode::Normal,
        }
    }
}

impl std::fmt::Display for PermissionMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Structured tool result, matching the Python `ToolResult` contract.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ToolOutcome {
    pub name: String,
    /// `ok` | `error` | `denied` | `invalid` | `timeout` | `cancelled`
    pub kind: String,
    #[serde(default)]
    pub output: String,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub elapsed: f64,
    #[serde(default)]
    pub policy: String,
    #[serde(default)]
    pub permission_reason: String,
}

impl ToolOutcome {
    pub fn ok(name: impl Into<String>, output: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            kind: "ok".into(),
            output: output.into(),
            policy: "read".into(),
            ..Default::default()
        }
    }

    pub fn error(name: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            kind: "error".into(),
            error: message.into(),
            ..Default::default()
        }
    }

    pub fn is_ok(&self) -> bool {
        self.kind == "ok"
    }

    /// Text form appended to the conversation, identical to Python's
    /// `ToolResult.text()`.
    pub fn to_text(&self) -> String {
        match self.kind.as_str() {
            "ok" => self.output.clone(),
            "denied" => format!("Permission denied: {}", self.error),
            "invalid" => format!("Invalid tool arguments for {}: {}", self.name, self.error),
            "timeout" => format!(
                "Tool timed out after {:.0}s: {}",
                self.elapsed, self.name
            ),
            "cancelled" => "Cancelled by user".to_string(),
            _ => format!("Tool error ({}): {}", self.name, self.error),
        }
    }
}

impl Default for ToolOutcome {
    fn default() -> Self {
        Self {
            name: String::new(),
            kind: "ok".into(),
            output: String::new(),
            error: String::new(),
            elapsed: 0.0,
            policy: String::new(),
            permission_reason: String::new(),
        }
    }
}

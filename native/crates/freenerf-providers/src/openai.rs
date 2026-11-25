//! OpenAI-compatible chat completions (OpenRouter, FreeLLMAPI and custom
//! endpoints), including SSE streaming and tool-call fragment assembly.

use std::time::Duration;

use async_trait::async_trait;
use freenerf_core::{
    Cancellation, Capabilities, Error, Message, ProviderKind, ProviderResponse, Result, ToolCall,
    Usage,
};
use futures_util::StreamExt;
use serde_json::{json, Value};

use crate::{status_error, transport_error, StreamFn};
use crate::{Provider, redact_detail};

pub struct OpenAiCompatible {
    pub kind: ProviderKind,
    pub api_key: String,
    pub model: String,
    pub base_url: String,
    pub temperature: f64,
    pub max_tokens: u32,
    pub timeout: f64,
    pub streaming: bool,
    client: reqwest::Client,
}

impl OpenAiCompatible {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        kind: ProviderKind,
        api_key: String,
        model: String,
        base_url: String,
        temperature: f64,
        max_tokens: u32,
        timeout: f64,
        streaming: bool,
    ) -> Self {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs_f64(timeout.max(5.0)))
            .build()
            .unwrap_or_default();
        Self {
            kind,
            api_key,
            model,
            base_url: base_url.trim_end_matches('/').to_string(),
            temperature,
            max_tokens,
            timeout,
            streaming,
            client,
        }
    }

    fn endpoint(&self) -> String {
        format!("{}/chat/completions", self.base_url)
    }

    pub fn payload(
        &self,
        messages: &[Message],
        tools: Option<&[Value]>,
        stream: bool,
    ) -> Value {
        let wire_messages: Vec<Value> = messages.iter().map(message_to_wire).collect();
        let mut payload = json!({
            "model": self.model,
            "messages": wire_messages,
            "temperature": self.temperature,
        });
        if self.max_tokens > 0 {
            payload["max_tokens"] = json!(self.max_tokens);
        }
        if let Some(tools) = tools {
            if !tools.is_empty() {
                payload["tools"] = json!(tools);
                payload["tool_choice"] = json!("auto");
            }
        }
        if stream {
            payload["stream"] = json!(true);
        }
        payload
    }

    fn message_from_payload(data: &Value) -> Result<ProviderResponse> {
        let message = data
            .get("choices")
            .and_then(|c| c.get(0))
            .and_then(|c| c.get("message"))
            .ok_or_else(|| {
                Error::provider(format!(
                    "Invalid API response: {}",
                    redact_detail(&data.to_string())
                ))
            })?;

        if !message.is_object() {
            return Err(Error::provider(
                "Invalid API response: message is not an object",
            ));
        }

        let content = message
            .get("content")
            .and_then(|c| c.as_str())
            .map(str::to_string);

        let mut calls = Vec::new();
        if let Some(items) = message.get("tool_calls").and_then(|c| c.as_array()) {
            for (index, item) in items.iter().enumerate() {
                let id = item
                    .get("id")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| format!("call_{index}"));
                let function = item.get("function").cloned().unwrap_or(Value::Null);
                let name = function
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let arguments = match function.get("arguments") {
                    Some(Value::String(s)) => s.clone(),
                    Some(other) => other.to_string(),
                    None => "{}".into(),
                };
                calls.push(ToolCall { id, name, arguments });
            }
        }

        let usage = parse_usage(data.get("usage"));

        let raw_message = Message {
            role: "assistant".into(),
            content: content.clone(),
            tool_calls: calls.clone(),
            tool_call_id: None,
            gemini_parts: None,
        };

        Ok(ProviderResponse {
            content,
            tool_calls: calls,
            raw_message: Some(raw_message),
            usage,
        })
    }

    async fn chat_complete(
        &self,
        messages: &[Message],
        tools: Option<&[Value]>,
    ) -> Result<ProviderResponse> {
        let response = self
            .client
            .post(self.endpoint())
            .header("Content-Type", "application/json")
            .header("Authorization", format!("Bearer {}", self.api_key))
            .json(&self.payload(messages, tools, false))
            .send()
            .await
            .map_err(|e| transport_error("Provider request", e))?;

        if !response.status().is_success() {
            let status = response.status().as_u16();
            let body = response.text().await.unwrap_or_default();
            return Err(status_error(status, &body));
        }

        let data: Value = response
            .json()
            .await
            .map_err(|e| Error::provider(format!("Invalid API response: {}", redact_detail(&e.to_string()))))?;
        Self::message_from_payload(&data)
    }

    async fn chat_stream(
        &self,
        messages: &[Message],
        tools: Option<&[Value]>,
        on_token: StreamFn<'_>,
        cancel: &Cancellation,
    ) -> Result<ProviderResponse> {
        let response = self
            .client
            .post(self.endpoint())
            .header("Content-Type", "application/json")
            .header("Authorization", format!("Bearer {}", self.api_key))
            .json(&self.payload(messages, tools, true))
            .send()
            .await
            .map_err(|e| transport_error("Provider stream", e))?;

        if !response.status().is_success() {
            let status = response.status().as_u16();
            let body = response.text().await.unwrap_or_default();
            return Err(status_error(status, &body));
        }

        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_ascii_lowercase();

        // Some OpenAI-compatible servers ignore `stream` and answer with JSON.
        if !content_type.contains("text/event-stream") {
            let data: Value = response.json().await.map_err(|e| {
                Error::provider(format!("Invalid API response: {}", redact_detail(&e.to_string())))
            })?;
            return Self::message_from_payload(&data);
        }

        let mut stream = response.bytes_stream();
        let mut buffer = String::new();
        let mut content_parts: Vec<String> = Vec::new();
        let mut tool_calls: std::collections::BTreeMap<usize, ToolCall> =
            std::collections::BTreeMap::new();
        let mut finished = false;
        let mut data_seen = false;
        let mut usage: Option<Usage> = None;

        'outer: while let Some(chunk) = stream.next().await {
            if cancel.is_cancelled() {
                return Err(Error::Cancelled);
            }
            let chunk = chunk.map_err(|e| transport_error("Provider stream", e))?;
            buffer.push_str(&String::from_utf8_lossy(&chunk));

            while let Some(position) = buffer.find('\n') {
                let line = buffer[..position].trim_end_matches('\r').to_string();
                buffer.drain(..=position);
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                if line.starts_with("event:") {
                    if line.contains("response.completed") {
                        finished = true;
                    }
                    continue;
                }
                let body = if let Some(rest) = line.strip_prefix("data:") {
                    rest.trim()
                } else if line.starts_with(':') {
                    continue;
                } else {
                    continue;
                };

                if body == "[DONE]" {
                    finished = true;
                    break 'outer;
                }
                if body.is_empty() {
                    continue;
                }

                data_seen = true;
                let Ok(chunk) = serde_json::from_str::<Value>(body) else {
                    continue;
                };

                if usage.is_none() {
                    usage = parse_usage(chunk.get("usage"));
                }

                let Some(choice) = chunk
                    .get("choices")
                    .and_then(|c| c.as_array())
                    .and_then(|c| c.first())
                else {
                    continue;
                };
                let delta = choice.get("delta").cloned().unwrap_or(Value::Null);

                if let Some(text) = delta.get("content").and_then(|c| c.as_str()) {
                    if !text.is_empty() {
                        content_parts.push(text.to_string());
                        on_token(text);
                    }
                }

                if let Some(fragments) = delta.get("tool_calls").and_then(|c| c.as_array()) {
                    for fragment in fragments {
                        let index = fragment
                            .get("index")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0) as usize;
                        let entry = tool_calls.entry(index).or_insert_with(|| ToolCall {
                            id: String::new(),
                            name: String::new(),
                            arguments: String::new(),
                        });
                        if let Some(id) = fragment.get("id").and_then(|v| v.as_str()) {
                            if !id.is_empty() {
                                entry.id = id.to_string();
                            }
                        }
                        if let Some(function) = fragment.get("function") {
                            if let Some(name) = function.get("name").and_then(|v| v.as_str()) {
                                entry.name.push_str(name);
                            }
                            if let Some(arguments) =
                                function.get("arguments").and_then(|v| v.as_str())
                            {
                                entry.arguments.push_str(arguments);
                            }
                        }
                    }
                }
            }
        }

        if !data_seen && !finished {
            return Err(Error::provider("Provider stream returned no data"));
        }

        let calls: Vec<ToolCall> = tool_calls
            .into_values()
            .enumerate()
            .map(|(position, mut call)| {
                if call.id.is_empty() {
                    call.id = format!("call_{position}");
                }
                call
            })
            .collect();

        let content = {
            let joined = content_parts.concat();
            if joined.is_empty() {
                None
            } else {
                Some(joined)
            }
        };

        let raw_message = if calls.is_empty() && content.is_none() {
            None
        } else {
            Some(Message {
                role: "assistant".into(),
                content: content.clone(),
                tool_calls: calls.clone(),
                tool_call_id: None,
                gemini_parts: None,
            })
        };

        Ok(ProviderResponse { content, tool_calls: calls, raw_message, usage })
    }
}

#[async_trait]
impl Provider for OpenAiCompatible {
    fn name(&self) -> &str {
        self.kind.as_str()
    }

    fn capabilities(&self) -> Capabilities {
        Capabilities {
            supports_tools: true,
            supports_streaming: self.streaming,
            supports_images: self.kind == ProviderKind::FreeLlmApi,
        }
    }

    async fn chat(
        &self,
        messages: &[Message],
        tools: Option<&[Value]>,
        on_token: Option<StreamFn<'_>>,
        cancel: &Cancellation,
    ) -> Result<ProviderResponse> {
        match on_token {
            Some(callback) if self.streaming => {
                self.chat_stream(messages, tools, callback, cancel).await
            }
            _ => self.chat_complete(messages, tools).await,
        }
    }
}

fn message_to_wire(message: &Message) -> Value {
    let content = message
        .content
        .clone()
        .map(Value::String)
        .unwrap_or(Value::Null);

    if message.role == "tool" {
        return json!({
            "role": "tool",
            "tool_call_id": message.tool_call_id.clone().unwrap_or_default(),
            "content": content,
        });
    }

    if message.role == "assistant" && !message.tool_calls.is_empty() {
        let calls: Vec<Value> = message
            .tool_calls
            .iter()
            .map(|call| {
                json!({
                    "id": &call.id,
                    "type": "function",
                    "function": {"name": &call.name, "arguments": &call.arguments},
                })
            })
            .collect();
        return json!({"role": "assistant", "content": content, "tool_calls": calls});
    }

    json!({"role": message.role, "content": content})
}

fn parse_usage(value: Option<&Value>) -> Option<Usage> {
    let usage = value?;
    let get = |key: &str| usage.get(key).and_then(|v| v.as_u64()).unwrap_or(0);
    let input = get("prompt_tokens");
    let output = get("completion_tokens");
    let total = get("total_tokens");
    if input == 0 && output == 0 && total == 0 {
        return None;
    }
    Some(Usage {
        input_tokens: input,
        output_tokens: output,
        total_tokens: if total == 0 { input + output } else { total },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn provider() -> OpenAiCompatible {
        OpenAiCompatible::new(
            ProviderKind::OpenRouter,
            "sk-test".into(),
            "openai/gpt-4o-mini".into(),
            "https://openrouter.ai/api/v1".into(),
            0.2,
            4096,
            90.0,
            true,
        )
    }

    #[test]
    fn serializes_python_compatible_payload() {
        let payload = provider().payload(
            &[
                Message::system("sys"),
                Message::user("hi"),
                Message {
                    role: "assistant".into(),
                    content: None,
                    tool_calls: vec![ToolCall {
                        id: "call_1".into(),
                        name: "read_file".into(),
                        arguments: "{\"path\":\"a\"}".into(),
                    }],
                    tool_call_id: None,
                    gemini_parts: None,
                },
                Message::tool("call_1", "contents"),
            ],
            Some(&[json!({"type": "function", "function": {"name": "read_file"}})]),
            true,
        );

        assert_eq!(payload["model"], "openai/gpt-4o-mini");
        assert_eq!(payload["temperature"], 0.2);
        assert_eq!(payload["max_tokens"], 4096);
        assert_eq!(payload["stream"], true);
        assert_eq!(payload["tool_choice"], "auto");
        assert_eq!(payload["messages"][0]["role"], "system");
        assert_eq!(payload["messages"][2]["tool_calls"][0]["function"]["name"], "read_file");
        assert_eq!(payload["messages"][3]["role"], "tool");
        assert_eq!(payload["messages"][3]["tool_call_id"], "call_1");
    }

    #[test]
    fn parses_non_streaming_message_with_tool_calls() {
        let data = json!({
            "choices": [{"message": {
                "role": "assistant",
                "content": "thinking",
                "tool_calls": [{
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": "{}"}
                }]
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        });
        let response = OpenAiCompatible::message_from_payload(&data).unwrap();
        assert_eq!(response.content.as_deref(), Some("thinking"));
        assert_eq!(response.tool_calls[0].name, "list_files");
        assert_eq!(response.usage.unwrap().total_tokens, 15);
    }

    #[test]
    fn invalid_response_is_an_error() {
        assert!(OpenAiCompatible::message_from_payload(&json!({"choices": []})).is_err());
    }
}

//! Google Gemini `generateContent` adapter.
//!
//! Gemini has its own tool protocol (`functionDeclarations`,
//! `functionCall`/`functionResponse`) and does not stream in the original
//! implementation. Provider metadata in `_gemini_parts` is preserved across
//! turns exactly like the Python adapter does.

use std::collections::HashMap;
use std::time::Duration;

use async_trait::async_trait;
use freenerf_core::{
    Cancellation, Capabilities, Error, Message, ProviderResponse, Result, ToolCall,
};
use serde_json::{json, Value};
use uuid::Uuid;

use crate::{redact_detail, status_error, transport_error, Provider, StreamFn};

pub struct Gemini {
    pub api_key: String,
    pub model: String,
    pub base_url: String,
    pub temperature: f64,
    pub max_tokens: u32,
    pub timeout: f64,
    client: reqwest::Client,
}

impl Gemini {
    pub fn new(
        api_key: String,
        model: String,
        base_url: String,
        temperature: f64,
        max_tokens: u32,
        timeout: f64,
    ) -> Self {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs_f64(timeout.max(5.0)))
            .build()
            .unwrap_or_default();
        Self {
            api_key,
            model: model.trim_start_matches("models/").to_string(),
            base_url: base_url.trim_end_matches('/').to_string(),
            temperature,
            max_tokens,
            timeout,
            client,
        }
    }

    fn clean_schema(value: &Value) -> Value {
        match value {
            Value::Array(items) => {
                Value::Array(items.iter().map(Self::clean_schema).collect())
            }
            Value::Object(map) => {
                let mut output = serde_json::Map::new();
                for (key, item) in map {
                    if matches!(
                        key.as_str(),
                        "$schema" | "additionalProperties" | "default" | "title" | "examples"
                    ) {
                        continue;
                    }
                    output.insert(key.clone(), Self::clean_schema(item));
                }
                Value::Object(output)
            }
            other => other.clone(),
        }
    }

    fn convert_tools(tools: Option<&[Value]>) -> Vec<Value> {
        let mut declarations = Vec::new();
        for item in tools.unwrap_or(&[]) {
            let function = item.get("function").cloned().unwrap_or(Value::Null);
            let Some(name) = function.get("name").and_then(|v| v.as_str()) else {
                continue;
            };
            let parameters = function
                .get("parameters")
                .cloned()
                .unwrap_or_else(|| json!({"type": "object", "properties": {}}));
            declarations.push(json!({
                "name": name,
                "description": function.get("description").and_then(|v| v.as_str()).unwrap_or(""),
                "parameters": Self::clean_schema(&parameters),
            }));
        }
        if declarations.is_empty() {
            Vec::new()
        } else {
            vec![json!({"functionDeclarations": declarations})]
        }
    }

    /// Build the Gemini payload. Returns the payload and the call-id → name map
    /// used to answer tool results.
    pub fn build_payload(
        &self,
        messages: &[Message],
        tools: Option<&[Value]>,
    ) -> Result<(Value, HashMap<String, String>)> {
        let mut system_text = "You are Freenerf, an AI coding assistant.".to_string();
        let mut contents: Vec<Value> = Vec::new();
        let mut call_names: HashMap<String, String> = HashMap::new();

        fn add_part(contents: &mut Vec<Value>, role: &str, part: Value) {
            if let Some(last) = contents.last_mut() {
                if last.get("role").and_then(|v| v.as_str()) == Some(role) {
                    if let Some(parts) = last.get_mut("parts").and_then(|p| p.as_array_mut()) {
                        parts.push(part);
                        return;
                    }
                }
            }
            contents.push(json!({"role": role, "parts": [part]}));
        }

        for message in messages {
            match message.role.as_str() {
                "system" => {
                    system_text = message.text().to_string();
                }
                "user" => {
                    add_part(&mut contents, "user", json!({"text": message.text()}));
                }
                "assistant" => {
                    if !message.tool_calls.is_empty() {
                        let preserved = message.gemini_parts.as_ref().and_then(|v| v.as_array());

                        if let Some(parts) = preserved {
                            for part in parts {
                                add_part(&mut contents, "model", part.clone());
                            }
                        }

                        for call in &message.tool_calls {
                            let call_id = if call.id.is_empty() {
                                Uuid::new_v4().simple().to_string()
                            } else {
                                call.id.clone()
                            };
                            call_names.insert(call_id.clone(), call.name.clone());

                            if preserved.is_some() {
                                continue;
                            }

                            let args: Value = serde_json::from_str(&call.arguments)
                                .unwrap_or_else(|_| json!({}));
                            add_part(
                                &mut contents,
                                "model",
                                json!({"functionCall": {
                                    "name": call.name,
                                    "args": if args.is_object() { args } else { json!({}) },
                                    "id": call_id,
                                }}),
                            );
                        }
                    } else if !message.text().is_empty() {
                        add_part(&mut contents, "model", json!({"text": message.text()}));
                    }
                }
                "tool" => {
                    let call_id = message.tool_call_id.clone().unwrap_or_default();
                    let Some(name) = call_names.get(&call_id).cloned() else {
                        return Err(Error::provider(format!(
                            "Gemini: missing function name for tool call {call_id}"
                        )));
                    };
                    add_part(
                        &mut contents,
                        "user",
                        json!({"functionResponse": {
                            "name": name,
                            "id": call_id,
                            "response": {"result": message.text()},
                        }}),
                    );
                }
                _ => {}
            }
        }

        if contents.is_empty() {
            return Err(Error::provider("Gemini: conversation is empty"));
        }

        let mut payload = json!({
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_text}]},
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        });

        let gemini_tools = Self::convert_tools(tools);
        if !gemini_tools.is_empty() {
            payload["tools"] = json!(gemini_tools);
        }

        Ok((payload, call_names))
    }

    pub fn parse_response(&self, data: &Value) -> Result<ProviderResponse> {
        let Some(parts) = data
            .get("candidates")
            .and_then(|c| c.get(0))
            .and_then(|c| c.get("content"))
            .and_then(|c| c.get("parts"))
            .and_then(|p| p.as_array())
        else {
            let blocked = data
                .get("promptFeedback")
                .map(|v| redact_detail(&v.to_string()))
                .unwrap_or_default();
            let detail = if blocked.is_empty() {
                redact_detail(&data.to_string())
            } else {
                blocked
            };
            return Err(Error::provider(format!("Invalid Gemini response: {detail}")));
        };

        let mut text_parts: Vec<String> = Vec::new();
        let mut tool_calls: Vec<ToolCall> = Vec::new();
        let mut preserved_parts: Vec<Value> = Vec::new();

        for original in parts {
            let mut part = original.clone();

            if let Some(text) = part.get("text").and_then(|v| v.as_str()) {
                text_parts.push(text.to_string());
            }

            if let Some(function_call) = part.get("functionCall").and_then(|v| v.as_object()) {
                let mut function_call = function_call.clone();
                let call_id = function_call
                    .get("id")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| Uuid::new_v4().simple().to_string());
                function_call.insert("id".into(), json!(call_id));

                let args = function_call.get("args").cloned().unwrap_or_else(|| json!({}));
                let name = function_call
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();

                part["functionCall"] = Value::Object(function_call);

                tool_calls.push(ToolCall {
                    id: call_id,
                    name,
                    arguments: args.to_string(),
                });
            }

            preserved_parts.push(part);
        }

        let content = {
            let joined = text_parts.join("\n");
            if joined.is_empty() {
                None
            } else {
                Some(joined)
            }
        };

        let raw_message = if tool_calls.is_empty() {
            None
        } else {
            Some(Message {
                role: "assistant".into(),
                content: None,
                tool_calls: tool_calls.clone(),
                tool_call_id: None,
                gemini_parts: Some(Value::Array(preserved_parts)),
            })
        };

        let usage = data.get("usageMetadata").map(|usage| freenerf_core::Usage {
            input_tokens: usage
                .get("promptTokenCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0),
            output_tokens: usage
                .get("candidatesTokenCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0),
            total_tokens: usage
                .get("totalTokenCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0),
        });

        Ok(ProviderResponse {
            content,
            tool_calls,
            raw_message,
            usage,
        })
    }
}

#[async_trait]
impl Provider for Gemini {
    fn name(&self) -> &str {
        "gemini"
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
        messages: &[Message],
        tools: Option<&[Value]>,
        _on_token: Option<StreamFn<'_>>,
        cancel: &Cancellation,
    ) -> Result<ProviderResponse> {
        if cancel.is_cancelled() {
            return Err(Error::Cancelled);
        }

        let (payload, _) = self.build_payload(messages, tools)?;

        let response = self
            .client
            .post(format!(
                "{}/v1beta/models/{}:generateContent",
                self.base_url, self.model
            ))
            .header("Content-Type", "application/json")
            .header("x-goog-api-key", &self.api_key)
            .json(&payload)
            .send()
            .await
            .map_err(|e| transport_error("Gemini request", e))?;

        if !response.status().is_success() {
            let status = response.status().as_u16();
            let body = response.text().await.unwrap_or_default();
            return Err(status_error(status, &body));
        }

        let data: Value = response.json().await.map_err(|e| {
            Error::provider(format!("Invalid Gemini response: {}", redact_detail(&e.to_string())))
        })?;
        self.parse_response(&data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gemini() -> Gemini {
        Gemini::new(
            "AIza-test".into(),
            "models/gemini-2.5-flash".into(),
            "https://generativelanguage.googleapis.com".into(),
            0.2,
            4096,
            90.0,
        )
    }

    #[test]
    fn converts_messages_and_tools() {
        let messages = vec![
            Message::system("custom system"),
            Message::user("hello"),
            Message {
                role: "assistant".into(),
                content: None,
                tool_calls: vec![ToolCall {
                    id: "call_a".into(),
                    name: "read_file".into(),
                    arguments: "{\"path\":\"README.md\"}".into(),
                }],
                tool_call_id: None,
                gemini_parts: None,
            },
            Message::tool("call_a", "file body"),
        ];
        let tools = vec![json!({"type": "function", "function": {
            "name": "read_file",
            "description": "read",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": false}
        }})];

        let (payload, names) = gemini().build_payload(&messages, Some(&tools)).unwrap();
        assert_eq!(gemini().model, "gemini-2.5-flash");
        assert_eq!(payload["systemInstruction"]["parts"][0]["text"], "custom system");
        assert_eq!(payload["contents"][0]["role"], "user");
        assert_eq!(
            payload["contents"][1]["parts"][0]["functionCall"]["name"],
            "read_file"
        );
        assert_eq!(
            payload["contents"][2]["parts"][0]["functionResponse"]["name"],
            "read_file"
        );
        assert_eq!(names.get("call_a").unwrap(), "read_file");
        // `additionalProperties` is stripped for Gemini.
        assert!(payload["tools"][0]["functionDeclarations"][0]["parameters"]
            .get("additionalProperties")
            .is_none());
    }

    #[test]
    fn parses_function_call_and_text() {
        let data = json!({
            "candidates": [{"content": {"parts": [
                {"text": "Let me check."},
                {"functionCall": {"name": "list_files", "args": {"path": "."}}}
            ]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 7}
        });
        let response = gemini().parse_response(&data).unwrap();
        assert_eq!(response.content.as_deref(), Some("Let me check."));
        assert_eq!(response.tool_calls[0].name, "list_files");
        assert!(response.tool_calls[0].id.len() >= 8);
        assert!(response.raw_message.unwrap().gemini_parts.is_some());
        assert_eq!(response.usage.unwrap().total_tokens, 7);
    }

    #[test]
    fn blocked_response_is_an_error() {
        let data = json!({"promptFeedback": {"blockReason": "SAFETY"}});
        let error = gemini().parse_response(&data).unwrap_err();
        assert!(error.to_string().contains("SAFETY"));
    }

    #[test]
    fn tool_result_without_call_name_fails() {
        let messages = vec![Message::user("x"), Message::tool("unknown", "body")];
        assert!(gemini().build_payload(&messages, None).is_err());
    }
}

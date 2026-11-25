//! LLM provider adapters.
//!
//! The four families implemented by the Python project are preserved:
//! OpenRouter, Google Gemini, FreeLLMAPI and any OpenAI-compatible endpoint.
//! Each adapter owns its wire format; the agent only sees `ProviderResponse`.

pub mod catalog;
pub mod gemini;
pub mod openai;

use async_trait::async_trait;
use freenerf_core::{Cancellation, Capabilities, Config, Error, Message, ProviderKind, ProviderResponse, Result};
use serde_json::Value;

pub use catalog::{clear_cache, get_models, CatalogError, ModelInfo};

/// Callback used for token-by-token streaming.
pub type StreamFn<'a> = &'a (dyn Fn(&str) + Send + Sync);

#[async_trait]
pub trait Provider: Send + Sync {
    fn name(&self) -> &str;

    fn capabilities(&self) -> Capabilities;

    async fn chat(
        &self,
        messages: &[Message],
        tools: Option<&[Value]>,
        on_token: Option<StreamFn<'_>>,
        cancel: &Cancellation,
    ) -> Result<ProviderResponse>;
}

pub fn provider_info(kind: ProviderKind) -> Value {
    let (label, default_model, tools, streaming, images) = match kind {
        ProviderKind::OpenRouter => ("OpenRouter", "openai/gpt-4o-mini", true, true, false),
        ProviderKind::Gemini => ("Google Gemini", "gemini-2.5-flash", true, false, false),
        ProviderKind::FreeLlmApi => ("FreeLLMAPI", "auto", true, true, true),
        ProviderKind::OpenAiCompatible => ("OpenAI-compatible", "gpt-4o-mini", true, true, false),
    };
    serde_json::json!({
        "provider": kind.as_str(),
        "label": label,
        "default_model": default_model,
        "supports_tools": tools,
        "supports_streaming": streaming,
        "supports_images": images,
    })
}

/// Build the provider described by `config`, matching Python's
/// `create_provider_for_config` (including its missing-key errors).
pub fn create_provider(config: &Config) -> Result<Box<dyn Provider>> {
    let api_key = config.active_key().trim();
    if api_key.is_empty() {
        let message = match config.provider {
            ProviderKind::Gemini => "Missing Gemini API key".to_string(),
            ProviderKind::FreeLlmApi => "Missing FreeLLMAPI unified API key".to_string(),
            _ => "No API key configured for the selected provider".to_string(),
        };
        return Err(Error::provider(message));
    }

    let model = config.active_model().to_string();
    let base_url = config.active_base_url().to_string();

    match config.provider {
        ProviderKind::Gemini => Ok(Box::new(gemini::Gemini::new(
            api_key.to_string(),
            model,
            base_url,
            config.temperature,
            config.max_tokens,
            config.request_timeout,
        ))),
        kind => Ok(Box::new(openai::OpenAiCompatible::new(
            kind,
            api_key.to_string(),
            model,
            base_url,
            config.temperature,
            config.max_tokens,
            config.request_timeout,
            config.streaming,
        ))),
    }
}

/// Map an HTTP status/body pair onto the Python error semantics.
pub(crate) fn status_error(status: u16, detail: &str) -> Error {
    let detail = redact_detail(detail);
    let message = match status {
        401 => format!("Invalid or missing API key (HTTP 401): {detail}"),
        403 => format!("Access denied (HTTP 403): {detail}"),
        404 => format!("Model or endpoint not found (HTTP 404): {detail}"),
        429 => format!("Rate limited (HTTP 429): {detail}"),
        s if s >= 500 => format!("Provider server error (HTTP {s}): {detail}"),
        other => format!("HTTP {other}: {detail}"),
    };
    let retriable = matches!(status, 408 | 409 | 425 | 429 | 500 | 502 | 503 | 504);
    Error::status(message, status, retriable)
}

pub(crate) fn redact_detail(detail: &str) -> String {
    let mut text: String = detail.chars().take(800).collect();
    for marker in ["sk-", "AIza"] {
        if text.contains(marker) {
            text = text
                .split_whitespace()
                .map(|word| {
                    if word.contains(marker) {
                        "[REDACTED]".to_string()
                    } else {
                        word.to_string()
                    }
                })
                .collect::<Vec<_>>()
                .join(" ");
        }
    }
    text
}

pub(crate) fn transport_error(context: &str, err: reqwest::Error) -> Error {
    if err.is_timeout() {
        return Error::status(format!("{context} timed out"), 0, true);
    }
    Error::status(
        format!("{context} connection error: {}", redact_detail(&err.to_string())),
        0,
        true,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_info_covers_all_families() {
        for kind in ProviderKind::ALL {
            let info = provider_info(kind);
            assert_eq!(info["provider"], kind.as_str());
            assert!(info["supports_tools"].as_bool().unwrap());
        }
        assert!(!provider_info(ProviderKind::Gemini)["supports_streaming"]
            .as_bool()
            .unwrap());
    }

    #[test]
    fn missing_key_is_reported_before_network() {
        let mut config = Config::default();
        config.provider = ProviderKind::Gemini;
        let error = match create_provider(&config) {
            Ok(_) => panic!("expected a missing-key error"),
            Err(error) => error,
        };
        assert!(error.to_string().contains("Missing Gemini API key"));
    }

    #[test]
    fn status_errors_are_retriable_like_python() {
        assert!(status_error(429, "slow down").is_retriable());
        assert!(status_error(503, "down").is_retriable());
        assert!(!status_error(401, "bad key").is_retriable());
        assert!(status_error(401, "bad sk-abcdefghijk").to_string().contains("[REDACTED]"));
    }
}

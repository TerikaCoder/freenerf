use serde::{Deserialize, Serialize};

use crate::types::PermissionMode;

pub const DEFAULT_OPENROUTER: &str = "openai/gpt-4o-mini";
pub const DEFAULT_GEMINI: &str = "gemini-2.5-flash";
pub const DEFAULT_FREELLMAPI: &str = "auto";
pub const DEFAULT_OPENAI: &str = "gpt-4o-mini";

pub const OPENROUTER_URL: &str = "https://openrouter.ai/api/v1";
pub const GEMINI_URL: &str = "https://generativelanguage.googleapis.com";
pub const FREELLMAPI_URL: &str = "https://freellmapi-3d3g.onrender.com/v1";
pub const OPENAI_URL: &str = "https://api.openai.com/v1";

/// The four provider families implemented by the original project.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ProviderKind {
    #[serde(rename = "openrouter")]
    OpenRouter,
    #[serde(rename = "gemini")]
    Gemini,
    #[serde(rename = "freellmapi")]
    FreeLlmApi,
    #[serde(rename = "openai-compatible")]
    OpenAiCompatible,
}

impl ProviderKind {
    pub const ALL: [ProviderKind; 4] = [
        ProviderKind::OpenRouter,
        ProviderKind::Gemini,
        ProviderKind::FreeLlmApi,
        ProviderKind::OpenAiCompatible,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            ProviderKind::OpenRouter => "openrouter",
            ProviderKind::Gemini => "gemini",
            ProviderKind::FreeLlmApi => "freellmapi",
            ProviderKind::OpenAiCompatible => "openai-compatible",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            ProviderKind::OpenRouter => "OpenRouter",
            ProviderKind::Gemini => "Google Gemini",
            ProviderKind::FreeLlmApi => "FreeLLMAPI",
            ProviderKind::OpenAiCompatible => "OpenAI-compatible",
        }
    }

    pub fn parse(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "gemini" => ProviderKind::Gemini,
            "freellmapi" => ProviderKind::FreeLlmApi,
            // The legacy provider name is normalized exactly like Python does.
            "openai" | "openai-compatible" => ProviderKind::OpenAiCompatible,
            _ => ProviderKind::OpenRouter,
        }
    }
}

impl Default for ProviderKind {
    fn default() -> Self {
        ProviderKind::OpenRouter
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct ApiKeys {
    pub openrouter: String,
    pub gemini: String,
    pub freellmapi: String,
    pub openai: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct ProviderModels {
    pub openrouter: String,
    pub gemini: String,
    pub freellmapi: String,
    pub openai: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ProviderUrls {
    pub openrouter: String,
    pub gemini: String,
    pub freellmapi: String,
    pub openai: String,
}

impl Default for ProviderUrls {
    fn default() -> Self {
        Self {
            openrouter: OPENROUTER_URL.into(),
            gemini: GEMINI_URL.into(),
            freellmapi: FREELLMAPI_URL.into(),
            openai: OPENAI_URL.into(),
        }
    }
}

/// Runtime configuration. API keys are supplied per invocation by the Android
/// layer from Keystore-encrypted storage and are never written to the session
/// or task databases.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub provider: ProviderKind,
    pub api_keys: ApiKeys,
    pub models: ProviderModels,
    pub base_urls: ProviderUrls,
    pub temperature: f64,
    pub max_tokens: u32,
    pub request_timeout: f64,
    pub max_retries: u32,
    pub max_agent_steps: u32,
    pub max_context_chars: usize,
    pub custom_prompt: String,
    pub streaming: bool,
    pub permission_mode: PermissionMode,
    pub image_model: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            provider: ProviderKind::OpenRouter,
            api_keys: ApiKeys::default(),
            models: ProviderModels {
                openrouter: DEFAULT_OPENROUTER.into(),
                gemini: DEFAULT_GEMINI.into(),
                freellmapi: DEFAULT_FREELLMAPI.into(),
                openai: DEFAULT_OPENAI.into(),
            },
            base_urls: ProviderUrls::default(),
            temperature: 0.2,
            max_tokens: 4096,
            request_timeout: 90.0,
            max_retries: 2,
            max_agent_steps: 25,
            max_context_chars: 120_000,
            custom_prompt: String::new(),
            streaming: true,
            permission_mode: PermissionMode::Normal,
            image_model: "gpt-image-1".into(),
        }
    }
}

impl Config {
    pub fn validate(&mut self) {
        self.temperature = clamp_f64(self.temperature, 0.0, 2.0, 0.2);
        self.max_tokens = clamp_u32(self.max_tokens, 1, 32768, 4096);
        self.request_timeout = clamp_f64(self.request_timeout, 5.0, 600.0, 90.0);
        self.max_retries = clamp_u32(self.max_retries, 0, 5, 2);
        self.max_agent_steps = clamp_u32(self.max_agent_steps, 1, 100, 25);
        self.max_context_chars =
            clamp_u32(self.max_context_chars as u32, 4_000, 2_000_000, 120_000) as usize;
        self.base_urls.openrouter = trim_url(&self.base_urls.openrouter, OPENROUTER_URL);
        self.base_urls.gemini = trim_url(&self.base_urls.gemini, GEMINI_URL);
        self.base_urls.freellmapi = trim_url(&self.base_urls.freellmapi, FREELLMAPI_URL);
        self.base_urls.openai = trim_url(&self.base_urls.openai, OPENAI_URL);
        if self.models.openrouter.trim().is_empty() {
            self.models.openrouter = DEFAULT_OPENROUTER.into();
        }
        if self.models.gemini.trim().is_empty() {
            self.models.gemini = DEFAULT_GEMINI.into();
        }
        if self.models.freellmapi.trim().is_empty() {
            self.models.freellmapi = DEFAULT_FREELLMAPI.into();
        }
        if self.models.openai.trim().is_empty() {
            self.models.openai = DEFAULT_OPENAI.into();
        }
        if self.image_model.trim().is_empty() {
            self.image_model = "gpt-image-1".into();
        }
    }

    pub fn active_key(&self) -> &str {
        match self.provider {
            ProviderKind::OpenRouter => &self.api_keys.openrouter,
            ProviderKind::Gemini => &self.api_keys.gemini,
            ProviderKind::FreeLlmApi => &self.api_keys.freellmapi,
            ProviderKind::OpenAiCompatible => &self.api_keys.openai,
        }
    }

    pub fn active_model(&self) -> &str {
        let value = match self.provider {
            ProviderKind::OpenRouter => &self.models.openrouter,
            ProviderKind::Gemini => &self.models.gemini,
            ProviderKind::FreeLlmApi => &self.models.freellmapi,
            ProviderKind::OpenAiCompatible => &self.models.openai,
        };
        value.trim()
    }

    pub fn active_base_url(&self) -> &str {
        match self.provider {
            ProviderKind::OpenRouter => &self.base_urls.openrouter,
            ProviderKind::Gemini => &self.base_urls.gemini,
            ProviderKind::FreeLlmApi => &self.base_urls.freellmapi,
            ProviderKind::OpenAiCompatible => &self.base_urls.openai,
        }
    }

    pub fn set_active_key(&mut self, key: &str) {
        let value = key.trim().to_string();
        match self.provider {
            ProviderKind::OpenRouter => self.api_keys.openrouter = value,
            ProviderKind::Gemini => self.api_keys.gemini = value,
            ProviderKind::FreeLlmApi => self.api_keys.freellmapi = value,
            ProviderKind::OpenAiCompatible => self.api_keys.openai = value,
        }
    }

    pub fn set_active_model(&mut self, model: &str) {
        let value = model.trim().to_string();
        match self.provider {
            ProviderKind::OpenRouter => self.models.openrouter = value,
            ProviderKind::Gemini => self.models.gemini = value,
            ProviderKind::FreeLlmApi => self.models.freellmapi = value,
            ProviderKind::OpenAiCompatible => self.models.openai = value,
        }
    }

    /// Settings serialization for the UI: keys are replaced by a placeholder.
    pub fn public_json(&self) -> serde_json::Value {
        let mut value = serde_json::to_value(self).unwrap_or_default();
        if let Some(keys) = value.get_mut("api_keys").and_then(|k| k.as_object_mut()) {
            for (_, item) in keys.iter_mut() {
                let present = item.as_str().map(|s| !s.is_empty()).unwrap_or(false);
                *item = serde_json::Value::String(if present {
                    "********".into()
                } else {
                    String::new()
                });
            }
        }
        value
    }
}

fn clamp_f64(value: f64, min: f64, max: f64, fallback: f64) -> f64 {
    if !value.is_finite() {
        return fallback;
    }
    value.max(min).min(max)
}

fn clamp_u32(value: u32, min: u32, max: u32, fallback: u32) -> u32 {
    if value == 0 {
        return fallback;
    }
    value.max(min).min(max)
}

fn trim_url(value: &str, fallback: &str) -> String {
    let trimmed = value.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        fallback.to_string()
    } else {
        trimmed.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_python_reference() {
        let config = Config::default();
        assert_eq!(config.provider, ProviderKind::OpenRouter);
        assert_eq!(config.active_model(), "openai/gpt-4o-mini");
        assert_eq!(config.active_base_url(), "https://openrouter.ai/api/v1");
        assert_eq!(config.max_agent_steps, 25);
        assert_eq!(config.permission_mode, PermissionMode::Normal);
    }

    #[test]
    fn legacy_openai_name_normalizes() {
        assert_eq!(
            ProviderKind::parse("openai"),
            ProviderKind::OpenAiCompatible
        );
        assert_eq!(
            ProviderKind::parse("openai-compatible"),
            ProviderKind::OpenAiCompatible
        );
        assert_eq!(ProviderKind::parse("bogus"), ProviderKind::OpenRouter);
    }

    #[test]
    fn validation_clamps_and_trims() {
        let mut config = Config {
            temperature: 9.0,
            max_tokens: 0,
            request_timeout: 1.0,
            max_retries: 99,
            max_agent_steps: 1000,
            max_context_chars: 10,
            base_urls: ProviderUrls {
                openrouter: " https://example.test/v1/ ".into(),
                ..Default::default()
            },
            ..Default::default()
        };
        config.validate();
        assert_eq!(config.temperature, 2.0);
        assert_eq!(config.max_tokens, 4096);
        assert_eq!(config.request_timeout, 5.0);
        assert_eq!(config.max_retries, 5);
        assert_eq!(config.max_agent_steps, 100);
        assert_eq!(config.max_context_chars, 4_000);
        assert_eq!(config.active_base_url(), "https://example.test/v1");
    }

    #[test]
    fn keys_are_scoped_per_provider() {
        let mut config = Config::default();
        config.set_active_key("sk-openrouter");
        config.provider = ProviderKind::Gemini;
        assert_eq!(config.active_key(), "");
        config.set_active_key("AIza-gemini");
        config.provider = ProviderKind::OpenRouter;
        assert_eq!(config.active_key(), "sk-openrouter");
        assert_eq!(config.public_json()["api_keys"]["openrouter"], "********");
        assert_eq!(config.public_json()["api_keys"]["gemini"], "********");
    }
}

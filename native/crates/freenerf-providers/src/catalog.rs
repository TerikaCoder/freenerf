//! Model catalogs for every supported provider, with a 5 minute cache.
//!
//! Ported from `src/freenerf/llm/catalog.py`; endpoints, filters and the
//! FreeLLMAPI `auto` aliases are preserved.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use freenerf_core::{ProviderKind, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::redact_detail;

pub const CACHE_TTL: Duration = Duration::from_secs(300);
const OPENROUTER_MODELS_URL: &str = "https://openrouter.ai/api/v1/models";
const GEMINI_MODELS_URL: &str = "https://generativelanguage.googleapis.com/v1beta/models";
const FREELLMAPI_DEFAULT_URL: &str = "https://freellmapi-3d3g.onrender.com/v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelInfo {
    pub id: String,
    pub name: String,
    pub provider: String,
    #[serde(default)]
    pub context_length: u64,
    #[serde(default)]
    pub output_limit: u64,
    #[serde(default)]
    pub pricing_label: String,
    #[serde(default)]
    pub supports_tools: Option<bool>,
    #[serde(default)]
    pub description: String,
}

#[derive(Debug, Clone)]
pub struct CatalogError(pub String);

impl std::fmt::Display for CatalogError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for CatalogError {}

impl From<CatalogError> for freenerf_core::Error {
    fn from(value: CatalogError) -> Self {
        freenerf_core::Error::Provider(value.0)
    }
}

type CacheKey = (String, String, String, bool);

fn cache() -> &'static Mutex<HashMap<CacheKey, (Instant, Vec<ModelInfo>)>> {
    static CACHE: OnceLock<Mutex<HashMap<CacheKey, (Instant, Vec<ModelInfo>)>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

pub fn clear_cache() {
    if let Ok(mut guard) = cache().lock() {
        guard.clear();
    }
}

fn price_label(pricing: Option<&Value>) -> String {
    let Some(pricing) = pricing else {
        return "Unknown".into();
    };
    let read = |key: &str| -> Option<f64> {
        match pricing.get(key) {
            Some(Value::String(s)) => s.parse::<f64>().ok(),
            Some(Value::Number(n)) => n.as_f64(),
            _ => None,
        }
    };
    let (Some(prompt), Some(completion)) = (read("prompt"), read("completion")) else {
        return "Unknown".into();
    };
    if prompt == 0.0 && completion == 0.0 {
        "Free".into()
    } else {
        "Paid".into()
    }
}

fn client() -> Result<reqwest::Client> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| freenerf_core::Error::Http(e.to_string()))
}

async fn get_json(request: reqwest::RequestBuilder) -> std::result::Result<Value, CatalogError> {
    let response = request
        .send()
        .await
        .map_err(|e| CatalogError(format!("catalog connection error: {}", redact_detail(&e.to_string()))))?;
    let status = response.status();
    if !status.is_success() {
        return Err(CatalogError(format!("catalog HTTP {}", status.as_u16())));
    }
    response
        .json::<Value>()
        .await
        .map_err(|_| CatalogError("catalog returned invalid JSON".into()))
}

async fn openrouter_models(
    api_key: &str,
    query: &str,
    tools_only: bool,
) -> std::result::Result<Vec<ModelInfo>, CatalogError> {
    let mut params: Vec<(&str, String)> = vec![
        ("output_modalities", "text".into()),
        ("sort", "most-popular".into()),
    ];
    if tools_only {
        params.push(("supported_parameters", "tools".into()));
    }
    if !query.is_empty() {
        params.push(("q", query.to_string()));
    }

    let mut request = client()
        .map_err(|e| CatalogError(e.to_string()))?
        .get(OPENROUTER_MODELS_URL)
        .query(&params)
        .header("Accept", "application/json");
    if !api_key.is_empty() {
        request = request.header("Authorization", format!("Bearer {api_key}"));
    }

    let payload = get_json(request).await.map_err(|e| {
        CatalogError(e.0.replace("catalog", "OpenRouter catalog"))
    })?;

    let mut result = Vec::new();
    for item in payload.get("data").and_then(|d| d.as_array()).cloned().unwrap_or_default() {
        let Some(model_id) = item.get("id").and_then(|v| v.as_str()) else {
            continue;
        };
        let supported = item
            .get("supported_parameters")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let supports_tools = supported.iter().any(|v| v.as_str() == Some("tools"));
        if tools_only && !supports_tools {
            continue;
        }
        result.push(ModelInfo {
            id: model_id.to_string(),
            name: item
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or(model_id)
                .to_string(),
            provider: "OpenRouter".into(),
            context_length: item.get("context_length").and_then(|v| v.as_u64()).unwrap_or(0),
            output_limit: 0,
            pricing_label: price_label(item.get("pricing")),
            supports_tools: Some(supports_tools),
            description: String::new(),
        });
    }
    Ok(result)
}

async fn gemini_models(
    api_key: &str,
    query: &str,
) -> std::result::Result<Vec<ModelInfo>, CatalogError> {
    if api_key.is_empty() {
        return Err(CatalogError("Gemini API key is required".into()));
    }
    let http = client().map_err(|e| CatalogError(e.to_string()))?;
    let mut result = Vec::new();
    let mut page_token: Option<String> = None;

    loop {
        let mut params: Vec<(&str, String)> = vec![("pageSize", "1000".into())];
        if let Some(token) = &page_token {
            params.push(("pageToken", token.clone()));
        }
        let payload = get_json(
            http.get(GEMINI_MODELS_URL)
                .query(&params)
                .header("x-goog-api-key", api_key)
                .header("Accept", "application/json"),
        )
        .await
        .map_err(|e| CatalogError(e.0.replace("catalog", "Gemini catalog")))?;

        for item in payload
            .get("models")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default()
        {
            let methods = item
                .get("supportedGenerationMethods")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();
            if !methods.iter().any(|m| m.as_str() == Some("generateContent")) {
                continue;
            }
            let model_id = item
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim_start_matches("models/");
            if model_id.is_empty() {
                continue;
            }
            let name = item
                .get("displayName")
                .and_then(|v| v.as_str())
                .unwrap_or(model_id);
            if !query.is_empty() && !format!("{model_id} {name}").to_lowercase().contains(&query.to_lowercase()) {
                continue;
            }
            result.push(ModelInfo {
                id: model_id.to_string(),
                name: name.to_string(),
                provider: "Google Gemini".into(),
                context_length: item.get("inputTokenLimit").and_then(|v| v.as_u64()).unwrap_or(0),
                output_limit: item.get("outputTokenLimit").and_then(|v| v.as_u64()).unwrap_or(0),
                pricing_label: "Unknown".into(),
                supports_tools: None,
                description: item
                    .get("description")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }

        page_token = payload
            .get("nextPageToken")
            .and_then(|v| v.as_str())
            .map(str::to_string);
        if page_token.is_none() {
            break;
        }
    }

    Ok(result)
}

async fn freellmapi_models(
    api_key: &str,
    query: &str,
    base_url: &str,
) -> std::result::Result<Vec<ModelInfo>, CatalogError> {
    if api_key.is_empty() {
        return Err(CatalogError("FreeLLMAPI unified API key is required".into()));
    }
    let endpoint = format!(
        "{}/models",
        if base_url.trim().is_empty() {
            FREELLMAPI_DEFAULT_URL
        } else {
            base_url.trim_end_matches('/')
        }
    );
    let payload = get_json(
        client()
            .map_err(|e| CatalogError(e.to_string()))?
            .get(endpoint)
            .header("Authorization", format!("Bearer {api_key}"))
            .header("Accept", "application/json"),
    )
    .await
    .map_err(|e| CatalogError(e.0.replace("catalog", "FreeLLMAPI catalog")))?;

    let items: Vec<Value> = if let Some(list) = payload.as_array() {
        list.clone()
    } else if let Some(data) = payload.get("data").and_then(|v| v.as_array()) {
        data.clone()
    } else if let Some(models) = payload.get("models").and_then(|v| v.as_array()) {
        models.clone()
    } else {
        Vec::new()
    };

    let mut result = Vec::new();
    for item in items {
        if item.get("available").and_then(|v| v.as_bool()) == Some(false) {
            continue;
        }
        let Some(model_id) = item
            .get("id")
            .or_else(|| item.get("name"))
            .or_else(|| item.get("model"))
            .and_then(|v| v.as_str())
        else {
            continue;
        };
        let name = item
            .get("name")
            .or_else(|| item.get("display_name"))
            .and_then(|v| v.as_str())
            .unwrap_or(model_id);
        if !query.is_empty() && !format!("{model_id} {name}").to_lowercase().contains(&query.to_lowercase()) {
            continue;
        }
        result.push(ModelInfo {
            id: model_id.to_string(),
            name: name.to_string(),
            provider: "FreeLLMAPI".into(),
            context_length: item.get("context_length").and_then(|v| v.as_u64()).unwrap_or(0),
            output_limit: 0,
            pricing_label: item
                .get("pricing_label")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown")
                .to_string(),
            supports_tools: item.get("supports_tools").and_then(|v| v.as_bool()),
            description: item
                .get("description")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
        });
    }

    let known: Vec<String> = result.iter().map(|m| m.id.clone()).collect();
    for (position, alias) in ["auto", "auto:fast"].into_iter().enumerate() {
        if known.iter().any(|id| id == alias) {
            continue;
        }
        if !query.is_empty() && !alias.contains(query.to_lowercase().as_str()) {
            continue;
        }
        result.insert(
            position,
            ModelInfo {
                id: alias.into(),
                name: alias.into(),
                provider: "FreeLLMAPI".into(),
                context_length: 0,
                output_limit: 0,
                pricing_label: "Unknown".into(),
                supports_tools: None,
                description: String::new(),
            },
        );
    }

    Ok(result)
}

async fn openai_compatible_models(
    api_key: &str,
    base_url: &str,
    query: &str,
) -> std::result::Result<Vec<ModelInfo>, CatalogError> {
    let base_url = base_url.trim_end_matches('/');
    if base_url.is_empty() {
        return Err(CatalogError("OpenAI-compatible base URL is required".into()));
    }
    let mut request = client()
        .map_err(|e| CatalogError(e.to_string()))?
        .get(format!("{base_url}/models"))
        .header("Accept", "application/json");
    if !api_key.is_empty() {
        request = request.header("Authorization", format!("Bearer {api_key}"));
    }
    let payload = get_json(request)
        .await
        .map_err(|e| CatalogError(e.0.replace("catalog", "Model catalog")))?;

    let items = if let Some(list) = payload.as_array() {
        list.clone()
    } else if let Some(data) = payload.get("data").and_then(|v| v.as_array()) {
        data.clone()
    } else {
        payload.get("models").and_then(|v| v.as_array()).cloned().unwrap_or_default()
    };

    let mut result = Vec::new();
    for item in items {
        let (model_id, name) = match &item {
            Value::String(text) => (text.clone(), text.clone()),
            Value::Object(_) => {
                let Some(id) = item
                    .get("id")
                    .or_else(|| item.get("name"))
                    .and_then(|v| v.as_str())
                else {
                    continue;
                };
                let name = item
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or(id)
                    .to_string();
                (id.to_string(), name)
            }
            _ => continue,
        };
        if !query.is_empty() && !format!("{model_id} {name}").to_lowercase().contains(&query.to_lowercase()) {
            continue;
        }
        result.push(ModelInfo {
            id: model_id,
            name,
            provider: "OpenAI-compatible".into(),
            context_length: item
                .get("context_length")
                .or_else(|| item.get("context_window"))
                .and_then(|v| v.as_u64())
                .unwrap_or(0),
            output_limit: 0,
            pricing_label: "Unknown".into(),
            supports_tools: None,
            description: String::new(),
        });
    }
    Ok(result)
}

/// Load models for `provider`, using the cache unless `force_refresh` is set.
pub async fn get_models(
    provider: ProviderKind,
    api_key: &str,
    base_url: &str,
    query: &str,
    tools_only: bool,
    force_refresh: bool,
) -> std::result::Result<Vec<ModelInfo>, CatalogError> {
    let key: CacheKey = (
        provider.as_str().into(),
        base_url.into(),
        query.into(),
        tools_only,
    );

    if !force_refresh {
        if let Ok(guard) = cache().lock() {
            if let Some((stamp, items)) = guard.get(&key) {
                if stamp.elapsed() < CACHE_TTL {
                    return Ok(items.clone());
                }
            }
        }
    }

    let items = match provider {
        ProviderKind::OpenRouter => openrouter_models(api_key, query, tools_only).await?,
        ProviderKind::Gemini => gemini_models(api_key, query).await?,
        ProviderKind::FreeLlmApi => freellmapi_models(api_key, query, base_url).await?,
        ProviderKind::OpenAiCompatible => {
            openai_compatible_models(api_key, base_url, query).await?
        }
    };

    if let Ok(mut guard) = cache().lock() {
        guard.insert(key, (Instant::now(), items.clone()));
    }
    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn price_labels_match_python() {
        assert_eq!(price_label(Some(&serde_json::json!({"prompt": "0", "completion": "0"}))), "Free");
        assert_eq!(price_label(Some(&serde_json::json!({"prompt": "0.1", "completion": "0"}))), "Paid");
        assert_eq!(price_label(None), "Unknown");
        assert_eq!(price_label(Some(&serde_json::json!({"prompt": "x"}))), "Unknown");
    }

    #[tokio::test]
    async fn openai_catalog_requires_base_url() {
        let error = get_models(ProviderKind::OpenAiCompatible, "k", "", "", false, true)
            .await
            .unwrap_err();
        assert!(error.0.contains("base URL is required"));
    }

    #[tokio::test]
    async fn gemini_catalog_requires_key() {
        let error = get_models(ProviderKind::Gemini, "", "", "", false, true)
            .await
            .unwrap_err();
        assert!(error.0.contains("API key is required"));
    }
}

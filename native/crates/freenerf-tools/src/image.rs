//! Image generation through the FreeLLMAPI `/images/generations` endpoint.

use std::path::{Path, PathBuf};
use std::time::Duration;

use base64::Engine;
use freenerf_core::{Error, Result};
use regex::Regex;
use serde_json::{json, Value};

use crate::web::WebFetch;

pub const SUPPORTED_SIZES: &[&str] = &["1024x1024", "1024x1536", "1536x1024", "512x512", "256x256"];
const MAX_DOWNLOAD_BYTES: usize = 8_000_000;

#[derive(Debug, Clone, Default)]
pub struct ImageConfig {
    pub api_key: String,
    pub base_url: String,
    pub model: String,
    pub output_dir: PathBuf,
}

#[derive(Debug, Clone)]
pub struct ImageResult {
    pub path: PathBuf,
    pub prompt: String,
    pub model: String,
    pub revised_prompt: String,
}

impl ImageResult {
    pub fn message(&self) -> String {
        let mut lines = vec![format!("Generated image saved to {}", self.path.display())];
        if !self.revised_prompt.is_empty() {
            lines.push(format!("Revised prompt: {}", self.revised_prompt));
        }
        lines.push(format!("Prompt: {}", self.prompt));
        lines.join("\n")
    }
}

pub struct ImageGenerator {
    config: ImageConfig,
    client: reqwest::blocking::Client,
}

impl ImageGenerator {
    pub fn new(config: ImageConfig) -> Self {
        Self {
            config,
            client: reqwest::blocking::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap_or_default(),
        }
    }

    pub fn available(&self) -> bool {
        !self.config.api_key.trim().is_empty()
            && !self.config.base_url.trim().is_empty()
            && !self.config.output_dir.as_os_str().is_empty()
    }

    fn endpoint(&self) -> String {
        format!(
            "{}/images/generations",
            self.config.base_url.trim_end_matches('/')
        )
    }

    fn request(&self, payload: &Value) -> Result<Value> {
        let response = self
            .client
            .post(self.endpoint())
            .header("Content-Type", "application/json")
            .header("Accept", "application/json")
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .json(payload)
            .send()
            .map_err(|e| Error::Tool(format!("Image provider connection error: {e}")))?;

        let status = response.status();
        // Some deployments reject optional parameters; retry minimally.
        let response = if status.as_u16() == 400 || status.as_u16() == 422 {
            let minimal = json!({
                "prompt": payload["prompt"],
                "n": payload.get("n").cloned().unwrap_or(json!(1)),
            });
            self.client
                .post(self.endpoint())
                .header("Content-Type", "application/json")
                .header("Authorization", format!("Bearer {}", self.config.api_key))
                .json(&minimal)
                .send()
                .map_err(|e| Error::Tool(format!("Image provider connection error: {e}")))?
        } else {
            response
        };

        if !response.status().is_success() {
            let detail: String = response
                .text()
                .unwrap_or_default()
                .chars()
                .take(600)
                .collect();
            return Err(Error::Tool(format!(
                "Image provider HTTP {}: {detail}",
                status.as_u16()
            )));
        }

        response
            .json::<Value>()
            .map_err(|_| Error::Tool("Image provider returned invalid JSON".into()))
    }

    pub fn generate(&self, prompt: &str, size: &str, count: u32) -> Result<Vec<ImageResult>> {
        let prompt = prompt.trim();
        if prompt.is_empty() {
            return Err(Error::Tool("Image prompt must not be empty".into()));
        }
        if !self.available() {
            return Err(Error::Tool(
                "Image generation requires a FreeLLMAPI key and a workspace. Add the key in \
                 Settings to enable it."
                    .into(),
            ));
        }

        let count = count.clamp(1, 4);
        let size = if SUPPORTED_SIZES.contains(&size) {
            size
        } else {
            "1024x1024"
        };

        std::fs::create_dir_all(&self.config.output_dir).map_err(|e| {
            Error::Tool(format!("Cannot create image directory: {e}"))
        })?;

        let payload = json!({
            "model": self.config.model,
            "prompt": prompt,
            "size": size,
            "n": count,
            "response_format": "b64_json",
        });

        let data = self.request(&payload)?;
        let items = data
            .get("data")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if items.is_empty() {
            return Err(Error::Tool("Image provider returned no images".into()));
        }

        let timestamp = chrono::Utc::now().format("%Y%m%d_%H%M%S").to_string();
        let mut results = Vec::new();

        for (index, item) in items.iter().take(count as usize).enumerate() {
            let raw = if let Some(encoded) = item.get("b64_json").and_then(|v| v.as_str()) {
                base64::engine::general_purpose::STANDARD
                    .decode(encoded)
                    .map_err(|_| Error::Tool("Invalid base64 image payload".into()))?
            } else if let Some(url) = item.get("url").and_then(|v| v.as_str()) {
                self.download(url)?
            } else {
                continue;
            };

            if raw.is_empty() {
                return Err(Error::Tool("Provider returned an empty image".into()));
            }

            let path = self.config.output_dir.join(format!(
                "{timestamp}_{}_{}.png",
                slug(prompt, 40),
                index + 1
            ));
            std::fs::write(&path, &raw)
                .map_err(|e| Error::Tool(format!("Cannot save image: {e}")))?;

            results.push(ImageResult {
                path,
                prompt: prompt.to_string(),
                model: data
                    .get("model")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&self.config.model)
                    .to_string(),
                revised_prompt: item
                    .get("revised_prompt")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }

        if results.is_empty() {
            return Err(Error::Tool(
                "Image provider response contained no usable image data".into(),
            ));
        }

        self.append_history(&results);
        Ok(results)
    }

    fn download(&self, url: &str) -> Result<Vec<u8>> {
        if WebFetch::validate_url(url).is_none() {
            return Err(Error::Tool("Image URL is not a public address".into()));
        }
        let response = self
            .client
            .get(url)
            .send()
            .map_err(|e| Error::Tool(format!("Cannot download image: {e}")))?;
        if !response.status().is_success() {
            return Err(Error::Tool(format!(
                "Image download HTTP {}",
                response.status().as_u16()
            )));
        }
        let bytes = response
            .bytes()
            .map_err(|e| Error::Tool(format!("Cannot read image: {e}")))?;
        if bytes.len() > MAX_DOWNLOAD_BYTES {
            return Err(Error::Tool("Generated image exceeds the download limit".into()));
        }
        Ok(bytes.to_vec())
    }

    fn append_history(&self, results: &[ImageResult]) {
        let directory = &self.config.output_dir;
        let manifest = directory.join("history.json");

        let mut history: Vec<Value> = std::fs::read_to_string(&manifest)
            .ok()
            .and_then(|text| serde_json::from_str::<Vec<Value>>(&text).ok())
            .unwrap_or_default();

        for result in results {
            history.push(json!({
                "path": result.path.to_string_lossy(),
                "prompt": result.prompt,
                "model": result.model,
                "revised_prompt": result.revised_prompt,
                "created_at": freenerf_core::now_rfc3339(),
            }));
        }

        let keep = history.len().saturating_sub(50);
        let trimmed: Vec<Value> = history.into_iter().skip(keep).collect();
        let _ = std::fs::write(
            manifest,
            serde_json::to_string_pretty(&trimmed).unwrap_or_else(|_| "[]".into()),
        );
    }
}

fn slug(value: &str, limit: usize) -> String {
    let cleaned = Regex::new(r"[^a-z0-9]+")
        .map(|pattern| pattern.replace_all(&value.to_lowercase(), "-").to_string())
        .unwrap_or_else(|_| value.to_lowercase());
    let trimmed = cleaned.trim_matches('-');
    if trimmed.is_empty() {
        "image".into()
    } else {
        trimmed.chars().take(limit).collect()
    }
}

pub fn image_history(output_dir: &Path, limit: usize) -> Vec<Value> {
    let manifest = output_dir.join("history.json");
    let Ok(text) = std::fs::read_to_string(manifest) else {
        return Vec::new();
    };
    let Ok(data) = serde_json::from_str::<Vec<Value>>(&text) else {
        return Vec::new();
    };
    data.into_iter().rev().take(limit).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs_are_filename_safe() {
        assert_eq!(slug("A Logo for My Project!", 40), "a-logo-for-my-project");
        assert_eq!(slug("***", 40), "image");
    }

    #[test]
    fn rejects_empty_prompts_and_missing_keys() {
        let generator = ImageGenerator::new(ImageConfig::default());
        assert!(generator.generate("", "1024x1024", 1).is_err());
        let error = generator.generate("a cat", "1024x1024", 1).unwrap_err();
        assert!(error.to_string().contains("FreeLLMAPI key"));
    }
}

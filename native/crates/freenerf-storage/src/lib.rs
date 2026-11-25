//! Persistent, non-secret configuration and legacy data migration.
//!
//! API keys are intentionally excluded: the Android layer stores them in
//! Keystore-encrypted preferences and injects them at run time, so they never
//! reach this database, session transcripts or task logs.

use std::path::{Path, PathBuf};

use freenerf_core::{Config, Error, PermissionMode, ProviderKind, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::Value;

pub const SCHEMA_VERSION: i64 = 1;
pub const CONFIG_KEY: &str = "config";
pub const WORKSPACE_KEY: &str = "workspace_root";
pub const PERMISSION_KEY: &str = "permission_mode";

pub struct Storage {
    database: PathBuf,
}

impl Storage {
    pub fn open(database: impl AsRef<Path>) -> Result<Self> {
        let database = database.as_ref().to_path_buf();
        if let Some(parent) = database.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::database(format!("cannot create settings directory: {e}")))?;
        }
        let storage = Self { database };
        storage.migrate()?;
        Ok(storage)
    }

    pub fn path(&self) -> &Path {
        &self.database
    }

    fn connect(&self) -> Result<Connection> {
        Connection::open(&self.database)
            .map_err(|e| Error::database(format!("cannot open settings.db: {e}")))
    }

    fn migrate(&self) -> Result<()> {
        let conn = self.connect()?;
        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );
            "#,
        )
        .map_err(|e| Error::database(e))?;
        conn.pragma_update(None, "user_version", SCHEMA_VERSION)
            .map_err(|e| Error::database(e))?;
        Ok(())
    }

    pub fn get(&self, key: &str) -> Result<Option<String>> {
        let conn = self.connect()?;
        conn.query_row(
            "SELECT value FROM settings WHERE key = ?1",
            params![key],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(|e| Error::database(e))
    }

    pub fn set(&self, key: &str, value: &str) -> Result<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3) \
             ON CONFLICT(key) DO UPDATE SET value = excluded.value, \
             updated_at = excluded.updated_at",
            params![key, value, freenerf_core::now_rfc3339()],
        )
        .map_err(|e| Error::database(e))?;
        Ok(())
    }

    pub fn remove(&self, key: &str) -> Result<()> {
        let conn = self.connect()?;
        conn.execute("DELETE FROM settings WHERE key = ?1", params![key])
            .map_err(|e| Error::database(e))?;
        Ok(())
    }

    pub fn load_config(&self) -> Config {
        let raw = self.get(CONFIG_KEY).ok().flatten();
        let mut config = raw
            .and_then(|text| serde_json::from_str::<Config>(&text).ok())
            .unwrap_or_default();
        // Keys are never persisted here.
        config.validate();
        config
    }

    pub fn save_config(&self, config: &Config) -> Result<()> {
        let mut safe = config.clone();
        safe.api_keys = Default::default();
        safe.validate();
        let payload = serde_json::to_string(&safe).map_err(Error::Json)?;
        self.set(CONFIG_KEY, &payload)
    }

    pub fn record_diagnostic(&self, level: &str, message: &str) -> Result<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO diagnostics (created_at, level, message) VALUES (?1, ?2, ?3)",
            params![freenerf_core::now_rfc3339(), level, message],
        )
        .map_err(|e| Error::database(e))?;
        Ok(())
    }

    pub fn diagnostics(&self, limit: u32) -> Result<Vec<Value>> {
        let conn = self.connect()?;
        let mut statement = conn
            .prepare(
                "SELECT created_at, level, message FROM diagnostics ORDER BY id DESC LIMIT ?1",
            )
            .map_err(|e| Error::database(e))?;
        let rows = statement
            .query_map(params![limit.clamp(1, 500)], |row| {
                Ok(serde_json::json!({
                    "created_at": row.get::<_, String>(0)?,
                    "level": row.get::<_, String>(1)?,
                    "message": row.get::<_, String>(2)?,
                }))
            })
            .map_err(|e| Error::database(e))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }
}

/// Import the Python `config.json` written by the desktop/Termux version.
///
/// Unknown or malformed values fall back to defaults; per-provider keys are
/// mapped to the right provider rather than leaking across providers.
pub fn config_from_python_json(text: &str) -> Config {
    let mut config = Config::default();
    let Ok(data) = serde_json::from_str::<Value>(text) else {
        return config;
    };
    let Some(object) = data.as_object() else {
        return config;
    };

    let get_str = |key: &str| -> Option<String> {
        object
            .get(key)
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .map(str::to_string)
    };
    let get_f64 = |key: &str| object.get(key).and_then(|v| v.as_f64());
    let get_u32 = |key: &str| object.get(key).and_then(|v| v.as_u64()).map(|v| v as u32);
    let get_bool = |key: &str| object.get(key).and_then(|v| v.as_bool());

    let provider = get_str("provider").unwrap_or_default();
    if !provider.is_empty() {
        config.provider = ProviderKind::parse(&provider);
    }

    config.api_keys.openrouter = get_str("openrouter_api_key").unwrap_or_default();
    config.api_keys.gemini = get_str("gemini_api_key").unwrap_or_default();
    config.api_keys.freellmapi = get_str("freellmapi_api_key").unwrap_or_default();
    config.api_keys.openai = get_str("openai_api_key").unwrap_or_default();

    // Legacy single key/model apply to the configured provider only.
    if let Some(key) = get_str("api_key") {
        if config.active_key().is_empty() {
            config.set_active_key(&key);
        }
    }
    // The legacy model field only applies when the selected provider has no
    // explicit model key of its own (the defaults would otherwise mask it).
    let provider_model_key = match config.provider {
        ProviderKind::OpenRouter => "openrouter_model",
        ProviderKind::Gemini => "gemini_model",
        ProviderKind::FreeLlmApi => "freellmapi_model",
        ProviderKind::OpenAiCompatible => "openai_model",
    };
    if let Some(model) = get_str("model") {
        let explicit = object
            .get(provider_model_key)
            .and_then(|v| v.as_str())
            .map(str::trim)
            .map(|v| !v.is_empty())
            .unwrap_or(false);
        if !explicit {
            config.set_active_model(&model);
        }
    }

    if let Some(value) = get_str("openrouter_model") {
        config.models.openrouter = value;
    }
    if let Some(value) = get_str("gemini_model") {
        config.models.gemini = value;
    }
    if let Some(value) = get_str("freellmapi_model") {
        config.models.freellmapi = value;
    }
    if let Some(value) = get_str("openai_model") {
        config.models.openai = value;
    }

    if let Some(value) = get_str("openrouter_base_url") {
        config.base_urls.openrouter = value;
    }
    if let Some(value) = get_str("gemini_base_url") {
        config.base_urls.gemini = value;
    }
    if let Some(value) = get_str("freellmapi_base_url") {
        config.base_urls.freellmapi = value;
    }
    if let Some(value) = get_str("openai_base_url").or_else(|| get_str("base_url")) {
        config.base_urls.openai = value;
    }

    if let Some(value) = get_f64("temperature") {
        config.temperature = value;
    }
    if let Some(value) = get_u32("max_tokens") {
        config.max_tokens = value;
    }
    if let Some(value) = get_f64("request_timeout") {
        config.request_timeout = value;
    }
    if let Some(value) = get_u32("max_retries") {
        config.max_retries = value;
    }
    if let Some(value) = get_u32("max_agent_steps") {
        config.max_agent_steps = value;
    }
    if let Some(value) = get_u32("max_context_chars") {
        config.max_context_chars = value as usize;
    }
    if let Some(value) = get_str("custom_prompt") {
        config.custom_prompt = value;
    }
    if let Some(value) = get_bool("streaming") {
        config.streaming = value;
    }
    if let Some(value) = get_str("permission_mode") {
        config.permission_mode = PermissionMode::parse(&value);
    }
    if let Some(value) = get_str("image_model") {
        config.image_model = value;
    }

    config.validate();
    config
}

/// Copy legacy global databases into a workspace exactly once.
///
/// The source directory is the Python data directory (`~/.local/share/freenerf`
/// on Linux/Termux). A marker file prevents repeated imports, matching the
/// Python `Workspace._migrate_legacy` behaviour.
pub fn import_legacy_databases(legacy_dir: &Path, workspace_data_dir: &Path) -> Result<Vec<String>> {
    let marker = legacy_dir.join(".migrated-to-workspace");
    if marker.exists() || !legacy_dir.is_dir() {
        return Ok(Vec::new());
    }

    let mut imported = Vec::new();
    std::fs::create_dir_all(workspace_data_dir)
        .map_err(|e| Error::workspace(format!("cannot create workspace data dir: {e}")))?;

    for name in ["sessions.db", "tasks.db"] {
        let source = legacy_dir.join(name);
        let target = workspace_data_dir.join(name);
        if source.is_file() && !target.exists() {
            std::fs::copy(&source, &target).map_err(|e| {
                Error::workspace(format!("cannot import legacy {name}: {e}"))
            })?;
            imported.push(name.to_string());
        }
    }

    let _ = std::fs::write(
        marker,
        serde_json::json!({
            "migrated_to": workspace_data_dir.to_string_lossy(),
            "at": freenerf_core::now_rfc3339(),
        })
        .to_string(),
    );

    Ok(imported)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn storage() -> (tempfile::TempDir, Storage) {
        let dir = tempfile::tempdir().unwrap();
        let storage = Storage::open(dir.path().join("settings.db")).unwrap();
        (dir, storage)
    }

    #[test]
    fn round_trips_config_without_secrets() {
        let (_dir, storage) = storage();
        let mut config = Config::default();
        config.provider = ProviderKind::Gemini;
        config.set_active_key("AIza-secret");
        config.max_agent_steps = 40;
        config.custom_prompt = "be terse".into();
        storage.save_config(&config).unwrap();

        let loaded = storage.load_config();
        assert_eq!(loaded.provider, ProviderKind::Gemini);
        assert_eq!(loaded.max_agent_steps, 40);
        assert_eq!(loaded.custom_prompt, "be terse");
        assert!(loaded.active_key().is_empty());
        let raw = storage.get(CONFIG_KEY).unwrap().unwrap();
        assert!(!raw.contains("AIza-secret"));
    }

    #[test]
    fn settings_are_upserted_and_removed() {
        let (_dir, storage) = storage();
        storage.set("theme", "dark").unwrap();
        storage.set("theme", "light").unwrap();
        assert_eq!(storage.get("theme").unwrap().unwrap(), "light");
        storage.remove("theme").unwrap();
        assert!(storage.get("theme").unwrap().is_none());
    }

    #[test]
    fn imports_python_config_with_per_provider_keys() {
        let python = serde_json::json!({
            "provider": "gemini",
            "api_key": "AIza-legacy",
            "model": "gemini-2.5-pro",
            "gemini_api_key": "",
            "openrouter_api_key": "sk-or",
            "permission_mode": "full",
            "temperature": 0.7,
            "max_tokens": 8192,
            "max_agent_steps": 12,
            "base_url": "http://localhost:1234/v1",
            "custom_prompt": "custom",
            "streaming": false
        })
        .to_string();

        let config = config_from_python_json(&python);
        assert_eq!(config.provider, ProviderKind::Gemini);
        assert_eq!(config.active_key(), "AIza-legacy");
        assert_eq!(config.active_model(), "gemini-2.5-pro");
        assert_eq!(config.api_keys.openrouter, "sk-or");
        assert_eq!(config.permission_mode, PermissionMode::Full);
        assert_eq!(config.temperature, 0.7);
        assert_eq!(config.max_tokens, 8192);
        assert_eq!(config.max_agent_steps, 12);
        assert_eq!(config.base_urls.openai, "http://localhost:1234/v1");
        assert_eq!(config.custom_prompt, "custom");
        assert!(!config.streaming);
    }

    #[test]
    fn malformed_python_config_falls_back_to_defaults() {
        let config = config_from_python_json("{not json");
        assert_eq!(config.provider, ProviderKind::OpenRouter);
        assert_eq!(config.max_agent_steps, 25);
    }

    #[test]
    fn imports_legacy_databases_once() {
        let legacy = tempfile::tempdir().unwrap();
        let workspace = tempfile::tempdir().unwrap();
        std::fs::write(legacy.path().join("sessions.db"), "db").unwrap();
        std::fs::write(legacy.path().join("tasks.db"), "db").unwrap();

        let imported = import_legacy_databases(legacy.path(), workspace.path()).unwrap();
        assert_eq!(imported.len(), 2);
        assert!(workspace.path().join("sessions.db").exists());

        std::fs::remove_file(workspace.path().join("sessions.db")).unwrap();
        let again = import_legacy_databases(legacy.path(), workspace.path()).unwrap();
        assert!(again.is_empty(), "marker prevents a second import");
    }
}

use thiserror::Error;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Error)]
pub enum Error {
    #[error("{0}")]
    Provider(String),

    #[error("{message}")]
    ProviderStatus { message: String, status: Option<u16>, retriable: bool },

    #[error("HTTP error: {0}")]
    Http(String),

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("database error: {0}")]
    Database(String),

    #[error("workspace error: {0}")]
    Workspace(String),

    #[error("permission denied: {0}")]
    Permission(String),

    #[error("tool error: {0}")]
    Tool(String),

    #[error("configuration error: {0}")]
    Config(String),

    #[error("cancelled by user")]
    Cancelled,

    #[error("{0}")]
    Other(String),
}

impl Error {
    pub fn provider(message: impl Into<String>) -> Self {
        Error::Provider(message.into())
    }

    pub fn status(message: impl Into<String>, status: u16, retriable: bool) -> Self {
        Error::ProviderStatus { message: message.into(), status: Some(status), retriable }
    }

    pub fn is_retriable(&self) -> bool {
        matches!(self, Error::ProviderStatus { retriable: true, .. })
    }

    pub fn is_cancelled(&self) -> bool {
        matches!(self, Error::Cancelled)
    }

    pub fn database(err: impl std::fmt::Display) -> Self {
        Error::Database(err.to_string())
    }

    pub fn workspace(message: impl Into<String>) -> Self {
        Error::Workspace(message.into())
    }
}


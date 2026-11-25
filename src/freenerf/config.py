from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir


CONFIG_DIR = Path(user_config_dir("freenerf"))
CONFIG_FILE = CONFIG_DIR / "config.json"

PROVIDERS = ("openrouter", "gemini", "freellmapi", "openai-compatible")
PERMISSION_MODES = ("denied", "normal", "full")

AVAILABLE_THEMES = (
    "ansi-dark",
    "ansi-light",
    "atom-one-dark",
    "atom-one-light",
    "catppuccin-frappe",
    "catppuccin-latte",
    "catppuccin-macchiato",
    "catppuccin-mocha",
    "dracula",
    "flexoki",
    "freenerf",
    "freenerf-custom",
    "gruvbox",
    "monokai",
    "nord",
    "rose-pine",
    "rose-pine-dawn",
    "rose-pine-moon",
    "solarized-dark",
    "solarized-light",
    "textual-dark",
    "textual-light",
    "tokyo-night",
)

DEFAULT_CUSTOM_THEME = {
    "primary": "#2DD4BF",
    "secondary": "#60A5FA",
    "accent": "#C084FC",
    "foreground": "#E7EAF2",
    "background": "#10131D",
    "surface": "#171C29",
    "panel": "#20283A",
    "success": "#4ADE80",
    "warning": "#FBBF24",
    "error": "#FB7185",
    "dark": True,
}

DEFAULT_MODELS = {
    "openrouter": "openai/gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
    "freellmapi": "auto",
    "openai-compatible": "gpt-4o-mini",
}

MODEL_FIELDS = {
    "openrouter": "openrouter_model",
    "gemini": "gemini_model",
    "freellmapi": "freellmapi_model",
    "openai-compatible": "openai_model",
}

KEY_FIELDS = {
    "openrouter": "openrouter_api_key",
    "gemini": "gemini_api_key",
    "freellmapi": "freellmapi_api_key",
    "openai-compatible": "openai_api_key",
}


def _clamp(value, minimum, maximum, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


def _coerce_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


@dataclass
class Config:
    provider: str = "openrouter"
    model: str = DEFAULT_MODELS["openrouter"]
    # Legacy single-key field, kept for backwards compatibility.
    api_key: str = ""
    permission_mode: str = "normal"
    theme: str = "freenerf"
    custom_theme: dict = field(default_factory=lambda: dict(DEFAULT_CUSTOM_THEME))
    typewriter_enabled: bool = True
    typewriter_speed: float = 0.025
    streaming: bool = True

    openrouter_api_key: str = ""
    gemini_api_key: str = ""
    freellmapi_api_key: str = ""
    openai_api_key: str = ""

    openrouter_model: str = ""
    gemini_model: str = ""
    freellmapi_model: str = "auto"
    openai_model: str = ""

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    freellmapi_base_url: str = "https://freellmapi-3d3g.onrender.com/v1"
    openai_base_url: str = "https://api.openai.com/v1"
    image_model: str = "gpt-image-1"
    # Legacy URL, reserved for OpenAI-compatible providers.
    base_url: str = "https://api.openai.com/v1"

    temperature: float = 0.2
    max_tokens: int = 4096
    request_timeout: float = 90.0
    max_retries: int = 2
    max_agent_steps: int = 25
    max_context_chars: int = 120_000
    custom_prompt: str = ""

    ui_size: str = "normal"
    font_size: str = "normal"
    font_size_custom: int = 12

    workspace_root: str = ""
    recent_workspaces: list = field(default_factory=list)

    def __post_init__(self):
        # Keep direct Config(...) construction backward-compatible too, not
        # only configurations loaded from config.json.
        if self.provider in KEY_FIELDS:
            key_field = KEY_FIELDS[self.provider]
            if not getattr(self, key_field, "") and self.api_key:
                setattr(self, key_field, self.api_key)
            self.api_key = str(getattr(self, key_field, "") or "")

    # ------------------------------------------------------------------
    # Loading & validation
    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        data = {}
        if CONFIG_FILE.exists():
            try:
                parsed = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    data = parsed
            except (json.JSONDecodeError, OSError):
                data = {}

        allowed = cls.__dataclass_fields__.keys()
        values = {key: value for key, value in data.items() if key in allowed}
        config = cls(**values)
        config.validate_values()

        # Migrate old Dark/Light values and validate Textual themes.
        legacy_themes = {"dark": "textual-dark", "light": "textual-light"}
        config.theme = legacy_themes.get(config.theme, config.theme)
        if config.theme not in AVAILABLE_THEMES:
            config.theme = "freenerf"

        # Normalize the old default provider.
        if config.provider == "openai":
            if "openrouter.ai" in (config.base_url or ""):
                config.provider = "openrouter"
            else:
                config.provider = "openai-compatible"
        if config.provider not in PROVIDERS:
            config.provider = "openrouter"

        # A legacy base_url only applies to OpenAI-compatible endpoints.
        if (
            config.provider == "openai-compatible"
            and "openai_base_url" not in data
        ):
            legacy_url = str(data.get("base_url") or "").strip()
            if legacy_url:
                config.openai_base_url = legacy_url.rstrip("/")

        # Migrate legacy single-key configuration.
        if config.api_key:
            key_field = KEY_FIELDS[config.provider]
            if not getattr(config, key_field):
                setattr(config, key_field, config.api_key)

        # Migrate legacy single-model configuration.
        if "model" in data and str(data.get("model") or "").strip():
            model_field = MODEL_FIELDS[config.provider]
            if not getattr(config, model_field):
                setattr(config, model_field, str(data["model"]))

        # Environment variables override the config file.
        config.provider = str(
            os.getenv("FREENERF_PROVIDER", config.provider) or ""
        ).strip().lower()
        if config.provider == "openai":
            config.provider = "openai-compatible"
        if config.provider not in PROVIDERS:
            config.provider = "openrouter"

        for provider, field_name in KEY_FIELDS.items():
            env_name = "FREENERF_" + provider.upper().replace("-", "_") + "_API_KEY"
            env_value = os.getenv(env_name)
            if provider == "openai-compatible":
                env_value = env_value or os.getenv("FREENERF_OPENAI_API_KEY")
            if env_value:
                setattr(config, field_name, env_value)

        legacy_env_key = os.getenv("FREENERF_API_KEY")
        if legacy_env_key:
            setattr(config, KEY_FIELDS[config.provider], legacy_env_key)

        for provider, field_name in (
            ("openrouter", "openrouter_base_url"),
            ("gemini", "gemini_base_url"),
            ("freellmapi", "freellmapi_base_url"),
            ("openai-compatible", "openai_base_url"),
        ):
            env_name = (
                "FREENERF_"
                + provider.upper().replace("-", "_")
                + "_BASE_URL"
            )
            env_value = os.getenv(env_name)
            if provider == "openai-compatible":
                env_value = env_value or os.getenv("FREENERF_OPENAI_BASE_URL")
            if env_value:
                setattr(config, field_name, env_value.rstrip("/"))

        for provider, field_name in MODEL_FIELDS.items():
            env_name = (
                "FREENERF_"
                + provider.upper().replace("-", "_")
                + "_MODEL"
            )
            env_value = os.getenv(env_name)
            if provider == "openai-compatible":
                env_value = env_value or os.getenv("FREENERF_OPENAI_MODEL")
            if env_value:
                setattr(config, field_name, env_value.strip())

        legacy_model = os.getenv("FREENERF_MODEL")
        if legacy_model:
            setattr(
                config,
                MODEL_FIELDS[config.provider],
                legacy_model.strip(),
            )

        legacy_base_url = os.getenv("FREENERF_BASE_URL")
        if legacy_base_url:
            config.base_url = legacy_base_url.rstrip("/")
            config.openai_base_url = config.base_url

        if config.workspace_root:
            config.workspace_root = str(
                Path(config.workspace_root).expanduser()
            )
        config.workspace_root = os.getenv(
            "FREENERF_WORKSPACE", config.workspace_root
        )

        env_permission = os.getenv("FREENERF_PERMISSION_MODE")
        if env_permission:
            config.permission_mode = env_permission

        config.model = config.active_model()
        config.validate_values()
        # ``api_key`` is a compatibility mirror of the currently selected
        # provider, not a global fallback.  In particular, an environment
        # override that changes providers must not carry the previous
        # provider's key into the newly selected one.
        config.api_key = config.active_api_key()
        return config

    def validate_values(self) -> None:
        """Clamp/normalize values loaded from disk or the environment."""
        if self.permission_mode not in PERMISSION_MODES:
            self.permission_mode = "normal"

        if self.ui_size not in ("compact", "normal", "large"):
            self.ui_size = "normal"

        if self.font_size not in ("small", "normal", "large", "custom"):
            self.font_size = "normal"

        try:
            self.font_size_custom = max(6, min(32, int(self.font_size_custom)))
        except (TypeError, ValueError):
            self.font_size_custom = 12

        self.typewriter_enabled = _coerce_bool(self.typewriter_enabled)
        self.streaming = _coerce_bool(self.streaming)

        self.typewriter_speed = _clamp(
            self.typewriter_speed, 0.001, 0.2, 0.025
        )
        self.temperature = _clamp(self.temperature, 0.0, 2.0, 0.2)
        self.max_tokens = int(_clamp(self.max_tokens, 1, 32768, 4096))
        self.request_timeout = _clamp(self.request_timeout, 5.0, 600.0, 90.0)
        self.max_retries = int(_clamp(self.max_retries, 0, 5, 2))
        self.max_agent_steps = int(_clamp(self.max_agent_steps, 1, 100, 25))
        self.max_context_chars = int(
            _clamp(self.max_context_chars, 4000, 2_000_000, 120_000)
        )

        palette = self.custom_theme if isinstance(self.custom_theme, dict) else {}
        normalized = dict(DEFAULT_CUSTOM_THEME)
        for key in DEFAULT_CUSTOM_THEME:
            if key == "dark":
                normalized[key] = _coerce_bool(palette.get(key, True))
                continue
            value = str(palette.get(key, normalized[key]) or "").strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                value = DEFAULT_CUSTOM_THEME[key]
            normalized[key] = value.upper()
        self.custom_theme = normalized

        if not isinstance(self.recent_workspaces, list):
            self.recent_workspaces = []
        self.recent_workspaces = [
            str(item) for item in self.recent_workspaces if isinstance(item, str)
        ][:8]

    # ------------------------------------------------------------------
    # Provider helpers
    # ------------------------------------------------------------------
    def active_api_key(self) -> str:
        # The legacy key is migrated while loading/saving.  Treating it as a
        # permanent fallback leaks one provider's key into another provider
        # when the user changes the provider in Settings.
        return str(getattr(self, KEY_FIELDS[self.provider], "") or "")

    def active_model(self) -> str:
        field_name = MODEL_FIELDS[self.provider]
        value = str(getattr(self, field_name, "") or "").strip()
        return value or DEFAULT_MODELS[self.provider]

    def active_base_url(self) -> str:
        if self.provider == "gemini":
            return self.gemini_base_url
        if self.provider == "freellmapi":
            return self.freellmapi_base_url
        if self.provider == "openrouter":
            return self.openrouter_base_url
        return self.openai_base_url or self.base_url

    def set_model(self, model: str) -> None:
        model = str(model or "").strip()
        self.model = model
        setattr(self, MODEL_FIELDS[self.provider], model)

    def set_api_key(self, key: str) -> None:
        key = str(key or "").strip()
        self.api_key = key
        setattr(self, KEY_FIELDS[self.provider], key)

    def select_provider(self, provider: str) -> None:
        provider = str(provider or "").strip().lower()
        if provider == "openai":
            provider = "openai-compatible"
        if provider not in PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")

        # Migrate an in-memory legacy key to the provider it belonged to
        # before changing the active provider.
        previous_field = KEY_FIELDS[self.provider]
        if not getattr(self, previous_field, "") and self.api_key:
            setattr(self, previous_field, self.api_key)

        self.provider = provider
        self.model = self.active_model()
        self.api_key = self.active_api_key()

    def remember_workspace(self, path) -> None:
        value = str(Path(path).expanduser().resolve())
        self.recent_workspaces = [
            item for item in self.recent_workspaces if item != value
        ]
        self.recent_workspaces.insert(0, value)
        self.recent_workspaces = self.recent_workspaces[:8]

    def missing_api_key(self) -> bool:
        return not self.active_api_key()

    def validation_warnings(self) -> list[str]:
        warnings = []
        if self.missing_api_key():
            warnings.append(
                f"No API key configured for provider '{self.provider}'."
            )
        if not self.active_model():
            warnings.append("No model selected.")
        if self.workspace_root and not Path(self.workspace_root).is_dir():
            warnings.append(
                f"Configured workspace does not exist: {self.workspace_root}"
            )
        return warnings

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self) -> None:
        # Preserve compatibility with callers that still construct Config
        # using only ``api_key``.  This migration is safe because provider
        # switching clears/rebinds the compatibility mirror above.
        key_field = KEY_FIELDS[self.provider]
        if not getattr(self, key_field, "") and self.api_key:
            setattr(self, key_field, self.api_key)
        self.api_key = self.active_api_key()
        self.model = self.active_model()
        self.validate_values()

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), indent=2),
            encoding="utf-8",
        )
        temporary.replace(CONFIG_FILE)

        try:
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass

    def public_dict(self) -> dict:
        data = asdict(self)
        for key in (
            "api_key",
            "openrouter_api_key",
            "gemini_api_key",
            "freellmapi_api_key",
            "openai_api_key",
        ):
            data[key] = "********" if data.get(key) else ""
        return data

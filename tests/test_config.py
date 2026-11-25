import json
import os

import pytest

from freenerf import config as config_module
from freenerf.config import Config


ENV_VARS = (
    "FREENERF_PROVIDER",
    "FREENERF_MODEL",
    "FREENERF_API_KEY",
    "FREENERF_OPENROUTER_API_KEY",
    "FREENERF_GEMINI_API_KEY",
    "FREENERF_FREELLMAPI_API_KEY",
    "FREENERF_OPENAI_API_KEY",
    "FREENERF_OPENROUTER_MODEL",
    "FREENERF_GEMINI_MODEL",
    "FREENERF_FREELLMAPI_MODEL",
    "FREENERF_OPENAI_MODEL",
    "FREENERF_WORKSPACE",
    "FREENERF_PERMISSION_MODE",
    "FREENERF_BASE_URL",
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "config.json"


def test_defaults_are_provider_specific():
    config = Config()
    assert config.provider == "openrouter"
    assert config.active_model() == "openai/gpt-4o-mini"

    config.select_provider("gemini")
    assert config.active_model() == "gemini-2.5-flash"

    config.select_provider("freellmapi")
    assert config.active_model() == "auto"

    config.select_provider("openai-compatible")
    assert config.active_model() == "gpt-4o-mini"


def test_load_invalid_json_is_tolerated(isolated_config):
    isolated_config.write_text("{not json", encoding="utf-8")
    config = Config.load()
    assert config.provider == "openrouter"


def test_load_clamps_and_coerces(isolated_config):
    isolated_config.write_text(json.dumps({
        "max_agent_steps": 0,
        "max_retries": 99,
        "typewriter_enabled": "false",
        "streaming": "off",
        "typewriter_speed": 9,
        "temperature": 7,
        "max_tokens": 999999,
        "recent_workspaces": "nope",
        "theme": "not-a-theme",
        "ui_size": "huge",
        "font_size": "gigantic",
        "font_size_custom": 100,
        "permission_mode": "whatever",
    }), encoding="utf-8")

    config = Config.load()

    assert config.max_agent_steps == 1
    assert config.max_retries == 5
    assert config.typewriter_enabled is False
    assert config.streaming is False
    assert config.typewriter_speed == 0.2
    assert config.temperature == 2.0
    assert config.max_tokens == 32768
    assert config.recent_workspaces == []
    assert config.theme == "freenerf"
    assert config.ui_size == "normal"
    assert config.font_size == "normal"
    assert config.font_size_custom == 32
    assert config.permission_mode == "normal"


def test_environment_model_is_honoured(isolated_config, monkeypatch):
    monkeypatch.setenv("FREENERF_PROVIDER", "gemini")
    monkeypatch.setenv("FREENERF_GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("FREENERF_MODEL", "gemini-2.5-pro")

    config = Config.load()

    assert config.provider == "gemini"
    assert config.active_model() == "gemini-2.5-pro"
    assert config.active_api_key() == "gemini-key"


def test_openai_provider_alias_is_supported_in_environment(
    isolated_config, monkeypatch
):
    monkeypatch.setenv("FREENERF_PROVIDER", "openai")
    monkeypatch.setenv("FREENERF_OPENAI_API_KEY", "local-key")
    monkeypatch.setenv("FREENERF_OPENAI_MODEL", "local-model")
    monkeypatch.setenv("FREENERF_OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")

    config = Config.load()

    assert config.provider == "openai-compatible"
    assert config.active_api_key() == "local-key"
    assert config.active_model() == "local-model"
    assert config.active_base_url() == "http://127.0.0.1:8000/v1"


def test_provider_specific_model_env(isolated_config, monkeypatch):
    monkeypatch.setenv("FREENERF_PROVIDER", "freellmapi")
    monkeypatch.setenv("FREENERF_FREELLMAPI_API_KEY", "k")
    monkeypatch.setenv("FREENERF_FREELLMAPI_MODEL", "auto:fast")

    config = Config.load()
    assert config.active_model() == "auto:fast"


def test_legacy_single_key_migrates_to_active_provider(isolated_config):
    config = Config(
        provider="gemini",
        api_key="legacy-key",
        gemini_api_key="",
    )
    config.save()

    reloaded = Config.load()
    assert reloaded.gemini_api_key == "legacy-key"
    assert reloaded.active_api_key() == "legacy-key"


def test_direct_legacy_config_key_is_available_before_save():
    config = Config(provider="gemini", api_key="legacy-key")

    assert config.active_api_key() == "legacy-key"
    assert config.gemini_api_key == "legacy-key"


def test_switching_provider_never_reuses_another_providers_key(isolated_config):
    config = Config()
    config.set_api_key("openrouter-secret")

    config.select_provider("gemini")
    assert config.active_api_key() == ""
    assert config.api_key == ""
    assert config.openrouter_api_key == "openrouter-secret"

    config.set_api_key("gemini-secret")
    config.save()

    config.select_provider("openrouter")
    assert config.active_api_key() == "openrouter-secret"
    config.select_provider("gemini")
    assert config.active_api_key() == "gemini-secret"

    reloaded = Config.load()
    assert reloaded.openrouter_api_key == "openrouter-secret"
    assert reloaded.gemini_api_key == "gemini-secret"


def test_provider_environment_override_does_not_leak_legacy_key(
    isolated_config, monkeypatch
):
    isolated_config.write_text(json.dumps({
        "provider": "openrouter",
        "api_key": "old-openrouter-key",
    }), encoding="utf-8")
    monkeypatch.setenv("FREENERF_PROVIDER", "gemini")

    config = Config.load()

    assert config.openrouter_api_key == "old-openrouter-key"
    assert config.active_api_key() == ""
    assert config.api_key == ""


def test_save_roundtrip_and_masking(isolated_config, tmp_path):
    config = Config()
    config.set_api_key("sk-secret-value")
    config.set_model("openai/gpt-4o")
    config.workspace_root = str(tmp_path)
    config.remember_workspace(tmp_path)
    config.save()

    reloaded = Config.load()

    assert reloaded.active_model() == "openai/gpt-4o"
    assert reloaded.active_api_key() == "sk-secret-value"
    assert reloaded.workspace_root == str(tmp_path)
    assert reloaded.recent_workspaces == [str(tmp_path)]

    public = reloaded.public_dict()
    assert public["openrouter_api_key"] == "********"
    assert public["api_key"] == "********"

    if os.name == "posix":
        assert (isolated_config.stat().st_mode & 0o777) == 0o600


def test_recent_workspaces_are_deduplicated(tmp_path):
    config = Config()
    config.remember_workspace(tmp_path)
    config.remember_workspace(tmp_path / ".." / tmp_path.name)
    assert config.recent_workspaces == [str(tmp_path)]


def test_legacy_base_url_migrates_to_openai_compatible(isolated_config):
    isolated_config.write_text(json.dumps({
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:9000/v1",
    }), encoding="utf-8")

    config = Config.load()

    assert config.active_base_url() == "http://127.0.0.1:9000/v1"


def test_openai_compatible_uses_its_own_key_and_url(isolated_config):
    config = Config(
        provider="openai-compatible",
        openai_api_key="local-key",
        openai_base_url="http://127.0.0.1:8000/v1",
        openai_model="local-model",
    )
    assert config.active_api_key() == "local-key"
    assert config.active_base_url() == "http://127.0.0.1:8000/v1"
    assert config.active_model() == "local-model"


def test_custom_theme_palette_is_normalized_and_invalid_colors_fall_back():
    config = Config(custom_theme={
        "primary": "#abcdef",
        "background": "not-a-color",
        "dark": "false",
    })
    config.validate_values()
    assert config.custom_theme["primary"] == "#ABCDEF"
    assert config.custom_theme["background"] == "#10131D"
    assert config.custom_theme["dark"] is False


def test_signature_theme_is_the_default():
    config = Config()
    assert config.theme == "freenerf"
    assert config.custom_theme["primary"] == "#2DD4BF"

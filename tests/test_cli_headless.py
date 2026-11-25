import pytest

from freenerf import cli
from freenerf.config import Config
from freenerf.sessions.store import SessionStore
from freenerf.workspace import Workspace


class FakeProvider:
    supports_tools = True
    supports_streaming = False
    supports_images = False

    def __init__(self, content="Headless answer"):
        self.content = content
        self.calls = []

    def chat(self, messages, tools=None, stream_callback=None):
        self.calls.append(messages)
        return {"content": self.content, "tool_calls": []}

    def close(self):
        pass


def test_run_headless_persists_session(tmp_path, monkeypatch):
    workspace = Workspace.open(tmp_path / "workspace").ensure()
    config = Config()
    config.workspace_root = str(workspace.root)

    provider = FakeProvider()
    monkeypatch.setattr(
        cli, "create_provider_for_config", lambda config: provider
    )

    exit_code = cli.run_headless(
        config, workspace, "Explain this project", auto_approve=True
    )

    assert exit_code == 0
    assert provider.calls

    store = SessionStore(workspace.sessions_db)
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["workspace"] == str(workspace.root)

    messages = store.get_messages(sessions[0]["id"])
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Explain this project"
    assert any(
        item["role"] == "assistant" and "Headless answer" in item["content"]
        for item in messages
    )


def test_run_headless_requires_provider(tmp_path, monkeypatch):
    workspace = Workspace.open(tmp_path / "workspace").ensure()
    config = Config()

    def fail(config):
        from freenerf.llm.providers import ProviderError
        raise ProviderError("Missing Gemini API key")

    monkeypatch.setattr(cli, "create_provider_for_config", fail)

    assert cli.run_headless(config, workspace, "hello") == 1


def test_config_flag_prints_path(capsys):
    assert cli.main(["--config"]) == 0
    output = capsys.readouterr().out.strip()
    assert output.endswith("config.json")

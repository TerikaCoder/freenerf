import json

import pytest

from freenerf.agents.runtime import Agent
from freenerf.agents.task_manager import AgentTaskManager
from freenerf.agents.task_store import TaskStore
from freenerf.config import Config
from freenerf.llm.providers import ProviderError
from freenerf.tools.filesystem import FileSystem
from freenerf.tools.terminal import Terminal


class DummyConsole:
    def print(self, *args, **kwargs):
        pass

    def input(self, prompt=""):
        return "n"


class ScriptedProvider:
    supports_tools = True
    supports_streaming = False

    def __init__(self, responses, repeat_last=False):
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.calls = []

    def chat(self, messages, tools=None, stream_callback=None):
        self.calls.append({
            "messages": [dict(item) for item in messages],
            "tools": tools,
        })

        if not self.responses:
            raise AssertionError("Provider called more times than expected")

        if self.repeat_last and len(self.responses) == 1:
            response = self.responses[0]
        else:
            response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response
        return response


class PlainProvider:
    supports_tools = False
    supports_streaming = False

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None, stream_callback=None):
        self.calls.append([dict(item) for item in messages])
        return self.responses.pop(0)


def call(name, args, call_id="c1"):
    return {
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args),
            },
        }]
    }


def make_agent(tmp_path, responses, permission="normal", **config_kwargs):
    filesystem = FileSystem(tmp_path)
    terminal = Terminal(tmp_path)
    provider = ScriptedProvider(responses)
    store = TaskStore(tmp_path / ".freenerf" / "tasks.db")
    manager = AgentTaskManager(
        store, session_id="session-1", workspace=str(tmp_path)
    )
    config = Config(permission_mode=permission, **config_kwargs)

    agent = Agent(
        provider=provider,
        filesystem=filesystem,
        terminal=terminal,
        console=DummyConsole(),
        tasks=manager,
        config=config,
        permission_mode=permission,
    )
    return agent, provider, manager, store


def test_finish_sets_status_and_returns_summary(tmp_path):
    agent, provider, manager, store = make_agent(tmp_path, [
        call("finish", {
            "status": "completed",
            "summary": "Verified with python -m pytest -q",
        }),
    ])

    answer = agent.chat("Fix the bug")

    assert answer == "Verified with python -m pytest -q"
    assert agent.last_outcome.status == "completed"
    task = store.get_task(manager.task_id)
    assert task["status"] == "completed"
    assert "Verified" in task["summary"]
    assert task["logs"]


def test_finish_partial_is_honoured(tmp_path):
    agent, _, _, store = make_agent(tmp_path, [
        call("finish", {
            "status": "partial",
            "summary": "Implemented the change but tests could not run",
        }),
    ])

    answer = agent.chat("Implement it")

    assert "tests could not run" in answer
    assert agent.last_outcome.status == "partial"


def test_implicit_completion_without_tools(tmp_path):
    agent, _, manager, store = make_agent(tmp_path, [
        {"content": "The answer is 42", "tool_calls": []},
    ])

    assert agent.chat("Question") == "The answer is 42"
    assert store.get_task(manager.task_id)["status"] == "completed"


def test_implicit_partial_when_plan_steps_remain(tmp_path):
    agent, _, manager, store = make_agent(tmp_path, [
        call("set_plan", {"steps": [{"title": "Inspect"}, {"title": "Fix"}]}),
        {"content": "Stopped early", "tool_calls": []},
    ])

    agent.chat("Do both things")

    task = store.get_task(manager.task_id)
    assert task["status"] == "partial"
    assert len(task["steps"]) == 2


def test_plan_and_step_tracking(tmp_path):
    agent, _, manager, store = make_agent(tmp_path, [
        call("set_plan", {"steps": [{"title": "Inspect"}, {"title": "Fix"}]}),
        call("update_step", {
            "step": 1,
            "status": "completed",
            "result": "Found the bug in config.py",
        }, "c2"),
        call("finish", {"status": "completed", "summary": "Done"}, "c3"),
    ])

    agent.chat("Fix it")

    task = store.get_task(manager.task_id)
    assert task["steps"][0]["status"] == "completed"
    assert "config.py" in task["steps"][0]["result"]
    assert task["steps"][1]["status"] == "pending"


def test_max_steps_blocks_the_task(tmp_path):
    agent, provider, manager, store = make_agent(
        tmp_path,
        [call("read_file", {"path": "a.txt"})],
        max_agent_steps=2,
    )
    provider.repeat_last = True

    answer = agent.chat("Keep going")

    assert answer is None
    assert agent.last_outcome.status == "blocked"
    assert store.get_task(manager.task_id)["status"] == "blocked"


def test_cancellation_before_first_step(tmp_path):
    agent, _, manager, store = make_agent(tmp_path, [
        {"content": "never", "tool_calls": []},
    ])
    agent.cancel()

    assert agent.chat("Do it") is None
    assert agent.last_outcome.status == "cancelled"
    assert store.get_task(manager.task_id)["status"] == "cancelled"


def test_invalid_json_arguments_are_reported_to_the_model(tmp_path):
    agent, provider, _, _ = make_agent(tmp_path, [
        {
            "tool_calls": [{
                "id": "bad",
                "function": {"name": "read_file", "arguments": "not json"},
            }]
        },
        {"content": "Recovered", "tool_calls": []},
    ])

    assert agent.chat("Read it") == "Recovered"

    tool_messages = [
        item for item in provider.calls[1]["messages"]
        if item.get("role") == "tool"
    ]
    assert "Invalid tool arguments" in tool_messages[-1]["content"]


def test_unknown_tool_is_rejected(tmp_path):
    agent, provider, _, _ = make_agent(tmp_path, [
        call("delete_everything", {}),
        {"content": "Understood", "tool_calls": []},
    ])

    assert agent.chat("Do something") == "Understood"

    tool_messages = [
        item for item in provider.calls[1]["messages"]
        if item.get("role") == "tool"
    ]
    assert "not registered" in tool_messages[-1]["content"]


def test_provider_retries_transient_errors(tmp_path):
    provider = ScriptedProvider([
        ProviderError("rate limited", status=429, retriable=True),
        {"content": "Recovered", "tool_calls": []},
    ])
    status_messages = []
    store = TaskStore(tmp_path / ".freenerf" / "tasks.db")

    agent = Agent(
        provider=provider,
        filesystem=FileSystem(tmp_path),
        terminal=Terminal(tmp_path),
        console=DummyConsole(),
        status=status_messages.append,
        tasks=AgentTaskManager(store, session_id="s"),
        config=Config(max_retries=1),
    )

    assert agent.chat("Try") == "Recovered"
    assert len(provider.calls) == 2
    assert any("retrying" in message.lower() for message in status_messages)


def test_non_retriable_provider_error_marks_task_failed(tmp_path):
    provider = ScriptedProvider([
        ProviderError("bad key", status=401, retriable=False),
    ])
    store = TaskStore(tmp_path / ".freenerf" / "tasks.db")
    manager = AgentTaskManager(store, session_id="s")

    agent = Agent(
        provider=provider,
        filesystem=FileSystem(tmp_path),
        terminal=Terminal(tmp_path),
        console=DummyConsole(),
        tasks=manager,
        config=Config(max_retries=2),
    )

    with pytest.raises(ProviderError):
        agent.chat("Try")

    assert agent.last_outcome.status == "failed"
    assert store.get_task(manager.task_id)["status"] == "failed"


def test_plain_text_tool_protocol_fallback(tmp_path):
    (tmp_path / "a.txt").write_text("hello from file", encoding="utf-8")
    provider = PlainProvider([
        '<tool>{"name": "read_file", "args": {"path": "a.txt"}}</tool>',
        "Done after reading.",
    ])
    store = TaskStore(tmp_path / ".freenerf" / "tasks.db")

    agent = Agent(
        provider=provider,
        filesystem=FileSystem(tmp_path),
        terminal=Terminal(tmp_path),
        console=DummyConsole(),
        tasks=AgentTaskManager(store, session_id="s"),
        config=Config(),
    )

    assert agent.chat("Read the file") == "Done after reading."
    transcript = provider.calls[1]
    assert any(
        "hello from file" in str(item.get("content"))
        for item in transcript
    )
    assert any(
        item.get("role") == "user" and "[tool result]" in str(item.get("content"))
        for item in transcript
    )


def test_duplicate_web_search_is_skipped(tmp_path):
    agent, _, _, _ = make_agent(tmp_path, [
        call("web_search", {"query": "Python docs"}, "c1"),
        call("web_search", {"query": "python  DOCS"}, "c2"),
        {"content": "Finished", "tool_calls": []},
    ])

    searched = []
    agent.web_search.search = lambda query, limit: (
        searched.append(query) or "Results"
    )

    assert agent.chat("Find docs") == "Finished"
    assert searched == ["Python docs"]


def test_denied_mode_returns_permission_error(tmp_path):
    agent, provider, _, _ = make_agent(
        tmp_path,
        [
            call("read_file", {"path": "a.txt"}),
            {"content": "Could not read", "tool_calls": []},
        ],
        permission="denied",
    )

    agent.chat("Read a.txt")

    tool_messages = [
        item for item in provider.calls[1]["messages"]
        if item.get("role") == "tool"
    ]
    assert "Permission denied" in tool_messages[-1]["content"]
    assert agent.denied_count == 1


def test_write_outside_workspace_is_blocked(tmp_path):
    agent, provider, _, _ = make_agent(
        tmp_path,
        [
            call("write_file", {"path": "../escape.txt", "content": "x"}),
            {"content": "Stopped", "tool_calls": []},
        ],
        permission="full",
    )

    agent.chat("Write outside")

    tool_messages = [
        item for item in provider.calls[1]["messages"]
        if item.get("role") == "tool"
    ]
    assert "outside project" in tool_messages[-1]["content"].lower()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_image_intent_uses_generate_image_tool(tmp_path):
    agent, provider, _, _ = make_agent(tmp_path, [
        call("generate_image", {"prompt": "a minimal logo for Freenerf"}),
        call("finish", {"status": "completed", "summary": "Generated the logo"}, "c2"),
    ], permission="full")

    class FakeGenerator:
        def __init__(self, path):
            self.path = path
            self.calls = []

        def generate(self, prompt, size="1024x1024", count=1):
            from freenerf.tools.image import ImageResult
            self.calls.append(prompt)
            path = self.path / "logo.png"
            path.write_bytes(b"\x89PNG")
            return [ImageResult(path, prompt, "test-model")]

    fake = FakeGenerator(tmp_path)
    agent.image_generator = fake

    assert agent.chat("Create a logo for Freenerf") == "Generated the logo"
    assert fake.calls == ["a minimal logo for Freenerf"]

    tool_messages = [
        item for item in provider.calls[1]["messages"]
        if item.get("role") == "tool"
    ]
    assert "Generated image saved" in tool_messages[-1]["content"]


def test_tool_timeout_is_reported(tmp_path):
    agent, provider, _, _ = make_agent(tmp_path, [
        call("run_shell", {"command": "sleep 5", "timeout": 1}),
        {"content": "Command timed out", "tool_calls": []},
    ], permission="full")

    agent.chat("Run a slow command")

    tool_messages = [
        item for item in provider.calls[1]["messages"]
        if item.get("role") == "tool"
    ]
    assert "timed out" in tool_messages[-1]["content"].lower()

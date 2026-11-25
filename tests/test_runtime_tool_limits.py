import json

from freenerf.agents.registry import ToolResult
from freenerf.agents.runtime import Agent, TOOLS


class DummyConsole:
    def print(self, *args, **kwargs):
        pass


class FakeProvider:
    supports_tools = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def chat(self, messages, tools=None):
        self.requests.append([dict(message) for message in messages])
        return next(self.responses)


def make_call(name, args, index):
    return {
        "tool_calls": [{
            "id": f"call_{index}",
            "function": {
                "name": name,
                "arguments": json.dumps(args),
            },
        }]
    }


def make_agent(responses):
    provider = FakeProvider(responses)
    return Agent(
        provider=provider,
        filesystem=None,
        terminal=None,
        console=DummyConsole(),
    )


def test_web_search_limit():
    responses = [
        make_call("web_search", {"query": f"query {i}"}, i)
        for i in range(4)
    ]
    responses.append({"content": "Finished", "tool_calls": []})

    agent = make_agent(responses)
    calls = []
    agent.web_search.search = lambda query, max_results: (
        calls.append(query) or "Results"
    )

    assert agent.chat("Search something") == "Finished"
    assert len(calls) == 3


def test_duplicate_web_search_blocked():
    responses = [
        make_call("web_search", {"query": "Python docs"}, i)
        for i in range(2)
    ]
    responses.append({"content": "Finished", "tool_calls": []})

    agent = make_agent(responses)
    calls = []
    agent.web_search.search = lambda query, max_results: (
        calls.append(query) or "Results"
    )

    assert agent.chat("Find Python docs") == "Finished"
    assert calls == ["Python docs"]


def test_web_fetch_limit():
    responses = [
        make_call(
            "web_fetch",
            {"url": f"https://example.com/page{i}"},
            i,
        )
        for i in range(6)
    ]
    responses.append({"content": "Finished", "tool_calls": []})

    agent = make_agent(responses)
    calls = []
    agent.web_fetch.fetch = lambda url: (
        calls.append(url) or "Page content"
    )

    assert agent.chat("Read some pages") == "Finished"
    assert len(calls) == 5

def test_repeated_read_file_is_allowed_after_reassessment():
    responses = [
        make_call("read_file", {"path": "README.md"}, 1),
        make_call("read_file", {"path": "README.md"}, 2),
        {"content": "README contains the project overview.", "tool_calls": []},
    ]
    agent = make_agent(responses)
    calls = []
    agent.execute_tool_result = lambda name, args: (
        calls.append((name, args))
        or ToolResult(name=name, output="README content")
    )

    result = agent.chat("Read README.md and summarize it")

    assert result == "README contains the project overview."
    assert len(calls) == 2
    assert all(name == "read_file" for name, args in calls)

def test_repeated_run_shell_is_allowed_after_reassessment():
    responses = [
        make_call("run_shell", {"command": "pwd"}, 1),
        make_call("run_shell", {"command": "pwd"}, 2),
        {"content": "The working directory was checked.", "tool_calls": []},
    ]
    agent = make_agent(responses)
    calls = []
    agent.execute_tool_result = lambda name, args: (
        calls.append((name, args))
        or ToolResult(name=name, output="/project")
    )

    result = agent.chat("Check the working directory")

    assert result == "The working directory was checked."
    assert len(calls) == 2

def test_max_steps_triggers_final_summary():
    responses = [
        make_call("list_files", {"path": "."}, 1),
        {"content": "I listed the project files.", "tool_calls": []},
    ]
    agent = make_agent(responses)
    calls = []
    agent.execute_tool_result = lambda name, args: (
        calls.append((name, args))
        or ToolResult(name=name, output="README.md\npyproject.toml")
    )

    result = agent.chat("Inspect project files", max_steps=1)

    assert result == "I listed the project files."
    assert len(calls) == 1
    # The budget warning must be sent only for the summary request; it must
    # not become a permanent system instruction for later turns.
    assert not any(
        message.get("role") == "system"
        and "step budget" in message.get("content", "").lower()
        for message in agent.messages[1:]
    )
    assert agent.last_outcome.status == "blocked"
    assert any(
        message.get("role") == "system"
        and "step budget" in message.get("content", "").lower()
        for message in agent.provider.requests[-1]
    )


def test_trim_context_drops_old_complete_turns_and_keeps_latest_turn():
    agent = make_agent([])
    agent.max_context_chars = 90
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old prompt " * 5},
        {"role": "assistant", "content": "old answer " * 5},
        {"role": "user", "content": "new prompt"},
        {"role": "assistant", "content": "new answer"},
    ]

    trimmed = agent._trim_context(messages)

    assert trimmed[0]["role"] == "system"
    assert not any("old prompt" in str(item.get("content")) for item in trimmed)
    assert any("new prompt" in str(item.get("content")) for item in trimmed)
    # The original in-memory history is not modified by preparing a request.
    assert len(messages) == 5


def test_trim_context_keeps_plain_tool_results_with_their_turn():
    agent = make_agent([])
    agent.max_context_chars = 4000
    long_result = "[tool result] " + ("result " * 1000)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current request"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": long_result},
        {"role": "assistant", "content": "final response"},
    ]

    trimmed = agent._trim_context(messages)

    assert any(
        item.get("role") == "user"
        and str(item.get("content", "")).startswith("[tool result]")
        and "truncated" in str(item.get("content", ""))
        for item in trimmed
    )


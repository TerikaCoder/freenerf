from freenerf.agents.runtime import Agent, TOOLS


class DummyConsole:
    def print(self, *args, **kwargs):
        pass


def make_agent(mode="normal"):
    return Agent(
        provider=None,
        filesystem=None,
        terminal=None,
        console=DummyConsole(),
        permission_mode=mode,
    )


def test_web_search_tool_is_registered():
    names = {
        item["function"]["name"]
        for item in TOOLS
    }
    assert "web_search" in names


def test_web_search_dispatch():
    agent = make_agent()
    calls = []

    def fake_search(query, max_results):
        calls.append((query, max_results))
        return "Fake search results"

    agent.web_search.search = fake_search

    result = agent.execute_tool(
        "web_search",
        {"query": "Python documentation", "max_results": 3},
    )

    assert result == "Fake search results"
    assert calls == [("Python documentation", 3)]


def test_web_search_default_max_results():
    agent = make_agent()
    calls = []

    agent.web_search.search = lambda query, limit: (
        calls.append((query, limit)) or "OK"
    )

    result = agent.execute_tool(
        "web_search",
        {"query": "Freenerf"},
    )

    assert result == "OK"
    assert calls == [("Freenerf", 5)]


def test_web_search_blocked_in_denied_mode():
    agent = make_agent(mode="denied")

    agent.web_search.search = lambda *args: (
        "ERROR: search should not execute"
    )

    result = agent.execute_tool(
        "web_search",
        {"query": "test"},
    )

    assert result.startswith("Permission denied:")

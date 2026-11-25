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


def test_web_fetch_tool_is_registered():
    names = {
        item["function"]["name"]
        for item in TOOLS
    }
    assert "web_fetch" in names


def test_web_fetch_dispatch():
    agent = make_agent()
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return "Fake page content"

    agent.web_fetch.fetch = fake_fetch

    result = agent.execute_tool(
        "web_fetch",
        {"url": "https://example.com"},
    )

    assert result == "Fake page content"
    assert calls == ["https://example.com"]


def test_web_fetch_allowed_in_normal_mode():
    agent = make_agent(mode="normal")
    agent.web_fetch.fetch = lambda url: "OK"

    result = agent.execute_tool(
        "web_fetch",
        {"url": "https://example.com"},
    )

    assert result == "OK"


def test_web_fetch_blocked_in_denied_mode():
    agent = make_agent(mode="denied")
    agent.web_fetch.fetch = lambda url: (
        "ERROR: fetch should not execute"
    )

    result = agent.execute_tool(
        "web_fetch",
        {"url": "https://example.com"},
    )

    assert result.startswith("Permission denied:")

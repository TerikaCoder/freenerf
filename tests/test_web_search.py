import httpx

from freenerf.tools.web_search import WebSearch


class MockResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "HTTP error",
                request=httpx.Request("GET", "https://example.com"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.data


def test_empty_query():
    assert WebSearch().search("   ") == "Search query is empty"


def test_search_results(monkeypatch):
    def fake_get(*args, **kwargs):
        return MockResponse({
            "ok": True,
            "results": [{
                "title": "Python AST",
                "domain": "docs.python.org",
                "url": "https://docs.python.org/3/library/ast.html",
                "snippet": "AST documentation",
            }],
        })

    monkeypatch.setattr("freenerf.tools.web_search.httpx.get", fake_get)

    result = WebSearch().search("Python AST")

    assert "Python AST" in result
    assert "https://docs.python.org/3/library/ast.html" in result
    assert "AST documentation" in result


def test_invalid_provider_response(monkeypatch):
    monkeypatch.setattr(
        "freenerf.tools.web_search.httpx.get",
        lambda *a, **kw: MockResponse({"ok": False}),
    )

    result = WebSearch().search("test")
    assert "invalid response" in result


def test_invalid_url_is_skipped(monkeypatch):
    monkeypatch.setattr(
        "freenerf.tools.web_search.httpx.get",
        lambda *a, **kw: MockResponse({
            "ok": True,
            "results": [{"title": "Bad", "url": "javascript:alert(1)"}],
        }),
    )

    assert WebSearch().search("test") == "No valid web results"


def test_result_limit(monkeypatch):
    captured = {}

    def fake_get(*args, **kwargs):
        captured.update(kwargs["params"])
        return MockResponse({"ok": True, "results": []})

    monkeypatch.setattr("freenerf.tools.web_search.httpx.get", fake_get)

    WebSearch().search("test", 100)

    assert captured["size"] == 5

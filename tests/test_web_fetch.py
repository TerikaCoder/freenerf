import httpx

from freenerf.tools.web_fetch import WebFetch


def test_blocks_localhost():
    fetcher = WebFetch()
    assert fetcher.validate_url("http://localhost/") is None


def test_blocks_private_ip():
    fetcher = WebFetch()
    assert fetcher.validate_url("http://127.0.0.1/") is None
    assert fetcher.validate_url("http://192.168.1.1/") is None


def test_blocks_unsupported_scheme():
    fetcher = WebFetch()
    assert fetcher.validate_url("file:///etc/passwd") is None


def test_blocks_credentials():
    fetcher = WebFetch()
    assert fetcher.validate_url("https://user:pass@example.com") is None


def test_extracts_html(monkeypatch):
    fetcher = WebFetch()

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"
        headers = {"content-type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield (
                b"<html><head><title>Test Page</title></head>"
                b"<body><nav>Menu</nav><main>"
                b"<h1>Hello Freenerf</h1>"
                b"<script>bad()</script>"
                b"<p>Useful content</p>"
                b"</main></body></html>"
            )

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        fetcher,
        "validate_url",
        lambda url: url if url.startswith("https://") else None,
    )

    result = fetcher.fetch("https://example.com")

    assert "Title: Test Page" in result
    assert "Hello Freenerf" in result
    assert "Useful content" in result
    assert "bad()" not in result
    assert "Menu" not in result


def test_rejects_non_html(monkeypatch):
    fetcher = WebFetch()

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        fetcher, "validate_url", lambda url: url
    )

    result = fetcher.fetch("https://example.com")
    assert "not an HTML page" in result


def test_rejects_oversized_page(monkeypatch):
    fetcher = WebFetch()
    fetcher.MAX_BYTES = 10

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"
        headers = {"content-type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"x" * 11

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        fetcher, "validate_url", lambda url: url
    )

    result = fetcher.fetch("https://example.com")
    assert "exceeds the 4 MB limit" in result


def test_handles_http_error(monkeypatch):
    fetcher = WebFetch()

    class FakeResponse:
        status_code = 404
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(
                "Not Found", request=request, response=response
            )

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        fetcher, "validate_url", lambda url: url
    )

    result = fetcher.fetch("https://example.com")
    assert "HTTP error: 404" in result


def test_blocks_unsafe_ipv6():
    fetcher = WebFetch()

    assert fetcher.validate_url("http://[::1]/") is None
    assert fetcher.validate_url("http://[fc00::1]/") is None
    assert fetcher.validate_url("http://[fe80::1]/") is None


def test_blocks_internal_hostnames():
    fetcher = WebFetch()

    assert fetcher.validate_url("http://localhost/") is None
    assert fetcher.validate_url("http://printer.local/") is None
    assert fetcher.validate_url("http://server.internal/") is None
    assert fetcher.validate_url("http://device.localhost/") is None


def test_blocks_unsafe_redirect(monkeypatch):
    fetcher = WebFetch()
    requested_urls = []

    class FakeResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1/"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url):
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        fetcher,
        "validate_url",
        lambda url: None if "127.0.0.1" in url else url,
    )

    result = fetcher.fetch("https://example.com")

    assert "unsafe redirect URL" in result
    assert requested_urls == ["https://example.com"]


def test_limits_redirect_count(monkeypatch):
    fetcher = WebFetch()
    fetcher.MAX_REDIRECTS = 2
    requested_urls = []

    class FakeResponse:
        status_code = 302
        headers = {"location": "/next"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url):
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(fetcher, "validate_url", lambda url: url)

    result = fetcher.fetch("https://example.com")

    assert "too many redirects" in result
    assert len(requested_urls) == 3

import pytest

from freenerf.llm import catalog


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def clear_cache():
    catalog.clear_cache()
    yield
    catalog.clear_cache()


def test_openai_compatible_catalog_uses_its_own_endpoint(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        return FakeResponse({
            "data": [
                {"id": "local-model", "name": "Local Model"},
                {"id": "other", "context_window": 8192},
            ]
        })

    monkeypatch.setattr(catalog.httpx, "get", fake_get)

    models = catalog.get_models(
        api_key="key",
        provider="openai-compatible",
        base_url="http://127.0.0.1:1234/v1",
    )

    assert calls[0]["url"] == "http://127.0.0.1:1234/v1/models"
    assert calls[0]["headers"]["Authorization"] == "Bearer key"
    assert [item["id"] for item in models] == ["local-model", "other"]
    assert models[1]["context_length"] == 8192


def test_openrouter_catalog_filters_models_without_tools(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({
            "data": [
                {
                    "id": "vendor/tools",
                    "name": "Tools",
                    "supported_parameters": ["tools"],
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "vendor/plain",
                    "name": "Plain",
                    "supported_parameters": [],
                    "pricing": {"prompt": "0.1", "completion": "0.2"},
                },
            ]
        })

    monkeypatch.setattr(catalog.httpx, "get", fake_get)

    models = catalog.get_models(api_key="key", provider="openrouter")

    assert len(models) == 1
    assert models[0]["id"] == "vendor/tools"
    assert models[0]["supports_tools"] is True
    assert models[0]["pricing_label"] == "Free"


def test_catalog_cache_can_be_bypassed(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return FakeResponse({"data": [{"id": "m"}]})

    monkeypatch.setattr(catalog.httpx, "get", fake_get)

    catalog.get_models(
        provider="openai-compatible", base_url="http://x/v1"
    )
    catalog.get_models(
        provider="openai-compatible", base_url="http://x/v1"
    )
    assert len(calls) == 1

    catalog.get_models(
        provider="openai-compatible",
        base_url="http://x/v1",
        force_refresh=True,
    )
    assert len(calls) == 2


def test_price_label_handles_unknown_values():
    assert catalog._price_label({"prompt": "0", "completion": "0"}) == "Free"
    assert catalog._price_label({"prompt": "0.1", "completion": "0.2"}) == "Paid"
    assert catalog._price_label({"prompt": "nan"}) == "Unknown"
    assert catalog._price_label({}) == "Unknown"


def test_unsupported_catalog_provider_raises():
    with pytest.raises(catalog.CatalogError):
        catalog.get_models(provider="nope")

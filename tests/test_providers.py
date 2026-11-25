import json

import httpx
import pytest

from freenerf.config import Config
from freenerf.llm.providers import (
    Gemini,
    OpenAICompatible,
    ProviderError,
    create_provider,
    create_provider_for_config,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.headers = headers or {}

    @property
    def is_error(self):
        return self.status_code >= 400

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.closed = False

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, lines):
        self.lines = lines
        self.headers = {"content-type": "text/event-stream"}
        self.status_code = 200
        self.is_error = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self):
        yield from self.lines

    def read(self):
        pass


class StreamClient(FakeClient):
    def __init__(self, lines):
        super().__init__()
        self.lines = lines

    def stream(self, method, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return FakeStream(self.lines)


def sse(payload):
    return "data: " + json.dumps(payload)


def test_openai_compatible_payload_uses_configuration():
    provider = OpenAICompatible(
        "key",
        "test/model",
        "https://example.test/v1",
        temperature=0.7,
        max_tokens=512,
    )
    provider.client = FakeClient([
        FakeResponse(payload={"choices": [{"message": {"content": "hello"}}]})
    ])

    tools = [{
        "type": "function",
        "function": {"name": "read_file", "parameters": {"type": "object"}},
    }]

    result = provider.chat(
        [{"role": "user", "content": "hi"}],
        tools=tools,
    )

    payload = provider.client.calls[0]["json"]
    assert payload["temperature"] == 0.7
    assert payload["max_tokens"] == 512
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert provider.client.calls[0]["headers"]["Authorization"] == "Bearer key"
    assert result["content"] == "hello"


def test_openai_error_mapping_is_retriable_aware():
    provider = OpenAICompatible("key", "m", "https://example.test/v1")

    provider.client = FakeClient([FakeResponse(401, text="bad key")])
    with pytest.raises(ProviderError) as error:
        provider.chat([{"role": "user", "content": "hi"}])
    assert error.value.status == 401
    assert error.value.retriable is False

    provider.client = FakeClient([FakeResponse(429, text="slow down")])
    with pytest.raises(ProviderError) as error:
        provider.chat([{"role": "user", "content": "hi"}])
    assert error.value.status == 429
    assert error.value.retriable is True


def test_streaming_assembles_content_and_tool_arguments():
    lines = [
        sse({"choices": [{"delta": {"content": "Hel"}}]}),
        sse({"choices": [{"delta": {"content": "lo"}}]}),
        sse({"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": "call_1",
            "function": {
                "name": "read_file",
                "arguments": '{"path":',
            },
        }]}}]}),
        sse({"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": ' "a.txt"}'},
        }]}}]}),
        "data: [DONE]",
    ]

    provider = OpenAICompatible("key", "m", "https://example.test/v1")
    provider.client = StreamClient(lines)

    chunks = []
    result = provider.chat(
        [{"role": "user", "content": "hi"}],
        stream_callback=chunks.append,
    )

    assert chunks == ["Hel", "lo"]
    assert result["content"] == "Hello"
    assert len(result["tool_calls"]) == 1

    arguments = json.loads(
        result["tool_calls"][0]["function"]["arguments"]
    )
    assert arguments == {"path": "a.txt"}
    assert provider.client.calls[0]["json"]["stream"] is True


def test_gemini_payload_and_tool_translation():
    gemini = Gemini("key", "gemini-2.5-flash", temperature=0.4, max_tokens=2048)
    gemini.client = FakeClient([
        FakeResponse(payload={
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "Thinking"},
                        {
                            "functionCall": {
                                "name": "read_file",
                                "args": {"path": "a.txt"},
                                "id": "fc_1",
                            },
                            "thoughtSignature": "sig",
                        },
                    ]
                }
            }]
        })
    ])

    result = gemini.chat(
        [
            {"role": "system", "content": "custom system"},
            {"role": "user", "content": "read a.txt"},
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"path": {"type": "string"}},
                },
            },
        }],
    )

    payload = gemini.client.calls[0]["json"]
    assert payload["generationConfig"]["temperature"] == 0.4
    assert payload["generationConfig"]["maxOutputTokens"] == 2048
    assert payload["systemInstruction"]["parts"][0]["text"] == "custom system"
    assert payload["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
    assert "additionalProperties" not in json.dumps(payload["tools"])

    assert result["content"] == "Thinking"
    assert result["tool_calls"][0]["function"]["name"] == "read_file"
    assert result["raw_message"]["_gemini_parts"][1]["thoughtSignature"] == "sig"


def test_gemini_requires_api_key():
    with pytest.raises(ProviderError):
        create_provider(provider="gemini", api_key="", model="gemini-2.5-flash")


def test_freellmapi_requires_api_key():
    with pytest.raises(ProviderError):
        create_provider(provider="freellmapi", api_key="", model="auto")


def test_create_provider_for_config_openai_compatible():
    config = Config(
        provider="openai-compatible",
        openai_api_key="local",
        openai_base_url="http://127.0.0.1:1234/v1",
        openai_model="local-model",
        temperature=0.1,
        max_tokens=64,
    )

    provider = create_provider_for_config(config)

    assert isinstance(provider, OpenAICompatible)
    assert provider.base_url == "http://127.0.0.1:1234/v1"
    assert provider.model == "local-model"
    assert provider.temperature == 0.1
    assert provider.max_tokens == 64


def test_provider_capabilities_are_explicit():
    config = Config(provider="gemini", gemini_api_key="key")
    provider = create_provider_for_config(config)

    capabilities = provider.capabilities()
    assert capabilities["provider"] == "gemini"
    assert capabilities["supports_tools"] is True
    assert capabilities["supports_streaming"] is False

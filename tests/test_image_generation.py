import base64
import json

import pytest

from freenerf.tools import image as image_module
from freenerf.tools.image import ImageGenerationError, ImageGenerator


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.is_error = status >= 400

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)

    def stream(self, method, url):
        raise AssertionError("not used in these tests")


def install_client(monkeypatch, client):
    monkeypatch.setattr(
        image_module.httpx, "Client", lambda *args, **kwargs: client
    )


def payload_for(prompt="A test image"):
    return {
        "model": "gpt-image-1",
        "data": [{
            "b64_json": base64.b64encode(PNG_BYTES).decode(),
            "revised_prompt": prompt,
        }],
    }


def test_generate_saves_image_and_history(tmp_path, monkeypatch):
    client = FakeClient([FakeResponse(payload=payload_for())])
    install_client(monkeypatch, client)

    generator = ImageGenerator(
        api_key="key",
        base_url="https://example.test/v1",
        output_dir=tmp_path / "images",
    )

    results = generator.generate("A futuristic city at dusk", size="512x512")

    assert len(results) == 1
    assert results[0].path.is_file()
    assert results[0].path.read_bytes().startswith(b"\x89PNG")
    assert "Generated image saved" in results[0].message()

    request = client.calls[0]
    assert request["url"] == "https://example.test/v1/images/generations"
    assert request["headers"]["Authorization"] == "Bearer key"
    assert request["json"]["prompt"] == "A futuristic city at dusk"
    assert request["json"]["size"] == "512x512"
    assert request["json"]["response_format"] == "b64_json"

    history = generator.history()
    assert len(history) == 1
    assert history[0]["prompt"] == "A futuristic city at dusk"


def test_generate_retries_without_optional_parameters(tmp_path, monkeypatch):
    client = FakeClient([
        FakeResponse(400, text="unknown parameter response_format"),
        FakeResponse(payload=payload_for()),
    ])
    install_client(monkeypatch, client)

    generator = ImageGenerator(
        api_key="key",
        base_url="https://example.test/v1",
        output_dir=tmp_path / "images",
    )

    results = generator.generate("A logo")

    assert results
    assert len(client.calls) == 2
    assert "response_format" in client.calls[0]["json"]
    assert "response_format" not in client.calls[1]["json"]


def test_generate_raises_without_key_or_output(tmp_path):
    generator = ImageGenerator(api_key="", base_url="https://x/v1")

    with pytest.raises(ImageGenerationError):
        generator.generate("A picture")


def test_generate_rejects_empty_prompt(tmp_path):
    generator = ImageGenerator(
        api_key="key",
        base_url="https://x/v1",
        output_dir=tmp_path,
    )

    with pytest.raises(ImageGenerationError):
        generator.generate("   ")


def test_generate_reports_provider_errors(tmp_path, monkeypatch):
    client = FakeClient([FakeResponse(500, text="boom")])
    install_client(monkeypatch, client)

    generator = ImageGenerator(
        api_key="key",
        base_url="https://x/v1",
        output_dir=tmp_path,
    )

    with pytest.raises(ImageGenerationError):
        generator.generate("A picture")

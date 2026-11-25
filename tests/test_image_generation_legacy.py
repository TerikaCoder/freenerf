import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from freenerf.agents.permissions import evaluate_permission
from freenerf.agents.runtime import Agent, TOOLS
from freenerf.tools.image_generation import ImageGenerationError, ImageGenerator


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-image-data"


def make_generator(tmp_path, api_key="test-key"):
    return ImageGenerator(
        api_key=api_key,
        base_url="https://example.test/v1",
        model="auto",
        output_dir=tmp_path / "generated_images",
    )


def mocked_response(payload):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def test_image_tool_is_registered():
    names = {item["function"]["name"] for item in TOOLS}
    assert "image_create" in names


def test_image_create_permission_respects_modes():
    assert evaluate_permission("normal", "image_create", {"prompt": "A cat"})[0] == "ask"
    assert evaluate_permission("full", "image_create", {"prompt": "A cat"})[0] == "allow"
    assert evaluate_permission("denied", "image_create", {"prompt": "A cat"})[0] == "deny"


def test_generate_saves_png_and_sends_expected_request(tmp_path):
    generator = make_generator(tmp_path)
    response = mocked_response({
        "data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]
    })

    with patch(
        "freenerf.tools.image_generation.urlopen",
        return_value=response,
    ) as mocked:
        saved_path = generator.generate("A small robot in a garden")

    assert saved_path.exists()
    assert saved_path.parent == (tmp_path / "generated_images")
    assert saved_path.suffix == ".png"
    assert saved_path.read_bytes() == PNG_BYTES

    request = mocked.call_args.args[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://example.test/v1/images/generations"
    assert request.get_method() == "POST"
    assert body == {
        "model": "auto",
        "prompt": "A small robot in a garden",
        "size": "1024x1024",
        "n": 1,
    }
    assert request.get_header("Authorization") == "Bearer test-key"


def test_generate_rejects_missing_key(tmp_path):
    generator = make_generator(tmp_path, api_key="")

    with pytest.raises(ImageGenerationError, match="not configured"):
        generator.generate("A cat")


def test_generate_rejects_empty_prompt(tmp_path):
    generator = make_generator(tmp_path)

    with pytest.raises(ImageGenerationError, match="must not be empty"):
        generator.generate("   ")


def test_generate_rejects_unsupported_size(tmp_path):
    generator = make_generator(tmp_path)

    with pytest.raises(ImageGenerationError, match="Unsupported image size"):
        generator.generate("A cat", "500x500")


def test_generate_rejects_invalid_base64(tmp_path):
    generator = make_generator(tmp_path)
    response = mocked_response({"data": [{"b64_json": "not base64!!!"}]})

    with patch(
        "freenerf.tools.image_generation.urlopen",
        return_value=response,
    ):
        with pytest.raises(ImageGenerationError, match="invalid base64"):
            generator.generate("A cat")


def test_agent_dispatches_natural_language_image_request(tmp_path):
    class FakeImageGenerator:
        is_configured = True

        def __init__(self):
            self.calls = []

        def generate(self, prompt, size):
            self.calls.append((prompt, size))
            return tmp_path / "generated_images" / "test.png"

    class FakeProvider:
        supports_tools = True

        def __init__(self):
            self.step = 0
            self.received_tools = None

        def chat(self, messages, tools=None):
            self.received_tools = tools
            self.step += 1
            if self.step == 1:
                return {
                    "tool_calls": [{
                        "id": "image_call_1",
                        "function": {
                            "name": "image_create",
                            "arguments": json.dumps({
                                "prompt": "A friendly robot",
                                "size": "1024x1024",
                            }),
                        },
                    }]
                }
            return {"content": "Ảnh đã được tạo.", "tool_calls": []}

    image_generator = FakeImageGenerator()
    provider = FakeProvider()
    agent = Agent(
        provider=provider,
        filesystem=None,
        terminal=None,
        console=MagicMock(),
        permission_mode="full",
        image_generator=image_generator,
    )

    result = agent.chat("Tạo ảnh một robot thân thiện.")

    assert result == "Ảnh đã được tạo."
    assert image_generator.calls == [("A friendly robot", "1024x1024")]
    assert any(
        item["function"]["name"] == "image_create"
        for item in provider.received_tools
    )

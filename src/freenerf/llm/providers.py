"""Provider implementations.

Each provider owns its request/response translation, including tool calling.
Nothing outside this module assumes one vendor's wire format.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx


RETRIABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ProviderError(Exception):
    def __init__(self, message, *, status=None, retriable=False):
        super().__init__(message)
        self.status = status
        self.retriable = retriable


PROVIDER_INFO = {
    "openrouter": {
        "label": "OpenRouter",
        "key_field": "openrouter_api_key",
        "model_field": "openrouter_model",
        "base_url_field": "openrouter_base_url",
        "default_model": "openai/gpt-4o-mini",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_images": False,
    },
    "gemini": {
        "label": "Google Gemini",
        "key_field": "gemini_api_key",
        "model_field": "gemini_model",
        "base_url_field": "gemini_base_url",
        "default_model": "gemini-2.5-flash",
        "supports_tools": True,
        "supports_streaming": False,
        "supports_images": False,
    },
    "freellmapi": {
        "label": "FreeLLMAPI",
        "key_field": "freellmapi_api_key",
        "model_field": "freellmapi_model",
        "base_url_field": "freellmapi_base_url",
        "default_model": "auto",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_images": True,
    },
    "openai-compatible": {
        "label": "OpenAI-compatible",
        "key_field": "openai_api_key",
        "model_field": "openai_model",
        "base_url_field": "openai_base_url",
        "default_model": "gpt-4o-mini",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_images": False,
    },
}


def provider_info(name: str) -> dict:
    return dict(PROVIDER_INFO.get(str(name).lower(), {}))


def _redact(value: str) -> str:
    text = str(value)
    for marker in ("sk-", "AIza"):
        if marker in text:
            parts = []
            for word in text.split():
                parts.append("[REDACTED]" if marker in word else word)
            text = " ".join(parts)
    return text


def _error_from_response(response: httpx.Response) -> ProviderError:
    status = response.status_code

    try:
        detail = response.text[:800]
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        detail = ""

    detail = _redact(detail)

    if status == 401:
        message = f"Invalid or missing API key (HTTP 401): {detail}"
    elif status == 403:
        message = f"Access denied (HTTP 403): {detail}"
    elif status == 404:
        message = f"Model or endpoint not found (HTTP 404): {detail}"
    elif status == 429:
        message = f"Rate limited (HTTP 429): {detail}"
    elif status >= 500:
        message = f"Provider server error (HTTP {status}): {detail}"
    else:
        message = f"HTTP {status}: {detail}"

    return ProviderError(
        message,
        status=status,
        retriable=status in RETRIABLE_STATUS,
    )


def _provider_json_logging_enabled() -> bool:
    return os.getenv("FREENERF_LOG_PROVIDER_JSON", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _log_provider_json(provider, model, response):
    """Optionally persist raw provider JSON (never request headers/keys)."""
    if not _provider_json_logging_enabled():
        return

    try:
        log_dir = Path.home() / ".config" / "freenerf" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        safe_model = "".join(
            char if char.isalnum() or char in "-_." else "_"
            for char in str(model)
        )[:80]

        try:
            body = response.json()
        except (ValueError, TypeError):
            body = {"raw_text": _redact(response.text[:10000])}

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "http_status": response.status_code,
            "response": body,
        }

        output = log_dir / f"{timestamp}_{provider}_{safe_model}.json"
        output.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        try:
            output.chmod(0o600)
        except OSError:
            pass
    except Exception:
        # Logging must never break a valid provider response.
        pass


class Provider:
    name = "base"
    supports_tools = False
    supports_streaming = False
    supports_images = False

    def close(self):
        pass

    def capabilities(self) -> dict:
        return {
            "provider": self.name,
            "supports_tools": self.supports_tools,
            "supports_streaming": self.supports_streaming,
            "supports_images": self.supports_images,
        }


class OpenAICompatible(Provider):
    supports_tools = True
    supports_streaming = True

    def __init__(
        self,
        api_key,
        model,
        base_url,
        provider_name="openrouter",
        temperature=0.2,
        max_tokens=4096,
        timeout=90.0,
        supports_tools=True,
        supports_streaming=True,
    ):
        self.api_key = api_key or ""
        self.model = model
        self.base_url = str(base_url or "").rstrip("/")
        self.provider_name = provider_name
        self.name = provider_name
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.supports_tools = bool(supports_tools)
        self.supports_streaming = bool(supports_streaming)
        self.client = httpx.Client(timeout=timeout)

    # ------------------------------------------------------------------
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, messages, tools, stream=False) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if stream:
            payload["stream"] = True

        return payload

    @staticmethod
    def _message_from_payload(data: dict) -> dict:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"Invalid API response: {json.dumps(data)[:800]}"
            ) from exc

        if not isinstance(message, dict):
            raise ProviderError("Invalid API response: message is not an object")

        return {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or [],
            "raw_message": message,
        }

    def chat(self, messages, tools=None, stream_callback=None):
        url = f"{self.base_url}/chat/completions"
        use_stream = bool(stream_callback and self.supports_streaming)

        if use_stream:
            return self._stream_chat(url, messages, tools, stream_callback)

        payload = self._payload(messages, tools, stream=False)

        try:
            response = self.client.post(
                url, headers=self._headers(), json=payload
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Provider request timed out", retriable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Provider connection error: {exc}", retriable=True
            ) from exc

        _log_provider_json(self.provider_name, self.model, response)

        if response.is_error:
            raise _error_from_response(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Invalid API response: {response.text[:800]}"
            ) from exc

        return self._message_from_payload(data)

    # ------------------------------------------------------------------
    def _stream_chat(self, url, messages, tools, stream_callback):
        payload = self._payload(messages, tools, stream=True)

        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        event_body: list[str] = []
        finished = False
        data_seen = False

        try:
            with self.client.stream(
                "POST", url, headers=self._headers(), json=payload
            ) as response:
                if response.is_error:
                    response.read()
                    raise _error_from_response(response)

                content_type = response.headers.get("content-type", "")

                if "text/event-stream" not in content_type:
                    response.read()
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise ProviderError(
                            f"Invalid API response: {response.text[:800]}"
                        ) from exc
                    return self._message_from_payload(data)

                for line in response.iter_lines():
                    line = line.strip()
                    if not line:
                        continue

                    if line.startswith("event:") and "response.completed" in line:
                        finished = True
                        continue

                    if line.startswith("data:"):
                        body = line[5:].strip()
                    elif event_body and not line.startswith(":"):
                        body = line
                    else:
                        continue

                    if body == "[DONE]":
                        finished = True
                        break
                    if not body:
                        continue

                    data_seen = True
                    try:
                        chunk = json.loads(body)
                    except ValueError:
                        continue

                    # Gemini-style stream wrappers are handled by Gemini.
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue

                    delta = choices[0].get("delta") or {}

                    text = delta.get("content")
                    if text:
                        content_parts.append(text)
                        try:
                            stream_callback(text)
                        except Exception:
                            pass

                    for fragment in delta.get("tool_calls") or []:
                        index = int(fragment.get("index", 0))
                        entry = tool_calls.setdefault(index, {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if fragment.get("id"):
                            entry["id"] = fragment["id"]
                        function = fragment.get("function") or {}
                        if function.get("name"):
                            entry["function"]["name"] += function["name"]
                        if function.get("arguments"):
                            entry["function"]["arguments"] += function["arguments"]

        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Provider stream timed out", retriable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Provider connection error: {exc}", retriable=True
            ) from exc

        if not data_seen and not finished:
            raise ProviderError("Provider stream returned no data")

        calls = [tool_calls[index] for index in sorted(tool_calls)]
        for position, call in enumerate(calls):
            if not call.get("id"):
                call["id"] = f"call_{position}"

        content = "".join(content_parts) or None

        raw_message = None
        if calls:
            raw_message = {
                "role": "assistant",
                "content": content,
                "tool_calls": calls,
            }

        return {
            "content": content,
            "tool_calls": calls,
            "raw_message": raw_message,
        }

    def close(self):
        self.client.close()


class Gemini(Provider):
    supports_tools = True
    supports_streaming = False
    supports_images = False
    name = "gemini"

    def __init__(
        self,
        api_key,
        model,
        base_url="https://generativelanguage.googleapis.com",
        temperature=0.2,
        max_tokens=4096,
        timeout=90.0,
    ):
        self.api_key = api_key
        self.model = str(model).removeprefix("models/")
        self.base_url = str(base_url or "").rstrip("/")
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.client = httpx.Client(timeout=timeout)

    @staticmethod
    def _clean_schema(value):
        if isinstance(value, list):
            return [Gemini._clean_schema(item) for item in value]

        if not isinstance(value, dict):
            return value

        ignored = {
            "$schema", "additionalProperties",
            "default", "title", "examples",
        }

        return {
            key: Gemini._clean_schema(item)
            for key, item in value.items()
            if key not in ignored
        }

    @staticmethod
    def _convert_tools(tools):
        declarations = []

        for item in tools or []:
            function = item.get("function", {})
            name = function.get("name")

            if not name:
                continue

            declarations.append({
                "name": name,
                "description": function.get("description", ""),
                "parameters": Gemini._clean_schema(
                    function.get("parameters") or {
                        "type": "object",
                        "properties": {},
                    }
                ),
            })

        return [{"functionDeclarations": declarations}] if declarations else []

    def chat(self, messages, tools=None, stream_callback=None):
        import uuid

        system_text = "You are Freenerf, an AI coding assistant."
        contents = []
        call_names = {}

        def add_part(role, part):
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": role, "parts": [part]})

        for message in messages:
            role = message.get("role")

            if role == "system":
                system_text = message.get("content") or system_text
                continue

            if role == "user":
                add_part("user", {"text": message.get("content") or ""})
                continue

            if role == "assistant":
                calls = message.get("tool_calls") or []

                if calls:
                    original_parts = message.get("_gemini_parts")

                    if original_parts:
                        for part in original_parts:
                            add_part("model", part)

                    for call in calls:
                        function = call.get("function", {})
                        name = function.get("name", "")
                        call_id = call.get("id") or uuid.uuid4().hex
                        call_names[call_id] = name

                        if original_parts:
                            continue

                        raw_args = function.get("arguments", "{}")
                        try:
                            args = (
                                json.loads(raw_args)
                                if isinstance(raw_args, str)
                                else raw_args
                            )
                        except (ValueError, TypeError):
                            args = {}

                        add_part("model", {
                            "functionCall": {
                                "name": name,
                                "args": args if isinstance(args, dict) else {},
                                "id": call_id,
                            }
                        })
                elif message.get("content"):
                    add_part("model", {"text": message["content"]})

                continue

            if role == "tool":
                call_id = message.get("tool_call_id", "")
                name = call_names.get(call_id)

                if not name:
                    raise ProviderError(
                        f"Gemini: missing function name for tool call {call_id}"
                    )

                add_part("user", {
                    "functionResponse": {
                        "name": name,
                        "id": call_id,
                        "response": {
                            "result": message.get("content", "")
                        },
                    }
                })

        if not contents:
            raise ProviderError("Gemini: conversation is empty")

        payload = {
            "contents": contents,
            "systemInstruction": {
                "parts": [{"text": system_text}]
            },
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }

        gemini_tools = self._convert_tools(tools)
        if gemini_tools:
            payload["tools"] = gemini_tools

        try:
            response = self.client.post(
                f"{self.base_url}/v1beta/models/{self.model}:generateContent",
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Gemini request timed out", retriable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Gemini connection error: {exc}", retriable=True
            ) from exc

        _log_provider_json("gemini", self.model, response)

        if response.is_error:
            error = _error_from_response(response)
            raise ProviderError(
                str(error), status=error.status,
                retriable=error.retriable,
            )

        try:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            blocked = ""
            try:
                blocked = json.dumps(
                    data.get("promptFeedback", {}), ensure_ascii=False
                )[:500]
            except Exception:
                pass
            raise ProviderError(
                f"Invalid Gemini response: {blocked or response.text[:800]}"
            ) from exc

        text_parts = []
        tool_calls = []
        preserved_parts = []

        for original_part in parts:
            part = dict(original_part)

            if part.get("text"):
                text_parts.append(part["text"])

            function_call = part.get("functionCall")
            if function_call:
                function_call = dict(function_call)

                call_id = function_call.get("id") or uuid.uuid4().hex
                function_call["id"] = call_id

                part["functionCall"] = function_call
                args = function_call.get("args") or {}

                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": function_call.get("name", ""),
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                })

            preserved_parts.append(part)

        raw_message = None
        if tool_calls:
            raw_message = {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
                "_gemini_parts": preserved_parts,
            }

        return {
            "content": "\n".join(text_parts) or None,
            "tool_calls": tool_calls,
            "raw_message": raw_message,
        }

    def close(self):
        self.client.close()


def create_provider(
    provider=None,
    api_key=None,
    model=None,
    base_url=None,
    *,
    temperature=0.2,
    max_tokens=4096,
    timeout=90.0,
    supports_streaming=True,
):
    provider = (
        provider or os.getenv("FREENERF_PROVIDER") or "openrouter"
    ).lower()

    if provider == "gemini":
        api_key = api_key or os.getenv("FREENERF_GEMINI_API_KEY", "")
        model = model or os.getenv(
            "FREENERF_GEMINI_MODEL", "gemini-2.5-flash"
        )

        if not api_key:
            raise ProviderError("Missing Gemini API key")

        base_url = (
            base_url
            or os.getenv("FREENERF_GEMINI_BASE_URL")
            or "https://generativelanguage.googleapis.com"
        )
        return Gemini(
            api_key,
            model,
            base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    if provider == "freellmapi":
        api_key = api_key or os.getenv("FREENERF_FREELLMAPI_API_KEY", "")
        model = model or os.getenv("FREENERF_FREELLMAPI_MODEL", "auto")
        base_url = (
            base_url
            or os.getenv("FREENERF_FREELLMAPI_BASE_URL")
            or "https://freellmapi-3d3g.onrender.com/v1"
        )
        if not api_key:
            raise ProviderError("Missing FreeLLMAPI unified API key")
        return OpenAICompatible(
            api_key,
            model,
            base_url,
            provider_name="freellmapi",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            supports_streaming=supports_streaming,
        )

    if provider in ("openai", "openai-compatible", "openrouter"):
        is_openrouter = provider == "openrouter"

        if is_openrouter:
            api_key = api_key or os.getenv("FREENERF_OPENROUTER_API_KEY", "")
            model = model or os.getenv(
                "FREENERF_OPENROUTER_MODEL", "openai/gpt-4o-mini"
            )
            base_url = (
                base_url
                or os.getenv("FREENERF_OPENROUTER_BASE_URL")
                or "https://openrouter.ai/api/v1"
            )
        else:
            api_key = (
                api_key
                or os.getenv("FREENERF_OPENAI_API_KEY", "")
                or os.getenv("FREENERF_API_KEY", "")
            )
            model = model or os.getenv(
                "FREENERF_OPENAI_MODEL", "gpt-4o-mini"
            )
            base_url = (
                base_url
                or os.getenv("FREENERF_OPENAI_BASE_URL")
                or os.getenv("FREENERF_BASE_URL")
                or "https://api.openai.com/v1"
            )

        return OpenAICompatible(
            api_key,
            model,
            base_url,
            provider_name="openrouter" if is_openrouter else "openai-compatible",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            supports_streaming=supports_streaming,
        )

    raise ProviderError(f"Unsupported provider: {provider}")


def create_provider_for_config(config, provider=None) -> Provider:
    """Build a provider for ``config`` (optionally for another provider)."""
    provider = (provider or config.provider).lower()
    info = PROVIDER_INFO.get(provider)

    if info is None:
        raise ProviderError(f"Unsupported provider: {provider}")

    api_key = getattr(config, info["key_field"], "")
    model = getattr(config, info["model_field"], "") or info["default_model"]
    base_url = getattr(config, info["base_url_field"], "")

    return create_provider(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.request_timeout,
        supports_streaming=config.streaming,
    )

# Providers

Freenerf supports four provider families. Each translates the shared message
list and tool schema into its own API format; nothing outside
`llm/providers.py` assumes a vendor format.

| Provider | Config key | Model field | Base URL field | Default model |
| --- | --- | --- | --- | --- |
| OpenRouter | `openrouter_api_key` | `openrouter_model` | `openrouter_base_url` | `openai/gpt-4o-mini` |
| Gemini | `gemini_api_key` | `gemini_model` | `gemini_base_url` | `gemini-2.5-flash` |
| FreeLLMAPI | `freellmapi_api_key` | `freellmapi_model` | `freellmapi_base_url` | `auto` |
| OpenAI-compatible | `openai_api_key` | `openai_model` | `openai_base_url` | `gpt-4o-mini` |

Environment variables use the pattern `FREENERF_<PROVIDER>_API_KEY`,
`FREENERF_<PROVIDER>_MODEL` and `FREENERF_<PROVIDER>_BASE_URL` with dashes
replaced by underscores (`FREENERF_OPENAI_COMPATIBLE_API_KEY`). The legacy
`FREENERF_MODEL`, `FREENERF_API_KEY` and `FREENERF_BASE_URL` are still honoured.

## OpenAI-compatible

Request body:

```json
{
  "model": "openai/gpt-4o-mini",
  "messages": [...],
  "temperature": 0.2,
  "max_tokens": 4096,
  "tools": [{"type": "function", "function": {...}}],
  "tool_choice": "auto",
  "stream": true
}
```

- Tool calls are read from `choices[0].message.tool_calls`.
- Responses are converted to `{content, tool_calls, raw_message}`.
- Streaming uses SSE (`data:` lines) and assembles tool-call fragments by
  `index` before returning.
- If a server ignores `stream` and returns JSON, the provider handles that too.
- `max_tokens` is omitted when set to 0.

This family covers OpenRouter, FreeLLMAPI and local servers (llama.cpp, vLLM,
LM Studio, Ollama's OpenAI endpoint).

## Gemini

- System message → `systemInstruction`.
- Assistant tool calls → `functionCall` parts; tool results → `functionResponse`.
- `thoughtSignature` and other provider metadata are preserved in
  `_gemini_parts` and replayed unchanged.
- Tool schemas are cleaned of unsupported keys such as `additionalProperties`.
- `generationConfig` carries `temperature` and `maxOutputTokens`.
- Streaming is not implemented; `supports_streaming` is false, so the UI simply
  receives the complete answer.

## Model catalogs

`llm/catalog.py` fetches:

- OpenRouter: `/api/v1/models`, optionally filtered to tool-capable models.
- Gemini: `/v1beta/models`, filtered to `generateContent`.
- FreeLLMAPI: `<base>/models`, including the `auto` aliases.
- OpenAI-compatible: `<base>/models` (never routed to OpenRouter).

Results include context length, pricing label, tool-capability flag and
description. Responses are cached for 5 minutes; the Model Explorer's Refresh
button bypasses the cache.

## Image generation

The `generate_image` tool calls:

```
POST <freellmapi_base_url>/images/generations
{
  "model": "<image_model>",
  "prompt": "...",
  "size": "1024x1024",
  "n": 1,
  "response_format": "b64_json"
}
```

If the provider rejects optional parameters, it retries with a minimal body.
Images can arrive as `b64_json` or as a URL (downloaded with a size cap). Files
are saved under the workspace's `.freenerf/images/` with a
`history.json` manifest. The terminal reply contains the saved path, because
terminals cannot render images inline.

Set `image_model` in Settings → Model. A FreeLLMAPI key is required.

## Retries and errors

`ProviderError` carries the HTTP status and a `retriable` flag. The agent
retries retriable errors (408, 409, 425, 429, 5xx, timeouts, connection errors)
with exponential backoff up to `max_retries` (default 2). Authentication and
model-not-found errors are not retried; they stop the run and mark the task
`failed`.

## Adding a provider

1. Add an entry to `PROVIDER_INFO` with key/model/base URL fields and capability
   flags.
2. Implement a `Provider` subclass with `chat(messages, tools=None,
   stream_callback=None)`.
3. Wire it into `create_provider` and `create_provider_for_config`.
4. Add catalog support in `llm/catalog.py` if it exposes a models endpoint.
5. Add the key field to `Config` and `KEY_FIELDS`.

Providers that do not support tool calling still work through the agent's
plain-text tool protocol, at lower reliability.

from __future__ import annotations

import time

import httpx


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
FREELLMAPI_DEFAULT_URL = "https://freellmapi-3d3g.onrender.com/v1"

CACHE_TTL = 300.0
_CACHE: dict[tuple, tuple[float, list]] = {}


class CatalogError(ValueError):
    pass


def _price_label(pricing):
    try:
        prompt = float((pricing or {}).get("prompt", "nan"))
        completion = float((pricing or {}).get("completion", "nan"))
    except (TypeError, ValueError):
        return "Unknown"

    if prompt != prompt or completion != completion:  # NaN
        return "Unknown"

    if prompt == 0 and completion == 0:
        return "Free"

    return "Paid"


def _cached(key, loader, force_refresh=False):
    now = time.monotonic()

    if not force_refresh:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            return list(cached[1])

    result = loader()
    _CACHE[key] = (now, list(result))
    return result


def clear_cache():
    _CACHE.clear()


def get_openrouter_models(api_key="", query="", tools_only=True):
    params = {
        "output_modalities": "text",
        "sort": "most-popular",
    }

    if tools_only:
        params["supported_parameters"] = "tools"

    if query:
        params["q"] = query

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.get(
            OPENROUTER_MODELS_URL,
            params=params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise CatalogError(
            f"OpenRouter catalog HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise CatalogError(f"OpenRouter catalog connection error: {exc}") from exc
    except ValueError as exc:
        raise CatalogError("OpenRouter catalog returned invalid JSON") from exc

    result = []

    for item in payload.get("data", []):
        model_id = item.get("id")
        if not model_id:
            continue

        pricing = item.get("pricing") or {}
        supported = item.get("supported_parameters") or []
        supports_tools = "tools" in supported

        if tools_only and not supports_tools:
            continue

        result.append({
            "id": model_id,
            "name": item.get("name") or model_id,
            "provider": "OpenRouter",
            "context_length": item.get("context_length", 0),
            "pricing": pricing,
            "pricing_label": _price_label(pricing),
            "supports_tools": supports_tools,
        })

    return result


def get_gemini_models(api_key="", query=""):
    if not api_key:
        raise CatalogError("Gemini API key is required")

    headers = {
        "x-goog-api-key": api_key,
        "Accept": "application/json",
    }

    result = []
    page_token = None

    try:
        with httpx.Client(timeout=30) as client:
            while True:
                params = {"pageSize": 1000}
                if page_token:
                    params["pageToken"] = page_token

                response = client.get(
                    GEMINI_MODELS_URL,
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()

                data = response.json()

                for item in data.get("models", []):
                    methods = item.get("supportedGenerationMethods") or []

                    if "generateContent" not in methods:
                        continue

                    model_id = item.get("name", "").removeprefix("models/")
                    if not model_id:
                        continue

                    name = item.get("displayName") or model_id

                    if query and query.lower() not in (
                        model_id + " " + name
                    ).lower():
                        continue

                    result.append({
                        "id": model_id,
                        "name": name,
                        "provider": "Google Gemini",
                        "context_length": item.get("inputTokenLimit", 0),
                        "output_limit": item.get("outputTokenLimit", 0),
                        "pricing": {},
                        "pricing_label": "Unknown",
                        # models.list does not expose a tool-capability flag.
                        "supports_tools": None,
                        "description": item.get("description", ""),
                    })

                page_token = data.get("nextPageToken")
                if not page_token:
                    break
    except httpx.HTTPStatusError as exc:
        raise CatalogError(
            f"Gemini catalog HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise CatalogError(f"Gemini catalog connection error: {exc}") from exc
    except ValueError as exc:
        raise CatalogError("Gemini catalog returned invalid JSON") from exc

    return result


def get_freellmapi_models(api_key="", query="", base_url=FREELLMAPI_DEFAULT_URL):
    if not api_key:
        raise CatalogError("FreeLLMAPI unified API key is required")

    endpoint = (base_url or FREELLMAPI_DEFAULT_URL).rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    try:
        response = httpx.get(endpoint, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise CatalogError(
            f"FreeLLMAPI catalog HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise CatalogError(
            f"FreeLLMAPI catalog connection error: {exc}"
        ) from exc
    except ValueError as exc:
        raise CatalogError("FreeLLMAPI catalog returned invalid JSON") from exc

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    else:
        items = []

    if not isinstance(items, list):
        raise CatalogError("Unexpected FreeLLMAPI models response")

    result = []
    for item in items:
        if not isinstance(item, dict) or item.get("available") is False:
            continue

        model_id = item.get("id") or item.get("name") or item.get("model")
        if not model_id:
            continue

        name = item.get("name") or item.get("display_name") or model_id
        if query and query.lower() not in (
            str(model_id) + " " + str(name)
        ).lower():
            continue

        result.append({
            "id": str(model_id),
            "name": str(name),
            "provider": "FreeLLMAPI",
            "context_length": item.get("context_length") or 0,
            "pricing": item.get("pricing") or {},
            "pricing_label": item.get("pricing_label") or "Unknown",
            "supports_tools": item.get("supports_tools"),
            "description": item.get("description", ""),
        })

    known = {item["id"] for item in result}
    for model_id in ("auto", "auto:fast"):
        if model_id not in known and (not query or query.lower() in model_id):
            result.insert(0, {
                "id": model_id,
                "name": model_id,
                "provider": "FreeLLMAPI",
                "context_length": 0,
                "pricing": {},
                "pricing_label": "Unknown",
                "supports_tools": None,
            })

    return result


def get_openai_compatible_models(api_key="", base_url="", query=""):
    base_url = str(base_url or "").rstrip("/")
    if not base_url:
        raise CatalogError("OpenAI-compatible base URL is required")

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.get(f"{base_url}/models", headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise CatalogError(
            f"Model catalog HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise CatalogError(f"Model catalog connection error: {exc}") from exc
    except ValueError as exc:
        raise CatalogError("Model catalog returned invalid JSON") from exc

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    else:
        items = []

    result = []
    for item in items:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue

        model_id = item.get("id") or item.get("name")
        if not model_id:
            continue

        name = item.get("name") or model_id
        if query and query.lower() not in (
            str(model_id) + " " + str(name)
        ).lower():
            continue

        result.append({
            "id": str(model_id),
            "name": str(name),
            "provider": "OpenAI-compatible",
            "context_length": item.get("context_length")
            or item.get("context_window")
            or 0,
            "pricing": {},
            "pricing_label": "Unknown",
            "supports_tools": None,
        })

    return result


def get_models(
    api_key="",
    query="",
    tools_only=True,
    provider="openrouter",
    base_url="",
    force_refresh=False,
):
    provider = (provider or "openrouter").lower()
    key = (provider, base_url, query, bool(tools_only))

    if provider == "gemini":
        return _cached(
            key,
            lambda: get_gemini_models(api_key=api_key, query=query),
            force_refresh,
        )

    if provider == "freellmapi":
        return _cached(
            key,
            lambda: get_freellmapi_models(
                api_key=api_key,
                query=query,
                base_url=base_url,
            ),
            force_refresh,
        )

    if provider == "openrouter":
        return _cached(
            key,
            lambda: get_openrouter_models(
                api_key=api_key,
                query=query,
                tools_only=tools_only,
            ),
            force_refresh,
        )

    if provider == "openai-compatible":
        return _cached(
            key,
            lambda: get_openai_compatible_models(
                api_key=api_key,
                base_url=base_url,
                query=query,
            ),
            force_refresh,
        )

    raise CatalogError(f"Unsupported model catalog provider: {provider}")

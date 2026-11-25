"""Natural-language image generation through the FreeLLMAPI endpoint."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx


MAX_DOWNLOAD_BYTES = 8_000_000
SUPPORTED_SIZES = ("1024x1024", "1024x1536", "1536x1024", "512x512", "256x256")


class ImageGenerationError(Exception):
    pass


@dataclass
class ImageResult:
    path: Path
    prompt: str
    model: str
    revised_prompt: str = ""

    def message(self) -> str:
        lines = [f"Generated image saved to {self.path}"]
        if self.revised_prompt:
            lines.append(f"Revised prompt: {self.revised_prompt}")
        lines.append(f"Prompt: {self.prompt}")
        return "\n".join(lines)


def _slug(value: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "image")[:limit]


def _decode_image(payload: bytes) -> bytes:
    if not payload:
        raise ImageGenerationError("Provider returned an empty image")

    if payload.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF", b"GIF8")):
        return payload

    # Some providers return base64 text without decoding it.
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        return payload

    return decoded


class ImageGenerator:
    """Calls ``/v1/images/generations`` and stores images in the workspace."""

    def __init__(
        self,
        api_key="",
        base_url="https://freellmapi-3d3g.onrender.com/v1",
        model="gpt-image-1",
        timeout=120.0,
        output_dir=None,
    ):
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or "gpt-image-1"
        self.timeout = timeout
        self.output_dir = Path(output_dir).resolve() if output_dir else None

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.output_dir)

    def _endpoint(self) -> str:
        return f"{self.base_url}/images/generations"

    def _request(self, client: httpx.Client, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = client.post(
            self._endpoint(), headers=headers, json=payload
        )

        if response.status_code in (400, 422):
            # Retry with the smallest documented payload when the provider
            # rejects optional parameters such as response_format.
            minimal = {
                "prompt": payload["prompt"],
                "n": payload.get("n", 1),
            }
            response = client.post(
                self._endpoint(), headers=headers, json=minimal
            )

        if response.is_error:
            detail = response.text[:600]
            raise ImageGenerationError(
                f"Image provider HTTP {response.status_code}: {detail}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ImageGenerationError(
                "Image provider returned invalid JSON"
            ) from exc

        if not isinstance(data, dict):
            raise ImageGenerationError("Unexpected image provider response")

        return data

    def generate(
        self,
        prompt,
        size="1024x1024",
        count=1,
        output_dir=None,
    ) -> list[ImageResult]:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ImageGenerationError("Image prompt must not be empty")

        if not self.available and not output_dir:
            raise ImageGenerationError(
                "Image generation requires an API key and a workspace"
            )

        directory = Path(output_dir).resolve() if output_dir else self.output_dir
        if directory is None:
            raise ImageGenerationError("No output directory for images")
        directory.mkdir(parents=True, exist_ok=True)

        try:
            count = max(1, min(int(count), 4))
        except (TypeError, ValueError):
            count = 1

        if size not in SUPPORTED_SIZES:
            size = "1024x1024"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "n": count,
            "response_format": "b64_json",
        }

        results: list[ImageResult] = []
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        with httpx.Client(timeout=self.timeout) as client:
            data = self._request(client, payload)
            items = data.get("data")

            if not isinstance(items, list) or not items:
                raise ImageGenerationError(
                    "Image provider returned no images"
                )

            for index, item in enumerate(items[:count]):
                if not isinstance(item, dict):
                    continue

                if item.get("b64_json"):
                    try:
                        raw = base64.b64decode(item["b64_json"])
                    except (ValueError, TypeError) as exc:
                        raise ImageGenerationError(
                            "Invalid base64 image payload"
                        ) from exc
                elif item.get("url"):
                    raw = self._download(client, str(item["url"]))
                else:
                    continue

                raw = _decode_image(raw)
                path = directory / (
                    f"{timestamp}_{_slug(prompt)}_{index + 1}.png"
                )
                path.write_bytes(raw)

                results.append(ImageResult(
                    path=path,
                    prompt=prompt,
                    model=str(data.get("model") or self.model),
                    revised_prompt=str(item.get("revised_prompt") or ""),
                ))

        if not results:
            raise ImageGenerationError(
                "Image provider response contained no usable image data"
            )

        self._append_history(results)
        return results

    @staticmethod
    def _download(client: httpx.Client, url: str) -> bytes:
        from urllib.parse import urlparse

        from .web_fetch import WebFetch

        if urlparse(str(url)).scheme not in ("http", "https"):
            raise ImageGenerationError("Image URL must be HTTP or HTTPS")

        if not WebFetch().validate_url(url):
            raise ImageGenerationError("Image URL is not a public address")

        buffer = bytearray()

        with client.stream("GET", url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                buffer.extend(chunk)
                if len(buffer) > MAX_DOWNLOAD_BYTES:
                    raise ImageGenerationError(
                        "Generated image exceeds the download limit"
                    )

        return bytes(buffer)

    def _append_history(self, results: list[ImageResult]) -> None:
        if not results:
            return

        directory = results[0].path.parent
        manifest = directory / "history.json"

        history = []
        if manifest.is_file():
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(existing, list):
                    history = existing
            except (OSError, ValueError):
                history = []

        for item in results:
            history.append({
                "path": str(item.path),
                "prompt": item.prompt,
                "model": item.model,
                "revised_prompt": item.revised_prompt,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        try:
            manifest.write_text(
                json.dumps(history[-50:], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def history(self, limit=20) -> list[dict]:
        if self.output_dir is None:
            return []
        manifest = self.output_dir / "history.json"
        if not manifest.is_file():
            return []
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return data[-limit:]

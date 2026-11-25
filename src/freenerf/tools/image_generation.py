from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ImageGenerationError(RuntimeError):
    """Raised when FreeLLMAPI image generation fails."""


class ImageGenerator:
    ALLOWED_SIZES = {"1024x1024", "1024x1536", "1536x1024"}
    MAX_RESPONSE_BYTES = 36 * 1024 * 1024
    MAX_IMAGE_BYTES = 25 * 1024 * 1024

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "auto",
        output_dir: str | Path = "generated_images",
        timeout: int = 180,
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").rstrip("/")
        self.model = str(model or "auto").strip() or "auto"
        self.output_dir = Path(output_dir)
        self.timeout = max(10, int(timeout))
        self.status_callback = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    @staticmethod
    def _image_extension(data: bytes) -> str:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if data.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if (
            len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
        ):
            return "webp"
        raise ImageGenerationError(
            "The API response was not a supported PNG, JPEG, or WebP image."
        )

    def _safe_error_detail(self, raw: bytes) -> str:
        detail = raw.decode("utf-8", errors="replace").strip()
        if self.api_key:
            detail = detail.replace(self.api_key, "[redacted]")
        return detail[:500]

    def generate(self, prompt: str, size: str = "1024x1024") -> Path:
        retryable_markers = (
            "FREE_LIMIT",
            "SERVICE_UNAVAILABLE",
            "HTTP 429",
            "HTTP 502",
            "HTTP 503",
            "timed out",
            "TimeoutError",
        )

        for attempt in range(1, 4):
            try:
                return self._generate_once(prompt, size)
            except ImageGenerationError as exc:
                message = str(exc)
                retryable = any(
                    marker.lower() in message.lower()
                    for marker in retryable_markers
                )
                if not retryable or attempt >= 3:
                    raise

                callback = self.status_callback
                for remaining in range(45, -1, -1):
                    if callback:
                        try:
                            callback(f"RETRYING · {remaining}s")
                        except Exception:
                            pass
                    if remaining:
                        time.sleep(1)

        raise ImageGenerationError("Image generation retry limit reached.")

    def _generate_once(self, prompt: str, size: str = "1024x1024") -> Path:
        if not self.is_configured:
            raise ImageGenerationError(
                "FreeLLMAPI image generation is not configured. "
                "Set FREENERF_FREELLMAPI_API_KEY and check its base URL."
            )

        if not isinstance(prompt, str) or not prompt.strip():
            raise ImageGenerationError("Image prompt must not be empty.")

        prompt = prompt.strip()
        if len(prompt) > 10000:
            raise ImageGenerationError("Image prompt is too long (maximum 10000 characters).")

        if size not in self.ALLOWED_SIZES:
            raise ImageGenerationError(
                "Unsupported image size. Use 1024x1024, 1024x1536, or 1536x1024."
            )

        endpoint = f"{self.base_url}/images/generations"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            try:
                detail = self._safe_error_detail(exc.read(4096))
            except Exception:
                detail = ""
            message = f"FreeLLMAPI returned HTTP {exc.code}."
            if detail:
                message += f" {message and detail}"
            raise ImageGenerationError(message) from None
        except URLError as exc:
            reason = str(getattr(exc, "reason", "connection error"))
            if self.api_key:
                reason = reason.replace(self.api_key, "[redacted]")
            raise ImageGenerationError(
                f"Could not connect to FreeLLMAPI: {reason[:300]}"
            ) from None
        except TimeoutError:
            raise ImageGenerationError(
                f"FreeLLMAPI request timed out after {self.timeout} seconds."
            ) from None

        if len(raw) > self.MAX_RESPONSE_BYTES:
            raise ImageGenerationError("FreeLLMAPI response exceeded the size limit.")

        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ImageGenerationError("FreeLLMAPI returned invalid JSON.") from None

        items = result.get("data") if isinstance(result, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            error = result.get("error") if isinstance(result, dict) else None
            detail = str(error)[:300] if error else "No image data was returned."
            if self.api_key:
                detail = detail.replace(self.api_key, "[redacted]")
            raise ImageGenerationError(f"FreeLLMAPI image response error: {detail}")

        encoded = items[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ImageGenerationError(
                "FreeLLMAPI response did not contain b64_json image data."
            )

        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ImageGenerationError(
                "FreeLLMAPI returned invalid base64 image data."
            ) from None

        if not image_bytes or len(image_bytes) > self.MAX_IMAGE_BYTES:
            raise ImageGenerationError("Generated image is empty or exceeds the size limit.")

        extension = self._image_extension(image_bytes)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for _ in range(5):
            filename = (
                f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{secrets.token_hex(4)}.{extension}"
            )
            image_path = self.output_dir / filename
            try:
                with image_path.open("xb") as handle:
                    handle.write(image_bytes)
                return image_path.resolve()
            except FileExistsError:
                continue
            except OSError as exc:
                image_path.unlink(missing_ok=True)
                raise ImageGenerationError(
                    f"Could not save generated image: {exc}"
                ) from None

        raise ImageGenerationError(
            "Could not create a unique filename for the generated image."
        )

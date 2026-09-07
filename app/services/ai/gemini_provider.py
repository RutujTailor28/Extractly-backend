"""Gemini provider using the official Google Gen AI SDK (`google-genai`)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.core.config import settings
from app.services.ai.exceptions import (
    AIConfigurationError,
    AIConnectionError,
    AIModelError,
    AIProviderError,
    AIRateLimitError,
)
from app.services.ai.foundation import (
    AIHealthResult,
    AIModelInfo,
    AIProvider,
    AITextResult,
)

logger = logging.getLogger(__name__)

# Patterns that might accidentally leak secrets into error strings
_SECRET_PATTERNS = (
    re.compile(r"AQ\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"key[=:\s]+[A-Za-z0-9_\-\.]{16,}", re.IGNORECASE),
)


def _safe_error_message(exc: BaseException, fallback: str) -> str:
    """Return a sanitized error message that never includes API keys."""
    text = str(exc) or fallback
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if settings.GEMINI_API_KEY:
        text = text.replace(settings.GEMINI_API_KEY, "[REDACTED]")
    # Truncate noisy SDK dumps
    if len(text) > 400:
        text = text[:400] + "…"
    return text or fallback


def _map_sdk_error(exc: BaseException) -> AIProviderError:
    message = _safe_error_message(exc, "AI provider request failed")
    lower = message.lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    status_str = str(status).lower() if status is not None else ""

    if "api key" in lower or "unauthorized" in lower or "permission" in lower or status_str in {
        "401",
        "403",
        "unauthenticated",
        "permission_denied",
    }:
        return AIConfigurationError(
            "Gemini authentication failed. Check GEMINI_API_KEY."
        )
    if "rate" in lower or "quota" in lower or "resource_exhausted" in lower or status_str in {
        "429",
        "resource_exhausted",
    }:
        return AIRateLimitError("Gemini rate limit or quota exceeded")
    if "not found" in lower or "model" in lower and (
        "invalid" in lower or "does not exist" in lower or "404" in lower
    ):
        return AIModelError(
            "Gemini model is invalid or unavailable. "
            "Use GET /api/v1/ai/models to list accessible models."
        )
    if "timeout" in lower or "connection" in lower or "network" in lower:
        return AIConnectionError("Unable to reach Gemini API")
    return AIConnectionError(message)


class GeminiProvider(AIProvider):
    """Reusable Gemini client for foundation health / models / text tests."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        key = (api_key if api_key is not None else settings.GEMINI_API_KEY) or ""
        key = key.strip()
        if not key:
            raise AIConfigurationError(
                "GEMINI_API_KEY is not configured"
            )
        self._api_key = key
        self._model = (model or settings.GEMINI_MODEL or "").strip()
        if not self._model:
            raise AIConfigurationError("GEMINI_MODEL is not configured")

        if client is not None:
            self._client = client
        else:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._model

    def health_check(self) -> AIHealthResult:
        try:
            # Lightweight connectivity: list models (no generation cost beyond list)
            models = self.list_models()
            connected = True
            configured_model_ok = any(
                m.name == self._model or m.name.endswith("/" + self._model)
                for m in models
            )
            if models and not configured_model_ok:
                message = (
                    "Connected to Gemini, but configured GEMINI_MODEL "
                    f"'{self._model}' was not found in accessible models. "
                    "Call GET /api/v1/ai/models to pick a valid name."
                )
            else:
                message = "AI provider is operational"
            return AIHealthResult(
                provider=self.name,
                configured=True,
                connected=connected,
                model=self._model,
                message=message,
            )
        except AIProviderError as exc:
            logger.warning("Gemini health_check failed: %s", exc.message)
            return AIHealthResult(
                provider=self.name,
                configured=True,
                connected=False,
                model=self._model,
                message=exc.message,
            )
        except Exception as exc:
            mapped = _map_sdk_error(exc)
            logger.warning("Gemini health_check failed: %s", mapped.message)
            return AIHealthResult(
                provider=self.name,
                configured=True,
                connected=False,
                model=self._model,
                message=mapped.message,
            )

    def list_models(self) -> list[AIModelInfo]:
        try:
            raw_models = list(self._client.models.list())
        except AIProviderError:
            raise
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

        results: list[AIModelInfo] = []
        for item in raw_models:
            name = getattr(item, "name", None) or ""
            # SDK returns names like "models/gemini-2.0-flash"
            short = name.split("/")[-1] if name else ""
            methods = list(getattr(item, "supported_actions", None) or [])
            if not methods:
                methods = list(getattr(item, "supported_generation_methods", None) or [])
            display = getattr(item, "display_name", None)
            results.append(
                AIModelInfo(
                    name=short or name,
                    display_name=display,
                    supported_generation_methods=[str(m) for m in methods],
                    input_token_limit=getattr(item, "input_token_limit", None),
                    output_token_limit=getattr(item, "output_token_limit", None),
                    description=getattr(item, "description", None),
                    raw={
                        "name": name,
                        "version": getattr(item, "version", None),
                    },
                )
            )
        results.sort(key=lambda m: m.name)
        return results

    def generate_text(self, prompt: str, *, model: str | None = None) -> AITextResult:
        cleaned = (prompt or "").strip()
        if not cleaned:
            raise AIModelError("prompt is required")
        if len(cleaned) > 4000:
            raise AIModelError("prompt exceeds maximum length of 4000 characters")

        target_model = (model or self._model).strip()
        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=target_model,
                contents=cleaned,
            )
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

        text = getattr(response, "text", None)
        if text is None:
            # Fallback for atypical response shapes
            text = str(response)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return AITextResult(
            provider=self.name,
            model=target_model,
            prompt=cleaned,
            text=str(text).strip(),
            latency_ms=latency_ms,
        )

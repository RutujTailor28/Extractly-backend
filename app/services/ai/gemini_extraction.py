"""Gemini Vision invoice/receipt extraction provider (real API)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.core.config import settings
from app.services.ai.base import (
    ExtractionProvider,
    ExtractionProviderResult,
    PageImage,
)
from app.services.ai.prompts import (
    build_extraction_system_prompt,
    build_extraction_user_prompt,
)
from app.services.ai.provider import AIProviderError, _parse_json_content

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = (
    re.compile(r"AQ\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
)


def _safe_msg(exc: BaseException) -> str:
    text = str(exc) or "Gemini request failed"
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    key = (settings.GEMINI_API_KEY or "").strip()
    if key:
        text = text.replace(key, "[REDACTED]")
    if len(text) > 400:
        text = text[:400] + "…"
    return text


def _is_valid_bbox(bbox: Any) -> bool:
    if not isinstance(bbox, dict):
        return False
    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        w = float(bbox["width"])
        h = float(bbox["height"])
    except (KeyError, TypeError, ValueError):
        return False
    if min(x, y, w, h) < 0.0 or max(x, y, w, h) > 1.0:
        return False
    if x + w > 1.0 + 1e-6 or y + h > 1.0 + 1e-6:
        return False
    return True


def _sanitize_field_obj(obj: Any) -> Any:
    """Null invalid bboxes / clamp confidence so Pydantic validation can succeed."""
    if obj is None:
        return None
    if not isinstance(obj, dict):
        return obj
    data = dict(obj)
    if "bbox" in data and data["bbox"] is not None and not _is_valid_bbox(data["bbox"]):
        data["bbox"] = None
    conf = data.get("confidence")
    if conf is not None:
        try:
            c = float(conf)
            data["confidence"] = max(0.0, min(1.0, c))
        except (TypeError, ValueError):
            data["confidence"] = 0.5
    elif data.get("value") is not None:
        data["confidence"] = 0.5
    page = data.get("page_number")
    if page is not None:
        try:
            data["page_number"] = max(1, int(page))
        except (TypeError, ValueError):
            data["page_number"] = 1
    elif data.get("value") is not None:
        data["page_number"] = 1
    return data


def sanitize_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Clean Gemini JSON before Pydantic validation."""
    out = dict(payload)
    fields = out.get("fields")
    if isinstance(fields, dict):
        cleaned: dict[str, Any] = {}
        for key, value in fields.items():
            cleaned[key] = _sanitize_field_obj(value)
        out["fields"] = cleaned

    items = out.get("line_items")
    if isinstance(items, list):
        cleaned_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cleaned_items.append(
                {k: _sanitize_field_obj(v) for k, v in item.items()}
            )
        out["line_items"] = cleaned_items

    oc = out.get("overall_confidence")
    if oc is not None:
        try:
            out["overall_confidence"] = max(0.0, min(1.0, float(oc)))
        except (TypeError, ValueError):
            out["overall_confidence"] = 0.5
    else:
        out["overall_confidence"] = 0.5

    doc_type = out.get("document_type")
    if doc_type not in {"invoice", "receipt", "unknown"}:
        out["document_type"] = "unknown"

    return out


class GeminiExtractionProvider(ExtractionProvider):
    """
    Real Gemini Vision extraction using google-genai.

    Sends processed page images + structured JSON instructions.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        client: Any | None = None,
    ) -> None:
        key = (api_key if api_key is not None else settings.GEMINI_API_KEY) or ""
        key = key.strip()
        if not key:
            raise AIProviderError("GEMINI_API_KEY is required when AI_PROVIDER=gemini")
        self.model = (model or settings.GEMINI_MODEL or "").strip()
        if not self.model:
            raise AIProviderError("GEMINI_MODEL is required when AI_PROVIDER=gemini")
        self.timeout_seconds = timeout_seconds or settings.AI_TIMEOUT_SECONDS
        if client is not None:
            self.client = client
        else:
            from google import genai

            self.client = genai.Client(api_key=key)

    def extract_invoice(
        self,
        document_pages: list[PageImage],
        vendor_hints: dict[str, Any] | None = None,
        *,
        validation_error: str | None = None,
    ) -> ExtractionProviderResult:
        """Alias matching the product naming for invoice extraction."""
        return self.extract_document(
            document_pages,
            vendor_hints=vendor_hints,
            validation_error=validation_error,
        )

    def extract_document(
        self,
        pages: list[PageImage],
        *,
        schema_hint: dict[str, Any] | None = None,
        vendor_hints: dict[str, Any] | None = None,
        validation_error: str | None = None,
    ) -> ExtractionProviderResult:
        if not pages:
            raise AIProviderError("No page images provided for extraction")

        from google.genai import types

        user_text = build_extraction_user_prompt(
            page_count=len(pages),
            vendor_hints=vendor_hints,
            validation_error=validation_error,
        )

        parts: list[Any] = [types.Part.from_text(text=user_text)]
        for page in pages:
            mime = (page.mime_type or "image/png").split(";")[0].strip().lower()
            if mime in {"image/jpg", "image/pjpeg"}:
                mime = "image/jpeg"
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                # Processed pipeline stores PNG; fall back safely
                mime = "image/png"
            parts.append(
                types.Part.from_text(text=f"Page {page.page_number} of {len(pages)}:")
            )
            parts.append(types.Part.from_bytes(data=page.content, mime_type=mime))

        config = types.GenerateContentConfig(
            system_instruction=build_extraction_system_prompt(),
            temperature=0,
            response_mime_type="application/json",
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            http_options=types.HttpOptions(timeout=self.timeout_seconds * 1000),
        )

        started = time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=parts,
                config=config,
            )
        except Exception as exc:
            lower = _safe_msg(exc).lower()
            if "rate" in lower or "quota" in lower or "resource_exhausted" in lower:
                raise AIProviderError(
                    "Gemini rate limit or quota exceeded"
                ) from exc
            if "api key" in lower or "unauthenticated" in lower or "permission" in lower:
                raise AIProviderError(
                    "Gemini authentication failed. Check GEMINI_API_KEY."
                ) from exc
            if "not found" in lower or "invalid" in lower and "model" in lower:
                raise AIProviderError(
                    f"Gemini model '{self.model}' is invalid or unavailable"
                ) from exc
            raise AIProviderError(f"Gemini extraction failed: {_safe_msg(exc)}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        text = getattr(response, "text", None)
        if not text:
            raise AIProviderError("Gemini returned an empty extraction response")

        try:
            parsed = _parse_json_content(text)
        except Exception as exc:
            raise AIProviderError(
                f"Invalid JSON from Gemini: {_safe_msg(exc)}"
            ) from exc

        sanitized = sanitize_extraction_payload(parsed)

        usage: dict[str, Any] = {}
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            usage = {
                "prompt_tokens": getattr(meta, "prompt_token_count", None),
                "completion_tokens": getattr(meta, "candidates_token_count", None),
                "total_tokens": getattr(meta, "total_token_count", None),
            }

        logger.info(
            "Gemini extraction completed model=%s pages=%s latency_ms=%s",
            self.model,
            len(pages),
            latency_ms,
        )

        return ExtractionProviderResult(
            raw_response={"content": text, "usage": usage, "model": self.model},
            parsed_payload=sanitized,
            model=self.model,
            latency_ms=latency_ms,
            cost_usd=None,
            usage=usage,
        )

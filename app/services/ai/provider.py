"""Configured AI extraction providers (OpenAI vision + mock)."""

from __future__ import annotations

import base64
import json
import logging
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

logger = logging.getLogger(__name__)


class AIProviderError(Exception):
    """Raised when the AI provider call fails."""


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        # Strip accidental markdown fences
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("AI response JSON must be an object")
    return data


class MockExtractionProvider(ExtractionProvider):
    """Deterministic provider for automated tests — never calls external APIs."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def extract_document(
        self,
        pages: list[PageImage],
        *,
        schema_hint: dict[str, Any] | None = None,
        vendor_hints: dict[str, Any] | None = None,
        validation_error: str | None = None,
    ) -> ExtractionProviderResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            # Intentionally invalid payload to exercise retry
            return ExtractionProviderResult(
                raw_response={"invalid": True},
                parsed_payload={"invalid": True},
                model="mock-extractor",
                latency_ms=5,
                cost_usd=None,
            )

        page_number = pages[0].page_number if pages else 1
        payload = {
            "document_type": "invoice",
            "fields": {
                "vendor_name": {
                    "value": "Acme Office Supplies",
                    "confidence": 0.97,
                    "page_number": page_number,
                    "bbox": {"x": 0.10, "y": 0.08, "width": 0.35, "height": 0.04},
                    "original_value": "Acme Office Supplies",
                },
                "vendor_address": {
                    "value": "742 Evergreen Terrace",
                    "confidence": 0.90,
                    "page_number": page_number,
                    "bbox": {"x": 0.10, "y": 0.12, "width": 0.40, "height": 0.04},
                },
                "vendor_tax_id": None,
                "invoice_number": {
                    "value": "INV-2041",
                    "confidence": 0.96,
                    "page_number": page_number,
                    "bbox": {"x": 0.62, "y": 0.10, "width": 0.22, "height": 0.04},
                    "original_value": "INV-2041",
                },
                "invoice_date": {
                    "value": "2026-03-12",
                    "confidence": 0.93,
                    "page_number": page_number,
                    "bbox": {"x": 0.62, "y": 0.16, "width": 0.18, "height": 0.03},
                    "original_value": "12/03/2026",
                    "original_format": "DD/MM/YYYY",
                },
                "due_date": None,
                "po_number": None,
                "bill_to_name": None,
                "bill_to_address": None,
                "currency": {
                    "value": "USD",
                    "confidence": 0.95,
                    "page_number": page_number,
                    "bbox": None,
                    "original_value": "$",
                },
                "subtotal": {
                    "value": 150.0,
                    "confidence": 0.94,
                    "page_number": page_number,
                    "bbox": {"x": 0.75, "y": 0.80, "width": 0.15, "height": 0.03},
                    "original_value": "$150.00",
                },
                "tax_amount": {
                    "value": 15.0,
                    "confidence": 0.92,
                    "page_number": page_number,
                    "bbox": {"x": 0.75, "y": 0.84, "width": 0.15, "height": 0.03},
                },
                "tax_rate": None,
                "discount": None,
                "shipping": None,
                "total": {
                    "value": 165.0,
                    "confidence": 0.98,
                    "page_number": page_number,
                    "bbox": {"x": 0.75, "y": 0.88, "width": 0.15, "height": 0.03},
                    "original_value": "$165.00",
                },
                "payment_terms": None,
                "bank_details": None,
            },
            "line_items": [
                {
                    "description": {
                        "value": "Office Supplies",
                        "confidence": 0.95,
                        "page_number": page_number,
                        "bbox": {"x": 0.10, "y": 0.45, "width": 0.35, "height": 0.03},
                    },
                    "quantity": {
                        "value": 2,
                        "confidence": 0.94,
                        "page_number": page_number,
                        "bbox": {"x": 0.55, "y": 0.45, "width": 0.05, "height": 0.03},
                    },
                    "unit": {
                        "value": "box",
                        "confidence": 0.90,
                        "page_number": page_number,
                        "bbox": {"x": 0.62, "y": 0.45, "width": 0.08, "height": 0.03},
                    },
                    "unit_price": {
                        "value": 75.0,
                        "confidence": 0.94,
                        "page_number": page_number,
                        "bbox": {"x": 0.72, "y": 0.45, "width": 0.10, "height": 0.03},
                    },
                    "line_tax": {
                        "value": 15.0,
                        "confidence": 0.91,
                        "page_number": page_number,
                        "bbox": {"x": 0.83, "y": 0.45, "width": 0.08, "height": 0.03},
                    },
                    "line_total": {
                        "value": 165.0,
                        "confidence": 0.96,
                        "page_number": page_number,
                        "bbox": {"x": 0.90, "y": 0.45, "width": 0.08, "height": 0.03},
                    },
                }
            ],
            "overall_confidence": 0.94,
            "page_count_seen": len(pages),
            "vendor_hints": vendor_hints,
            "validation_error_echo": validation_error,
        }
        return ExtractionProviderResult(
            raw_response=payload,
            parsed_payload=payload,
            model="mock-extractor",
            latency_ms=12,
            cost_usd=0.0,
        )


class OpenAIExtractionProvider(ExtractionProvider):
    """OpenAI vision chat completions adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
    ) -> None:
        from openai import OpenAI

        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = OpenAI(api_key=api_key, timeout=timeout_seconds)

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

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": build_extraction_user_prompt(
                    page_count=len(pages),
                    vendor_hints=vendor_hints,
                    validation_error=validation_error,
                ),
            }
        ]

        for page in pages:
            encoded = base64.b64encode(page.content).decode("ascii")
            media_type = page.mime_type or "image/png"
            content.append(
                {
                    "type": "text",
                    "text": f"Page {page.page_number}:",
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{encoded}",
                    },
                }
            )

        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": build_extraction_system_prompt()},
                    {"role": "user", "content": content},
                ],
            )
        except Exception as exc:
            raise AIProviderError(f"OpenAI extraction failed: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        message = response.choices[0].message.content if response.choices else None
        if not message:
            raise AIProviderError("OpenAI returned an empty response")

        try:
            parsed = _parse_json_content(message)
        except Exception as exc:
            raise AIProviderError(f"Invalid JSON from OpenAI: {exc}") from exc

        cost_usd = None
        usage: dict[str, Any] = {}
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ExtractionProviderResult(
            raw_response={"content": message, "usage": usage},
            parsed_payload=parsed,
            model=self.model,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            usage=usage,
        )


def get_extraction_provider() -> ExtractionProvider:
    """Factory for the configured extraction provider."""
    provider_name = (settings.AI_PROVIDER or "mock").strip().lower()
    model = settings.AI_MODEL or "gpt-4o"

    if provider_name in {"mock", "fake", "test"}:
        return MockExtractionProvider()

    if provider_name in {"openai", "gpt"}:
        if not settings.AI_API_KEY:
            raise AIProviderError("AI_API_KEY is required when AI_PROVIDER=openai")
        return OpenAIExtractionProvider(
            api_key=settings.AI_API_KEY,
            model=model,
            timeout_seconds=settings.AI_TIMEOUT_SECONDS,
        )

    if provider_name == "gemini":
        from app.services.ai.gemini_extraction import GeminiExtractionProvider

        return GeminiExtractionProvider(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
            timeout_seconds=settings.AI_TIMEOUT_SECONDS,
        )

    raise AIProviderError(
        f"Unsupported AI_PROVIDER '{settings.AI_PROVIDER}'. "
        "Use 'openai', 'mock', or 'gemini'."
    )

"""Tests for real Gemini vision extraction provider (mocked SDK)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.ai.base import PageImage
from app.services.ai.gemini_extraction import (
    GeminiExtractionProvider,
    sanitize_extraction_payload,
)
from app.services.ai.provider import AIProviderError, get_extraction_provider


def test_sanitize_nulls_invalid_bbox() -> None:
    payload = {
        "document_type": "invoice",
        "fields": {
            "total": {
                "value": 10,
                "confidence": 0.9,
                "page_number": 1,
                "bbox": {"x": 0.9, "y": 0.9, "width": 0.5, "height": 0.5},
            }
        },
        "line_items": [],
        "overall_confidence": 0.9,
    }
    cleaned = sanitize_extraction_payload(payload)
    assert cleaned["fields"]["total"]["bbox"] is None


def test_gemini_factory_wired(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config.settings, "GEMINI_MODEL", "gemini-2.5-flash")
    with patch("app.services.ai.gemini_extraction.genai", create=True), patch(
        "google.genai.Client",
        return_value=MagicMock(),
    ):
        provider = get_extraction_provider()
    assert provider.__class__.__name__ == "GeminiExtractionProvider"


def test_gemini_extract_document_parses_json() -> None:
    sample = {
        "document_type": "invoice",
        "fields": {
            "vendor_name": {
                "value": "Acme",
                "confidence": 0.95,
                "page_number": 1,
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.05},
            },
            "invoice_number": {
                "value": "INV-1",
                "confidence": 0.9,
                "page_number": 1,
                "bbox": None,
            },
            "total": {
                "value": 100,
                "confidence": 0.92,
                "page_number": 1,
                "bbox": None,
            },
        },
        "line_items": [],
        "overall_confidence": 0.91,
    }
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        text=__import__("json").dumps(sample),
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=20,
            total_token_count=30,
        ),
    )
    provider = GeminiExtractionProvider(
        api_key="test-key",
        model="gemini-2.5-flash",
        client=client,
    )
    pages = [PageImage(page_number=1, content=b"\x89PNG\r\n\x1a\n" + b"0" * 32)]
    result = provider.extract_invoice(pages)
    assert result.model == "gemini-2.5-flash"
    assert result.parsed_payload["fields"]["vendor_name"]["value"] == "Acme"
    assert client.models.generate_content.called
    # Images + text parts sent
    call_kwargs = client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-flash"
    assert len(call_kwargs["contents"]) >= 2


def test_gemini_empty_pages_error() -> None:
    provider = GeminiExtractionProvider(
        api_key="test-key",
        model="gemini-2.5-flash",
        client=MagicMock(),
    )
    with pytest.raises(AIProviderError, match="No page images"):
        provider.extract_document([])


def test_gemini_invalid_json_error() -> None:
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        text="not-json",
        usage_metadata=None,
    )
    provider = GeminiExtractionProvider(
        api_key="test-key",
        model="gemini-2.5-flash",
        client=client,
    )
    with pytest.raises(AIProviderError, match="Invalid JSON"):
        provider.extract_document(
            [PageImage(page_number=1, content=b"abc", mime_type="image/png")]
        )

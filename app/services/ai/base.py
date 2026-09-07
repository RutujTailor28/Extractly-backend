"""AI provider abstraction for document extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PageImage:
    page_number: int
    content: bytes
    mime_type: str = "image/png"


@dataclass
class ExtractionProviderResult:
    raw_response: dict[str, Any] | list[Any] | str
    parsed_payload: dict[str, Any]
    model: str
    latency_ms: int
    cost_usd: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class ExtractionProvider(ABC):
    """Provider interface for vision-based document extraction."""

    @abstractmethod
    def extract_document(
        self,
        pages: list[PageImage],
        *,
        schema_hint: dict[str, Any] | None = None,
        vendor_hints: dict[str, Any] | None = None,
        validation_error: str | None = None,
    ) -> ExtractionProviderResult:
        """Extract structured invoice/receipt data from ordered page images."""

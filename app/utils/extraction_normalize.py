"""Normalization helpers for extracted invoice fields."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.schemas.extraction import (
    HEADER_FIELD_NAMES,
    DocumentExtraction,
    ExtractedField,
    LineItem,
)

CURRENCY_SYMBOL_MAP = {
    "₹": "INR",
    "€": "EUR",
    "£": "GBP",
}

MONEY_FIELDS = {
    "subtotal",
    "tax_amount",
    "tax_rate",
    "discount",
    "shipping",
    "total",
    "quantity",
    "unit_price",
    "line_tax",
    "line_total",
}

DATE_FIELDS = {"invoice_date", "due_date"}


def _clean_number_string(raw: str) -> str:
    text = raw.strip()
    text = text.replace(",", "")
    text = re.sub(r"[^\d.\-]", "", text)
    return text


def normalize_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = _clean_number_string(value)
        if cleaned in {"", "-", ".", "-."}:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def normalize_currency(value: Any) -> tuple[Any, str | None]:
    """Return (normalized_code_or_value, original_value)."""
    if value is None:
        return None, None
    original = str(value).strip()
    if not original:
        return None, None

    upper = original.upper()
    if upper in {"USD", "EUR", "INR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY"}:
        return upper, original

    if original in CURRENCY_SYMBOL_MAP:
        return CURRENCY_SYMBOL_MAP[original], original

    # Ambiguous "$" — do not assume USD
    if original == "$":
        return None, original

    if "USD" in upper or "US$" in upper:
        return "USD", original
    if "EUR" in upper:
        return "EUR", original
    if "INR" in upper or "RS" == upper or upper.startswith("RS"):
        return "INR", original
    if "GBP" in upper:
        return "GBP", original

    return original, original


def normalize_date_field(field: ExtractedField) -> ExtractedField:
    """
    Keep ISO dates; preserve ambiguous originals without guessing.

    If value already looks like YYYY-MM-DD, keep it.
    If original_format is provided by the model with a clear mapping, trust ISO value.
    Otherwise keep original text in original_value and leave value as provided ISO or null.
    """
    data = field.model_dump()
    value = data.get("value")
    original = data.get("original_value") or (str(value) if value is not None else None)

    if isinstance(value, str):
        iso_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
        if iso_match:
            data["value"] = value.strip()
            data["original_value"] = original
            return ExtractedField(**data)

        # Ambiguous numeric dates — do not silently convert
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", value.strip()):
            data["original_value"] = original or value
            # Only keep normalized value if model already supplied ISO separately
            data["value"] = None
            return ExtractedField(**data)

    return field


def normalize_extracted_field(name: str, field: ExtractedField | None) -> ExtractedField | None:
    if field is None:
        return None

    data = field.model_dump()
    value = data.get("value")

    if isinstance(value, str) and value.strip() == "":
        data["value"] = None
        value = None

    if name in MONEY_FIELDS and value is not None:
        original = data.get("original_value") or str(value)
        normalized = normalize_decimal(value)
        data["value"] = float(normalized) if normalized is not None else None
        data["original_value"] = original

    if name == "currency" and value is not None:
        normalized, original = normalize_currency(value)
        data["value"] = normalized
        data["original_value"] = original or data.get("original_value")

    normalized_field = ExtractedField(**data)
    if name in DATE_FIELDS:
        return normalize_date_field(normalized_field)
    return normalized_field


def normalize_extraction(extraction: DocumentExtraction) -> DocumentExtraction:
    """Normalize dates, currency, decimals, and empty values."""
    fields: dict[str, ExtractedField | None] = {}
    for name in HEADER_FIELD_NAMES:
        fields[name] = normalize_extracted_field(name, extraction.fields.get(name))

    # Keep any unexpected fields after normalization attempt
    for name, field in extraction.fields.items():
        if name not in fields:
            fields[name] = normalize_extracted_field(name, field)

    line_items: list[LineItem] = []
    for item in extraction.line_items:
        line_items.append(
            LineItem(
                description=normalize_extracted_field("description", item.description),
                quantity=normalize_extracted_field("quantity", item.quantity),
                unit=normalize_extracted_field("unit", item.unit),
                unit_price=normalize_extracted_field("unit_price", item.unit_price),
                line_tax=normalize_extracted_field("line_tax", item.line_tax),
                line_total=normalize_extracted_field("line_total", item.line_total),
            )
        )

    return DocumentExtraction(
        document_type=extraction.document_type,
        fields=fields,
        line_items=line_items,
        overall_confidence=extraction.overall_confidence,
    )

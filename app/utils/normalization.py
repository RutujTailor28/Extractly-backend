"""Shared normalization helpers for validation rules."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        text = text.replace(",", "")
        text = re.sub(r"[^\d.\-]", "", text)
        if text in {"", "-", ".", "-."}:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                return date.fromisoformat(text)
            except ValueError:
                return None
    return None


def normalize_vendor_name(value: Any) -> str | None:
    if is_missing_value(value):
        return None
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text or None


def normalize_invoice_number(value: Any) -> str | None:
    if is_missing_value(value):
        return None
    text = str(value).strip().lower()
    text = re.sub(r"\s+", "", text)
    return text or None


def normalize_tax_rate(value: Any) -> Decimal | None:
    """
    Normalize tax rate to percentage points.

    Examples:
    - 18 -> 18
    - "18%" -> 18
    - 0.18 -> 18 (treated as fraction when abs(value) <= 1)
    """
    if isinstance(value, str) and "%" in value:
        numeric = parse_decimal(value.replace("%", ""))
        return numeric

    numeric = parse_decimal(value)
    if numeric is None:
        return None

    if abs(numeric) <= Decimal("1"):
        return numeric * Decimal("100")
    return numeric


def field_value(fields: dict[str, Any], name: str) -> Any:
    """Extract raw value from fields_json entry (object or null)."""
    entry = fields.get(name)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def field_confidence(fields: dict[str, Any], name: str) -> Decimal | None:
    entry = fields.get(name)
    if not isinstance(entry, dict):
        return None
    return parse_decimal(entry.get("confidence"))


def money_format(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}"

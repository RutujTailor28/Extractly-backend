"""Independent financial recalculation helpers (Decimal-safe)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.utils.normalization import field_value, parse_decimal


def _line_total_amount(item: dict[str, Any]) -> Decimal | None:
    """Read line_total from flat or ExtractedField-shaped line item."""
    raw = item.get("line_total")
    if isinstance(raw, dict):
        return parse_decimal(raw.get("value"))
    return parse_decimal(raw)


def calculate_line_items_subtotal(line_items: list[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for item in line_items:
        amount = _line_total_amount(item)
        if amount is not None:
            total += amount
    return total


def calculate_expected_total(
    *,
    fields: dict[str, Any],
    flat_fields: bool = False,
) -> Decimal:
    """
    expected_total = subtotal + tax_amount + shipping - discount

    Missing tax/shipping/discount treated as 0.
    Missing subtotal treated as 0 for display calculation.
    """

    def _get(name: str) -> Decimal:
        if flat_fields:
            return parse_decimal(fields.get(name)) or Decimal("0")
        return parse_decimal(field_value(fields, name)) or Decimal("0")

    return _get("subtotal") + _get("tax_amount") + _get("shipping") - _get("discount")


def calculate_totals_from_effective(
    *,
    fields: dict[str, Any],
    line_items: list[dict[str, Any]],
) -> dict[str, Decimal]:
    return {
        "line_items_subtotal": calculate_line_items_subtotal(line_items),
        "expected_total": calculate_expected_total(fields=fields, flat_fields=False),
    }

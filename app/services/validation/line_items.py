"""Line item subtotal validation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.validation.base import BaseValidationRule, ValidationResult
from app.utils.normalization import field_value, money_format, parse_decimal


class LineItemsSubtotalRule(BaseValidationRule):
    rule_code = "LINE_ITEMS_SUBTOTAL_MISMATCH"
    default_config = {"tolerance": 0.02}

    def validate(
        self,
        *,
        extraction: dict[str, Any],
        document: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        fields = extraction.get("fields") or {}
        line_items = extraction.get("line_items") or []
        if not line_items:
            return []

        totals: list[Decimal] = []
        for item in line_items:
            if not isinstance(item, dict):
                continue
            entry = item.get("line_total")
            raw = entry.get("value") if isinstance(entry, dict) else None
            amount = parse_decimal(raw)
            if amount is not None:
                totals.append(amount)

        if not totals:
            return []

        subtotal = parse_decimal(field_value(fields, "subtotal"))
        if subtotal is None:
            return []

        calculated = sum(totals, Decimal("0"))
        tolerance = parse_decimal(config.get("tolerance", 0.02)) or Decimal("0.02")
        diff = abs(calculated - subtotal)
        if diff <= tolerance:
            return []

        return [
            ValidationResult(
                rule_code=self.rule_code,
                severity="error",
                message=(
                    f"Line items sum to {money_format(calculated)} but subtotal "
                    f"reads {money_format(subtotal)} — possible extraction error."
                ),
                passed=False,
                details={
                    "calculated_subtotal": str(calculated),
                    "extracted_subtotal": str(subtotal),
                    "difference": str(diff),
                    "tolerance": str(tolerance),
                },
            )
        ]

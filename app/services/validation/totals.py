"""Grand total arithmetic validation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.validation.base import BaseValidationRule, ValidationResult
from app.utils.normalization import field_value, money_format, parse_decimal


class TotalMismatchRule(BaseValidationRule):
    rule_code = "TOTAL_MISMATCH"
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
        subtotal = parse_decimal(field_value(fields, "subtotal"))
        total = parse_decimal(field_value(fields, "total"))
        if subtotal is None or total is None:
            return []

        tax = parse_decimal(field_value(fields, "tax_amount")) or Decimal("0")
        shipping = parse_decimal(field_value(fields, "shipping")) or Decimal("0")
        discount = parse_decimal(field_value(fields, "discount")) or Decimal("0")

        calculated = subtotal + tax + shipping - discount
        tolerance = parse_decimal(config.get("tolerance", 0.02)) or Decimal("0.02")
        diff = abs(calculated - total)
        if diff <= tolerance:
            return []

        return [
            ValidationResult(
                rule_code=self.rule_code,
                severity="error",
                message=(
                    f"Calculated total is {money_format(calculated)} but "
                    f"extracted total is {money_format(total)}."
                ),
                passed=False,
                details={
                    "calculated_total": str(calculated),
                    "extracted_total": str(total),
                    "subtotal": str(subtotal),
                    "tax_amount": str(tax),
                    "shipping": str(shipping),
                    "discount": str(discount),
                    "difference": str(diff),
                    "tolerance": str(tolerance),
                },
            )
        ]

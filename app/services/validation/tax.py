"""Tax rate plausibility and low-confidence field checks."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.extraction import HEADER_FIELD_NAMES, LINE_ITEM_FIELD_NAMES
from app.services.validation.base import BaseValidationRule, ValidationResult
from app.utils.normalization import (
    field_confidence,
    field_value,
    is_missing_value,
    normalize_tax_rate,
    parse_decimal,
)


class UnusualTaxRateRule(BaseValidationRule):
    rule_code = "UNUSUAL_TAX_RATE"
    default_config = {"min_rate": 0, "max_rate": 30}

    def validate(
        self,
        *,
        extraction: dict[str, Any],
        document: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        fields = extraction.get("fields") or {}
        raw = field_value(fields, "tax_rate")
        if is_missing_value(raw):
            return []

        rate = normalize_tax_rate(raw)
        if rate is None:
            return []

        min_rate = parse_decimal(config.get("min_rate", 0)) or Decimal("0")
        max_rate = parse_decimal(config.get("max_rate", 30)) or Decimal("30")
        if min_rate <= rate <= max_rate:
            return []

        return [
            ValidationResult(
                rule_code=self.rule_code,
                severity="warning",
                message=(
                    f"Extracted tax rate {rate}% is outside the configured "
                    f"plausible range of {min_rate}%–{max_rate}%."
                ),
                passed=False,
                details={
                    "tax_rate": str(rate),
                    "raw_value": raw,
                    "min_rate": str(min_rate),
                    "max_rate": str(max_rate),
                },
            )
        ]


class LowConfidenceFieldRule(BaseValidationRule):
    rule_code = "LOW_CONFIDENCE_FIELD"
    default_config = {"threshold": 0.70}

    def validate(
        self,
        *,
        extraction: dict[str, Any],
        document: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        threshold = parse_decimal(config.get("threshold", 0.70)) or Decimal("0.70")
        fields = extraction.get("fields") or {}
        results: list[ValidationResult] = []

        for name in HEADER_FIELD_NAMES:
            value = field_value(fields, name)
            if is_missing_value(value):
                continue
            confidence = field_confidence(fields, name)
            if confidence is None:
                continue
            if confidence < threshold:
                results.append(
                    ValidationResult(
                        rule_code=self.rule_code,
                        severity="warning",
                        message=(
                            f"Field '{name}' has low extraction confidence "
                            f"({float(confidence):.2f})."
                        ),
                        passed=False,
                        details={
                            "field_name": name,
                            "confidence": float(confidence),
                            "threshold": float(threshold),
                        },
                    )
                )

        line_items = extraction.get("line_items") or []
        for index, item in enumerate(line_items, start=1):
            if not isinstance(item, dict):
                continue
            for name in LINE_ITEM_FIELD_NAMES:
                entry = item.get(name)
                if not isinstance(entry, dict):
                    continue
                value = entry.get("value")
                if is_missing_value(value):
                    continue
                confidence = parse_decimal(entry.get("confidence"))
                if confidence is None or confidence >= threshold:
                    continue
                results.append(
                    ValidationResult(
                        rule_code=self.rule_code,
                        severity="warning",
                        message=(
                            f"Line item {index} field '{name}' has low extraction "
                            f"confidence ({float(confidence):.2f})."
                        ),
                        passed=False,
                        details={
                            "line_item_index": index,
                            "field_name": name,
                            "confidence": float(confidence),
                            "threshold": float(threshold),
                        },
                    )
                )

        return results

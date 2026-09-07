"""Invoice and due date chronological validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.validation.base import BaseValidationRule, ValidationResult
from app.utils.normalization import field_value, parse_date


class InvoiceDateInFutureRule(BaseValidationRule):
    rule_code = "INVOICE_DATE_IN_FUTURE"
    default_config: dict[str, Any] = {}

    def validate(
        self,
        *,
        extraction: dict[str, Any],
        document: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        fields = extraction.get("fields") or {}
        invoice_date = parse_date(field_value(fields, "invoice_date"))
        if invoice_date is None:
            return []

        today = datetime.now(timezone.utc).date()
        if invoice_date <= today:
            return []

        return [
            ValidationResult(
                rule_code=self.rule_code,
                severity="error",
                message=f"Invoice date {invoice_date.isoformat()} is in the future.",
                passed=False,
                details={"invoice_date": invoice_date.isoformat(), "today": today.isoformat()},
            )
        ]


class DueDateBeforeInvoiceDateRule(BaseValidationRule):
    rule_code = "DUE_DATE_BEFORE_INVOICE_DATE"
    default_config: dict[str, Any] = {}

    def validate(
        self,
        *,
        extraction: dict[str, Any],
        document: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        fields = extraction.get("fields") or {}
        invoice_date = parse_date(field_value(fields, "invoice_date"))
        due_date = parse_date(field_value(fields, "due_date"))
        if invoice_date is None or due_date is None:
            return []

        if due_date >= invoice_date:
            return []

        return [
            ValidationResult(
                rule_code=self.rule_code,
                severity="error",
                message=(
                    f"Due date {due_date.isoformat()} is earlier than "
                    f"invoice date {invoice_date.isoformat()}."
                ),
                passed=False,
                details={
                    "due_date": due_date.isoformat(),
                    "invoice_date": invoice_date.isoformat(),
                },
            )
        ]

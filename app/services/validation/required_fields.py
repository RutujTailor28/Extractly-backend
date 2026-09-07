"""Required field validation by document type."""

from __future__ import annotations

from typing import Any

from app.services.validation.base import BaseValidationRule, ValidationResult
from app.utils.normalization import field_value, is_missing_value

REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "invoice": ("vendor_name", "invoice_number", "invoice_date", "currency", "total"),
    "receipt": ("vendor_name", "invoice_date", "currency", "total"),
    "unknown": ("vendor_name", "total"),
}


class RequiredFieldMissingRule(BaseValidationRule):
    rule_code = "REQUIRED_FIELD_MISSING"
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
        document_type = extraction.get("document_type") or fields.get("document_type") or "unknown"
        if isinstance(document_type, dict):
            document_type = document_type.get("value") or "unknown"
        document_type = str(document_type).lower()
        required = REQUIRED_BY_TYPE.get(document_type, REQUIRED_BY_TYPE["unknown"])

        results: list[ValidationResult] = []
        for name in required:
            if is_missing_value(field_value(fields, name)):
                results.append(
                    ValidationResult(
                        rule_code=self.rule_code,
                        severity="error",
                        message=f"Required field '{name}' is missing.",
                        passed=False,
                        details={"field_name": name, "document_type": document_type},
                    )
                )
        return results

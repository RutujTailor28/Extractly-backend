"""Validation rule registry."""

from __future__ import annotations

from app.services.validation.base import (
    DEFAULT_RULE_CONFIGS,
    RULE_ORDER,
    BaseValidationRule,
    ValidationResult,
    merge_rule_settings,
)
from app.services.validation.dates import (
    DueDateBeforeInvoiceDateRule,
    InvoiceDateInFutureRule,
)
from app.services.validation.duplicates import (
    DuplicateInvoiceRule,
    PossibleNearDuplicateRule,
)
from app.services.validation.line_items import LineItemsSubtotalRule
from app.services.validation.required_fields import RequiredFieldMissingRule
from app.services.validation.tax import LowConfidenceFieldRule, UnusualTaxRateRule
from app.services.validation.totals import TotalMismatchRule

RULE_REGISTRY: dict[str, BaseValidationRule] = {
    LineItemsSubtotalRule.rule_code: LineItemsSubtotalRule(),
    TotalMismatchRule.rule_code: TotalMismatchRule(),
    InvoiceDateInFutureRule.rule_code: InvoiceDateInFutureRule(),
    DueDateBeforeInvoiceDateRule.rule_code: DueDateBeforeInvoiceDateRule(),
    RequiredFieldMissingRule.rule_code: RequiredFieldMissingRule(),
    DuplicateInvoiceRule.rule_code: DuplicateInvoiceRule(),
    PossibleNearDuplicateRule.rule_code: PossibleNearDuplicateRule(),
    UnusualTaxRateRule.rule_code: UnusualTaxRateRule(),
    LowConfidenceFieldRule.rule_code: LowConfidenceFieldRule(),
}

__all__ = [
    "DEFAULT_RULE_CONFIGS",
    "RULE_ORDER",
    "RULE_REGISTRY",
    "BaseValidationRule",
    "ValidationResult",
    "merge_rule_settings",
]

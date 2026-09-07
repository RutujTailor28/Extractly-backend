"""Base types and defaults for the validation rules engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class ValidationResult:
    rule_code: str
    severity: Severity
    message: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


DEFAULT_RULE_CONFIGS: dict[str, dict[str, Any]] = {
    "LINE_ITEMS_SUBTOTAL_MISMATCH": {
        "enabled": True,
        "config": {"tolerance": 0.02},
    },
    "TOTAL_MISMATCH": {
        "enabled": True,
        "config": {"tolerance": 0.02},
    },
    "INVOICE_DATE_IN_FUTURE": {
        "enabled": True,
        "config": {},
    },
    "DUE_DATE_BEFORE_INVOICE_DATE": {
        "enabled": True,
        "config": {},
    },
    "REQUIRED_FIELD_MISSING": {
        "enabled": True,
        "config": {},
    },
    "DUPLICATE_INVOICE": {
        "enabled": True,
        "config": {},
    },
    "POSSIBLE_NEAR_DUPLICATE": {
        "enabled": True,
        "config": {"amount_tolerance": 0.02, "date_window_days": 7},
    },
    "UNUSUAL_TAX_RATE": {
        "enabled": True,
        "config": {"min_rate": 0, "max_rate": 30},
    },
    "LOW_CONFIDENCE_FIELD": {
        "enabled": True,
        "config": {"threshold": 0.70},
    },
}

RULE_ORDER = list(DEFAULT_RULE_CONFIGS.keys())


class BaseValidationRule(ABC):
    rule_code: str
    default_config: dict[str, Any]

    @abstractmethod
    def validate(
        self,
        *,
        extraction: dict[str, Any],
        document: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        """Return failing/warning results only (passed=False). Empty list = pass."""


def merge_rule_settings(
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge application defaults with optional user/DB overrides."""
    merged: dict[str, dict[str, Any]] = {}
    for code, defaults in DEFAULT_RULE_CONFIGS.items():
        merged[code] = {
            "rule_code": code,
            "enabled": bool(defaults["enabled"]),
            "config": dict(defaults["config"]),
        }

    for row in overrides or []:
        code = row.get("rule_code")
        if code not in merged:
            continue
        if "enabled" in row and row["enabled"] is not None:
            merged[code]["enabled"] = bool(row["enabled"])
        cfg = row.get("config_json") if "config_json" in row else row.get("config")
        if isinstance(cfg, dict) and cfg:
            merged[code]["config"] = {**merged[code]["config"], **cfg}
    return merged

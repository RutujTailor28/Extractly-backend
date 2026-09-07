"""Pydantic schemas for the validation rules engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["error", "warning", "info"]

KNOWN_RULE_CODES = {
    "LINE_ITEMS_SUBTOTAL_MISMATCH",
    "TOTAL_MISMATCH",
    "INVOICE_DATE_IN_FUTURE",
    "DUE_DATE_BEFORE_INVOICE_DATE",
    "REQUIRED_FIELD_MISSING",
    "DUPLICATE_INVOICE",
    "POSSIBLE_NEAR_DUPLICATE",
    "UNUSUAL_TAX_RATE",
    "LOW_CONFIDENCE_FIELD",
}


class ValidationFlagResponse(BaseModel):
    id: str
    rule_code: str
    severity: Severity
    message: str
    resolved: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class ValidationSummary(BaseModel):
    total_rules_run: int | None = None
    passed: int | None = None
    errors: int = 0
    warnings: int = 0
    info: int = 0
    resolved: int | None = None


class ValidationResponse(BaseModel):
    document_id: str
    status: str
    summary: ValidationSummary
    flags: list[ValidationFlagResponse] = Field(default_factory=list)


class ValidationRuleConfig(BaseModel):
    rule_code: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rule_code")
    @classmethod
    def known_rule(cls, value: str) -> str:
        if value not in KNOWN_RULE_CODES:
            raise ValueError(f"Unknown rule_code '{value}'")
        return value


class ValidationRulesResponse(BaseModel):
    rules: list[ValidationRuleConfig]


class ValidationRulesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[ValidationRuleConfig]

    @model_validator(mode="after")
    def validate_configs(self) -> "ValidationRulesUpdateRequest":
        for rule in self.rules:
            cfg = rule.config or {}
            if "tolerance" in cfg and float(cfg["tolerance"]) < 0:
                raise ValueError(f"{rule.rule_code}: tolerance must be >= 0")
            if "amount_tolerance" in cfg and float(cfg["amount_tolerance"]) < 0:
                raise ValueError(f"{rule.rule_code}: amount_tolerance must be >= 0")
            if "threshold" in cfg:
                threshold = float(cfg["threshold"])
                if threshold < 0 or threshold > 1:
                    raise ValueError(f"{rule.rule_code}: threshold must be between 0 and 1")
            if "min_rate" in cfg and "max_rate" in cfg:
                if float(cfg["max_rate"]) < float(cfg["min_rate"]):
                    raise ValueError(f"{rule.rule_code}: max_rate must be >= min_rate")
            if "date_window_days" in cfg and int(cfg["date_window_days"]) < 0:
                raise ValueError(f"{rule.rule_code}: date_window_days must be >= 0")
        return self


class ResolveFlagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool

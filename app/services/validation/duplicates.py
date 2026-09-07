"""Duplicate and near-duplicate invoice detection."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.validation.base import BaseValidationRule, ValidationResult
from app.utils.normalization import (
    field_value,
    normalize_invoice_number,
    normalize_vendor_name,
    parse_date,
    parse_decimal,
)


def _latest_extraction_map(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map document_id -> latest extraction row (assuming created_at desc order)."""
    latest: dict[str, dict[str, Any]] = {}
    for row in candidates:
        doc_id = row.get("document_id")
        if not doc_id or doc_id in latest:
            continue
        latest[doc_id] = row
    return latest


def _parse_extraction_payload(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("fields_json") or {}
    document_type = "unknown"
    if isinstance(fields, dict):
        document_type = fields.get("document_type") or "unknown"
        fields = {k: v for k, v in fields.items() if k != "document_type"}
    return {
        "document_type": document_type,
        "fields": fields if isinstance(fields, dict) else {},
        "line_items": row.get("line_items_json") or [],
        "id": row.get("id"),
        "document_id": row.get("document_id"),
    }


class DuplicateInvoiceRule(BaseValidationRule):
    rule_code = "DUPLICATE_INVOICE"
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
        vendor = normalize_vendor_name(field_value(fields, "vendor_name"))
        invoice_number = normalize_invoice_number(field_value(fields, "invoice_number"))
        if not vendor or not invoice_number:
            return []

        current_doc_id = document.get("id")
        current_extraction_id = extraction.get("id")
        peer_extractions = (context or {}).get("peer_extractions") or []
        latest = _latest_extraction_map(peer_extractions)

        for peer_doc_id, peer_row in latest.items():
            if peer_doc_id == current_doc_id:
                continue
            if peer_row.get("id") == current_extraction_id:
                continue
            peer = _parse_extraction_payload(peer_row)
            peer_fields = peer["fields"]
            peer_vendor = normalize_vendor_name(field_value(peer_fields, "vendor_name"))
            peer_invoice = normalize_invoice_number(field_value(peer_fields, "invoice_number"))
            if peer_vendor == vendor and peer_invoice == invoice_number:
                display_vendor = field_value(fields, "vendor_name") or vendor
                display_invoice = field_value(fields, "invoice_number") or invoice_number
                return [
                    ValidationResult(
                        rule_code=self.rule_code,
                        severity="warning",
                        message=(
                            f"Possible duplicate invoice: vendor '{display_vendor}' "
                            f"with invoice number '{display_invoice}' already exists "
                            f"in document {peer_doc_id}."
                        ),
                        passed=False,
                        details={
                            "matched_document_id": peer_doc_id,
                            "vendor_name": vendor,
                            "invoice_number": invoice_number,
                        },
                    )
                ]
        return []


class PossibleNearDuplicateRule(BaseValidationRule):
    rule_code = "POSSIBLE_NEAR_DUPLICATE"
    default_config = {"amount_tolerance": 0.02, "date_window_days": 7}

    def validate(
        self,
        *,
        extraction: dict[str, Any],
        document: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        fields = extraction.get("fields") or {}
        vendor = normalize_vendor_name(field_value(fields, "vendor_name"))
        total = parse_decimal(field_value(fields, "total"))
        invoice_date = parse_date(field_value(fields, "invoice_date"))
        if not vendor or total is None or invoice_date is None:
            return []

        amount_tol = parse_decimal(config.get("amount_tolerance", 0.02)) or Decimal("0.02")
        window_days = int(config.get("date_window_days", 7))
        current_doc_id = document.get("id")
        peer_extractions = (context or {}).get("peer_extractions") or []
        latest = _latest_extraction_map(peer_extractions)

        for peer_doc_id, peer_row in latest.items():
            if peer_doc_id == current_doc_id:
                continue
            peer = _parse_extraction_payload(peer_row)
            peer_fields = peer["fields"]
            peer_vendor = normalize_vendor_name(field_value(peer_fields, "vendor_name"))
            peer_total = parse_decimal(field_value(peer_fields, "total"))
            peer_date = parse_date(field_value(peer_fields, "invoice_date"))
            if peer_vendor != vendor or peer_total is None or peer_date is None:
                continue
            if abs(peer_total - total) > amount_tol:
                continue
            if abs((peer_date - invoice_date).days) > window_days:
                continue
            return [
                ValidationResult(
                    rule_code=self.rule_code,
                    severity="warning",
                    message=(
                        "Possible near-duplicate invoice detected with similar "
                        "vendor, amount, and date."
                    ),
                    passed=False,
                    details={
                        "matched_document_id": peer_doc_id,
                        "vendor_name": vendor,
                        "total": str(total),
                        "peer_total": str(peer_total),
                        "invoice_date": invoice_date.isoformat(),
                        "peer_invoice_date": peer_date.isoformat(),
                        "date_window_days": window_days,
                        "amount_tolerance": str(amount_tol),
                    },
                )
            ]
        return []

"""Vendor correction learning — deterministic hint generation."""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from app.core.database import get_supabase_admin_client
from app.schemas.vendor import (
    VendorHint,
    VendorHintAnalysisResponse,
    VendorHintUpdateRequest,
    VendorHintsResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.vendor_service import (
    fetch_vendor,
    get_hints_from_vendor,
    get_vendor_hints,
)
from app.utils.normalization import field_value

logger = logging.getLogger(__name__)

DEFAULT_MIN_CORRECTIONS = 3
DEFAULT_MIN_CURRENCY_DOCS = 3


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def _priority_for_count(count: int) -> str:
    if count >= 5:
        return "high"
    if count >= 3:
        return "medium"
    return "low"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_hint(
    *,
    hint_type: str,
    message: str,
    field: str | None = None,
    evidence_count: int = 0,
    priority: str | None = None,
    value: Any = None,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "type": hint_type,
        "source": "generated",
        "field": field,
        "message": message,
        "priority": priority or _priority_for_count(evidence_count),
        "evidence_count": evidence_count,
        "value": value,
        "created_at": _now_iso(),
    }


def _detect_invoice_prefix(values: list[str]) -> str | None:
    """Simple common prefix heuristic for invoice numbers."""
    cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
    if len(cleaned) < DEFAULT_MIN_CORRECTIONS:
        return None
    # Prefer PREFIX ending with non-digit (e.g. INV-)
    prefixes: list[str] = []
    for value in cleaned:
        match = re.match(r"^([A-Za-z]+[-_/]?)", value)
        if match:
            prefixes.append(match.group(1).upper())
    if not prefixes:
        return None
    common, count = Counter(prefixes).most_common(1)[0]
    if count >= DEFAULT_MIN_CORRECTIONS and len(common) >= 2:
        return common
    return None


def _detect_date_format(originals: list[Any]) -> str | None:
    """Detect DD/MM/YYYY vs MM/DD/YYYY from original_value strings."""
    texts = [str(v) for v in originals if v]
    if len(texts) < DEFAULT_MIN_CORRECTIONS:
        return None
    dmy = 0
    mdy = 0
    for text in texts:
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text.strip()):
            parts = text.strip().split("/")
            day, month = int(parts[0]), int(parts[1])
            # If first component > 12, must be DD/MM
            if day > 12:
                dmy += 1
            elif month > 12:
                mdy += 1
            else:
                # Ambiguous — count as DD/MM preference if day-like
                dmy += 1
    if dmy >= DEFAULT_MIN_CORRECTIONS and dmy > mdy:
        return "DD/MM/YYYY"
    if mdy >= DEFAULT_MIN_CORRECTIONS and mdy > dmy:
        return "MM/DD/YYYY"
    return None


def analyze_vendor_corrections(
    vendor_id: str,
    *,
    min_corrections: int = DEFAULT_MIN_CORRECTIONS,
) -> VendorHintAnalysisResponse:
    client = _require_client()
    vendor = fetch_vendor(client, vendor_id)

    docs = (
        client.table("documents")
        .select("id,status")
        .eq("vendor_id", vendor_id)
        .execute()
    )
    doc_rows = docs.data or []
    doc_ids = [str(d["id"]) for d in doc_rows]
    documents_analyzed = len(doc_ids)

    corrections: list[dict[str, Any]] = []
    if doc_ids:
        # Chunk to avoid oversized filters
        for i in range(0, len(doc_ids), 100):
            chunk = doc_ids[i : i + 100]
            response = (
                client.table("document_corrections")
                .select("*")
                .in_("document_id", chunk)
                .eq("correction_type", "field_correction")
                .execute()
            )
            corrections.extend(response.data or [])

    corrections_analyzed = len(corrections)
    by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in corrections:
        field = row.get("field_name")
        if field:
            by_field[field].append(row)

    generated: list[dict[str, Any]] = []

    # Pattern 1: frequently corrected fields
    for field, rows in by_field.items():
        count = len(rows)
        if count < min_corrections:
            continue
        generated.append(
            _make_hint(
                hint_type="frequently_corrected_field",
                field=field,
                evidence_count=count,
                message=(
                    f"{field.replace('_', ' ').title()} has been manually corrected "
                    f"frequently for this vendor ({count} times). Review this field carefully."
                ),
            )
        )

    # Pattern 2: low confidence + correction — inspect latest extractions field confidence
    low_conf_corrected: dict[str, int] = defaultdict(int)
    if doc_ids:
        for i in range(0, len(doc_ids), 50):
            chunk = doc_ids[i : i + 50]
            extractions = (
                client.table("extractions")
                .select("document_id,fields_json,created_at")
                .in_("document_id", chunk)
                .order("created_at", desc=True)
                .execute()
            )
            seen_docs: set[str] = set()
            for ext in extractions.data or []:
                doc_id = ext.get("document_id")
                if not doc_id or doc_id in seen_docs:
                    continue
                seen_docs.add(doc_id)
                fields = ext.get("fields_json") or {}
                if not isinstance(fields, dict):
                    continue
                corrected_fields = {
                    r.get("field_name")
                    for r in corrections
                    if r.get("document_id") == doc_id
                }
                for field in corrected_fields:
                    entry = fields.get(field)
                    conf = None
                    if isinstance(entry, dict):
                        conf = entry.get("confidence")
                    try:
                        if conf is not None and float(conf) < 0.70:
                            low_conf_corrected[field] += 1
                    except (TypeError, ValueError):
                        continue

    for field, count in low_conf_corrected.items():
        if count < min_corrections:
            continue
        # Avoid duplicate if already frequently_corrected — still add stronger pattern
        generated.append(
            _make_hint(
                hint_type="low_confidence_correction_pattern",
                field=field,
                evidence_count=count,
                priority="high",
                message=(
                    f"{field.replace('_', ' ').title()} frequently has low extraction "
                    "confidence and is often corrected."
                ),
            )
        )

    # Pattern 3: invoice number format
    inv_values = [
        str(r.get("corrected_value_json"))
        for r in by_field.get("invoice_number", [])
        if r.get("corrected_value_json") is not None
    ]
    prefix = _detect_invoice_prefix(inv_values)
    if prefix:
        generated.append(
            _make_hint(
                hint_type="value_format_pattern",
                field="invoice_number",
                evidence_count=len(inv_values),
                value=prefix,
                message=f"Invoice numbers commonly use prefix {prefix}.",
            )
        )

    # Pattern 4: date format from original values in corrections
    date_originals = [
        r.get("original_value_json")
        for r in by_field.get("invoice_date", [])
    ]
    # Also check extraction original_value metadata when available
    date_fmt = _detect_date_format(date_originals)
    if date_fmt:
        generated.append(
            _make_hint(
                hint_type="date_format_hint",
                field="invoice_date",
                evidence_count=len([x for x in date_originals if x]),
                value=date_fmt,
                message=f"This vendor commonly uses {date_fmt} date formatting.",
            )
        )

    # Pattern 5: preferred currency from document extractions
    currency_counter: Counter[str] = Counter()
    if doc_ids:
        for i in range(0, len(doc_ids), 50):
            chunk = doc_ids[i : i + 50]
            extractions = (
                client.table("extractions")
                .select("document_id,fields_json,created_at")
                .in_("document_id", chunk)
                .order("created_at", desc=True)
                .execute()
            )
            seen: set[str] = set()
            for ext in extractions.data or []:
                doc_id = ext.get("document_id")
                if not doc_id or doc_id in seen:
                    continue
                seen.add(doc_id)
                fields = ext.get("fields_json") or {}
                value = field_value(fields, "currency") if isinstance(fields, dict) else None
                if value:
                    currency_counter[str(value).upper()] += 1

    if currency_counter:
        top_currency, top_count = currency_counter.most_common(1)[0]
        if top_count >= DEFAULT_MIN_CURRENCY_DOCS:
            generated.append(
                _make_hint(
                    hint_type="preferred_currency",
                    field="currency",
                    evidence_count=top_count,
                    value=top_currency,
                    priority="medium",
                    message=f"This vendor commonly uses {top_currency} currency.",
                )
            )

    # Merge: keep manual, replace generated
    existing = get_hints_from_vendor(vendor)
    manual = [h for h in existing if isinstance(h, dict) and h.get("source") == "manual"]
    merged = manual + generated
    payload = {
        "version": 1,
        "generated_at": _now_iso(),
        "hints": merged,
    }
    client.table("vendors").update({"template_hints_json": payload}).eq(
        "id", vendor_id
    ).execute()

    return VendorHintAnalysisResponse(
        vendor_id=vendor_id,
        documents_analyzed=documents_analyzed,
        corrections_analyzed=corrections_analyzed,
        hints_generated=len(generated),
        hints=[VendorHint.model_validate(h) for h in merged],
    )


def update_vendor_hints(
    vendor_id: str,
    request: VendorHintUpdateRequest,
) -> VendorHintsResponse:
    client = _require_client()
    vendor = fetch_vendor(client, vendor_id)
    payload = vendor.get("template_hints_json") or {"version": 1, "hints": []}
    if not isinstance(payload, dict):
        payload = {"version": 1, "hints": []}
    hints = list(payload.get("hints") or [])

    if request.operation == "add":
        if request.hint is None:
            raise DocumentServiceError("hint is required for add", status_code=400)
        new_hint = {
            "id": str(uuid.uuid4()),
            "type": request.hint.type,
            "source": "manual",
            "field": request.hint.field,
            "message": request.hint.message,
            "priority": request.hint.priority,
            "evidence_count": 0,
            "value": request.hint.value,
            "created_at": _now_iso(),
        }
        hints.append(new_hint)

    elif request.operation == "update":
        hint_id = request.hint_id or (request.hint.id if request.hint else None)
        if not hint_id or request.hint is None:
            raise DocumentServiceError(
                "hint_id and hint are required for update",
                status_code=400,
            )
        found = False
        for idx, existing in enumerate(hints):
            if not isinstance(existing, dict):
                continue
            if existing.get("id") != hint_id:
                continue
            if existing.get("source") != "manual":
                raise DocumentServiceError(
                    "Only manual hints can be updated",
                    status_code=400,
                )
            hints[idx] = {
                **existing,
                "type": request.hint.type,
                "field": request.hint.field,
                "message": request.hint.message,
                "priority": request.hint.priority,
                "value": request.hint.value,
            }
            found = True
            break
        if not found:
            raise DocumentServiceError("Hint not found", status_code=404)

    elif request.operation == "delete":
        hint_id = request.hint_id or (request.hint.id if request.hint else None)
        if not hint_id:
            raise DocumentServiceError("hint_id is required for delete", status_code=400)
        before = len(hints)
        hints = [
            h
            for h in hints
            if not (
                isinstance(h, dict)
                and h.get("id") == hint_id
                and h.get("source") == "manual"
            )
        ]
        if len(hints) == before:
            # Also allow deleting if id matches (generated cannot be deleted via this? Spec says delete manual)
            raise DocumentServiceError(
                "Manual hint not found",
                status_code=404,
            )

    payload = {
        "version": int(payload.get("version") or 1),
        "generated_at": payload.get("generated_at"),
        "hints": hints,
    }
    client.table("vendors").update({"template_hints_json": payload}).eq(
        "id", vendor_id
    ).execute()
    return get_vendor_hints(vendor_id)

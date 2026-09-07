"""Human Review service — field/line-item corrections, approval, history."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.schemas.extraction import LINE_ITEM_FIELD_NAMES
from app.schemas.review import (
    DATE_HEADER_FIELDS,
    EDITABLE_HEADER_FIELDS,
    MONEY_HEADER_FIELDS,
    ApprovalResponse,
    BulkFieldCorrectionRequest,
    BulkFieldCorrectionResponse,
    CalculatedTotals,
    FieldCorrectionResultItem,
    LineItemCreateRequest,
    LineItemRecalculationResponse,
    LineItemUpdateRequest,
    ReviewDocumentInfo,
    ReviewDocumentResponse,
    ReviewExtractionInfo,
    ReviewFieldResponse,
    ReviewHistoryItemResponse,
    ReviewHistoryResponse,
    ReviewLineItemResponse,
    ReviewPageInfo,
    ReviewValidationFlagItem,
    ReviewValidationResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.recalculation_service import calculate_totals_from_effective
from app.services.review_data_service import (
    build_effective_extraction,
    build_review_fields,
    build_review_line_items,
    fetch_corrections,
    insert_correction,
)
from app.utils.extraction_normalize import normalize_currency, normalize_decimal
from app.utils.normalization import field_value, parse_date, parse_decimal

logger = logging.getLogger(__name__)

DEFAULT_REVIEWER = "system-reviewer"

PROCESSING_STATUSES = {"queued", "preprocessing", "extracting", "validating"}
EDITABLE_STATUSES = {"ready", "needs_review", "failed"}
ISO_CURRENCY = {"USD", "EUR", "INR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY"}


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def _fetch_document(client, document_id: str) -> dict:
    response = (
        client.table("documents")
        .select("*")
        .eq("id", document_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Document not found", status_code=404)
    return response.data[0]


def _update_document(client, document_id: str, updates: dict) -> dict:
    response = (
        client.table("documents")
        .update(updates)
        .eq("id", document_id)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Failed to update document", status_code=500)
    return response.data[0]


def _latest_extraction(client, document_id: str) -> dict:
    from app.services.validation_service import get_latest_extraction_row

    row = get_latest_extraction_row(client, document_id)
    if row is None:
        raise DocumentServiceError(
            "Extraction not found. Process and extract this document before opening Human Review.",
            status_code=404,
            code="EXTRACTION_NOT_FOUND",
        )
    return row


def _assert_editable(document: dict) -> None:
    status = document.get("status")
    if status == "approved":
        raise DocumentServiceError(
            "Approved documents are immutable. Unapprove before editing.",
            status_code=409,
        )
    # "validating" is a brief revalidation window — allow review edits to avoid
    # race 409s when the UI saves multiple field/line-item patches quickly.
    if status == "validating":
        return
    if status in PROCESSING_STATUSES:
        raise DocumentServiceError(
            "Document cannot be edited while processing",
            status_code=409,
        )
    if status not in EDITABLE_STATUSES:
        raise DocumentServiceError(
            f"Document cannot be edited while status is '{status}'",
            status_code=409,
        )


def _normalize_correction_value(field_name: str, value: Any) -> Any:
    if value is None:
        return None

    if field_name in MONEY_HEADER_FIELDS:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        parsed = normalize_decimal(value)
        if parsed is None:
            raise DocumentServiceError(
                f"Invalid money value for '{field_name}'",
                status_code=400,
            )
        # Store as string to preserve Decimal precision in JSON
        return str(parsed)

    if field_name in DATE_HEADER_FIELDS:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        parsed = parse_date(value)
        if parsed is None:
            # Try common formats
            text = str(value).strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                try:
                    return datetime.strptime(text, fmt).date().isoformat()
                except ValueError:
                    continue
            raise DocumentServiceError(
                f"Invalid date value for '{field_name}'",
                status_code=400,
            )
        return parsed.isoformat()

    if field_name == "currency":
        if isinstance(value, str):
            value = value.strip()
        code, _original = normalize_currency(value)
        if code is None or str(code).upper() not in ISO_CURRENCY:
            raise DocumentServiceError(
                f"Invalid currency code '{value}'",
                status_code=400,
            )
        return str(code).upper()

    if isinstance(value, str):
        return value.strip()
    return value


def _values_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    da = parse_decimal(a)
    db = parse_decimal(b)
    if da is not None and db is not None:
        return da == db
    return str(a).strip() == str(b).strip()


def _update_field_values_row(
    client,
    *,
    extraction_id: str,
    field_name: str,
    corrected_value: Any,
    corrected_by: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        existing = (
            client.table("field_values")
            .select("id")
            .eq("extraction_id", extraction_id)
            .eq("field_name", field_name)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return
        client.table("field_values").update(
            {
                "corrected_value": None if corrected_value is None else str(corrected_value),
                "corrected_at": now,
                # column is UUID in schema; store null when using text reviewer identity
                "corrected_by": None,
            }
        ).eq("id", existing.data[0]["id"]).execute()
    except Exception as exc:
        logger.warning("Failed to update field_values for %s: %s", field_name, exc)


def _revalidate(document_id: str) -> dict[str, Any]:
    from app.services.validation_service import validate_document

    # quiet=True avoids flipping status to "validating" (prevents edit races)
    result = validate_document(document_id, quiet=True)
    return result.model_dump(mode="json")


def _signed_page_url(client, storage_path: str) -> str | None:
    try:
        signed = client.storage.from_(settings.PROCESSED_DOCUMENTS_BUCKET).create_signed_url(
            storage_path,
            settings.FILE_URL_EXPIRY_SECONDS,
        )
    except Exception as exc:
        logger.warning("Failed to sign page URL: %s", exc)
        return None
    if isinstance(signed, dict):
        return signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url")
    return None


def get_review_payload(document_id: str) -> ReviewDocumentResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    extraction_row = _latest_extraction(client, document_id)
    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )

    pages_resp = (
        client.table("document_pages")
        .select("*")
        .eq("document_id", document_id)
        .order("page_number", desc=False)
        .execute()
    )
    pages: list[ReviewPageInfo] = []
    for page in pages_resp.data or []:
        path = page.get("processed_storage_path")
        pages.append(
            ReviewPageInfo(
                page_number=int(page["page_number"]),
                image_url=_signed_page_url(client, path) if path else None,
                width=page.get("width"),
                height=page.get("height"),
                mime_type=page.get("mime_type") or "image/png",
            )
        )

    fields = [
        ReviewFieldResponse(**item) for item in build_review_fields(effective)
    ]
    line_items = [
        ReviewLineItemResponse(**item) for item in build_review_line_items(effective)
    ]

    flags_resp = (
        client.table("validation_flags")
        .select("*")
        .eq("document_id", document_id)
        .order("created_at", desc=True)
        .execute()
    )
    flags = flags_resp.data or []
    errors: list[ReviewValidationFlagItem] = []
    warnings: list[ReviewValidationFlagItem] = []
    resolved_count = 0
    for flag in flags:
        if flag.get("resolved"):
            resolved_count += 1
            continue
        item = ReviewValidationFlagItem(
            id=str(flag["id"]),
            rule_code=flag["rule_code"],
            severity=flag["severity"],
            message=flag["message"],
            resolved=False,
            created_at=flag.get("created_at"),
        )
        if flag.get("severity") == "error":
            errors.append(item)
        elif flag.get("severity") == "warning":
            warnings.append(item)

    return ReviewDocumentResponse(
        document=ReviewDocumentInfo(
            id=str(document["id"]),
            filename=document.get("filename") or "",
            status=document.get("status") or "ready",
            page_count=document.get("page_count"),
            source=document.get("source"),
            mime_type=document.get("mime_type"),
            approved_at=document.get("approved_at"),
            approved_by=document.get("approved_by"),
        ),
        pages=pages,
        extraction=ReviewExtractionInfo(
            id=str(extraction_row["id"]),
            overall_confidence=(
                float(extraction_row["overall_confidence"])
                if extraction_row.get("overall_confidence") is not None
                else None
            ),
            model=extraction_row.get("model"),
            document_type=effective.get("document_type"),
            created_at=extraction_row.get("created_at"),
        ),
        fields=fields,
        line_items=line_items,
        validation=ReviewValidationResponse(
            errors=errors,
            warnings=warnings,
            resolved_count=resolved_count,
        ),
    )


def correct_fields(
    document_id: str,
    payload: BulkFieldCorrectionRequest,
    *,
    corrected_by: str = DEFAULT_REVIEWER,
) -> BulkFieldCorrectionResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    _assert_editable(document)
    extraction_row = _latest_extraction(client, document_id)
    extraction_id = str(extraction_row["id"])

    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )
    results: list[FieldCorrectionResultItem] = []

    for item in payload.fields:
        name = item.field_name
        if name not in EDITABLE_HEADER_FIELDS:
            raise DocumentServiceError(
                f"Field '{name}' is not editable",
                status_code=400,
            )
        new_value = _normalize_correction_value(name, item.value)
        current_effective = field_value(effective.get("fields") or {}, name)
        original_ai = (effective.get("original_field_values") or {}).get(name)

        if _values_equal(current_effective, new_value):
            results.append(
                FieldCorrectionResultItem(
                    field_name=name,
                    value=current_effective,
                    original_value=original_ai,
                    corrected_value=(
                        (effective.get("field_corrections") or {}).get(name)
                        if name in (effective.get("field_corrections") or {})
                        else None
                    ),
                    is_corrected=name in (effective.get("field_corrections") or {}),
                    changed=False,
                )
            )
            continue

        insert_correction(
            client,
            document_id=document_id,
            extraction_id=extraction_id,
            correction_type="field_correction",
            field_name=name,
            original_value=current_effective,
            corrected_value=new_value,
            corrected_by=corrected_by,
        )
        _update_field_values_row(
            client,
            extraction_id=extraction_id,
            field_name=name,
            corrected_value=new_value,
            corrected_by=corrected_by,
        )
        results.append(
            FieldCorrectionResultItem(
                field_name=name,
                value=new_value,
                original_value=original_ai,
                corrected_value=new_value,
                is_corrected=True,
                changed=True,
            )
        )
        # Keep in-memory effective in sync for subsequent fields in same request
        from app.services.review_data_service import _set_field_value

        _set_field_value(effective["fields"], name, new_value)
        effective.setdefault("field_corrections", {})[name] = new_value

    validation = _revalidate(document_id)
    updated = _fetch_document(client, document_id)

    return BulkFieldCorrectionResponse(
        document_id=document_id,
        status=updated.get("status") or "ready",
        fields=results,
        validation=validation,
    )


def _normalize_line_item_fields(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in LINE_ITEM_FIELD_NAMES:
        if key not in payload:
            continue
        value = payload[key]
        if key == "description" or key == "unit":
            if isinstance(value, str):
                value = value.strip()
            normalized[key] = value if value != "" else None
            continue
        if key == "quantity":
            if value is None or (isinstance(value, str) and value.strip() == ""):
                normalized[key] = None
                continue
            qty = parse_decimal(value)
            if qty is None:
                raise DocumentServiceError("Invalid quantity", status_code=400)
            if qty < 0:
                raise DocumentServiceError("Quantity must be >= 0", status_code=400)
            normalized[key] = str(qty)
            continue
        # money fields
        if value is None or (isinstance(value, str) and value.strip() == ""):
            normalized[key] = None
            continue
        amount = parse_decimal(value)
        if amount is None:
            raise DocumentServiceError(f"Invalid money value for '{key}'", status_code=400)
        if amount < 0:
            raise DocumentServiceError(
                f"'{key}' cannot be negative",
                status_code=400,
            )
        normalized[key] = str(amount)
    return normalized


def _line_item_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    snap: dict[str, Any] = {"item_id": item.get("item_id")}
    for key in LINE_ITEM_FIELD_NAMES:
        raw = item.get(key)
        if isinstance(raw, dict):
            snap[key] = raw.get("value")
        else:
            snap[key] = raw
    return snap


def _line_items_response(effective: dict[str, Any]) -> list[ReviewLineItemResponse]:
    return [ReviewLineItemResponse(**item) for item in build_review_line_items(effective)]


def _calculated_response(effective: dict[str, Any]) -> CalculatedTotals:
    totals = calculate_totals_from_effective(
        fields=effective.get("fields") or {},
        line_items=effective.get("line_items") or [],
    )
    return CalculatedTotals(
        line_items_subtotal=totals["line_items_subtotal"],
        expected_total=totals["expected_total"],
    )


def update_line_items(
    document_id: str,
    payload: LineItemUpdateRequest,
    *,
    corrected_by: str = DEFAULT_REVIEWER,
) -> LineItemRecalculationResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    _assert_editable(document)
    extraction_row = _latest_extraction(client, document_id)
    extraction_id = str(extraction_row["id"])
    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )
    items = effective.get("line_items") or []

    for patch in payload.line_items:
        if patch.index < 0 or patch.index >= len(items):
            raise DocumentServiceError(
                f"Line item index {patch.index} not found",
                status_code=404,
            )
        raw_patch = patch.model_dump(exclude={"index"}, exclude_unset=True)
        normalized = _normalize_line_item_fields(raw_patch)
        if not normalized:
            continue
        original_snap = _line_item_snapshot(items[patch.index])
        # Skip no-op
        if all(_values_equal(original_snap.get(k), v) for k, v in normalized.items()):
            continue
        insert_correction(
            client,
            document_id=document_id,
            extraction_id=extraction_id,
            correction_type="line_item_update",
            line_item_index=patch.index,
            original_value=original_snap,
            corrected_value=normalized,
            corrected_by=corrected_by,
        )
        from app.services.review_data_service import _set_line_field

        for key, value in normalized.items():
            _set_line_field(items[patch.index], key, value)

    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )
    validation = _revalidate(document_id)
    updated = _fetch_document(client, document_id)

    return LineItemRecalculationResponse(
        document_id=document_id,
        status=updated.get("status") or "ready",
        line_items=_line_items_response(effective),
        calculated=_calculated_response(effective),
        validation=validation,
    )


def add_line_item(
    document_id: str,
    payload: LineItemCreateRequest,
    *,
    corrected_by: str = DEFAULT_REVIEWER,
) -> LineItemRecalculationResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    _assert_editable(document)
    extraction_row = _latest_extraction(client, document_id)
    extraction_id = str(extraction_row["id"])
    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )

    raw = payload.model_dump(exclude_unset=False)
    normalized = _normalize_line_item_fields(raw)
    item_id = str(uuid.uuid4())
    corrected_payload = {**normalized, "item_id": item_id}
    new_index = len(effective.get("line_items") or [])

    insert_correction(
        client,
        document_id=document_id,
        extraction_id=extraction_id,
        correction_type="line_item_added",
        line_item_index=new_index,
        original_value=None,
        corrected_value=corrected_payload,
        corrected_by=corrected_by,
    )

    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )
    validation = _revalidate(document_id)
    updated = _fetch_document(client, document_id)

    return LineItemRecalculationResponse(
        document_id=document_id,
        status=updated.get("status") or "ready",
        line_items=_line_items_response(effective),
        calculated=_calculated_response(effective),
        validation=validation,
    )


def delete_line_item(
    document_id: str,
    item_index: int,
    *,
    corrected_by: str = DEFAULT_REVIEWER,
) -> LineItemRecalculationResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    _assert_editable(document)
    extraction_row = _latest_extraction(client, document_id)
    extraction_id = str(extraction_row["id"])
    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )
    items = effective.get("line_items") or []
    if item_index < 0 or item_index >= len(items):
        raise DocumentServiceError(
            f"Line item index {item_index} not found",
            status_code=404,
        )

    original_snap = _line_item_snapshot(items[item_index])
    insert_correction(
        client,
        document_id=document_id,
        extraction_id=extraction_id,
        correction_type="line_item_deleted",
        line_item_index=item_index,
        original_value=original_snap,
        corrected_value=None,
        corrected_by=corrected_by,
    )

    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_row,
    )
    validation = _revalidate(document_id)
    updated = _fetch_document(client, document_id)

    return LineItemRecalculationResponse(
        document_id=document_id,
        status=updated.get("status") or "ready",
        line_items=_line_items_response(effective),
        calculated=_calculated_response(effective),
        validation=validation,
    )


def approve_document(
    document_id: str,
    *,
    approved_by: str = DEFAULT_REVIEWER,
    force: bool = False,
) -> ApprovalResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    status = document.get("status")

    if status in PROCESSING_STATUSES and status != "validating":
        raise DocumentServiceError(
            "Document cannot be approved while processing",
            status_code=409,
        )
    if status == "approved":
        return ApprovalResponse(
            id=str(document["id"]),
            status="approved",
            approved_at=document.get("approved_at"),
            approved_by=document.get("approved_by"),
        )

    extraction_row = _latest_extraction(client, document_id)

    errors = (
        client.table("validation_flags")
        .select("id,rule_code,message")
        .eq("document_id", document_id)
        .eq("severity", "error")
        .eq("resolved", False)
        .execute()
    )
    unresolved = errors.data or []
    resolved_error_count = 0

    if unresolved and not force:
        codes = ", ".join(
            sorted({str(row.get("rule_code") or "ERROR") for row in unresolved})
        )
        raise DocumentServiceError(
            "Document cannot be approved while unresolved validation errors exist "
            f"({codes}). Fix the fields or approve with force=true to acknowledge "
            "discrepancies.",
            status_code=409,
            code="UNRESOLVED_VALIDATION_ERRORS",
        )

    if unresolved and force:
        now_resolve = datetime.now(timezone.utc).isoformat()
        for row in unresolved:
            client.table("validation_flags").update(
                {
                    "resolved": True,
                    "resolved_at": now_resolve,
                }
            ).eq("id", row["id"]).execute()
            insert_correction(
                client,
                document_id=document_id,
                extraction_id=str(extraction_row["id"]),
                correction_type="validation_resolved",
                original_value={
                    "flag_id": row["id"],
                    "rule_code": row.get("rule_code"),
                    "resolved": False,
                },
                corrected_value={
                    "resolved": True,
                    "acknowledged_on_approve": True,
                },
                corrected_by=approved_by,
            )
        resolved_error_count = len(unresolved)

    now = datetime.now(timezone.utc).isoformat()
    updated = _update_document(
        client,
        document_id,
        {
            "status": "approved",
            "approved_at": now,
            "approved_by": approved_by,
        },
    )
    insert_correction(
        client,
        document_id=document_id,
        extraction_id=str(extraction_row["id"]),
        correction_type="document_approved",
        original_value={"status": status},
        corrected_value={
            "status": "approved",
            "force": force,
            "resolved_error_count": resolved_error_count,
        },
        corrected_by=approved_by,
    )
    return ApprovalResponse(
        id=str(updated["id"]),
        status=updated.get("status") or "approved",
        approved_at=updated.get("approved_at"),
        approved_by=updated.get("approved_by"),
        forced=force and resolved_error_count > 0,
        resolved_error_count=resolved_error_count,
    )


def unapprove_document(
    document_id: str,
    *,
    corrected_by: str = DEFAULT_REVIEWER,
) -> ApprovalResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    if document.get("status") != "approved":
        raise DocumentServiceError(
            "Document is not approved",
            status_code=409,
        )

    extraction_row = _latest_extraction(client, document_id)
    updated = _update_document(
        client,
        document_id,
        {"status": "ready"},
    )
    insert_correction(
        client,
        document_id=document_id,
        extraction_id=str(extraction_row["id"]),
        correction_type="document_unapproved",
        original_value={
            "status": "approved",
            "approved_at": document.get("approved_at"),
            "approved_by": document.get("approved_by"),
        },
        corrected_value={"status": "ready"},
        corrected_by=corrected_by,
    )
    return ApprovalResponse(
        id=str(updated["id"]),
        status=updated.get("status") or "ready",
        approved_at=document.get("approved_at"),
        approved_by=document.get("approved_by"),
    )


def get_review_history(document_id: str) -> ReviewHistoryResponse:
    client = _require_client()
    _fetch_document(client, document_id)
    corrections = fetch_corrections(client, document_id=document_id)
    history = [
        ReviewHistoryItemResponse(
            id=str(row["id"]),
            type=row["correction_type"],
            field_name=row.get("field_name"),
            line_item_index=row.get("line_item_index"),
            original_value=row.get("original_value_json"),
            corrected_value=row.get("corrected_value_json"),
            corrected_by=row.get("corrected_by"),
            created_at=row.get("created_at"),
        )
        for row in corrections
    ]
    return ReviewHistoryResponse(document_id=document_id, history=history)


def record_validation_resolved(
    client,
    *,
    document_id: str,
    extraction_id: str,
    flag_id: str,
    rule_code: str,
    corrected_by: str = DEFAULT_REVIEWER,
) -> None:
    try:
        insert_correction(
            client,
            document_id=document_id,
            extraction_id=extraction_id,
            correction_type="validation_resolved",
            field_name=rule_code,
            original_value={"flag_id": flag_id, "resolved": False},
            corrected_value={"flag_id": flag_id, "resolved": True},
            corrected_by=corrected_by,
        )
    except Exception as exc:
        logger.warning("Failed to record validation_resolved history: %s", exc)

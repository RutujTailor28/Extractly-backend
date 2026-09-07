"""Validation rules engine — run rules, persist flags, route document status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.database import get_supabase_admin_client
from app.schemas.validation import (
    ValidationFlagResponse,
    ValidationResponse,
    ValidationRuleConfig,
    ValidationRulesResponse,
    ValidationRulesUpdateRequest,
    ValidationSummary,
)
from app.services.document_service import DocumentServiceError
from app.services.validation import RULE_ORDER, RULE_REGISTRY, merge_rule_settings
from app.services.validation.base import ValidationResult

logger = logging.getLogger(__name__)

VALIDATABLE_STATUSES = {"ready", "needs_review", "failed"}
BLOCKED_STATUSES = {"queued", "preprocessing", "extracting", "validating"}


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


def _parse_extraction_row(row: dict[str, Any]) -> dict[str, Any]:
    fields_json = row.get("fields_json") or {}
    document_type = "unknown"
    fields: dict[str, Any] = {}
    if isinstance(fields_json, dict):
        document_type = fields_json.get("document_type") or "unknown"
        fields = {k: v for k, v in fields_json.items() if k != "document_type"}
    return {
        "id": row.get("id"),
        "document_id": row.get("document_id"),
        "document_type": document_type,
        "fields": fields,
        "line_items": row.get("line_items_json") or [],
        "overall_confidence": row.get("overall_confidence"),
        "created_at": row.get("created_at"),
    }


def get_latest_extraction_row(client, document_id: str) -> dict[str, Any] | None:
    response = (
        client.table("extractions")
        .select("*")
        .eq("document_id", document_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def load_rule_settings(client, user_id: str | None) -> dict[str, dict[str, Any]]:
    query = client.table("validation_rule_settings").select("*")
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.is_("user_id", "null")
    try:
        response = query.execute()
        overrides = response.data or []
    except Exception as exc:
        logger.warning("Could not load validation_rule_settings (using defaults): %s", exc)
        overrides = []
    return merge_rule_settings(overrides)


def fetch_peer_extractions(client, document: dict[str, Any]) -> list[dict[str, Any]]:
    """Latest extractions for documents belonging to the same user scope."""
    user_id = document.get("user_id")
    docs_query = client.table("documents").select("id")
    if user_id:
        docs_query = docs_query.eq("user_id", user_id)
    else:
        docs_query = docs_query.is_("user_id", "null")
    docs_response = docs_query.execute()
    doc_ids = [row["id"] for row in (docs_response.data or []) if row.get("id")]
    if not doc_ids:
        return []

    # Fetch extractions newest-first; rule code keeps first per document.
    extractions: list[dict[str, Any]] = []
    # Chunk to avoid oversized IN filters
    chunk_size = 100
    for i in range(0, len(doc_ids), chunk_size):
        chunk = doc_ids[i : i + chunk_size]
        response = (
            client.table("extractions")
            .select("id,document_id,fields_json,line_items_json,created_at")
            .in_("document_id", chunk)
            .order("created_at", desc=True)
            .execute()
        )
        extractions.extend(response.data or [])
    return extractions


def run_enabled_rules(
    *,
    extraction: dict[str, Any],
    document: dict[str, Any],
    rule_settings: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> tuple[list[ValidationResult], int, int]:
    """
    Returns (failed_results, rules_run, rules_passed).
    Only failing/warning ValidationResult rows are returned.
    """
    failed: list[ValidationResult] = []
    rules_run = 0
    rules_passed = 0

    for code in RULE_ORDER:
        settings = rule_settings.get(code)
        if not settings or not settings.get("enabled", True):
            continue
        rule = RULE_REGISTRY.get(code)
        if rule is None:
            continue
        rules_run += 1
        results = rule.validate(
            extraction=extraction,
            document=document,
            config=settings.get("config") or {},
            context=context,
        )
        if results:
            failed.extend(results)
        else:
            rules_passed += 1

    return failed, rules_run, rules_passed


def resolve_active_flags(client, document_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    client.table("validation_flags").update(
        {"resolved": True, "resolved_at": now}
    ).eq("document_id", document_id).eq("resolved", False).execute()


def insert_flags(
    client,
    document_id: str,
    results: list[ValidationResult],
) -> list[dict[str, Any]]:
    if not results:
        return []
    rows = [
        {
            "document_id": document_id,
            "rule_code": result.rule_code,
            "severity": result.severity,
            "message": result.message,
            "resolved": False,
            "details_json": result.details or {},
        }
        for result in results
        if not result.passed
    ]
    if not rows:
        return []
    response = client.table("validation_flags").insert(rows).execute()
    return response.data or []


def determine_status(flags: list[dict[str, Any]] | list[ValidationResult]) -> str:
    for flag in flags:
        severity = flag.severity if isinstance(flag, ValidationResult) else flag.get("severity")
        resolved = False if isinstance(flag, ValidationResult) else bool(flag.get("resolved"))
        if severity == "error" and not resolved:
            return "needs_review"
    return "ready"


def _flag_to_response(row: dict[str, Any]) -> ValidationFlagResponse:
    return ValidationFlagResponse(
        id=str(row["id"]),
        rule_code=row["rule_code"],
        severity=row["severity"],
        message=row["message"],
        resolved=bool(row.get("resolved", False)),
        details=row.get("details_json") or {},
        created_at=row.get("created_at"),
        resolved_at=row.get("resolved_at"),
    )


def validate_document(document_id: str, *, quiet: bool = False) -> ValidationResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)

    status = document.get("status")
    if status == "approved":
        raise DocumentServiceError(
            "Approved documents cannot be re-validated. Unapprove first.",
            status_code=409,
        )
    # Allow re-entry while a quiet/prior validation left status as validating
    if status in BLOCKED_STATUSES and status != "validating":
        raise DocumentServiceError(
            f"Document cannot be validated while status is '{status}'",
            status_code=400,
        )
    if status == "validating" and not quiet:
        # Another validation is mid-flight; treat as busy unless quiet revalidate
        raise DocumentServiceError(
            "Document cannot be validated while status is 'validating'",
            status_code=400,
        )

    extraction_row = get_latest_extraction_row(client, document_id)
    if extraction_row is None:
        raise DocumentServiceError(
            "Document must be extracted before validation",
            status_code=400,
        )

    if not quiet:
        _update_document(client, document_id, {"status": "validating", "processing_error": None})

    try:
        from app.services.review_data_service import build_effective_extraction

        extraction = build_effective_extraction(
            client,
            document_id=document_id,
            extraction_row=extraction_row,
        )
        rule_settings = load_rule_settings(client, document.get("user_id"))
        peer_extractions = fetch_peer_extractions(client, document)
        context = {"peer_extractions": peer_extractions}

        failed, rules_run, rules_passed = run_enabled_rules(
            extraction=extraction,
            document=document,
            rule_settings=rule_settings,
            context=context,
        )

        resolve_active_flags(client, document_id)
        inserted = insert_flags(client, document_id, failed)
        final_status = determine_status(failed)
        updated = _update_document(
            client,
            document_id,
            {"status": final_status, "processing_error": None},
        )

        errors = sum(1 for r in failed if r.severity == "error")
        warnings = sum(1 for r in failed if r.severity == "warning")
        info = sum(1 for r in failed if r.severity == "info")

        return ValidationResponse(
            document_id=document_id,
            status=updated.get("status", final_status),
            summary=ValidationSummary(
                total_rules_run=rules_run,
                passed=rules_passed,
                errors=errors,
                warnings=warnings,
                info=info,
            ),
            flags=[_flag_to_response(row) for row in inserted],
        )
    except DocumentServiceError:
        _update_document(
            client,
            document_id,
            {
                "status": "failed",
                "processing_error": "Validation failed",
            },
        )
        raise
    except Exception as exc:
        logger.exception("Validation crashed for document %s", document_id)
        _update_document(
            client,
            document_id,
            {
                "status": "failed",
                "processing_error": f"Validation failed: {exc}",
            },
        )
        raise DocumentServiceError(
            f"Validation failed: {exc}",
            status_code=500,
        ) from exc


def get_validation(
    document_id: str,
    *,
    include_resolved: bool = False,
) -> ValidationResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)

    query = (
        client.table("validation_flags")
        .select("*")
        .eq("document_id", document_id)
        .order("created_at", desc=True)
    )
    if not include_resolved:
        query = query.eq("resolved", False)
    response = query.execute()
    flags = response.data or []
    unresolved = [f for f in flags if not f.get("resolved")]
    resolved = [f for f in flags if f.get("resolved")]
    unresolved.sort(key=lambda f: f.get("created_at") or "", reverse=True)
    resolved.sort(key=lambda f: f.get("created_at") or "", reverse=True)
    ordered = unresolved + resolved

    errors = sum(1 for f in ordered if f.get("severity") == "error" and not f.get("resolved"))
    warnings = sum(1 for f in ordered if f.get("severity") == "warning" and not f.get("resolved"))
    info = sum(1 for f in ordered if f.get("severity") == "info" and not f.get("resolved"))
    resolved_count = sum(1 for f in ordered if f.get("resolved"))

    return ValidationResponse(
        document_id=document_id,
        status=document.get("status") or "ready",
        summary=ValidationSummary(
            errors=errors,
            warnings=warnings,
            info=info,
            resolved=resolved_count,
        ),
        flags=[_flag_to_response(f) for f in ordered],
    )


def resolve_validation_flag(
    document_id: str,
    flag_id: str,
    *,
    resolved: bool,
) -> ValidationFlagResponse:
    client = _require_client()
    _fetch_document(client, document_id)

    response = (
        client.table("validation_flags")
        .select("*")
        .eq("id", flag_id)
        .eq("document_id", document_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Validation flag not found", status_code=404)

    now = datetime.now(timezone.utc).isoformat() if resolved else None
    updates: dict[str, Any] = {"resolved": resolved, "resolved_at": now}
    updated = (
        client.table("validation_flags")
        .update(updates)
        .eq("id", flag_id)
        .eq("document_id", document_id)
        .execute()
    )
    if not updated.data:
        raise DocumentServiceError("Failed to update validation flag", status_code=500)

    flag_row = updated.data[0]

    # Audit history for human flag resolution
    try:
        extraction_row = get_latest_extraction_row(client, document_id)
        if extraction_row and resolved:
            from app.services.review_service import record_validation_resolved

            record_validation_resolved(
                client,
                document_id=document_id,
                extraction_id=str(extraction_row["id"]),
                flag_id=flag_id,
                rule_code=str(flag_row.get("rule_code") or ""),
            )
    except Exception as exc:
        logger.warning("Could not record validation_resolved history: %s", exc)

    # Recalculate status from unresolved error flags
    active = (
        client.table("validation_flags")
        .select("severity,resolved")
        .eq("document_id", document_id)
        .eq("resolved", False)
        .execute()
    )
    new_status = determine_status(active.data or [])
    # Do not demote approved documents via flag resolve
    current = _fetch_document(client, document_id)
    if current.get("status") != "approved":
        _update_document(client, document_id, {"status": new_status})

    return _flag_to_response(flag_row)


def get_validation_rules(user_id: str | None = None) -> ValidationRulesResponse:
    client = _require_client()
    settings = load_rule_settings(client, user_id)
    rules = [
        ValidationRuleConfig(
            rule_code=code,
            enabled=settings[code]["enabled"],
            config=settings[code]["config"],
        )
        for code in RULE_ORDER
        if code in settings
    ]
    return ValidationRulesResponse(rules=rules)


def update_validation_rules(
    payload: ValidationRulesUpdateRequest,
    user_id: str | None = None,
) -> ValidationRulesResponse:
    client = _require_client()

    for rule in payload.rules:
        existing_q = (
            client.table("validation_rule_settings")
            .select("id")
            .eq("rule_code", rule.rule_code)
        )
        if user_id:
            existing_q = existing_q.eq("user_id", user_id)
        else:
            existing_q = existing_q.is_("user_id", "null")
        existing = existing_q.limit(1).execute()

        row = {
            "user_id": user_id,
            "rule_code": rule.rule_code,
            "enabled": rule.enabled,
            "config_json": rule.config or {},
        }
        if existing.data:
            client.table("validation_rule_settings").update(
                {
                    "enabled": rule.enabled,
                    "config_json": rule.config or {},
                }
            ).eq("id", existing.data[0]["id"]).execute()
        else:
            client.table("validation_rule_settings").insert(row).execute()

    return get_validation_rules(user_id=user_id)

"""Validation rules engine API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.validation import (
    ResolveFlagRequest,
    ValidationFlagResponse,
    ValidationResponse,
    ValidationRulesResponse,
    ValidationRulesUpdateRequest,
)
from app.services.document_service import DocumentServiceError
from app.services.validation_service import (
    get_validation,
    get_validation_rules,
    resolve_validation_flag,
    update_validation_rules,
    validate_document,
)

document_validation_router = APIRouter(prefix="/api/v1/documents", tags=["validation"])
rules_router = APIRouter(prefix="/api/v1/validation", tags=["validation"])


def _raise_service_error(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@document_validation_router.post(
    "/{document_id}/validate",
    response_model=ValidationResponse,
)
def validate_document_endpoint(document_id: str) -> ValidationResponse:
    """Run the validation rules engine against the latest extraction."""
    try:
        return validate_document(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@document_validation_router.get(
    "/{document_id}/validation",
    response_model=ValidationResponse,
)
def get_validation_endpoint(
    document_id: str,
    include_resolved: bool = Query(default=False),
) -> ValidationResponse:
    """Return validation flags and summary for a document."""
    try:
        return get_validation(document_id, include_resolved=include_resolved)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@document_validation_router.patch(
    "/{document_id}/validation-flags/{flag_id}",
    response_model=ValidationFlagResponse,
)
def resolve_flag_endpoint(
    document_id: str,
    flag_id: str,
    payload: ResolveFlagRequest,
) -> ValidationFlagResponse:
    """Mark a validation flag resolved/unresolved and recalculate status."""
    try:
        return resolve_validation_flag(
            document_id,
            flag_id,
            resolved=payload.resolved,
        )
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@rules_router.get("/rules", response_model=ValidationRulesResponse)
def get_rules_endpoint() -> ValidationRulesResponse:
    """Return default validation rules merged with stored overrides."""
    try:
        return get_validation_rules(user_id=None)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@rules_router.patch("/rules", response_model=ValidationRulesResponse)
def patch_rules_endpoint(payload: ValidationRulesUpdateRequest) -> ValidationRulesResponse:
    """Enable/disable rules and update configuration (tolerances, thresholds)."""
    try:
        return update_validation_rules(payload, user_id=None)
    except DocumentServiceError as exc:
        _raise_service_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

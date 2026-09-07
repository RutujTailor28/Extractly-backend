"""Human Review & Correction API routes."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.schemas.review import (
    ApprovalRequest,
    ApprovalResponse,
    BulkFieldCorrectionRequest,
    BulkFieldCorrectionResponse,
    LineItemCreateRequest,
    LineItemRecalculationResponse,
    LineItemUpdateRequest,
    ReviewDocumentResponse,
    ReviewHistoryResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.review_service import (
    DEFAULT_REVIEWER,
    add_line_item,
    approve_document,
    correct_fields,
    delete_line_item,
    get_review_history,
    get_review_payload,
    unapprove_document,
    update_line_items,
)

router = APIRouter(prefix="/api/v1/documents", tags=["review"])


def _raise_service_error(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/{document_id}/review", response_model=ReviewDocumentResponse)
def get_review_endpoint(document_id: str) -> ReviewDocumentResponse:
    """Return all data needed by the Human Review workspace."""
    try:
        return get_review_payload(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.patch("/{document_id}/fields", response_model=BulkFieldCorrectionResponse)
def patch_fields_endpoint(
    document_id: str,
    payload: BulkFieldCorrectionRequest,
) -> BulkFieldCorrectionResponse:
    """Apply bulk header field corrections and re-run validation."""
    try:
        return correct_fields(document_id, payload)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.patch("/{document_id}/line-items", response_model=LineItemRecalculationResponse)
def patch_line_items_endpoint(
    document_id: str,
    payload: LineItemUpdateRequest,
) -> LineItemRecalculationResponse:
    """Update one or more line items and return recalculated totals."""
    try:
        return update_line_items(document_id, payload)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.post("/{document_id}/line-items", response_model=LineItemRecalculationResponse)
def post_line_item_endpoint(
    document_id: str,
    payload: LineItemCreateRequest,
) -> LineItemRecalculationResponse:
    """Add a line item to the corrected representation."""
    try:
        return add_line_item(document_id, payload)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.delete(
    "/{document_id}/line-items/{item_index}",
    response_model=LineItemRecalculationResponse,
)
def delete_line_item_endpoint(
    document_id: str,
    item_index: int,
) -> LineItemRecalculationResponse:
    """Remove a line item from the corrected representation (AI data preserved)."""
    try:
        return delete_line_item(document_id, item_index)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.post("/{document_id}/approve", response_model=ApprovalResponse)
def approve_endpoint(
    document_id: str,
    payload: ApprovalRequest = Body(default_factory=ApprovalRequest),
) -> ApprovalResponse:
    """Approve a document. Pass force=true to acknowledge unresolved validation errors."""
    try:
        return approve_document(
            document_id,
            approved_by=(payload.approved_by or DEFAULT_REVIEWER),
            force=bool(payload.force),
        )
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.post("/{document_id}/unapprove", response_model=ApprovalResponse)
def unapprove_endpoint(document_id: str) -> ApprovalResponse:
    """Reopen an approved document for further edits."""
    try:
        return unapprove_document(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.get("/{document_id}/review-history", response_model=ReviewHistoryResponse)
def review_history_endpoint(document_id: str) -> ReviewHistoryResponse:
    """Return append-only human correction / approval history."""
    try:
        return get_review_history(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)

"""Accounting integration APIs (mock provider for Phase 10)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.integration import (
    IntegrationHistoryResponse,
    IntegrationPreviewResponse,
    IntegrationResultResponse,
    SendToAccountingRequest,
)
from app.services.accounting_integration_service import (
    get_integration_history,
    preview_integration,
    send_to_accounting,
)
from app.services.document_service import DocumentServiceError

router = APIRouter(prefix="/api/v1/documents", tags=["integrations"])


def _http_error(exc: DocumentServiceError) -> None:
    detail: str | dict = exc.message
    if exc.code:
        detail = {"detail": exc.message, "code": exc.code}
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.get(
    "/{document_id}/integration-preview",
    response_model=IntegrationPreviewResponse,
)
def integration_preview_endpoint(document_id: str) -> IntegrationPreviewResponse:
    """Preview the accounting payload that would be sent (approved docs only)."""
    try:
        return preview_integration(document_id, provider="mock")
    except DocumentServiceError as exc:
        _http_error(exc)


@router.post(
    "/{document_id}/send-to-accounting",
    response_model=IntegrationResultResponse,
)
def send_to_accounting_endpoint(
    document_id: str,
    payload: SendToAccountingRequest,
) -> IntegrationResultResponse:
    """
    Send invoice to the mock accounting system.

    Idempotent: a successful prior send for the same document+provider
    returns the existing result without creating a duplicate log.
    """
    try:
        return send_to_accounting(document_id, provider=payload.provider)
    except DocumentServiceError as exc:
        _http_error(exc)


@router.get(
    "/{document_id}/integration-history",
    response_model=IntegrationHistoryResponse,
)
def integration_history_endpoint(document_id: str) -> IntegrationHistoryResponse:
    """List integration attempts for a document (no secrets exposed)."""
    try:
        return get_integration_history(document_id)
    except DocumentServiceError as exc:
        _http_error(exc)

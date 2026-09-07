"""AI extraction API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.extraction import (
    DocumentExtractionDetailResponse,
    ExtractionRunResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.extraction_service import (
    extract_document,
    get_latest_extraction,
    reextract_document,
)

router = APIRouter(prefix="/api/v1/documents", tags=["extraction"])


def _raise_service_error(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{document_id}/extract", response_model=ExtractionRunResponse)
def extract_document_endpoint(document_id: str) -> ExtractionRunResponse:
    """Extract structured invoice/receipt data from processed pages."""
    try:
        return extract_document(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.get("/{document_id}/extraction", response_model=DocumentExtractionDetailResponse)
def get_extraction_endpoint(document_id: str) -> DocumentExtractionDetailResponse:
    """Return the latest extraction for a document."""
    try:
        return get_latest_extraction(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.post("/{document_id}/reextract", response_model=ExtractionRunResponse)
def reextract_document_endpoint(document_id: str) -> ExtractionRunResponse:
    """Re-run extraction and preserve previous extraction history."""
    try:
        return reextract_document(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)

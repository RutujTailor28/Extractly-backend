"""Document processing API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.schemas.processing import (
    DocumentPageResponse,
    DocumentPagesResponse,
    DocumentProcessResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.processing_service import (
    get_document_page,
    list_document_pages,
    process_document,
)

router = APIRouter(prefix="/api/v1/documents", tags=["processing"])


def _raise_service_error(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{document_id}/process", response_model=DocumentProcessResponse)
def process_document_endpoint(document_id: str) -> DocumentProcessResponse:
    """Process an uploaded document into normalized page images."""
    try:
        return process_document(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.get("/{document_id}/pages", response_model=DocumentPagesResponse)
def get_document_pages_endpoint(document_id: str) -> DocumentPagesResponse:
    """List processed pages for a document."""
    try:
        return list_document_pages(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.get(
    "/{document_id}/pages/{page_number}",
    response_model=DocumentPageResponse,
)
def get_document_page_endpoint(
    document_id: str,
    page_number: int = Path(..., ge=1),
) -> DocumentPageResponse:
    """Return a temporary signed URL for a processed page image."""
    try:
        return get_document_page(document_id, page_number)
    except DocumentServiceError as exc:
        _raise_service_error(exc)

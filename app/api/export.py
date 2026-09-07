"""Export download APIs (CSV / XLSX / JSON / QuickBooks-compatible)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.schemas.export import QuickBooksPreviewResponse
from app.services.document_service import DocumentServiceError
from app.services.export_service import generate_export, parse_document_ids
from app.services.quickbooks_mapping_service import (
    generate_quickbooks_csv,
    preview_quickbooks,
)

router = APIRouter(prefix="/api/v1/export", tags=["export"])


def _http_error(exc: DocumentServiceError) -> None:
    detail: str | dict = exc.message
    if exc.code:
        detail = {"detail": exc.message, "code": exc.code}
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.get("")
def export_documents(
    format: Literal["csv", "xlsx", "json"] = Query("csv"),
    status: Literal["approved"] = Query("approved"),
    vendor_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    document_ids: str | None = Query(
        None,
        description="Comma-separated document UUIDs",
    ),
    include_line_items: bool = Query(True),
) -> Response:
    """
    Export approved invoices.

    Date filters use `approved_at`. CSV with line items returns a ZIP
    (`invoices.csv` + `line_items.csv`).
    """
    try:
        content, media_type, filename = generate_export(
            format=format,
            status=status,
            vendor_id=vendor_id,
            date_from=date_from,
            date_to=date_to,
            document_ids=parse_document_ids(document_ids),
            include_line_items=include_line_items,
            user_id=None,
        )
    except DocumentServiceError as exc:
        _http_error(exc)

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/quickbooks")
def export_quickbooks(
    vendor_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    document_ids: str | None = Query(None),
) -> Response:
    """
    QuickBooks-compatible CSV mapping (accounts-payable friendly columns).

    This is NOT an official QuickBooks API integration.
    """
    try:
        content, media_type, filename = generate_quickbooks_csv(
            vendor_id=vendor_id,
            date_from=date_from,
            date_to=date_to,
            document_ids=parse_document_ids(document_ids),
            user_id=None,
        )
    except DocumentServiceError as exc:
        _http_error(exc)

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/quickbooks/preview", response_model=QuickBooksPreviewResponse)
def export_quickbooks_preview(
    vendor_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    document_ids: str | None = Query(None),
) -> QuickBooksPreviewResponse:
    """Preview QuickBooks-compatible rows (max 100)."""
    try:
        return preview_quickbooks(
            vendor_id=vendor_id,
            date_from=date_from,
            date_to=date_to,
            document_ids=parse_document_ids(document_ids),
            user_id=None,
        )
    except DocumentServiceError as exc:
        _http_error(exc)

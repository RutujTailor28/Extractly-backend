"""Vendor management and learning APIs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.schemas.vendor import (
    VendorDetailResponse,
    VendorHintAnalysisResponse,
    VendorHintsResponse,
    VendorHintUpdateRequest,
    VendorListResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.vendor_learning_service import (
    analyze_vendor_corrections,
    update_vendor_hints,
)
from app.services.vendor_service import (
    get_vendor_detail,
    get_vendor_hints,
    list_vendors,
)

router = APIRouter(prefix="/api/v1/vendors", tags=["vendors"])


def _raise(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("", response_model=VendorListResponse)
def list_vendors_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    sort_by: Literal["name", "invoice_count", "created_at", "last_invoice_at"] = "name",
    sort_order: Literal["asc", "desc"] = "asc",
) -> VendorListResponse:
    try:
        return list_vendors(
            page=page,
            page_size=page_size,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except DocumentServiceError as exc:
        _raise(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Unable to load vendors right now. Please retry.",
        ) from exc


@router.get("/{vendor_id}", response_model=VendorDetailResponse)
def get_vendor_endpoint(vendor_id: str) -> VendorDetailResponse:
    try:
        return get_vendor_detail(vendor_id)
    except DocumentServiceError as exc:
        _raise(exc)


@router.get("/{vendor_id}/hints", response_model=VendorHintsResponse)
def get_hints_endpoint(
    vendor_id: str,
    source: Literal["generated", "manual"] | None = Query(None),
) -> VendorHintsResponse:
    try:
        return get_vendor_hints(vendor_id, source=source)
    except DocumentServiceError as exc:
        _raise(exc)


@router.patch("/{vendor_id}/hints", response_model=VendorHintsResponse)
def patch_hints_endpoint(
    vendor_id: str,
    payload: VendorHintUpdateRequest,
) -> VendorHintsResponse:
    try:
        return update_vendor_hints(vendor_id, payload)
    except DocumentServiceError as exc:
        _raise(exc)


@router.post(
    "/{vendor_id}/analyze-corrections",
    response_model=VendorHintAnalysisResponse,
)
def analyze_corrections_endpoint(vendor_id: str) -> VendorHintAnalysisResponse:
    try:
        return analyze_vendor_corrections(vendor_id)
    except DocumentServiceError as exc:
        _raise(exc)

"""Analytics APIs — overview, accuracy, vendors, trends."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.schemas.analytics import (
    AccuracyAnalyticsResponse,
    AnalyticsOverviewResponse,
    TrendAnalyticsResponse,
    VendorAnalyticsResponse,
)
from app.services.analytics_service import (
    get_accuracy_analytics,
    get_analytics_overview,
    get_trend_analytics,
    get_vendor_analytics,
)
from app.services.document_service import DocumentServiceError

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _raise(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/overview", response_model=AnalyticsOverviewResponse)
def overview_endpoint(
    days: int = Query(30, ge=1, le=365),
) -> AnalyticsOverviewResponse:
    try:
        return get_analytics_overview(days=days)
    except DocumentServiceError as exc:
        _raise(exc)


@router.get("/accuracy", response_model=AccuracyAnalyticsResponse)
def accuracy_endpoint(
    days: int = Query(30, ge=1, le=365),
    vendor_id: str | None = Query(None),
    field_name: str | None = Query(None),
    group_by: Literal["day", "week", "month", "field", "vendor"] = Query("field"),
) -> AccuracyAnalyticsResponse:
    try:
        return get_accuracy_analytics(
            days=days,
            vendor_id=vendor_id,
            field_name=field_name,
            group_by=group_by,
        )
    except DocumentServiceError as exc:
        _raise(exc)


@router.get("/vendors", response_model=VendorAnalyticsResponse)
def vendors_endpoint(
    limit: int = Query(50, ge=1, le=200),
    sort_by: Literal[
        "documents",
        "corrections",
        "correction_rate",
        "average_confidence",
        "accuracy",
    ] = Query("documents"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
) -> VendorAnalyticsResponse:
    try:
        return get_vendor_analytics(
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except DocumentServiceError as exc:
        _raise(exc)


@router.get("/trends", response_model=TrendAnalyticsResponse)
def trends_endpoint(
    days: int = Query(30, ge=1, le=365),
    metric: Literal[
        "documents",
        "corrections",
        "correction_rate",
        "confidence",
        "cost",
        "processing_time",
        "accuracy",
    ] = Query("corrections"),
    group_by: Literal["day", "week", "month"] = Query("day"),
) -> TrendAnalyticsResponse:
    try:
        return get_trend_analytics(days=days, metric=metric, group_by=group_by)
    except DocumentServiceError as exc:
        _raise(exc)

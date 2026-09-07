"""Pydantic schemas for analytics APIs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GroupBy = Literal["day", "week", "month", "field", "vendor"]
TrendMetric = Literal[
    "documents",
    "corrections",
    "correction_rate",
    "confidence",
    "cost",
    "processing_time",
    "accuracy",
]
TrendGroupBy = Literal["day", "week", "month"]


class PeriodInfo(BaseModel):
    days: int


class DocumentStats(BaseModel):
    total: int = 0
    approved: int = 0
    needs_review: int = 0
    failed: int = 0
    ready: int = 0
    processing: int = 0


class ProcessingStats(BaseModel):
    average_latency_ms: float | None = None
    average_confidence: float | None = None
    average_cost_usd: float | None = None
    total_cost_usd: float | None = None


class ReviewStats(BaseModel):
    documents_reviewed: int = 0
    total_corrections: int = 0
    average_corrections_per_document: float | None = None
    documents_requiring_review: int = 0


class QualityStats(BaseModel):
    accuracy: float | None = None
    correction_rate: float | None = None
    review_coverage: float | None = None


class AnalyticsOverviewResponse(BaseModel):
    period: PeriodInfo
    documents: DocumentStats
    processing: ProcessingStats
    review: ReviewStats
    quality: QualityStats


class AccuracySummary(BaseModel):
    accuracy: float | None = None
    correction_rate: float | None = None
    coverage: float | None = None
    fields_analyzed: int = 0
    fields_corrected: int = 0
    fields_unchanged: int = 0
    unknown_fields: int = 0


class AccuracyByFieldItem(BaseModel):
    field_name: str
    accuracy: float | None = None
    correction_rate: float | None = None
    samples: int = 0


class AccuracyTrendPoint(BaseModel):
    date: str
    value: float | None = None


class AccuracyAnalyticsResponse(BaseModel):
    period: PeriodInfo
    summary: AccuracySummary
    by_field: list[AccuracyByFieldItem] = Field(default_factory=list)
    by_vendor: list[dict[str, Any]] = Field(default_factory=list)
    trend: list[AccuracyTrendPoint] = Field(default_factory=list)


class VendorAnalyticsItem(BaseModel):
    vendor_id: str
    vendor_name: str
    documents: int = 0
    corrections: int = 0
    correction_rate: float | None = None
    accuracy: float | None = None
    average_confidence: float | None = None
    hint_count: int = 0


class VendorAnalyticsResponse(BaseModel):
    vendors: list[VendorAnalyticsItem] = Field(default_factory=list)


class TrendPoint(BaseModel):
    date: str
    value: float | None = None


class TrendAnalyticsResponse(BaseModel):
    metric: TrendMetric
    group_by: TrendGroupBy
    data: list[TrendPoint] = Field(default_factory=list)

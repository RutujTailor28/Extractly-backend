"""Public developer API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "processing", "completed", "failed"]


class PublicVendorResult(BaseModel):
    name: Any = None
    address: Any = None
    tax_id: Any = None


class PublicInvoiceResult(BaseModel):
    number: Any = None
    date: Any = None
    due_date: Any = None
    po_number: Any = None


class PublicAmountsResult(BaseModel):
    currency: Any = None
    subtotal: str | None = None
    tax_amount: str | None = None
    discount: str | None = None
    shipping: str | None = None
    total: str | None = None


class PublicLineItem(BaseModel):
    description: Any = None
    quantity: str | None = None
    unit: Any = None
    unit_price: str | None = None
    line_tax: str | None = None
    line_total: str | None = None


class PublicExtractionResult(BaseModel):
    vendor: PublicVendorResult = Field(default_factory=PublicVendorResult)
    invoice: PublicInvoiceResult = Field(default_factory=PublicInvoiceResult)
    amounts: PublicAmountsResult = Field(default_factory=PublicAmountsResult)
    line_items: list[PublicLineItem] = Field(default_factory=list)


class PublicFieldMetadata(BaseModel):
    field_name: str
    confidence: float | None = None
    page_number: int | None = None
    bbox: dict[str, float] | None = None


class PublicExtractionMetadata(BaseModel):
    overall_confidence: float | None = None
    fields: list[PublicFieldMetadata] = Field(default_factory=list)


class PublicWebhookInfo(BaseModel):
    url: str
    signing_secret: str


class PublicErrorBody(BaseModel):
    code: str
    message: str


class PublicErrorResponse(BaseModel):
    error: dict[str, Any]


class ExtractionJobAcceptedResponse(BaseModel):
    status: Literal["processing", "queued"] = "processing"
    job_id: str
    document_id: str
    status_url: str
    webhook: PublicWebhookInfo | None = None


class ExtractionCompletedResponse(BaseModel):
    status: Literal["completed"] = "completed"
    job_id: str
    document_id: str
    result: PublicExtractionResult
    metadata: PublicExtractionMetadata | None = None
    webhook: PublicWebhookInfo | None = None


class ExtractionJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    document_id: str
    created_at: datetime | None = None
    result: PublicExtractionResult | None = None
    metadata: PublicExtractionMetadata | None = None
    error: PublicErrorBody | None = None
    webhook: PublicWebhookInfo | None = None


class ApiUsageDayPoint(BaseModel):
    date: str
    count: int


class ApiUsageEndpointPoint(BaseModel):
    endpoint: str
    count: int


class ApiUsageResponse(BaseModel):
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rate_limited_requests: int = 0
    average_latency_ms: float | None = None
    requests_by_day: list[ApiUsageDayPoint] = Field(default_factory=list)
    requests_by_endpoint: list[ApiUsageEndpointPoint] = Field(default_factory=list)

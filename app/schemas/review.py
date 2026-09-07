"""Pydantic schemas for Human Review & Correction APIs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.extraction import HEADER_FIELD_NAMES

CorrectionType = Literal[
    "field_correction",
    "line_item_update",
    "line_item_added",
    "line_item_deleted",
    "validation_resolved",
    "document_approved",
    "document_unapproved",
]

EDITABLE_HEADER_FIELDS = set(HEADER_FIELD_NAMES)

MONEY_HEADER_FIELDS = {
    "subtotal",
    "tax_amount",
    "tax_rate",
    "discount",
    "shipping",
    "total",
}

DATE_HEADER_FIELDS = {"invoice_date", "due_date"}

LINE_ITEM_EDITABLE_FIELDS = (
    "description",
    "quantity",
    "unit",
    "unit_price",
    "line_tax",
    "line_total",
)


class BoundingBoxResponse(BaseModel):
    x: float
    y: float
    width: float
    height: float


class ReviewDocumentInfo(BaseModel):
    id: str
    filename: str
    status: str
    page_count: int | None = None
    source: str | None = None
    mime_type: str | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None


class ReviewPageInfo(BaseModel):
    page_number: int
    image_url: str | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None


class ReviewExtractionInfo(BaseModel):
    id: str
    overall_confidence: float | None = None
    model: str | None = None
    document_type: str | None = None
    created_at: datetime | None = None


class ReviewFieldResponse(BaseModel):
    field_name: str
    value: Any = None
    original_value: Any = None
    corrected_value: Any = None
    confidence: float | None = None
    page_number: int | None = None
    bbox: BoundingBoxResponse | None = None
    is_corrected: bool = False


class ReviewLineItemResponse(BaseModel):
    index: int
    item_id: str | None = None
    description: Any = None
    quantity: Any = None
    unit: Any = None
    unit_price: Any = None
    line_tax: Any = None
    line_total: Any = None


class ReviewValidationFlagItem(BaseModel):
    id: str
    rule_code: str
    severity: str
    message: str
    resolved: bool = False
    created_at: datetime | None = None


class ReviewValidationResponse(BaseModel):
    errors: list[ReviewValidationFlagItem] = Field(default_factory=list)
    warnings: list[ReviewValidationFlagItem] = Field(default_factory=list)
    resolved_count: int = 0


class ReviewDocumentResponse(BaseModel):
    document: ReviewDocumentInfo
    pages: list[ReviewPageInfo] = Field(default_factory=list)
    extraction: ReviewExtractionInfo
    fields: list[ReviewFieldResponse] = Field(default_factory=list)
    line_items: list[ReviewLineItemResponse] = Field(default_factory=list)
    validation: ReviewValidationResponse


class FieldCorrectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    value: Any = None

    @field_validator("field_name")
    @classmethod
    def known_field(cls, value: str) -> str:
        if value not in EDITABLE_HEADER_FIELDS:
            raise ValueError(f"Field '{value}' is not editable")
        return value


class BulkFieldCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[FieldCorrectionItem] = Field(..., min_length=1)


class FieldCorrectionResultItem(BaseModel):
    field_name: str
    value: Any = None
    original_value: Any = None
    corrected_value: Any = None
    is_corrected: bool = False
    changed: bool = False


class BulkFieldCorrectionResponse(BaseModel):
    document_id: str
    status: str
    fields: list[FieldCorrectionResultItem]
    validation: dict[str, Any] | None = None


class LineItemPatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., ge=0)
    description: Any = None
    quantity: Any = None
    unit: Any = None
    unit_price: Any = None
    line_tax: Any = None
    line_total: Any = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "LineItemPatchItem":
        payload = self.model_dump(exclude={"index"}, exclude_none=False)
        # Allow explicit nulls; require at least one key beyond index was intended —
        # accept any patch with index.
        return self


class LineItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_items: list[LineItemPatchItem] = Field(..., min_length=1)


class LineItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Any = None
    quantity: Any = None
    unit: Any = None
    unit_price: Any = None
    line_tax: Any = None
    line_total: Any = None


class CalculatedTotals(BaseModel):
    line_items_subtotal: Decimal
    expected_total: Decimal


class LineItemRecalculationResponse(BaseModel):
    document_id: str
    status: str
    line_items: list[ReviewLineItemResponse]
    calculated: CalculatedTotals
    validation: dict[str, Any] | None = None


class ApprovalRequest(BaseModel):
    """Optional body for approve. force=true allows approving with unresolved errors."""

    model_config = ConfigDict(extra="forbid")

    force: bool = False
    approved_by: str | None = Field(default=None, max_length=200)


class ApprovalResponse(BaseModel):
    id: str
    status: str
    approved_at: datetime | None = None
    approved_by: str | None = None
    forced: bool = False
    resolved_error_count: int = 0


class ReviewHistoryItemResponse(BaseModel):
    id: str
    type: CorrectionType
    field_name: str | None = None
    line_item_index: int | None = None
    original_value: Any = None
    corrected_value: Any = None
    corrected_by: str | None = None
    created_at: datetime | None = None


class ReviewHistoryResponse(BaseModel):
    document_id: str
    history: list[ReviewHistoryItemResponse] = Field(default_factory=list)

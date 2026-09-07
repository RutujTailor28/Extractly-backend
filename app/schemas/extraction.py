"""Strict Pydantic schemas for AI invoice/receipt extraction."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DocumentType = Literal["invoice", "receipt", "unknown"]

HEADER_FIELD_NAMES = (
    "vendor_name",
    "vendor_address",
    "vendor_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "bill_to_name",
    "bill_to_address",
    "currency",
    "subtotal",
    "tax_amount",
    "tax_rate",
    "discount",
    "shipping",
    "total",
    "payment_terms",
    "bank_details",
)

LINE_ITEM_FIELD_NAMES = (
    "description",
    "quantity",
    "unit",
    "unit_price",
    "line_tax",
    "line_total",
)


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    width: float
    height: float

    @field_validator("x", "y", "width", "height")
    @classmethod
    def between_zero_and_one(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("Bounding box values must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> "BoundingBox":
        if self.x + self.width > 1.0 + 1e-9:
            raise ValueError("x + width must be <= 1")
        if self.y + self.height > 1.0 + 1e-9:
            raise ValueError("y + height must be <= 1")
        return self


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: Any = None
    confidence: float
    page_number: int = Field(..., ge=1)
    bbox: BoundingBox | None = None
    original_value: str | None = None
    original_format: str | None = None

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def empty_string_to_null(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == "":
            return None
        if isinstance(value, str) and value.strip().upper() in {"N/A", "NA", "UNKNOWN", "NULL"}:
            # Only treat as null if clearly placeholder — still allow literal if needed later
            return None
        return value


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: ExtractedField | None = None
    quantity: ExtractedField | None = None
    unit: ExtractedField | None = None
    unit_price: ExtractedField | None = None
    line_tax: ExtractedField | None = None
    line_total: ExtractedField | None = None


class DocumentExtraction(BaseModel):
    """Validated AI extraction payload."""

    model_config = ConfigDict(extra="ignore")

    document_type: DocumentType = "unknown"
    fields: dict[str, ExtractedField | None] = Field(default_factory=dict)
    line_items: list[LineItem] = Field(default_factory=list)
    overall_confidence: float

    @field_validator("overall_confidence")
    @classmethod
    def validate_overall_confidence(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("overall_confidence must be between 0 and 1")
        return value

    @model_validator(mode="before")
    @classmethod
    def coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = data.get("fields")
        if isinstance(fields, dict):
            normalized: dict[str, Any] = {}
            for key, value in fields.items():
                if value is None or isinstance(value, ExtractedField):
                    normalized[key] = value
                elif isinstance(value, dict):
                    normalized[key] = value
                else:
                    raise ValueError(f"Field '{key}' must be an object or null")
            data = {**data, "fields": normalized}
        return data


class ExtractionRunResponse(BaseModel):
    message: str
    document_id: str
    extraction_id: str
    document_type: DocumentType
    overall_confidence: float
    status: str
    fields: dict[str, ExtractedField | None]
    line_items: list[LineItem]


class ExtractionSummary(BaseModel):
    id: str
    document_type: DocumentType | str = "unknown"
    schema_version: str | None = None
    model: str | None = None
    overall_confidence: float | None = None
    latency_ms: int | None = None
    cost_usd: Decimal | None = None
    created_at: datetime | None = None


class DocumentExtractionDetailResponse(BaseModel):
    document_id: str
    extraction: ExtractionSummary
    fields: dict[str, Any] = Field(default_factory=dict)
    line_items: list[Any] = Field(default_factory=list)
    validation_flags: list[Any] = Field(default_factory=list)

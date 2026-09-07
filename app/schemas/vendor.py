"""Pydantic schemas for vendor management and learning."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HintSource = Literal["generated", "manual"]
HintPriority = Literal["high", "medium", "low"]
HintOperation = Literal["add", "update", "delete"]


class VendorHint(BaseModel):
    id: str
    type: str
    source: HintSource = "generated"
    field: str | None = None
    message: str
    priority: HintPriority = "medium"
    evidence_count: int = 0
    value: Any = None
    created_at: datetime | str | None = None


class VendorListItem(BaseModel):
    id: str
    name: str
    tax_id: str | None = None
    invoice_count: int = 0
    accuracy: float | None = None
    correction_rate: float | None = None
    correction_count: int = 0
    last_invoice_at: datetime | None = None
    hint_count: int = 0


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class VendorListResponse(BaseModel):
    items: list[VendorListItem]
    pagination: PaginationMeta


class VendorInfo(BaseModel):
    id: str
    name: str
    tax_id: str | None = None
    invoice_count: int = 0
    last_invoice_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class VendorStatistics(BaseModel):
    invoice_count: int = 0
    documents_reviewed: int = 0
    field_accuracy: float | None = None
    correction_rate: float | None = None
    correction_count: int = 0
    hint_count: int = 0


class FieldAccuracyItem(BaseModel):
    field_name: str
    accuracy: float | None = None
    correction_rate: float | None = None
    total_reviewed: int = 0
    corrected: int = 0


class VendorRecentDocument(BaseModel):
    id: str
    filename: str
    status: str
    created_at: datetime | None = None


class VendorDetailResponse(BaseModel):
    vendor: VendorInfo
    statistics: VendorStatistics
    field_accuracy: list[FieldAccuracyItem] = Field(default_factory=list)
    recent_documents: list[VendorRecentDocument] = Field(default_factory=list)
    hints: list[VendorHint] = Field(default_factory=list)


class VendorHintsResponse(BaseModel):
    vendor_id: str
    vendor_name: str
    hints: list[VendorHint] = Field(default_factory=list)


class VendorHintPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    type: str = "manual_instruction"
    field: str | None = None
    priority: HintPriority = "medium"
    message: str
    value: Any = None


class VendorHintUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: HintOperation
    hint: VendorHintPayload | None = None
    hint_id: str | None = None

    @field_validator("hint_id", mode="before")
    @classmethod
    def empty_to_none(cls, value: Any) -> Any:
        if value == "":
            return None
        return value


class VendorHintAnalysisResponse(BaseModel):
    vendor_id: str
    documents_analyzed: int
    corrections_analyzed: int
    hints_generated: int
    hints: list[VendorHint] = Field(default_factory=list)

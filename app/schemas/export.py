"""Pydantic schemas for export APIs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

ExportFormat = Literal["csv", "xlsx", "json"]


class ExportFilters(BaseModel):
    format: ExportFormat = "csv"
    status: Literal["approved"] = "approved"
    vendor_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    document_ids: list[str] | None = None
    include_line_items: bool = True


class ExportInvoiceHeader(BaseModel):
    document_id: str
    filename: str | None = None
    vendor_name: Any = None
    vendor_address: Any = None
    vendor_tax_id: Any = None
    invoice_number: Any = None
    invoice_date: Any = None
    due_date: Any = None
    po_number: Any = None
    bill_to_name: Any = None
    bill_to_address: Any = None
    currency: Any = None
    subtotal: Any = None
    tax_amount: Any = None
    tax_rate: Any = None
    discount: Any = None
    shipping: Any = None
    total: Any = None
    payment_terms: Any = None
    bank_details: Any = None
    status: str | None = None
    approved_at: datetime | str | None = None
    created_at: datetime | str | None = None


class ExportLineItem(BaseModel):
    document_id: str
    invoice_number: Any = None
    vendor_name: Any = None
    line_number: int
    description: Any = None
    quantity: Any = None
    unit: Any = None
    unit_price: Any = None
    line_tax: Any = None
    line_total: Any = None


class QuickBooksPreviewResponse(BaseModel):
    document_count: int
    row_count: int
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class ExportJsonMetadata(BaseModel):
    generated_at: datetime
    document_count: int
    filters: dict[str, Any] = Field(default_factory=dict)


class ExportJsonResponse(BaseModel):
    export_metadata: ExportJsonMetadata
    documents: list[dict[str, Any]] = Field(default_factory=list)

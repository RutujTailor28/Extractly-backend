"""QuickBooks-compatible CSV mapping (NOT an official QuickBooks API)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Any

from app.schemas.export import QuickBooksPreviewResponse
from app.services.document_service import DocumentServiceError
from app.services.export_service import (
    _cell_value,
    _require_client,
    build_export_rows,
    export_filename,
    fetch_approved_documents,
)
from app.utils.normalization import parse_decimal

logger = logging.getLogger(__name__)

QUICKBOOKS_COLUMNS = [
    "Vendor",
    "Bill No",
    "Bill Date",
    "Due Date",
    "PO Number",
    "Currency",
    "Subtotal",
    "Tax Amount",
    "Shipping",
    "Discount",
    "Total",
    "Payment Terms",
    "Description",
    "Quantity",
    "Rate",
    "Line Amount",
]

PREVIEW_MAX_ROWS = 100


def _money(value: Any) -> str:
    parsed = parse_decimal(value)
    if parsed is None:
        return ""
    return format(parsed, "f")


def map_to_quickbooks_rows(
    invoices: list[dict[str, Any]],
    line_items: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """One row per line item; invoice-level fields repeated. No line items → one row."""
    items_by_doc: dict[str, list[dict[str, Any]]] = {}
    for item in line_items:
        items_by_doc.setdefault(str(item["document_id"]), []).append(item)

    rows: list[dict[str, str]] = []
    for inv in invoices:
        doc_id = str(inv["document_id"])
        base = {
            "Vendor": _cell_value(inv.get("vendor_name")),
            "Bill No": _cell_value(inv.get("invoice_number")),
            "Bill Date": _cell_value(inv.get("invoice_date")),
            "Due Date": _cell_value(inv.get("due_date")),
            "PO Number": _cell_value(inv.get("po_number")),
            "Currency": _cell_value(inv.get("currency")),
            "Subtotal": _money(inv.get("subtotal")),
            "Tax Amount": _money(inv.get("tax_amount")),
            "Shipping": _money(inv.get("shipping")),
            "Discount": _money(inv.get("discount")),
            "Total": _money(inv.get("total")),
            "Payment Terms": _cell_value(inv.get("payment_terms")),
        }
        items = items_by_doc.get(doc_id) or []
        if not items:
            rows.append(
                {
                    **base,
                    "Description": "",
                    "Quantity": "",
                    "Rate": "",
                    "Line Amount": "",
                }
            )
            continue
        for item in items:
            rows.append(
                {
                    **base,
                    "Description": _cell_value(item.get("description")),
                    "Quantity": _money(item.get("quantity")),
                    "Rate": _money(item.get("unit_price")),
                    "Line Amount": _money(item.get("line_total")),
                }
            )
    return rows


def build_quickbooks_dataset(
    *,
    vendor_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    document_ids: list[str] | None = None,
    user_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    client = _require_client()
    documents = fetch_approved_documents(
        client,
        vendor_id=vendor_id,
        date_from=date_from,
        date_to=date_to,
        document_ids=document_ids,
        user_id=user_id,
    )
    if not documents:
        raise DocumentServiceError(
            "No approved documents found for the selected export filters.",
            status_code=404,
            code="NO_DOCUMENTS_FOUND",
        )
    invoices, line_items = build_export_rows(client, documents)
    rows = map_to_quickbooks_rows(invoices, line_items)
    return invoices, rows


def generate_quickbooks_csv(
    *,
    vendor_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    document_ids: list[str] | None = None,
    user_id: str | None = None,
) -> tuple[bytes, str, str]:
    """
    QuickBooks-compatible CSV mapping only — not an official QuickBooks API.
    """
    _invoices, rows = build_quickbooks_dataset(
        vendor_id=vendor_id,
        date_from=date_from,
        date_to=date_to,
        document_ids=document_ids,
        user_id=user_id,
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=QUICKBOOKS_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    content = buffer.getvalue().encode("utf-8-sig")
    today = date.today().isoformat()
    filename = f"extractly_quickbooks_compatible_{today}.csv"
    return content, "text/csv; charset=utf-8", filename


def preview_quickbooks(
    *,
    vendor_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    document_ids: list[str] | None = None,
    user_id: str | None = None,
) -> QuickBooksPreviewResponse:
    invoices, rows = build_quickbooks_dataset(
        vendor_id=vendor_id,
        date_from=date_from,
        date_to=date_to,
        document_ids=document_ids,
        user_id=user_id,
    )
    limited = rows[:PREVIEW_MAX_ROWS]
    return QuickBooksPreviewResponse(
        document_count=len(invoices),
        row_count=len(limited),
        columns=list(QUICKBOOKS_COLUMNS),
        rows=limited,
    )

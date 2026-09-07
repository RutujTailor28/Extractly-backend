"""Export service — CSV / XLSX / JSON from approved effective invoice data."""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.schemas.extraction import HEADER_FIELD_NAMES, LINE_ITEM_FIELD_NAMES
from app.services.document_service import DocumentServiceError
from app.services.review_data_service import (
    build_effective_extraction,
    build_review_line_items,
)
from app.utils.normalization import field_value, parse_decimal

logger = logging.getLogger(__name__)

INVOICE_CSV_COLUMNS = [
    "document_id",
    "filename",
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
    "status",
    "approved_at",
    "created_at",
]

LINE_ITEM_CSV_COLUMNS = [
    "document_id",
    "invoice_number",
    "vendor_name",
    "line_number",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "line_tax",
    "line_total",
]

MONEY_FIELDS = {
    "subtotal",
    "tax_amount",
    "tax_rate",
    "discount",
    "shipping",
    "total",
    "unit_price",
    "line_tax",
    "line_total",
}


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def _decimal_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    parsed = parse_decimal(value)
    if parsed is None:
        return str(value)
    return format(parsed, "f")


def _cell_value(value: Any, *, money: bool = False) -> Any:
    if value is None:
        return ""
    if money:
        parsed = parse_decimal(value)
        return "" if parsed is None else format(parsed, "f")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def validate_date_range(date_from: date | None, date_to: date | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise DocumentServiceError(
            "Invalid date range: date_from must be <= date_to",
            status_code=422,
            code="INVALID_DATE_RANGE",
        )
    if date_from and date_to:
        delta = (date_to - date_from).days
        if delta > settings.EXPORT_MAX_DATE_RANGE_DAYS:
            raise DocumentServiceError(
                f"Date range exceeds maximum of {settings.EXPORT_MAX_DATE_RANGE_DAYS} days",
                status_code=422,
                code="INVALID_DATE_RANGE",
            )


def parse_document_ids(raw: str | None) -> list[str] | None:
    if raw is None or raw.strip() == "":
        return None
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    return ids or None


def fetch_approved_documents(
    client,
    *,
    vendor_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    document_ids: list[str] | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load approved documents filtered by approved_at (and optional IDs)."""
    validate_date_range(date_from, date_to)

    if document_ids:
        # Explicit selection — validate all exist, approved, ownership
        found: list[dict[str, Any]] = []
        for doc_id in document_ids:
            response = (
                client.table("documents")
                .select("*")
                .eq("id", doc_id)
                .limit(1)
                .execute()
            )
            if not response.data:
                raise DocumentServiceError(
                    f"Document not found: {doc_id}",
                    status_code=404,
                    code="DOCUMENT_NOT_FOUND",
                )
            doc = response.data[0]
            if user_id is not None and doc.get("user_id") != user_id:
                raise DocumentServiceError(
                    f"Document access denied: {doc_id}",
                    status_code=403,
                    code="DOCUMENT_ACCESS_DENIED",
                )
            if user_id is None and doc.get("user_id") is not None:
                # Demo mode: allow null-user documents only when no auth
                pass
            if doc.get("status") != "approved":
                raise DocumentServiceError(
                    f"Document is not approved: {doc_id}",
                    status_code=422,
                    code="DOCUMENT_NOT_APPROVED",
                )
            if vendor_id and doc.get("vendor_id") != vendor_id:
                raise DocumentServiceError(
                    f"Document does not match vendor filter: {doc_id}",
                    status_code=422,
                    code="DOCUMENT_VENDOR_MISMATCH",
                )
            found.append(doc)

        # Apply date filters if provided
        if date_from or date_to:
            filtered = []
            for doc in found:
                approved_at = doc.get("approved_at")
                if not approved_at:
                    continue
                day = str(approved_at)[:10]
                if date_from and day < date_from.isoformat():
                    continue
                if date_to and day > date_to.isoformat():
                    continue
                filtered.append(doc)
            found = filtered
        return found

    query = (
        client.table("documents")
        .select("*")
        .eq("status", "approved")
        .order("approved_at", desc=True)
        .limit(settings.EXPORT_MAX_DOCUMENTS + 1)
    )
    if vendor_id:
        query = query.eq("vendor_id", vendor_id)
    if user_id is not None:
        query = query.eq("user_id", user_id)
    else:
        query = query.is_("user_id", "null")
    if date_from:
        query = query.gte("approved_at", f"{date_from.isoformat()}T00:00:00+00:00")
    if date_to:
        query = query.lte("approved_at", f"{date_to.isoformat()}T23:59:59.999999+00:00")

    response = query.execute()
    rows = response.data or []
    if len(rows) > settings.EXPORT_MAX_DOCUMENTS:
        raise DocumentServiceError(
            "Export limit exceeded. Please narrow your filters.",
            status_code=422,
            code="EXPORT_LIMIT_EXCEEDED",
        )
    return rows


def build_export_rows(
    client,
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build invoice headers + line items from effective extraction data."""
    invoices: list[dict[str, Any]] = []
    line_items: list[dict[str, Any]] = []

    for doc in documents:
        doc_id = str(doc["id"])
        extraction_resp = (
            client.table("extractions")
            .select("*")
            .eq("document_id", doc_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not extraction_resp.data:
            raise DocumentServiceError(
                f"Extraction not found for approved document {doc_id}",
                status_code=422,
                code="EXTRACTION_NOT_FOUND",
            )
        effective = build_effective_extraction(
            client,
            document_id=doc_id,
            extraction_row=extraction_resp.data[0],
        )
        fields = effective.get("fields") or {}
        header = {
            "document_id": doc_id,
            "filename": doc.get("filename"),
            "status": doc.get("status"),
            "approved_at": doc.get("approved_at"),
            "created_at": doc.get("created_at"),
        }
        for name in HEADER_FIELD_NAMES:
            header[name] = field_value(fields, name)
        invoices.append(header)

        flat_items = build_review_line_items(effective)
        invoice_number = header.get("invoice_number")
        vendor_name = header.get("vendor_name")
        for item in flat_items:
            row = {
                "document_id": doc_id,
                "invoice_number": invoice_number,
                "vendor_name": vendor_name,
                "line_number": int(item.get("index", 0)) + 1,
            }
            for name in LINE_ITEM_FIELD_NAMES:
                row[name] = item.get(name)
            line_items.append(row)

    return invoices, line_items


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        out = {}
        for col in columns:
            value = row.get(col)
            money = col in MONEY_FIELDS
            cell = _cell_value(value, money=money)
            out[col] = "" if cell is None else cell
        writer.writerow(out)
    return buffer.getvalue().encode("utf-8-sig")


def _zip_csv_bytes(
    invoices: list[dict[str, Any]],
    line_items: list[dict[str, Any]],
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("invoices.csv", _csv_bytes(invoices, INVOICE_CSV_COLUMNS))
        zf.writestr("line_items.csv", _csv_bytes(line_items, LINE_ITEM_CSV_COLUMNS))
    return buffer.getvalue()


def _xlsx_bytes(
    invoices: list[dict[str, Any]],
    line_items: list[dict[str, Any]],
    *,
    include_line_items: bool,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(INVOICE_CSV_COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(INVOICE_CSV_COLUMNS))}1"

    for row in invoices:
        values = []
        for col in INVOICE_CSV_COLUMNS:
            raw = row.get(col)
            if col in MONEY_FIELDS:
                parsed = parse_decimal(raw)
                values.append(float(parsed) if parsed is not None else None)
            elif col in {"invoice_date", "due_date"} and raw:
                values.append(str(raw)[:10])
            else:
                values.append(raw if raw is not None else None)
        ws.append(values)

    for idx, col in enumerate(INVOICE_CSV_COLUMNS, start=1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = min(max(len(col) + 2, 12), 28)
        if col in MONEY_FIELDS:
            for cell in ws[letter][1:]:
                cell.number_format = "0.00"

    if include_line_items:
        ws2 = wb.create_sheet("Line Items")
        ws2.append(LINE_ITEM_CSV_COLUMNS)
        for cell in ws2[1]:
            cell.font = Font(bold=True)
        ws2.freeze_panes = "A2"
        ws2.auto_filter.ref = f"A1:{get_column_letter(len(LINE_ITEM_CSV_COLUMNS))}1"
        for row in line_items:
            values = []
            for col in LINE_ITEM_CSV_COLUMNS:
                raw = row.get(col)
                if col in MONEY_FIELDS or col == "quantity":
                    parsed = parse_decimal(raw)
                    if col == "quantity":
                        values.append(float(parsed) if parsed is not None else None)
                    else:
                        values.append(float(parsed) if parsed is not None else None)
                else:
                    values.append(raw if raw is not None else None)
            ws2.append(values)
        for idx, col in enumerate(LINE_ITEM_CSV_COLUMNS, start=1):
            letter = get_column_letter(idx)
            ws2.column_dimensions[letter].width = min(max(len(col) + 2, 12), 28)
            if col in MONEY_FIELDS:
                for cell in ws2[letter][1:]:
                    cell.number_format = "0.00"
            if col == "quantity":
                for cell in ws2[letter][1:]:
                    cell.number_format = "0.####"

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _json_payload(
    invoices: list[dict[str, Any]],
    line_items: list[dict[str, Any]],
    *,
    include_line_items: bool,
    filters: dict[str, Any],
) -> dict[str, Any]:
    items_by_doc: dict[str, list[dict[str, Any]]] = {}
    for item in line_items:
        items_by_doc.setdefault(item["document_id"], []).append(item)

    documents = []
    for inv in invoices:
        doc_id = inv["document_id"]
        line_payload = []
        if include_line_items:
            for item in items_by_doc.get(doc_id, []):
                line_payload.append(
                    {
                        "line_number": item.get("line_number"),
                        "description": _json_safe(item.get("description")),
                        "quantity": _json_safe(parse_decimal(item.get("quantity"))),
                        "unit": _json_safe(item.get("unit")),
                        "unit_price": _json_safe(parse_decimal(item.get("unit_price"))),
                        "line_tax": _json_safe(parse_decimal(item.get("line_tax"))),
                        "line_total": _json_safe(parse_decimal(item.get("line_total"))),
                    }
                )
        documents.append(
            {
                "document_id": doc_id,
                "filename": inv.get("filename"),
                "status": inv.get("status"),
                "approved_at": _json_safe(inv.get("approved_at")),
                "created_at": _json_safe(inv.get("created_at")),
                "invoice": {
                    "vendor": {
                        "name": _json_safe(inv.get("vendor_name")),
                        "address": _json_safe(inv.get("vendor_address")),
                        "tax_id": _json_safe(inv.get("vendor_tax_id")),
                    },
                    "invoice_number": _json_safe(inv.get("invoice_number")),
                    "invoice_date": _json_safe(inv.get("invoice_date")),
                    "due_date": _json_safe(inv.get("due_date")),
                    "po_number": _json_safe(inv.get("po_number")),
                    "bill_to_name": _json_safe(inv.get("bill_to_name")),
                    "bill_to_address": _json_safe(inv.get("bill_to_address")),
                    "currency": _json_safe(inv.get("currency")),
                    "payment_terms": _json_safe(inv.get("payment_terms")),
                    "bank_details": _json_safe(inv.get("bank_details")),
                    "amounts": {
                        "subtotal": _json_safe(parse_decimal(inv.get("subtotal"))),
                        "tax": _json_safe(parse_decimal(inv.get("tax_amount"))),
                        "tax_rate": _json_safe(parse_decimal(inv.get("tax_rate"))),
                        "discount": _json_safe(parse_decimal(inv.get("discount"))),
                        "shipping": _json_safe(parse_decimal(inv.get("shipping"))),
                        "total": _json_safe(parse_decimal(inv.get("total"))),
                    },
                    "line_items": line_payload,
                },
            }
        )

    return {
        "export_metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "document_count": len(documents),
            "filters": filters,
        },
        "documents": documents,
    }


def export_filename(
    *,
    format: str,
    date_from: date | None,
    date_to: date | None,
    zipped: bool = False,
) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    if zipped:
        if date_from and date_to:
            return f"extractly_export_{date_from.isoformat()}_to_{date_to.isoformat()}.zip"
        return f"extractly_export_{today}.zip"
    if format == "json":
        return f"extractly_invoices_{today}.json"
    if format == "xlsx":
        return f"extractly_invoices_{today}.xlsx"
    return f"extractly_invoices_{today}.csv"


def generate_export(
    *,
    format: str = "csv",
    status: str = "approved",
    vendor_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    document_ids: list[str] | None = None,
    include_line_items: bool = True,
    user_id: str | None = None,
) -> tuple[bytes, str, str]:
    """
    Returns (content_bytes, media_type, filename).

    Date filtering uses documents.approved_at.
    """
    if format not in {"csv", "xlsx", "json"}:
        raise DocumentServiceError(
            f"Unsupported export format '{format}'",
            status_code=422,
            code="UNSUPPORTED_EXPORT_FORMAT",
        )
    if status != "approved":
        raise DocumentServiceError(
            "Only approved documents can be exported",
            status_code=422,
            code="DOCUMENT_NOT_APPROVED",
        )

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
    filters = {
        "format": format,
        "status": status,
        "vendor_id": vendor_id,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "document_ids": document_ids,
        "include_line_items": include_line_items,
    }

    if format == "csv":
        if include_line_items:
            content = _zip_csv_bytes(invoices, line_items)
            filename = export_filename(
                format=format, date_from=date_from, date_to=date_to, zipped=True
            )
            return content, "application/zip", filename
        content = _csv_bytes(invoices, INVOICE_CSV_COLUMNS)
        filename = export_filename(format=format, date_from=date_from, date_to=date_to)
        return content, "text/csv; charset=utf-8", filename

    if format == "xlsx":
        content = _xlsx_bytes(
            invoices, line_items, include_line_items=include_line_items
        )
        filename = export_filename(format=format, date_from=date_from, date_to=date_to)
        return (
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename,
        )

    payload = _json_payload(
        invoices,
        line_items,
        include_line_items=include_line_items,
        filters=filters,
    )
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = export_filename(format=format, date_from=date_from, date_to=date_to)
    return content, "application/json", filename

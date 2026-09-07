"""Vendor CRUD, association, and listing."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from app.core.database import get_supabase_admin_client
from app.schemas.vendor import (
    FieldAccuracyItem,
    PaginationMeta,
    VendorDetailResponse,
    VendorHintsResponse,
    VendorInfo,
    VendorListItem,
    VendorListResponse,
    VendorRecentDocument,
    VendorStatistics,
)
from app.services.document_service import DocumentServiceError
from app.utils.normalization import field_value, normalize_vendor_name

logger = logging.getLogger(__name__)


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def fetch_vendor(client, vendor_id: str) -> dict:
    response = (
        client.table("vendors")
        .select("*")
        .eq("id", vendor_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Vendor not found", status_code=404)
    return response.data[0]


def get_hints_from_vendor(vendor: dict) -> list[dict[str, Any]]:
    payload = vendor.get("template_hints_json") or {}
    if not isinstance(payload, dict):
        return []
    hints = payload.get("hints") or []
    return hints if isinstance(hints, list) else []


def recount_vendor_invoices(client, vendor_id: str) -> int:
    response = (
        client.table("documents")
        .select("id,created_at")
        .eq("vendor_id", vendor_id)
        .execute()
    )
    rows = response.data or []
    count = len(rows)
    last_at = None
    for row in rows:
        created = row.get("created_at")
        if created and (last_at is None or str(created) > str(last_at)):
            last_at = created
    client.table("vendors").update(
        {
            "invoice_count": count,
            "last_invoice_at": last_at,
        }
    ).eq("id", vendor_id).execute()
    return count


def find_or_create_vendor(
    client,
    *,
    vendor_name: str,
    tax_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    normalized = normalize_vendor_name(vendor_name)
    if not normalized:
        raise DocumentServiceError("Vendor name is required", status_code=400)

    query = client.table("vendors").select("*").eq("name_normalized", normalized)
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.is_("user_id", "null")
    existing = query.limit(1).execute()
    if existing.data:
        vendor = existing.data[0]
        updates: dict[str, Any] = {}
        if tax_id and not vendor.get("tax_id"):
            updates["tax_id"] = tax_id
        if updates:
            updated = (
                client.table("vendors")
                .update(updates)
                .eq("id", vendor["id"])
                .execute()
            )
            if updated.data:
                return updated.data[0]
        return vendor

    display_name = " ".join(str(vendor_name).strip().split())
    row = {
        "name": display_name,
        "name_normalized": normalized,
        "tax_id": tax_id,
        "user_id": user_id,
        "invoice_count": 0,
        "template_hints_json": {"version": 1, "hints": []},
    }
    inserted = client.table("vendors").insert(row).execute()
    if not inserted.data:
        raise DocumentServiceError("Failed to create vendor", status_code=500)
    return inserted.data[0]


def associate_document_vendor(
    client,
    *,
    document_id: str,
    document: dict[str, Any],
    extraction: Any,
) -> dict | None:
    """
    Link document to vendor from extracted vendor_name.
    Returns vendor row or None if no vendor name.
    """
    fields = getattr(extraction, "fields", None) or {}
    if isinstance(fields, dict):
        name_entry = fields.get("vendor_name")
        tax_entry = fields.get("vendor_tax_id")
    else:
        return None

    vendor_name = None
    if name_entry is not None:
        vendor_name = getattr(name_entry, "value", None)
        if vendor_name is None and isinstance(name_entry, dict):
            vendor_name = name_entry.get("value")

    tax_id = None
    if tax_entry is not None:
        tax_id = getattr(tax_entry, "value", None)
        if tax_id is None and isinstance(tax_entry, dict):
            tax_id = tax_entry.get("value")

    if not vendor_name or not str(vendor_name).strip():
        return None

    previous_vendor_id = document.get("vendor_id")
    vendor = find_or_create_vendor(
        client,
        vendor_name=str(vendor_name),
        tax_id=str(tax_id) if tax_id else None,
        user_id=document.get("user_id"),
    )
    client.table("documents").update({"vendor_id": vendor["id"]}).eq(
        "id", document_id
    ).execute()

    recount_vendor_invoices(client, str(vendor["id"]))
    if previous_vendor_id and previous_vendor_id != vendor["id"]:
        recount_vendor_invoices(client, str(previous_vendor_id))
    return vendor


def load_vendor_hints_for_document(client, document: dict[str, Any]) -> dict[str, Any] | None:
    """Load formatted hint payload for extraction prompt from linked vendor."""
    vendor_id = document.get("vendor_id")
    if not vendor_id:
        return None
    try:
        vendor = fetch_vendor(client, str(vendor_id))
    except DocumentServiceError:
        return None
    hints = get_hints_from_vendor(vendor)
    if not hints:
        return None
    return {
        "vendor_id": vendor["id"],
        "vendor_name": vendor.get("name"),
        "hints": hints,
    }


def format_hints_for_prompt(hint_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not hint_payload:
        return None
    hints = hint_payload.get("hints") or []
    lines = []
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        msg = hint.get("message")
        if msg:
            lines.append(f"- {msg}")
    if not lines:
        return None
    return {
        "vendor_name": hint_payload.get("vendor_name"),
        "guidance": lines,
        "instructions": [
            "Use these as hints only.",
            "Do not invent values based on hints.",
            "Always extract from visible document evidence.",
        ],
    }


def list_vendors(
    *,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> VendorListResponse:
    from app.services.accuracy_service import compute_vendor_quality_metrics_batch

    client = _require_client()
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    allowed_sort = {"name", "invoice_count", "created_at", "last_invoice_at"}
    if sort_by not in allowed_sort:
        sort_by = "name"
    if sort_order not in {"asc", "desc"}:
        sort_order = "asc"

    try:
        query = client.table("vendors").select("*", count="exact")
        if search:
            query = query.ilike("name", f"%{search.strip()}%")
        query = query.order(sort_by, desc=(sort_order == "desc"))
        start = (page - 1) * page_size
        end = start + page_size - 1
        response = query.range(start, end).execute()
    except Exception as exc:
        logger.exception("Failed to list vendors from database")
        raise DocumentServiceError(
            "Unable to load vendors right now. Please retry.",
            status_code=503,
        ) from exc

    rows = response.data or []
    total = response.count if response.count is not None else len(rows)
    vendor_ids = [str(row["id"]) for row in rows]

    metrics_by_id: dict[str, dict] = {}
    try:
        metrics_by_id = compute_vendor_quality_metrics_batch(client, vendor_ids)
    except Exception as exc:
        logger.warning("Vendor list metrics skipped: %s", exc)

    items: list[VendorListItem] = []
    for row in rows:
        vid = str(row["id"])
        metrics = metrics_by_id.get(vid) or {}
        hints = get_hints_from_vendor(row)
        items.append(
            VendorListItem(
                id=vid,
                name=row.get("name") or "",
                tax_id=row.get("tax_id"),
                invoice_count=int(row.get("invoice_count") or 0),
                accuracy=metrics.get("accuracy"),
                correction_rate=metrics.get("correction_rate"),
                correction_count=int(metrics.get("correction_count") or 0),
                last_invoice_at=row.get("last_invoice_at"),
                hint_count=len(hints),
            )
        )

    total_pages = math.ceil(total / page_size) if page_size else 0
    return VendorListResponse(
        items=items,
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        ),
    )


def get_vendor_detail(vendor_id: str) -> VendorDetailResponse:
    from app.services.accuracy_service import (
        compute_vendor_field_accuracy,
        compute_vendor_quality_metrics,
    )
    from app.schemas.vendor import VendorHint

    client = _require_client()
    vendor = fetch_vendor(client, vendor_id)
    try:
        metrics = compute_vendor_quality_metrics(client, vendor_id)
        field_acc = compute_vendor_field_accuracy(client, vendor_id)
    except Exception as exc:
        logger.warning("Vendor detail metrics failed for %s: %s", vendor_id, exc)
        metrics = {
            "accuracy": None,
            "correction_rate": None,
            "coverage": None,
            "correction_count": 0,
            "documents_reviewed": 0,
        }
        field_acc = []
    hints = [VendorHint.model_validate(h) for h in get_hints_from_vendor(vendor) if isinstance(h, dict)]

    docs = (
        client.table("documents")
        .select("id,filename,status,created_at")
        .eq("vendor_id", vendor_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    recent = [
        VendorRecentDocument(
            id=str(d["id"]),
            filename=d.get("filename") or "",
            status=d.get("status") or "",
            created_at=d.get("created_at"),
        )
        for d in (docs.data or [])
    ]

    reviewed = int(metrics.get("documents_reviewed") or 0)
    return VendorDetailResponse(
        vendor=VendorInfo(
            id=str(vendor["id"]),
            name=vendor.get("name") or "",
            tax_id=vendor.get("tax_id"),
            invoice_count=int(vendor.get("invoice_count") or 0),
            last_invoice_at=vendor.get("last_invoice_at"),
            created_at=vendor.get("created_at"),
            updated_at=vendor.get("updated_at"),
        ),
        statistics=VendorStatistics(
            invoice_count=int(vendor.get("invoice_count") or 0),
            documents_reviewed=reviewed,
            field_accuracy=metrics.get("accuracy"),
            correction_rate=metrics.get("correction_rate"),
            correction_count=int(metrics.get("correction_count") or 0),
            hint_count=len(hints),
        ),
        field_accuracy=[
            FieldAccuracyItem(**item) for item in field_acc
        ],
        recent_documents=recent,
        hints=hints,
    )


def get_vendor_hints(
    vendor_id: str,
    *,
    source: str | None = None,
) -> VendorHintsResponse:
    from app.schemas.vendor import VendorHint

    client = _require_client()
    vendor = fetch_vendor(client, vendor_id)
    hints = get_hints_from_vendor(vendor)
    if source in {"generated", "manual"}:
        hints = [h for h in hints if isinstance(h, dict) and h.get("source") == source]
    return VendorHintsResponse(
        vendor_id=str(vendor["id"]),
        vendor_name=vendor.get("name") or "",
        hints=[VendorHint.model_validate(h) for h in hints if isinstance(h, dict)],
    )

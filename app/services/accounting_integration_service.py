"""Mock accounting integration — payload preview, send, history, idempotency."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.database import get_supabase_admin_client
from app.schemas.integration import (
    IntegrationHistoryItem,
    IntegrationHistoryResponse,
    IntegrationPreviewResponse,
    IntegrationResultResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.export_service import build_export_rows
from app.utils.normalization import parse_decimal

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = {"mock"}


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def _fetch_document(client, document_id: str) -> dict:
    response = (
        client.table("documents")
        .select("*")
        .eq("id", document_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError(
            "Document not found",
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
        )
    return response.data[0]


def _assert_approved(document: dict) -> None:
    if document.get("status") != "approved":
        raise DocumentServiceError(
            "Document must be approved before accounting integration",
            status_code=422,
            code="DOCUMENT_NOT_APPROVED",
        )


def _json_money(value: Any) -> float | None:
    parsed = parse_decimal(value)
    if parsed is None:
        return None
    return float(parsed)


def build_accounting_payload(
    client,
    *,
    document: dict[str, Any],
    provider: str = "mock",
) -> dict[str, Any]:
    doc_id = str(document["id"])
    invoices, line_items = build_export_rows(client, [document])
    inv = invoices[0]
    items = [
        {
            "description": item.get("description"),
            "quantity": _json_money(item.get("quantity")),
            "unit_price": _json_money(item.get("unit_price")),
            "tax": _json_money(item.get("line_tax")),
            "total": _json_money(item.get("line_total")),
        }
        for item in line_items
    ]

    return {
        "provider": provider,
        "invoice": {
            "external_reference": doc_id,
            "vendor": {
                "name": inv.get("vendor_name"),
                "tax_id": inv.get("vendor_tax_id"),
                "address": inv.get("vendor_address"),
            },
            "invoice_number": inv.get("invoice_number"),
            "invoice_date": inv.get("invoice_date"),
            "due_date": inv.get("due_date"),
            "currency": inv.get("currency"),
            "payment_terms": inv.get("payment_terms"),
            "line_items": items,
            "subtotal": _json_money(inv.get("subtotal")),
            "tax_amount": _json_money(inv.get("tax_amount")),
            "discount": _json_money(inv.get("discount")),
            "shipping": _json_money(inv.get("shipping")),
            "total": _json_money(inv.get("total")),
        },
    }


def validate_accounting_payload(payload: dict[str, Any]) -> None:
    invoice = payload.get("invoice") or {}
    if not invoice.get("vendor") or not (invoice.get("vendor") or {}).get("name"):
        raise DocumentServiceError(
            "Accounting payload missing vendor name",
            status_code=422,
            code="INVALID_ACCOUNTING_PAYLOAD",
        )
    if invoice.get("total") is None:
        raise DocumentServiceError(
            "Accounting payload missing total",
            status_code=422,
            code="INVALID_ACCOUNTING_PAYLOAD",
        )


def get_successful_integration(
    client,
    *,
    document_id: str,
    provider: str,
) -> dict | None:
    response = (
        client.table("integration_logs")
        .select("*")
        .eq("document_id", document_id)
        .eq("provider", provider)
        .eq("status", "success")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def preview_integration(
    document_id: str,
    *,
    provider: str = "mock",
) -> IntegrationPreviewResponse:
    if provider not in SUPPORTED_PROVIDERS:
        raise DocumentServiceError(
            f"Unsupported integration provider '{provider}'",
            status_code=422,
            code="INTEGRATION_PROVIDER_NOT_SUPPORTED",
        )
    client = _require_client()
    document = _fetch_document(client, document_id)
    _assert_approved(document)
    payload = build_accounting_payload(client, document=document, provider=provider)
    validate_accounting_payload(payload)
    return IntegrationPreviewResponse(
        provider=provider,
        document_id=document_id,
        ready=True,
        payload=payload,
    )


def send_to_accounting(
    document_id: str,
    *,
    provider: str = "mock",
) -> IntegrationResultResponse:
    """
    Mock accounting send with idempotency on (document_id, provider, success).

    Clearly labeled as mock — does not contact real QuickBooks/Xero/etc.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise DocumentServiceError(
            f"Unsupported integration provider '{provider}'",
            status_code=422,
            code="INTEGRATION_PROVIDER_NOT_SUPPORTED",
        )

    client = _require_client()
    document = _fetch_document(client, document_id)
    _assert_approved(document)

    existing = get_successful_integration(
        client, document_id=document_id, provider=provider
    )
    if existing:
        return IntegrationResultResponse(
            success=True,
            provider=provider,
            document_id=document_id,
            external_reference=existing.get("external_reference"),
            message=(
                "Invoice was already sent successfully to the mock accounting system. "
                "Returning existing integration result (idempotent)."
            ),
            sent_at=existing.get("created_at"),
            already_sent=True,
        )

    payload = build_accounting_payload(client, document=document, provider=provider)
    validate_accounting_payload(payload)

    now = datetime.now(timezone.utc)
    external_ref = f"MOCK-ACC-{uuid.uuid4().hex[:8].upper()}"
    response_payload = {
        "success": True,
        "provider": "mock",
        "external_reference": external_ref,
        "message": "Invoice accepted by mock accounting system.",
        "received_at": now.isoformat(),
    }

    # Simulate external API request (no network)
    logger.info(
        "Mock accounting send for document %s → %s",
        document_id,
        external_ref,
    )

    log_row = {
        "user_id": document.get("user_id"),
        "document_id": document_id,
        "provider": provider,
        "status": "success",
        "request_payload_json": payload,
        "response_payload_json": response_payload,
        "external_reference": external_ref,
        "error_message": None,
    }
    inserted = client.table("integration_logs").insert(log_row).execute()
    if not inserted.data:
        raise DocumentServiceError(
            "Failed to record integration log",
            status_code=500,
        )

    return IntegrationResultResponse(
        success=True,
        provider=provider,
        document_id=document_id,
        external_reference=external_ref,
        message="Invoice successfully sent to mock accounting system.",
        sent_at=now,
        already_sent=False,
    )


def get_integration_history(document_id: str) -> IntegrationHistoryResponse:
    client = _require_client()
    _fetch_document(client, document_id)
    response = (
        client.table("integration_logs")
        .select("id,provider,status,external_reference,error_message,created_at")
        .eq("document_id", document_id)
        .order("created_at", desc=True)
        .execute()
    )
    items = [
        IntegrationHistoryItem(
            id=str(row["id"]),
            provider=row["provider"],
            status=row["status"],
            external_reference=row.get("external_reference"),
            error_message=row.get("error_message"),
            created_at=row.get("created_at"),
        )
        for row in (response.data or [])
    ]
    return IntegrationHistoryResponse(document_id=document_id, integrations=items)

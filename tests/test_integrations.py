"""Tests for Phase 10 mock accounting integrations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.accounting_integration_service import (
    build_accounting_payload,
    send_to_accounting,
    validate_accounting_payload,
)
from app.services.document_service import DocumentServiceError


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _chain(data=None):
    q = MagicMock()
    for name in ("select", "eq", "order", "limit", "update", "insert", "is_", "in_"):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(data=data if data is not None else [])
    return q


def _doc(**overrides):
    base = {
        "id": "doc-1",
        "filename": "inv.pdf",
        "status": "approved",
        "user_id": None,
        "approved_at": "2026-09-03T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_validate_payload_requires_vendor_and_total() -> None:
    with pytest.raises(DocumentServiceError):
        validate_accounting_payload({"provider": "mock", "invoice": {}})


def test_preview_rejects_unapproved(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([_doc(status="needs_review")])
    with patch(
        "app.services.accounting_integration_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents/doc-1/integration-preview")
    assert response.status_code == 422


def test_preview_and_send_and_idempotency(client: TestClient) -> None:
    doc = _doc()
    invoice_row = {
        "document_id": "doc-1",
        "filename": "inv.pdf",
        "vendor_name": "Acme",
        "vendor_tax_id": None,
        "vendor_address": None,
        "invoice_number": "INV-1",
        "invoice_date": "2026-09-01",
        "due_date": None,
        "currency": "USD",
        "payment_terms": None,
        "subtotal": "50",
        "tax_amount": "5",
        "discount": "0",
        "shipping": "0",
        "total": "55",
        "status": "approved",
        "approved_at": "2026-09-03T00:00:00+00:00",
        "created_at": "2026-09-01T00:00:00+00:00",
    }
    line_rows = [
        {
            "document_id": "doc-1",
            "description": "Paper",
            "quantity": "10",
            "unit_price": "5",
            "line_tax": "5",
            "line_total": "55",
            "line_number": 1,
        }
    ]

    mock_client = MagicMock()
    log_calls = {"n": 0}

    def table(name):
        if name == "documents":
            return _chain([doc])
        if name == "integration_logs":
            log_calls["n"] += 1
            q = MagicMock()
            for method in ("select", "eq", "order", "limit", "insert"):
                getattr(q, method).return_value = q
            if log_calls["n"] == 1:
                # get_successful_integration → none yet
                q.execute.return_value = MagicMock(data=[])
            else:
                # insert log
                q.execute.return_value = MagicMock(
                    data=[
                        {
                            "id": "log-1",
                            "external_reference": "MOCK-ACC-AAAA",
                            "created_at": "2026-09-03T12:00:00+00:00",
                        }
                    ]
                )
            return q
        return _chain([])

    mock_client.table.side_effect = table

    with (
        patch(
            "app.services.accounting_integration_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.accounting_integration_service.build_export_rows",
            return_value=([invoice_row], line_rows),
        ),
    ):
        preview = client.get("/api/v1/documents/doc-1/integration-preview")
        send = client.post(
            "/api/v1/documents/doc-1/send-to-accounting",
            json={"provider": "mock"},
        )

    assert preview.status_code == 200
    assert preview.json()["ready"] is True
    assert preview.json()["payload"]["invoice"]["total"] == pytest.approx(55.0)
    assert "raw_response" not in json_dumps(preview.json())

    assert send.status_code == 200, send.text
    assert send.json()["success"] is True
    assert send.json()["already_sent"] is False
    assert send.json()["external_reference"].startswith("MOCK-ACC-")

    # Idempotency: existing success returned
    existing = {
        "id": "log-1",
        "external_reference": "MOCK-ACC-EXISTING",
        "created_at": "2026-09-03T12:00:00+00:00",
        "provider": "mock",
        "status": "success",
    }
    mock_client2 = MagicMock()

    def table2(name):
        if name == "documents":
            return _chain([doc])
        if name == "integration_logs":
            return _chain([existing])
        return _chain([])

    mock_client2.table.side_effect = table2
    with (
        patch(
            "app.services.accounting_integration_service.get_supabase_admin_client",
            return_value=mock_client2,
        ),
        patch(
            "app.services.accounting_integration_service.build_export_rows",
            return_value=([invoice_row], line_rows),
        ),
    ):
        again = client.post(
            "/api/v1/documents/doc-1/send-to-accounting",
            json={"provider": "mock"},
        )
    assert again.status_code == 200
    assert again.json()["already_sent"] is True
    assert again.json()["external_reference"] == "MOCK-ACC-EXISTING"


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj)


def test_unsupported_provider(client: TestClient) -> None:
    response = client.post(
        "/api/v1/documents/doc-1/send-to-accounting",
        json={"provider": "quickbooks"},
    )
    assert response.status_code == 422


def test_integration_history(client: TestClient) -> None:
    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain([_doc()])
        if name == "integration_logs":
            return _chain(
                [
                    {
                        "id": "log-1",
                        "provider": "mock",
                        "status": "success",
                        "external_reference": "MOCK-ACC-1",
                        "error_message": None,
                        "created_at": "2026-09-03T12:00:00+00:00",
                    }
                ]
            )
        return _chain([])

    mock_client.table.side_effect = table
    with patch(
        "app.services.accounting_integration_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents/doc-1/integration-history")
    assert response.status_code == 200
    assert response.json()["integrations"][0]["provider"] == "mock"

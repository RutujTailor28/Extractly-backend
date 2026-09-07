"""Tests for Phase 8 Human Review & Correction APIs (mocked Supabase, no AI)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.review import BulkFieldCorrectionRequest, FieldCorrectionItem
from app.services.recalculation_service import (
    calculate_expected_total,
    calculate_line_items_subtotal,
)
from app.services.review_data_service import apply_corrections, parse_original_extraction
from app.services.review_service import (
    _normalize_correction_value,
    _values_equal,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _field(value, confidence=0.95, bbox=None):
    return {
        "value": value,
        "confidence": confidence,
        "page_number": 1,
        "bbox": bbox or {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.03},
    }


def _extraction_row(**overrides):
    base = {
        "id": "ext-1",
        "document_id": "doc-1",
        "model": "mock-extractor",
        "overall_confidence": 0.9,
        "created_at": "2026-09-01T00:00:00Z",
        "fields_json": {
            "document_type": "invoice",
            "vendor_name": _field("Acme Corp"),
            "invoice_number": _field("INV-10042", 0.52),
            "invoice_date": _field("2026-03-10"),
            "currency": _field("USD"),
            "subtotal": _field("1000.00"),
            "tax_amount": _field("180.00"),
            "shipping": _field("50.00"),
            "discount": _field("30.00"),
            "total": _field("1200.00"),
        },
        "line_items_json": [
            {
                "description": _field("Laptop"),
                "quantity": _field(2),
                "unit": _field("pcs"),
                "unit_price": _field("500.00"),
                "line_tax": _field("90.00"),
                "line_total": _field("1090.00"),
            }
        ],
        "raw_response_json": {"untouched": True},
    }
    base.update(overrides)
    return base


def _doc(**overrides):
    base = {
        "id": "doc-1",
        "filename": "invoice.pdf",
        "status": "needs_review",
        "page_count": 1,
        "source": "upload",
        "mime_type": "application/pdf",
        "user_id": None,
    }
    base.update(overrides)
    return base


def _chain(data=None):
    q = MagicMock()
    for name in ("select", "eq", "order", "limit", "update", "delete", "insert", "is_", "in_"):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(data=data if data is not None else [])
    return q


# --- unit: recalculation / effective data ---


def test_recalculation_decimal_safe() -> None:
    items = [
        {"line_total": {"value": "500.00"}},
        {"line_total": {"value": "740.00"}},
    ]
    assert calculate_line_items_subtotal(items) == Decimal("1240.00")
    fields = {
        "subtotal": {"value": "1000.00"},
        "tax_amount": {"value": "180.00"},
        "shipping": {"value": "50.00"},
        "discount": {"value": "30.00"},
    }
    assert calculate_expected_total(fields=fields) == Decimal("1200.00")


def test_apply_field_corrections_preserves_original() -> None:
    original = parse_original_extraction(_extraction_row())
    corrections = [
        {
            "correction_type": "field_correction",
            "field_name": "invoice_number",
            "original_value_json": "INV-10042",
            "corrected_value_json": "INV-10024",
            "created_at": "2026-09-02T00:00:00Z",
        },
        {
            "correction_type": "field_correction",
            "field_name": "invoice_number",
            "original_value_json": "INV-10024",
            "corrected_value_json": "INV-99999",
            "created_at": "2026-09-02T01:00:00Z",
        },
    ]
    effective = apply_corrections(original, corrections)
    assert effective["fields"]["invoice_number"]["value"] == "INV-99999"
    assert effective["original_field_values"]["invoice_number"] == "INV-10042"
    assert effective["field_corrections"]["invoice_number"] == "INV-99999"
    # Original extraction row untouched
    assert original["fields"]["invoice_number"]["value"] == "INV-10042"


def test_apply_line_item_add_update_delete() -> None:
    original = parse_original_extraction(_extraction_row())
    corrections = [
        {
            "correction_type": "line_item_update",
            "line_item_index": 0,
            "corrected_value_json": {"line_total": "1000.00"},
            "created_at": "t1",
        },
        {
            "correction_type": "line_item_added",
            "line_item_index": 1,
            "corrected_value_json": {
                "item_id": "new-1",
                "description": "Shipping",
                "line_total": "50.00",
            },
            "created_at": "t2",
        },
        {
            "correction_type": "line_item_deleted",
            "line_item_index": 0,
            "created_at": "t3",
        },
    ]
    effective = apply_corrections(original, corrections)
    assert len(effective["line_items"]) == 1
    assert effective["line_items"][0]["item_id"] == "new-1"
    assert effective["line_items"][0]["description"]["value"] == "Shipping"
    # AI original untouched
    assert len(original["line_items"]) == 1
    assert original["line_items"][0]["line_total"]["value"] == "1090.00"


def test_normalize_money_date_currency_and_trim() -> None:
    assert _normalize_correction_value("subtotal", " 1,240.00 ") == "1240.00"
    assert _normalize_correction_value("invoice_date", "2026-03-15") == "2026-03-15"
    assert _normalize_correction_value("currency", " usd ") == "USD"
    assert _normalize_correction_value("vendor_name", "  Acme  ") == "Acme"
    from app.services.document_service import DocumentServiceError

    with pytest.raises(DocumentServiceError):
        _normalize_correction_value("subtotal", "abc")
    with pytest.raises(DocumentServiceError):
        _normalize_correction_value("invoice_date", "not-a-date")
    with pytest.raises(DocumentServiceError):
        _normalize_correction_value("currency", "$$$")


def test_values_equal_and_invalid_field_schema() -> None:
    assert _values_equal("1000.00", "1000.0")
    assert not _values_equal("a", "b")
    with pytest.raises(ValidationError):
        BulkFieldCorrectionRequest(
            fields=[FieldCorrectionItem(field_name="not_a_field", value="x")]
        )


# --- API mocks ---


def test_review_api_returns_fields_bbox_confidence(client: TestClient) -> None:
    doc = _doc()
    extraction = _extraction_row()
    page = {
        "page_number": 1,
        "processed_storage_path": "processed/doc-1/page-1.png",
        "width": 1000,
        "height": 1400,
        "mime_type": "image/png",
    }
    flag = {
        "id": "flag-1",
        "rule_code": "TOTAL_MISMATCH",
        "severity": "error",
        "message": "bad",
        "resolved": False,
        "created_at": "2026-09-03T00:00:00Z",
    }

    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain([doc])
        if name == "extractions":
            return _chain([extraction])
        if name == "document_pages":
            return _chain([page])
        if name == "document_corrections":
            return _chain([])
        if name == "validation_flags":
            return _chain([flag])
        return _chain([])

    mock_client.table.side_effect = table
    mock_client.storage.from_.return_value.create_signed_url.return_value = {
        "signedURL": "https://example.com/page.png"
    }

    with patch(
        "app.services.review_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents/doc-1/review")

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["status"] == "needs_review"
    assert body["pages"][0]["image_url"] == "https://example.com/page.png"
    inv = next(f for f in body["fields"] if f["field_name"] == "invoice_number")
    assert inv["confidence"] == 0.52
    assert inv["bbox"]["x"] == 0.1
    assert inv["is_corrected"] is False
    assert body["validation"]["errors"][0]["rule_code"] == "TOTAL_MISMATCH"


def test_field_correction_triggers_validation(client: TestClient) -> None:
    doc = _doc(status="needs_review")
    extraction = _extraction_row()
    inserted = {
        "id": "corr-1",
        "document_id": "doc-1",
        "extraction_id": "ext-1",
        "correction_type": "field_correction",
        "field_name": "subtotal",
        "original_value_json": "1000.00",
        "corrected_value_json": "1240.00",
        "corrected_by": "system-reviewer",
        "created_at": "2026-09-03T00:00:00Z",
    }

    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain([doc])
        if name == "extractions":
            return _chain([extraction])
        if name == "document_corrections":
            return _chain([])
        if name == "field_values":
            return _chain([{"id": "fv-1"}])
        return _chain([])

    mock_client.table.side_effect = table

    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.review_service.insert_correction",
            return_value=inserted,
        ) as insert_mock,
        patch(
            "app.services.review_service._revalidate",
            return_value={"status": "ready", "summary": {"errors": 0}},
        ),
        patch(
            "app.services.review_service._update_field_values_row",
            return_value=None,
        ),
    ):
        response = client.patch(
            "/api/v1/documents/doc-1/fields",
            json={"fields": [{"field_name": "subtotal", "value": "1240.00"}]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["fields"][0]["changed"] is True
    assert body["fields"][0]["corrected_value"] == "1240.00"
    insert_mock.assert_called()
    # raw AI payload never written
    assert extraction["raw_response_json"] == {"untouched": True}


def test_cannot_edit_processing_or_approved(client: TestClient) -> None:
    for status in ("extracting", "approved"):
        mock_client = MagicMock()
        mock_client.table.return_value = _chain([_doc(status=status)])
        with patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ):
            response = client.patch(
                "/api/v1/documents/doc-1/fields",
                json={"fields": [{"field_name": "total", "value": "1"}]},
            )
        assert response.status_code == 409


def test_line_item_add_update_delete_flow(client: TestClient) -> None:
    doc = _doc(status="ready")
    extraction = _extraction_row()
    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain([doc])
        if name == "extractions":
            return _chain([extraction])
        if name == "document_corrections":
            return _chain([])
        return _chain([])

    mock_client.table.side_effect = table

    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.review_service.insert_correction",
            return_value={"id": "c1"},
        ),
        patch(
            "app.services.review_service.build_effective_extraction",
            side_effect=[
                # update: before
                apply_corrections(parse_original_extraction(extraction), []),
                # update: after
                apply_corrections(
                    parse_original_extraction(extraction),
                    [
                        {
                            "correction_type": "line_item_update",
                            "line_item_index": 0,
                            "corrected_value_json": {"line_total": "1000.00"},
                        }
                    ],
                ),
            ],
        ),
        patch(
            "app.services.review_service._revalidate",
            return_value={"status": "ready"},
        ),
    ):
        response = client.patch(
            "/api/v1/documents/doc-1/line-items",
            json={"line_items": [{"index": 0, "line_total": "1000.00"}]},
        )
    assert response.status_code == 200
    assert "calculated" in response.json()
    assert "line_items_subtotal" in response.json()["calculated"]

    # add
    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch("app.services.review_service.insert_correction", return_value={"id": "c2"}),
        patch(
            "app.services.review_service.build_effective_extraction",
            return_value=apply_corrections(parse_original_extraction(extraction), []),
        ),
        patch(
            "app.services.review_service._revalidate",
            return_value={"status": "ready"},
        ),
    ):
        response = client.post(
            "/api/v1/documents/doc-1/line-items",
            json={
                "description": "Shipping Service",
                "quantity": 1,
                "unit": "service",
                "unit_price": 50,
                "line_tax": 0,
                "line_total": 50,
            },
        )
    assert response.status_code == 200

    # delete
    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch("app.services.review_service.insert_correction", return_value={"id": "c3"}),
        patch(
            "app.services.review_service.build_effective_extraction",
            return_value=apply_corrections(parse_original_extraction(extraction), []),
        ),
        patch(
            "app.services.review_service._revalidate",
            return_value={"status": "ready"},
        ),
    ):
        response = client.delete("/api/v1/documents/doc-1/line-items/0")
    assert response.status_code == 200


def test_approve_blocked_by_errors_and_allows_warnings(client: TestClient) -> None:
    doc = _doc(status="needs_review")
    extraction = _extraction_row()

    # blocked by error
    mock_client = MagicMock()

    def table_err(name):
        if name == "documents":
            return _chain([doc])
        if name == "extractions":
            return _chain([extraction])
        if name == "validation_flags":
            return _chain([{"id": "f1", "rule_code": "TOTAL_MISMATCH", "message": "mismatch"}])
        return _chain([])

    mock_client.table.side_effect = table_err
    with patch(
        "app.services.review_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post("/api/v1/documents/doc-1/approve")
    assert response.status_code == 409

    # warnings only — empty error query
    mock_client2 = MagicMock()
    approved_doc = {**doc, "status": "approved", "approved_at": "2026-09-03T00:00:00Z", "approved_by": "system-reviewer"}

    def table_ok(name):
        if name == "documents":
            q = _chain([doc])
            u = _chain([approved_doc])
            q.update.return_value = u
            return q
        if name == "extractions":
            return _chain([extraction])
        if name == "validation_flags":
            return _chain([])  # no unresolved errors
        if name == "document_corrections":
            return _chain([{"id": "h1"}])
        return _chain([])

    mock_client2.table.side_effect = table_ok
    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client2,
        ),
        patch(
            "app.services.review_service.insert_correction",
            return_value={"id": "h1"},
        ),
    ):
        response = client.post("/api/v1/documents/doc-1/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_approve_force_with_errors(client: TestClient) -> None:
    doc = _doc(status="needs_review")
    extraction = _extraction_row()
    approved_doc = {
        **doc,
        "status": "approved",
        "approved_at": "2026-09-03T00:00:00Z",
        "approved_by": "Alex",
    }
    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            q = _chain([doc])
            u = _chain([approved_doc])
            q.update.return_value = u
            return q
        if name == "extractions":
            return _chain([extraction])
        if name == "validation_flags":
            q = _chain(
                [{"id": "f1", "rule_code": "TOTAL_MISMATCH", "message": "mismatch"}]
            )
            return q
        return _chain([])

    mock_client.table.side_effect = table
    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.review_service.insert_correction",
            return_value={"id": "h1"},
        ),
    ):
        response = client.post(
            "/api/v1/documents/doc-1/approve",
            json={"force": True, "approved_by": "Alex"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["forced"] is True
    assert body["resolved_error_count"] == 1


def test_unapprove_and_history(client: TestClient) -> None:
    approved = _doc(
        status="approved",
        approved_at="2026-09-03T00:00:00Z",
        approved_by="system-reviewer",
    )
    extraction = _extraction_row()
    history_rows = [
        {
            "id": "h1",
            "correction_type": "field_correction",
            "field_name": "invoice_number",
            "line_item_index": None,
            "original_value_json": "INV-1",
            "corrected_value_json": "INV-2",
            "corrected_by": "system-reviewer",
            "created_at": "2026-09-02T00:00:00Z",
        },
        {
            "id": "h2",
            "correction_type": "document_approved",
            "field_name": None,
            "line_item_index": None,
            "original_value_json": {"status": "ready"},
            "corrected_value_json": {"status": "approved"},
            "corrected_by": "system-reviewer",
            "created_at": "2026-09-03T00:00:00Z",
        },
    ]

    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            q = _chain([approved])
            u = _chain([{**approved, "status": "ready"}])
            q.update.return_value = u
            return q
        if name == "extractions":
            return _chain([extraction])
        if name == "document_corrections":
            return _chain(history_rows)
        return _chain([])

    mock_client.table.side_effect = table
    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.review_service.insert_correction",
            return_value={"id": "h3"},
        ),
    ):
        response = client.post("/api/v1/documents/doc-1/unapprove")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"

    mock_client2 = MagicMock()
    mock_client2.table.side_effect = lambda name: (
        _chain([_doc()]) if name == "documents" else _chain(history_rows)
    )
    with patch(
        "app.services.review_service.get_supabase_admin_client",
        return_value=mock_client2,
    ):
        response = client.get("/api/v1/documents/doc-1/review-history")
    assert response.status_code == 200
    types = [h["type"] for h in response.json()["history"]]
    assert "field_correction" in types
    assert "document_approved" in types


def test_noop_field_correction_skips_history(client: TestClient) -> None:
    doc = _doc(status="ready")
    extraction = _extraction_row()
    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain([doc])
        if name == "extractions":
            return _chain([extraction])
        if name == "document_corrections":
            return _chain([])
        return _chain([])

    mock_client.table.side_effect = table
    with (
        patch(
            "app.services.review_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.review_service.insert_correction",
        ) as insert_mock,
        patch(
            "app.services.review_service._revalidate",
            return_value={"status": "ready"},
        ),
    ):
        response = client.patch(
            "/api/v1/documents/doc-1/fields",
            json={"fields": [{"field_name": "subtotal", "value": "1000.00"}]},
        )
    assert response.status_code == 200
    assert response.json()["fields"][0]["changed"] is False
    insert_mock.assert_not_called()


def test_review_missing_document(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([])
    with patch(
        "app.services.review_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents/missing/review")
    assert response.status_code == 404

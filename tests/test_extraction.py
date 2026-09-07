"""Tests for Phase 6 AI extraction (mocked provider + mocked Supabase)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.extraction import BoundingBox, DocumentExtraction, ExtractedField
from app.services.ai.provider import MockExtractionProvider
from app.services.extraction_service import call_ai_provider_with_retries
from app.services.ai.base import PageImage
from app.utils.extraction_normalize import normalize_currency, normalize_decimal


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _chain(data=None):
    q = MagicMock()
    for name in ("select", "eq", "order", "limit", "update", "delete", "insert"):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(data=data if data is not None else [])
    return q


def _doc(**overrides):
    base = {
        "id": "11111111-1111-4111-8111-111111111111",
        "filename": "invoice.pdf",
        "mime_type": "application/pdf",
        "status": "ready",
        "page_count": 1,
        "storage_path": "11111111-1111-4111-8111-111111111111/invoice.pdf",
    }
    base.update(overrides)
    return base


def _page(**overrides):
    base = {
        "id": "page-1",
        "document_id": "11111111-1111-4111-8111-111111111111",
        "page_number": 1,
        "processed_storage_path": "processed/11111111-1111-4111-8111-111111111111/page-1.png",
        "mime_type": "image/png",
        "width": 1000,
        "height": 1400,
    }
    base.update(overrides)
    return base


def test_bbox_validation_accepts_valid() -> None:
    box = BoundingBox(x=0.1, y=0.2, width=0.3, height=0.4)
    assert box.x == 0.1


@pytest.mark.parametrize(
    "payload",
    [
        {"x": -0.1, "y": 0.0, "width": 0.2, "height": 0.2},
        {"x": 0.0, "y": 0.0, "width": 1.1, "height": 0.2},
        {"x": 0.8, "y": 0.0, "width": 0.3, "height": 0.2},
        {"x": 0.0, "y": 0.8, "width": 0.2, "height": 0.3},
    ],
)
def test_bbox_validation_rejects_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        BoundingBox(**payload)


def test_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(value="x", confidence=1.2, page_number=1)
    with pytest.raises(ValidationError):
        ExtractedField(value="x", confidence=-0.1, page_number=1)


def test_normalize_decimal_and_currency() -> None:
    assert normalize_decimal("$1,250.50") == normalize_decimal("1250.50")
    code, original = normalize_currency("₹")
    assert code == "INR"
    assert original == "₹"
    code, original = normalize_currency("$")
    assert code is None
    assert original == "$"


def test_retry_on_invalid_then_success() -> None:
    provider = MockExtractionProvider(fail_times=1)
    pages = [PageImage(page_number=1, content=b"png")]
    extraction, raw, model, latency, cost = call_ai_provider_with_retries(provider, pages)
    assert provider.calls == 2
    assert extraction.document_type == "invoice"
    assert model == "mock-extractor"


def test_retry_stops_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.extraction_service.settings.AI_MAX_RETRIES", 1)
    provider = MockExtractionProvider(fail_times=5)
    pages = [PageImage(page_number=1, content=b"png")]
    from app.services.document_service import DocumentServiceError

    with pytest.raises(DocumentServiceError):
        call_ai_provider_with_retries(provider, pages)
    assert provider.calls == 2  # retries=1 => 2 attempts


def test_extract_missing_document(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([])
    with patch(
        "app.services.extraction_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents/00000000-0000-4000-8000-000000000099/extract"
        )
    assert response.status_code == 404


def test_extract_without_pages(client: TestClient) -> None:
    docs = _chain([_doc()])
    pages = _chain([])

    def table_side_effect(name):
        if name == "documents":
            return docs
        if name == "document_pages":
            return pages
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.extraction_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents/11111111-1111-4111-8111-111111111111/extract"
        )
    assert response.status_code == 400
    assert "processed" in response.json()["detail"].lower()


def test_extract_blocked_status(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([_doc(status="extracting")])
    with patch(
        "app.services.extraction_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents/11111111-1111-4111-8111-111111111111/extract"
        )
    assert response.status_code == 409


def test_extract_success(client: TestClient) -> None:
    document_id = "11111111-1111-4111-8111-111111111111"
    docs = _chain([_doc()])
    pages = _chain([_page()])
    extractions = _chain([])
    fields = _chain([])
    vendors = _chain(
        [
            {
                "id": "vendor-1",
                "name": "Acme Office Supplies",
                "name_normalized": "acme office supplies",
                "tax_id": None,
                "invoice_count": 0,
                "template_hints_json": {"hints": []},
            }
        ]
    )

    docs.execute.side_effect = [
        MagicMock(data=[_doc()]),  # fetch
        MagicMock(data=[_doc(status="extracting")]),  # mark extracting
        MagicMock(data=[_doc()]),  # refresh for vendor associate
        MagicMock(data=[_doc(status="ready")]),  # link vendor update
        MagicMock(data=[_doc(status="ready")]),  # mark ready
        MagicMock(data=[_doc(status="ready")]),
    ]
    pages.execute.return_value = MagicMock(data=[_page()])
    extractions.insert.return_value = extractions
    extractions.execute.return_value = MagicMock(
        data=[
            {
                "id": "ext-1",
                "document_id": document_id,
                "schema_version": "v1",
                "model": "mock-extractor",
                "overall_confidence": 0.94,
            }
        ]
    )
    fields.insert.return_value = fields
    fields.execute.return_value = MagicMock(data=[{"id": "f1"}])
    vendors.update.return_value = vendors
    vendors.insert.return_value = vendors

    def table_side_effect(name):
        if name == "documents":
            return docs
        if name == "document_pages":
            return pages
        if name == "extractions":
            return extractions
        if name == "field_values":
            return fields
        if name == "vendors":
            return vendors
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    storage = MagicMock()
    storage.download.return_value = b"fakepng"
    mock_client.storage.from_.return_value = storage

    with (
        patch(
            "app.services.extraction_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.extraction_service.get_extraction_provider",
            return_value=MockExtractionProvider(),
        ),
    ):
        response = client.post(f"/api/v1/documents/{document_id}/extract")

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_id"] == "ext-1"
    assert body["document_type"] == "invoice"
    assert body["status"] == "ready"
    assert "invoice_number" in body["fields"]
    assert len(body["line_items"]) == 1
    storage.download.assert_called()
    extractions.insert.assert_called_once()
    fields.insert.assert_called_once()


def test_get_extraction_latest(client: TestClient) -> None:
    document_id = "11111111-1111-4111-8111-111111111111"
    docs = _chain([_doc()])
    extractions = _chain(
        [
            {
                "id": "ext-2",
                "document_id": document_id,
                "schema_version": "v1",
                "model": "mock-extractor",
                "overall_confidence": 0.91,
                "latency_ms": 100,
                "cost_usd": None,
                "created_at": "2026-09-03T12:00:00+00:00",
                "fields_json": {
                    "document_type": "invoice",
                    "vendor_name": {
                        "value": "Acme",
                        "confidence": 0.9,
                        "page_number": 1,
                        "bbox": None,
                    },
                },
                "line_items_json": [],
            }
        ]
    )

    def table_side_effect(name):
        if name == "documents":
            return docs
        if name == "extractions":
            return extractions
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.extraction_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(f"/api/v1/documents/{document_id}/extraction")
    assert response.status_code == 200
    body = response.json()
    assert body["extraction"]["id"] == "ext-2"
    assert body["validation_flags"] == []
    assert body["fields"]["vendor_name"]["value"] == "Acme"


def test_get_extraction_not_found(client: TestClient) -> None:
    docs = _chain([_doc()])
    extractions = _chain([])

    def table_side_effect(name):
        if name == "documents":
            return docs
        return extractions

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.extraction_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(
            "/api/v1/documents/11111111-1111-4111-8111-111111111111/extraction"
        )
    assert response.status_code == 404


def test_reextract_creates_new_record(client: TestClient) -> None:
    document_id = "11111111-1111-4111-8111-111111111111"
    docs = _chain([_doc()])
    pages = _chain([_page()])
    extractions = _chain([])
    fields = _chain([])
    vendors = _chain(
        [
            {
                "id": "vendor-1",
                "name": "Acme Office Supplies",
                "name_normalized": "acme office supplies",
                "invoice_count": 1,
                "template_hints_json": {"hints": []},
            }
        ]
    )

    docs.execute.side_effect = [
        MagicMock(data=[_doc()]),
        MagicMock(data=[_doc(status="extracting")]),
        MagicMock(data=[_doc()]),
        MagicMock(data=[_doc(status="ready")]),
        MagicMock(data=[_doc(status="ready")]),
        MagicMock(data=[_doc(status="ready")]),
    ]
    pages.execute.return_value = MagicMock(data=[_page()])
    extractions.insert.return_value = extractions
    extractions.execute.return_value = MagicMock(
        data=[{"id": "ext-new", "document_id": document_id}]
    )
    fields.insert.return_value = fields
    fields.execute.return_value = MagicMock(data=[{"id": "f1"}])
    vendors.update.return_value = vendors

    def table_side_effect(name):
        if name == "documents":
            return docs
        if name == "document_pages":
            return pages
        if name == "extractions":
            return extractions
        if name == "field_values":
            return fields
        if name == "vendors":
            return vendors
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    storage = MagicMock()
    storage.download.return_value = b"fakepng"
    mock_client.storage.from_.return_value = storage

    with (
        patch(
            "app.services.extraction_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.extraction_service.get_extraction_provider",
            return_value=MockExtractionProvider(),
        ),
    ):
        response = client.post(f"/api/v1/documents/{document_id}/reextract")

    assert response.status_code == 200
    assert response.json()["extraction_id"] == "ext-new"
    # Insert once for this run — history preserved because we never delete old rows
    extractions.insert.assert_called_once()


def test_document_extraction_schema_rejects_bad_overall() -> None:
    with pytest.raises(ValidationError):
        DocumentExtraction(
            document_type="invoice",
            fields={},
            line_items=[],
            overall_confidence=1.5,
        )

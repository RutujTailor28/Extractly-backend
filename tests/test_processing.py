"""Tests for Phase 5 document processing (mocked Supabase + real image utils)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.utils.image_utils import (
    detect_and_correct_skew,
    normalize_contrast,
    normalize_resolution,
    preprocess_image,
)
from app.utils.pdf_utils import get_pdf_page_count, render_pdf_pages

MINI_PDF = b"""%PDF-1.4
1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj
2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj
3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>endobj
4 0 obj<< /Length 44 >>stream
BT /F1 24 Tf 50 100 Td (Hello) Tj ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000068 00000 n 
0000000125 00000 n 
0000000228 00000 n 
trailer<< /Size 5 /Root 1 0 R >>
startxref
320
%%EOF
"""


def _png_bytes(size=(400, 500), color=(240, 240, 240)) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _jpg_bytes(size=(400, 500)) -> bytes:
    image = Image.new("RGB", size, (250, 250, 250))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _doc_row(**overrides):
    base = {
        "id": "11111111-1111-4111-8111-111111111111",
        "filename": "invoice.pdf",
        "mime_type": "application/pdf",
        "file_size": 100,
        "page_count": 1,
        "status": "queued",
        "source": "upload",
        "storage_path": "11111111-1111-4111-8111-111111111111/invoice.pdf",
        "processing_error": None,
    }
    base.update(overrides)
    return base


def _chain(data=None, count=None):
    q = MagicMock()
    for name in (
        "select",
        "eq",
        "order",
        "limit",
        "update",
        "delete",
        "insert",
    ):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(
        data=data if data is not None else [],
        count=count if count is not None else (len(data) if data is not None else 0),
    )
    return q


def test_pdf_page_count_and_render() -> None:
    # Use a real tiny PDF created by PyMuPDF for reliability
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((50, 100), "Hello")
    pdf_bytes = doc.tobytes()
    doc.close()

    assert get_pdf_page_count(pdf_bytes) == 1
    pages = render_pdf_pages(pdf_bytes, dpi=72)
    assert len(pages) == 1
    image = Image.open(io.BytesIO(pages[0]))
    assert image.format == "PNG"


def test_image_preprocess_pipeline() -> None:
    png_bytes, metadata, width, height = preprocess_image(_png_bytes((3000, 4000)))
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert metadata.contrast_normalized is True
    assert width <= 2500
    assert height <= 3500


def test_deskew_respects_max_angle() -> None:
    image = Image.new("RGB", (400, 500), (255, 255, 255))
    result, angle = detect_and_correct_skew(image, max_angle=10)
    assert abs(angle) <= 10
    assert result.size[0] > 0


def test_normalize_contrast_and_resolution() -> None:
    image = Image.new("RGB", (3000, 4000), (180, 180, 180))
    contrasted = normalize_contrast(image)
    resized = normalize_resolution(contrasted, max_width=2500, max_height=3500)
    assert resized.width <= 2500
    assert resized.height <= 3500


def test_process_missing_document(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([])
    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents/00000000-0000-4000-8000-000000000099/process"
        )
    assert response.status_code == 404


def test_process_blocked_status(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([_doc_row(status="extracting")])
    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents/11111111-1111-4111-8111-111111111111/process"
        )
    assert response.status_code == 409


def test_process_unsupported_type(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain(
        [_doc_row(mime_type="application/zip", filename="x.zip")]
    )
    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents/11111111-1111-4111-8111-111111111111/process"
        )
    assert response.status_code == 415


def test_process_image_success(client: TestClient) -> None:
    document_id = "11111111-1111-4111-8111-111111111111"
    docs = _chain([_doc_row(mime_type="image/png", filename="scan.png", status="queued")])
    pages_table = _chain([])

    # Sequence of document table operations: fetch, update preprocessing, update ready
    docs.execute.side_effect = [
        MagicMock(data=[_doc_row(mime_type="image/png", filename="scan.png")]),
        MagicMock(data=[_doc_row(status="preprocessing", mime_type="image/png")]),
        MagicMock(data=[_doc_row(status="ready", mime_type="image/png", page_count=1)]),
    ]

    def table_side_effect(name):
        if name == "documents":
            return docs
        if name == "document_pages":
            return pages_table
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    storage = MagicMock()
    storage.download.return_value = _png_bytes()
    storage.upload.return_value = {"path": "ok"}
    storage.remove.return_value = {"data": []}
    mock_client.storage.from_.return_value = storage

    # page insert returns a row
    pages_table.insert.return_value = pages_table
    pages_table.execute.side_effect = [
        MagicMock(data=[]),  # list existing for cleanup
        MagicMock(data=[]),  # delete existing
        MagicMock(
            data=[
                {
                    "id": "p1",
                    "document_id": document_id,
                    "page_number": 1,
                    "processed_storage_path": f"processed/{document_id}/page-1.png",
                    "width": 400,
                    "height": 500,
                    "mime_type": "image/png",
                }
            ]
        ),
    ]

    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(f"/api/v1/documents/{document_id}/process")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["page_count"] == 1
    assert body["pages_processed"] == 1
    storage.upload.assert_called()
    upload_path = storage.upload.call_args.kwargs.get("path") or storage.upload.call_args.args[0]
    assert "page-1.png" in str(upload_path)


def test_list_pages(client: TestClient) -> None:
    document_id = "11111111-1111-4111-8111-111111111111"
    docs = _chain([_doc_row()])
    pages = _chain(
        [
            {
                "page_number": 1,
                "width": 100,
                "height": 200,
                "mime_type": "image/png",
                "processing_metadata": {},
            }
        ]
    )

    def table_side_effect(name):
        if name == "documents":
            return docs
        if name == "document_pages":
            return pages
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(f"/api/v1/documents/{document_id}/pages")
    assert response.status_code == 200
    body = response.json()
    assert body["total_pages"] == 1
    assert body["pages"][0]["page_number"] == 1


def test_get_page_signed_url(client: TestClient) -> None:
    document_id = "11111111-1111-4111-8111-111111111111"
    docs = _chain([_doc_row()])
    pages = _chain(
        [
            {
                "page_number": 1,
                "width": 100,
                "height": 200,
                "mime_type": "image/png",
                "processed_storage_path": f"processed/{document_id}/page-1.png",
                "created_at": "2026-09-03T10:00:00+00:00",
            }
        ]
    )

    def table_side_effect(name):
        if name == "documents":
            return docs
        if name == "document_pages":
            return pages
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    storage = MagicMock()
    storage.create_signed_url.return_value = {
        "signedURL": "https://example.supabase.co/signed/page"
    }
    mock_client.storage.from_.return_value = storage

    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(f"/api/v1/documents/{document_id}/pages/1")
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://")
    assert body["expires_in"] == 3600


def test_get_page_not_found(client: TestClient) -> None:
    mock_client = MagicMock()

    def table_side_effect(name):
        if name == "documents":
            return _chain([_doc_row()])
        return _chain([])

    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(
            "/api/v1/documents/11111111-1111-4111-8111-111111111111/pages/9"
        )
    assert response.status_code == 404


def test_process_failure_sets_failed(client: TestClient) -> None:
    document_id = "11111111-1111-4111-8111-111111111111"
    docs = _chain([_doc_row(mime_type="image/jpeg")])
    pages = _chain([])
    docs.execute.side_effect = [
        MagicMock(data=[_doc_row(mime_type="image/jpeg")]),
        MagicMock(data=[_doc_row(status="preprocessing", mime_type="image/jpeg")]),
        MagicMock(data=[_doc_row(status="failed", mime_type="image/jpeg")]),
    ]

    def table_side_effect(name):
        if name == "documents":
            return docs
        return pages

    mock_client = MagicMock()
    mock_client.table.side_effect = table_side_effect
    storage = MagicMock()
    storage.download.side_effect = Exception("download boom")
    storage.remove.return_value = {"data": []}
    mock_client.storage.from_.return_value = storage

    with patch(
        "app.services.processing_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(f"/api/v1/documents/{document_id}/process")
    assert response.status_code == 500

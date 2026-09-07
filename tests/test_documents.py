"""Tests for Phase 3 document upload (validation + API with mocked Supabase)."""

from __future__ import annotations

import hashlib
import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.utils.file_utils import (
    FileValidationError,
    build_storage_path,
    compute_sha256,
    sanitize_filename,
    validate_extension,
    validate_file_size,
    validate_mime_type,
)

# Minimal valid-ish payloads for MIME/extension testing (not real production files)
MINI_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
    b"\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
MINI_JPG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xd9"
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_sanitize_filename_strips_path_traversal() -> None:
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_filename("folder/invoice.pdf") == "invoice.pdf"


def test_sanitize_filename_rejects_empty() -> None:
    with pytest.raises(FileValidationError):
        sanitize_filename("")
    with pytest.raises(FileValidationError):
        sanitize_filename("../")


def test_validate_extension_allows_supported() -> None:
    assert validate_extension("invoice.PDF") == ".pdf"
    assert validate_extension("scan.JPEG") == ".jpeg"


def test_validate_extension_rejects_unsupported() -> None:
    with pytest.raises(FileValidationError):
        validate_extension("malware.exe")


def test_validate_mime_type_rejects_mismatch() -> None:
    with pytest.raises(FileValidationError):
        validate_mime_type("image/png", ".pdf")


def test_validate_mime_type_accepts_jpeg_alias() -> None:
    assert validate_mime_type("image/jpg", ".jpg") == "image/jpeg"


def test_validate_mime_type_infers_from_extension_for_octet_stream() -> None:
    assert validate_mime_type("application/octet-stream", ".jpeg") == "image/jpeg"
    assert validate_mime_type("application/octet-stream", ".pdf") == "application/pdf"
    assert validate_mime_type(None, ".png") == "image/png"


def test_validate_file_size_rejects_oversized() -> None:
    with pytest.raises(FileValidationError):
        validate_file_size(11 * 1024 * 1024, max_bytes=10 * 1024 * 1024)


def test_compute_sha256_from_bytes() -> None:
    content = b"hello-extractly"
    assert compute_sha256(content) == hashlib.sha256(content).hexdigest()


def test_build_storage_path_uses_document_uuid() -> None:
    path = build_storage_path("550e8400-e29b-41d4-a716-446655440000", "invoice.pdf")
    assert path == "550e8400-e29b-41d4-a716-446655440000/invoice.pdf"


def _mock_supabase_success(document_id: str = "11111111-1111-4111-8111-111111111111"):
    storage_bucket = MagicMock()
    storage_bucket.upload.return_value = {"path": "ok"}
    storage_bucket.remove.return_value = {"data": []}

    table = MagicMock()
    table.insert.return_value.execute.return_value = MagicMock(
        data=[
            {
                "id": document_id,
                "filename": "invoice.pdf",
                "mime_type": "application/pdf",
                "file_size": len(MINI_PDF),
                "status": "queued",
                "source": "upload",
                "created_at": "2026-09-03T10:00:00+00:00",
            }
        ]
    )

    client = MagicMock()
    client.storage.from_.return_value = storage_bucket
    client.table.return_value = table
    return client, storage_bucket, table


def test_upload_valid_pdf(client: TestClient) -> None:
    mock_client, storage_bucket, table = _mock_supabase_success()
    fixed_id = __import__("uuid").UUID("11111111-1111-4111-8111-111111111111")
    with (
        patch("app.services.document_service.get_supabase_admin_client", return_value=mock_client),
        patch("app.services.document_service.uuid.uuid4", return_value=fixed_id),
    ):
        response = client.post(
            "/api/v1/documents",
            files=[("files", ("invoice.pdf", io.BytesIO(MINI_PDF), "application/pdf"))],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["successful"] == 1
    assert body["failed"] == 0
    assert body["documents"][0]["status"] == "queued"
    assert body["documents"][0]["mime_type"] == "application/pdf"
    storage_bucket.upload.assert_called_once()
    table.insert.assert_called_once()
    inserted = table.insert.call_args.args[0]
    assert inserted["file_hash"] == hashlib.sha256(MINI_PDF).hexdigest()
    assert inserted["page_count"] == 1
    assert inserted["source"] == "upload"
    assert inserted["storage_path"] == "11111111-1111-4111-8111-111111111111/invoice.pdf"
    upload_path = storage_bucket.upload.call_args.kwargs["path"]
    assert upload_path.startswith("11111111-1111-4111-8111-111111111111/")


def test_upload_valid_png(client: TestClient) -> None:
    mock_client, _, _ = _mock_supabase_success()
    mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "filename": "receipt.png",
                "mime_type": "image/png",
                "file_size": len(MINI_PNG),
                "status": "queued",
                "source": "upload",
                "created_at": "2026-09-03T10:00:00+00:00",
            }
        ]
    )
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[("files", ("receipt.png", io.BytesIO(MINI_PNG), "image/png"))],
        )
    assert response.status_code == 200
    assert response.json()["successful"] == 1


def test_upload_valid_jpg(client: TestClient) -> None:
    mock_client, _, _ = _mock_supabase_success()
    mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[
            {
                "id": "33333333-3333-4333-8333-333333333333",
                "filename": "scan.jpg",
                "mime_type": "image/jpeg",
                "file_size": len(MINI_JPG),
                "status": "queued",
                "source": "upload",
                "created_at": "2026-09-03T10:00:00+00:00",
            }
        ]
    )
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[("files", ("scan.jpg", io.BytesIO(MINI_JPG), "image/jpeg"))],
        )
    assert response.status_code == 200
    assert response.json()["successful"] == 1


def test_upload_multiple_files(client: TestClient) -> None:
    mock_client, _, table = _mock_supabase_success()

    def insert_side_effect(record):
        result = MagicMock()
        result.data = [
            {
                **record,
                "created_at": "2026-09-03T10:00:00+00:00",
            }
        ]
        return MagicMock(execute=MagicMock(return_value=result))

    table.insert.side_effect = insert_side_effect

    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[
                ("files", ("invoice.pdf", io.BytesIO(MINI_PDF), "application/pdf")),
                ("files", ("receipt.png", io.BytesIO(MINI_PNG), "image/png")),
            ],
        )

    body = response.json()
    assert response.status_code == 200
    assert body["successful"] == 2
    assert body["failed"] == 0


def test_reject_unsupported_extension(client: TestClient) -> None:
    mock_client, storage_bucket, _ = _mock_supabase_success()
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[("files", ("bad.exe", io.BytesIO(b"MZ"), "application/octet-stream"))],
        )
    body = response.json()
    assert body["successful"] == 0
    assert body["failed"] == 1
    assert "Unsupported" in body["errors"][0]["error"]
    storage_bucket.upload.assert_not_called()


def test_reject_unsupported_mime_type(client: TestClient) -> None:
    mock_client, storage_bucket, _ = _mock_supabase_success()
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[("files", ("invoice.pdf", io.BytesIO(MINI_PDF), "application/zip"))],
        )
    body = response.json()
    assert body["failed"] == 1
    assert body["successful"] == 0
    storage_bucket.upload.assert_not_called()


def test_reject_file_larger_than_limit(client: TestClient) -> None:
    mock_client, storage_bucket, _ = _mock_supabase_success()
    big = b"x" * (10 * 1024 * 1024 + 1)
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[("files", ("huge.pdf", io.BytesIO(big), "application/pdf"))],
        )
    body = response.json()
    assert body["successful"] == 0
    assert body["failed"] == 1
    assert "maximum allowed size" in body["errors"][0]["error"]
    storage_bucket.upload.assert_not_called()


def test_partial_batch_failure(client: TestClient) -> None:
    mock_client, _, table = _mock_supabase_success()
    table.insert.return_value.execute.return_value = MagicMock(
        data=[
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "filename": "invoice.pdf",
                "mime_type": "application/pdf",
                "file_size": len(MINI_PDF),
                "status": "queued",
                "source": "upload",
                "created_at": "2026-09-03T10:00:00+00:00",
            }
        ]
    )
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[
                ("files", ("invoice.pdf", io.BytesIO(MINI_PDF), "application/pdf")),
                ("files", ("bad.exe", io.BytesIO(b"MZ"), "application/octet-stream")),
            ],
        )
    body = response.json()
    assert body["successful"] == 1
    assert body["failed"] == 1


def test_db_failure_cleans_up_storage(client: TestClient) -> None:
    mock_client, storage_bucket, table = _mock_supabase_success()
    table.insert.return_value.execute.side_effect = Exception("insert failed")

    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post(
            "/api/v1/documents",
            files=[("files", ("invoice.pdf", io.BytesIO(MINI_PDF), "application/pdf"))],
        )

    body = response.json()
    assert body["successful"] == 0
    assert body["failed"] == 1
    storage_bucket.upload.assert_called_once()
    storage_bucket.remove.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 4 — documents module
# ---------------------------------------------------------------------------


DOC_ROW = {
    "id": "11111111-1111-4111-8111-111111111111",
    "filename": "invoice.pdf",
    "mime_type": "application/pdf",
    "file_size": 45,
    "page_count": 1,
    "status": "queued",
    "source": "upload",
    "storage_path": "11111111-1111-4111-8111-111111111111/invoice.pdf",
    "file_hash": "abc",
    "created_at": "2026-09-03T10:00:00+00:00",
    "updated_at": "2026-09-03T10:00:00+00:00",
}


def _chain_mock(data=None, count=None):
    q = MagicMock()
    for name in (
        "select",
        "eq",
        "ilike",
        "gte",
        "lte",
        "in_",
        "order",
        "range",
        "limit",
        "update",
        "delete",
        "insert",
    ):
        getattr(q, name).return_value = q
    not_builder = MagicMock()
    not_builder.in_.return_value = q
    q.not_ = not_builder
    q.execute.return_value = MagicMock(
        data=data if data is not None else [],
        count=count if count is not None else (len(data) if data is not None else 0),
    )
    return q


def test_list_documents_empty(client: TestClient) -> None:
    mock_client = MagicMock()

    def table_side_effect(name):
        if name == "documents":
            return _chain_mock([], count=0)
        return _chain_mock([])

    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


def test_list_documents_pagination_and_sort(client: TestClient) -> None:
    mock_client = MagicMock()
    docs = _chain_mock([DOC_ROW], count=25)

    def table_side_effect(name):
        if name == "documents":
            return docs
        return _chain_mock([])

    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(
            "/api/v1/documents",
            params={"page": 2, "page_size": 10, "sort_by": "filename", "sort_order": "asc"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["page"] == 2
    assert body["pagination"]["page_size"] == 10
    assert body["pagination"]["total"] == 25
    assert body["pagination"]["total_pages"] == 3
    docs.order.assert_called_with("filename", desc=False)
    docs.range.assert_called_with(10, 19)


def test_list_page_size_validation(client: TestClient) -> None:
    response = client.get("/api/v1/documents", params={"page_size": 101})
    assert response.status_code == 422


def test_list_status_filter(client: TestClient) -> None:
    mock_client = MagicMock()
    docs = _chain_mock([DOC_ROW], count=1)

    def table_side_effect(name):
        if name == "documents":
            return docs
        return _chain_mock([])

    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents", params={"status": "needs_review"})
    assert response.status_code == 200
    docs.eq.assert_any_call("status", "needs_review")


def test_list_invalid_status(client: TestClient) -> None:
    response = client.get("/api/v1/documents", params={"status": "nope"})
    assert response.status_code == 422


def test_list_search(client: TestClient) -> None:
    mock_client = MagicMock()
    docs = _chain_mock([DOC_ROW], count=1)

    def table_side_effect(name):
        if name == "documents":
            return docs
        return _chain_mock([])

    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents", params={"search": "invoice"})
    assert response.status_code == 200
    docs.ilike.assert_called()


def test_list_date_filter(client: TestClient) -> None:
    mock_client = MagicMock()
    docs = _chain_mock([DOC_ROW], count=1)

    def table_side_effect(name):
        if name == "documents":
            return docs
        return _chain_mock([])

    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(
            "/api/v1/documents",
            params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
        )
    assert response.status_code == 200
    docs.gte.assert_called()
    docs.lte.assert_called()


def test_list_invalid_date_range(client: TestClient) -> None:
    response = client.get(
        "/api/v1/documents",
        params={"date_from": "2026-12-31", "date_to": "2026-01-01"},
    )
    assert response.status_code == 422


def test_get_document_detail(client: TestClient) -> None:
    mock_client = MagicMock()

    def table_side_effect(name):
        if name == "documents":
            return _chain_mock([DOC_ROW])
        if name == "extractions":
            return _chain_mock([])
        if name == "validation_flags":
            return _chain_mock([])
        return _chain_mock([])

    mock_client.table.side_effect = table_side_effect
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(f"/api/v1/documents/{DOC_ROW['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["document"]["id"] == DOC_ROW["id"]
    assert body["extraction"] is None
    assert body["fields"] == []
    assert body["validation_flags"] == []


def test_get_document_not_found(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain_mock([])
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents/00000000-0000-4000-8000-000000000099")
    assert response.status_code == 404


def test_patch_document_status(client: TestClient) -> None:
    mock_client = MagicMock()
    updated = {**DOC_ROW, "status": "needs_review", "updated_at": "2026-09-03T11:00:00+00:00"}
    docs = _chain_mock([DOC_ROW])
    docs.update.return_value = docs
    docs.execute.side_effect = [
        MagicMock(data=[DOC_ROW]),  # fetch
        MagicMock(data=[updated]),  # update
    ]

    mock_client.table.return_value = docs
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.patch(
            f"/api/v1/documents/{DOC_ROW['id']}",
            json={"status": "needs_review"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "needs_review"


def test_patch_invalid_status(client: TestClient) -> None:
    response = client.patch(
        f"/api/v1/documents/{DOC_ROW['id']}",
        json={"status": "not_a_status"},
    )
    assert response.status_code == 422


def test_patch_rejects_protected_fields(client: TestClient) -> None:
    response = client.patch(
        f"/api/v1/documents/{DOC_ROW['id']}",
        json={"id": "hack", "file_hash": "x", "status": "ready"},
    )
    assert response.status_code == 422


def test_delete_document(client: TestClient) -> None:
    mock_client, storage_bucket, _ = _mock_supabase_success()
    docs = _chain_mock([DOC_ROW])
    docs.delete.return_value = docs
    docs.execute.side_effect = [
        MagicMock(data=[DOC_ROW]),
        MagicMock(data=[DOC_ROW]),
    ]
    mock_client.table.return_value = docs
    mock_client.storage.from_.return_value = storage_bucket

    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.delete(f"/api/v1/documents/{DOC_ROW['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == DOC_ROW["id"]
    storage_bucket.remove.assert_called_once()


def test_delete_document_not_found(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain_mock([])
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.delete("/api/v1/documents/00000000-0000-4000-8000-000000000099")
    assert response.status_code == 404


def test_get_document_file_signed_url(client: TestClient) -> None:
    mock_client = MagicMock()
    docs = _chain_mock([DOC_ROW])
    mock_client.table.return_value = docs
    storage = MagicMock()
    storage.create_signed_url.return_value = {
        "signedURL": "https://example.supabase.co/signed/test"
    }
    mock_client.storage.from_.return_value = storage

    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(f"/api/v1/documents/{DOC_ROW['id']}/file")
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://")
    assert body["expires_in"] == 3600
    storage.create_signed_url.assert_called_once()


def test_get_document_file_not_found(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain_mock([])
    with patch(
        "app.services.document_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get(
            "/api/v1/documents/00000000-0000-4000-8000-000000000099/file"
        )
    assert response.status_code == 404

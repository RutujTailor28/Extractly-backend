"""Tests for public extract API and job status."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.public_api import PublicExtractionResult, PublicAmountsResult
from app.schemas.document import DocumentUploadItem
from app.services.api_key_service import APIKeyContext
from app.services.rate_limit_service import reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_limiter():
    reset_rate_limiter()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def api_ctx() -> APIKeyContext:
    return APIKeyContext(
        key_id="key-1",
        user_id=None,
        rate_limit=100,
        permissions=["extract:read", "extract:write"],
    )


def _png_bytes() -> bytes:
    # Minimal valid-ish PNG signature + padding
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 64
    )


def test_api_key_required(client: TestClient) -> None:
    response = client.post(
        "/api/v1/extract",
        files={"file": ("inv.png", BytesIO(_png_bytes()), "image/png")},
    )
    assert response.status_code == 401


def test_invalid_file_rejected(client: TestClient, api_ctx: APIKeyContext) -> None:
    with patch("app.core.api_key_auth.authenticate_api_key", return_value=api_ctx), patch(
        "app.core.api_key_auth.touch_api_key_usage"
    ):
        response = client.post(
            "/api/v1/extract",
            headers={"X-API-Key": "ext_live_test"},
            files={"file": ("virus.exe", BytesIO(b"MZ"), "application/octet-stream")},
        )
    assert response.status_code == 422


def test_small_image_sync_flow(client: TestClient, api_ctx: APIKeyContext) -> None:
    doc = DocumentUploadItem(
        id="doc-1",
        filename="inv.png",
        mime_type="image/png",
        file_size=100,
        status="queued",
        source="api",
        created_at=datetime.now(timezone.utc),
    )
    job = {
        "id": "job-1",
        "document_id": "doc-1",
        "status": "completed",
        "api_key_id": "key-1",
        "user_id": None,
        "error_message": None,
    }
    result = PublicExtractionResult(
        amounts=PublicAmountsResult(currency="USD", total="110.00")
    )

    with (
        patch("app.core.api_key_auth.authenticate_api_key", return_value=api_ctx),
        patch("app.core.api_key_auth.touch_api_key_usage"),
        patch(
            "app.services.public_extract_service.upload_single_document",
            return_value=doc,
        ),
        patch(
            "app.services.public_extract_service.create_job",
            return_value=(job, None),
        ),
        patch(
            "app.services.public_extract_service.process_extraction_job",
        ),
        patch(
            "app.services.public_extract_service.get_job",
            return_value=job,
        ),
        patch(
            "app.services.public_extract_service.get_supabase_admin_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.public_extract_service.build_public_result",
            return_value=(result, None),
        ),
        patch(
            "app.services.public_extract_service.validate_webhook_url",
        ),
    ):
        response = client.post(
            "/api/v1/extract",
            headers={"X-API-Key": "ext_live_test"},
            files={"file": ("inv.png", BytesIO(_png_bytes()), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["document_id"] == "doc-1"
    assert body["result"]["amounts"]["total"] == "110.00"


def test_pdf_creates_async_job(client: TestClient, api_ctx: APIKeyContext) -> None:
    doc = DocumentUploadItem(
        id="doc-pdf",
        filename="inv.pdf",
        mime_type="application/pdf",
        file_size=1000,
        status="queued",
        source="api",
        created_at=datetime.now(timezone.utc),
    )
    job = {
        "id": "job-pdf",
        "document_id": "doc-pdf",
        "status": "queued",
        "api_key_id": "key-1",
        "user_id": None,
    }

    with (
        patch("app.core.api_key_auth.authenticate_api_key", return_value=api_ctx),
        patch("app.core.api_key_auth.touch_api_key_usage"),
        patch(
            "app.services.public_extract_service.upload_single_document",
            return_value=doc,
        ),
        patch(
            "app.services.public_extract_service.create_job",
            return_value=(job, "whsec_test"),
        ),
        patch(
            "app.services.public_extract_service.process_extraction_job",
        ) as process_mock,
        patch(
            "app.services.public_extract_service.get_supabase_admin_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.public_extract_service.validate_webhook_url",
        ),
    ):
        response = client.post(
            "/api/v1/extract",
            headers={"X-API-Key": "ext_live_test"},
            files={
                "file": ("inv.pdf", BytesIO(b"%PDF-1.4 mock"), "application/pdf"),
            },
            data={"webhook_url": "https://example.com/hook"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    assert body["job_id"] == "job-pdf"
    assert body["status_url"] == "/api/v1/extract/job-pdf"
    assert body["webhook"]["signing_secret"] == "whsec_test"
    process_mock.assert_called()


def test_job_status_completed_and_failed(client: TestClient, api_ctx: APIKeyContext) -> None:
    completed = {
        "id": "job-1",
        "document_id": "doc-1",
        "status": "completed",
        "api_key_id": "key-1",
        "user_id": None,
        "created_at": "2026-09-03T00:00:00+00:00",
    }
    failed = {
        "id": "job-2",
        "document_id": "doc-2",
        "status": "failed",
        "api_key_id": "key-1",
        "user_id": None,
        "created_at": "2026-09-03T00:00:00+00:00",
        "error_code": "EXTRACTION_FAILED",
        "error_message": "Unable to extract invoice data.",
    }
    result = PublicExtractionResult()

    with (
        patch("app.core.api_key_auth.authenticate_api_key", return_value=api_ctx),
        patch("app.core.api_key_auth.touch_api_key_usage"),
        patch(
            "app.services.public_extract_service.get_job",
            side_effect=[completed, failed],
        ),
        patch(
            "app.services.public_extract_service.get_supabase_admin_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.public_extract_service.build_public_result",
            return_value=(result, None),
        ),
    ):
        ok = client.get(
            "/api/v1/extract/job-1",
            headers={"X-API-Key": "ext_live_test"},
        )
        bad = client.get(
            "/api/v1/extract/job-2",
            headers={"X-API-Key": "ext_live_test"},
        )

    assert ok.status_code == 200
    assert ok.json()["status"] == "completed"
    assert ok.json()["result"] is not None

    assert bad.status_code == 200
    assert bad.json()["status"] == "failed"
    assert bad.json()["error"]["code"] == "EXTRACTION_FAILED"
    assert "traceback" not in str(bad.json()).lower()


def test_job_ownership_enforced(client: TestClient) -> None:
    ctx = APIKeyContext(
        key_id="key-a",
        user_id=None,
        rate_limit=100,
        permissions=["extract:read", "extract:write"],
    )
    foreign_job = {
        "id": "job-x",
        "document_id": "doc-x",
        "status": "completed",
        "api_key_id": "key-other",
        "user_id": None,
        "created_at": "2026-09-03T00:00:00+00:00",
    }
    with (
        patch("app.core.api_key_auth.authenticate_api_key", return_value=ctx),
        patch("app.core.api_key_auth.touch_api_key_usage"),
        patch(
            "app.services.public_extract_service.get_job",
            return_value=foreign_job,
        ),
    ):
        response = client.get(
            "/api/v1/extract/job-x",
            headers={"X-API-Key": "ext_live_test"},
        )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "JOB_NOT_FOUND"


def test_include_metadata(client: TestClient, api_ctx: APIKeyContext) -> None:
    from app.schemas.public_api import PublicExtractionMetadata, PublicFieldMetadata

    job = {
        "id": "job-1",
        "document_id": "doc-1",
        "status": "completed",
        "api_key_id": "key-1",
        "user_id": None,
        "created_at": "2026-09-03T00:00:00+00:00",
    }
    meta = PublicExtractionMetadata(
        overall_confidence=0.94,
        fields=[
            PublicFieldMetadata(
                field_name="invoice_number",
                confidence=0.98,
                page_number=1,
                bbox={"x": 0.2, "y": 0.3, "width": 0.1, "height": 0.05},
            )
        ],
    )
    with (
        patch("app.core.api_key_auth.authenticate_api_key", return_value=api_ctx),
        patch("app.core.api_key_auth.touch_api_key_usage"),
        patch("app.services.public_extract_service.get_job", return_value=job),
        patch(
            "app.services.public_extract_service.get_supabase_admin_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.public_extract_service.build_public_result",
            return_value=(PublicExtractionResult(), meta),
        ) as build,
    ):
        response = client.get(
            "/api/v1/extract/job-1?include_metadata=true",
            headers={"X-API-Key": "ext_live_test"},
        )
    assert response.status_code == 200
    assert response.json()["metadata"]["overall_confidence"] == 0.94
    build.assert_called()
    assert build.call_args.kwargs.get("include_metadata") is True

"""Tests for API usage logging and analytics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.api_key_service import APIKeyContext
from app.services.rate_limit_service import reset_rate_limiter
from app.services.usage_service import get_api_usage, record_api_usage


@pytest.fixture(autouse=True)
def _reset_limiter():
    reset_rate_limiter()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _chain(data=None):
    q = MagicMock()
    for name in (
        "select",
        "eq",
        "order",
        "limit",
        "update",
        "insert",
        "is_",
        "gte",
    ):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(data=data if data is not None else [])
    return q


def test_record_api_usage_metadata_only() -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([{"id": "u1"}])
    with patch(
        "app.services.usage_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        record_api_usage(
            api_key_id="key-1",
            user_id=None,
            request_id="req_abc",
            endpoint="/api/v1/extract",
            method="POST",
            status_code=202,
            latency_ms=120,
        )
    insert_call = mock_client.table.return_value.insert.call_args[0][0]
    assert insert_call["api_key_id"] == "key-1"
    assert insert_call["endpoint"] == "/api/v1/extract"
    assert insert_call["latency_ms"] == 120
    assert "api_key" not in insert_call
    assert "body" not in insert_call
    assert "file" not in insert_call


def test_usage_analytics_aggregation() -> None:
    rows = [
        {
            "endpoint": "/api/v1/extract",
            "status_code": 200,
            "latency_ms": 100,
            "created_at": "2026-09-01T10:00:00+00:00",
        },
        {
            "endpoint": "/api/v1/extract",
            "status_code": 429,
            "latency_ms": 5,
            "created_at": "2026-09-01T11:00:00+00:00",
        },
        {
            "endpoint": "/api/v1/extract/job-1",
            "status_code": 500,
            "latency_ms": 50,
            "created_at": "2026-09-02T10:00:00+00:00",
        },
    ]
    mock_client = MagicMock()
    mock_client.table.return_value = _chain(rows)
    with patch(
        "app.services.usage_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        stats = get_api_usage(days=30, user_id=None)
    assert stats.total_requests == 3
    assert stats.successful_requests == 1
    assert stats.rate_limited_requests == 1
    assert stats.failed_requests == 1
    assert stats.average_latency_ms == pytest.approx((100 + 5 + 50) / 3)
    assert len(stats.requests_by_day) == 2
    assert stats.requests_by_endpoint[0].endpoint == "/api/v1/extract"


def test_api_usage_endpoint(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain(
        [
            {
                "endpoint": "/api/v1/extract",
                "status_code": 200,
                "latency_ms": 80,
                "created_at": "2026-09-03T00:00:00+00:00",
            }
        ]
    )
    with patch(
        "app.services.usage_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/api-usage?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["total_requests"] == 1
    assert body["successful_requests"] == 1


def test_middleware_records_public_usage(client: TestClient) -> None:
    from app.services.document_service import DocumentServiceError

    ctx = APIKeyContext(
        key_id="key-1",
        user_id=None,
        rate_limit=100,
        permissions=["extract:read", "extract:write"],
    )
    with (
        patch("app.core.api_key_auth.authenticate_api_key", return_value=ctx),
        patch("app.core.api_key_auth.touch_api_key_usage"),
        patch(
            "app.services.public_extract_service.get_job",
            side_effect=DocumentServiceError(
                "Job not found",
                status_code=404,
                code="JOB_NOT_FOUND",
            ),
        ),
        patch("app.middleware.api_usage.record_api_usage") as record,
    ):
        response = client.get(
            "/api/v1/extract/job-1",
            headers={"X-API-Key": "ext_live_test", "X-Request-ID": "req_test_1"},
        )
    assert response.status_code == 404
    assert record.called
    kwargs = record.call_args.kwargs
    assert kwargs["api_key_id"] == "key-1"
    assert kwargs["endpoint"] == "/api/v1/extract/job-1"
    assert kwargs["method"] == "GET"
    assert kwargs["request_id"] == "req_test_1"
    assert kwargs["status_code"] == 404
    assert "latency_ms" in kwargs

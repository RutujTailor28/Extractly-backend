"""Tests for per-API-key rate limiting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.api_key_service import APIKeyContext, generate_api_key_material
from app.services.rate_limit_service import MemoryRateLimitService, reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_limiter():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_memory_limiter_allows_then_blocks() -> None:
    limiter = MemoryRateLimitService()
    for _ in range(3):
        result = limiter.check("k1", limit=3, window_seconds=60)
        assert result.allowed is True
    blocked = limiter.check("k1", limit=3, window_seconds=60)
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after is not None


def test_separate_keys_have_separate_limits() -> None:
    limiter = MemoryRateLimitService()
    assert limiter.check("a", limit=1, window_seconds=60).allowed
    assert limiter.check("b", limit=1, window_seconds=60).allowed
    assert limiter.check("a", limit=1, window_seconds=60).allowed is False
    assert limiter.check("b", limit=1, window_seconds=60).allowed is False


def test_public_endpoint_returns_429_and_headers(client: TestClient) -> None:
    ctx = APIKeyContext(
        key_id="key-rl",
        user_id=None,
        rate_limit=2,
        permissions=["extract:read", "extract:write"],
    )

    with patch(
        "app.core.api_key_auth.authenticate_api_key",
        return_value=ctx,
    ), patch(
        "app.core.api_key_auth.touch_api_key_usage",
    ):
        # First two should pass auth+limit (will fail later on missing file — ok)
        r1 = client.post("/api/v1/extract", headers={"X-API-Key": "ext_live_test"})
        r2 = client.post("/api/v1/extract", headers={"X-API-Key": "ext_live_test"})
        r3 = client.post("/api/v1/extract", headers={"X-API-Key": "ext_live_test"})

    assert r1.status_code != 429
    assert r2.status_code != 429
    assert r3.status_code == 429
    assert "X-RateLimit-Limit" in r3.headers
    assert r3.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in r3.headers
    detail = r3.json()["detail"]
    assert detail["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_rate_limit_headers_on_success(client: TestClient) -> None:
    full, _, _ = generate_api_key_material()
    ctx = APIKeyContext(
        key_id="key-ok",
        user_id=None,
        rate_limit=60,
        permissions=["extract:read", "extract:write"],
    )
    job = {
        "id": "job-missing",
        "document_id": "doc-1",
        "status": "processing",
        "api_key_id": "key-ok",
        "user_id": None,
        "created_at": "2026-09-03T00:00:00+00:00",
    }
    with patch(
        "app.core.api_key_auth.authenticate_api_key",
        return_value=ctx,
    ), patch(
        "app.core.api_key_auth.touch_api_key_usage",
    ), patch(
        "app.services.public_extract_service.get_job",
        return_value=job,
    ):
        response = client.get(
            "/api/v1/extract/job-missing",
            headers={"X-API-Key": full},
        )
    assert response.status_code == 200
    assert "X-RateLimit-Limit" in response.headers
    assert response.headers["X-RateLimit-Limit"] == "60"

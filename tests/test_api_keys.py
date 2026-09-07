"""Tests for API key lifecycle and authentication."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.api_key_service import (
    KEY_PREFIX,
    authenticate_api_key,
    generate_api_key_material,
    hash_api_key,
    verify_key_hash,
)
from app.services.document_service import DocumentServiceError
from app.services.rate_limit_service import reset_rate_limiter


@pytest.fixture
def client() -> TestClient:
    reset_rate_limiter()
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
        "in_",
        "gte",
    ):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(data=data if data is not None else [])
    return q


def test_key_format_and_hash() -> None:
    full, prefix, digest = generate_api_key_material()
    assert full.startswith(KEY_PREFIX)
    assert prefix.startswith(KEY_PREFIX)
    assert digest == hash_api_key(full)
    assert verify_key_hash(full, digest)
    assert not verify_key_hash(full + "x", digest)
    assert full not in digest


def test_create_api_key_shows_secret_once(client: TestClient) -> None:
    full, prefix, digest = generate_api_key_material()
    saved = {
        "id": "key-1",
        "label": "Production Integration",
        "rate_limit": 60,
        "created_at": "2026-09-03T00:00:00+00:00",
        "key_hash": digest,
        "key_prefix": prefix,
    }
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([saved])

    with (
        patch(
            "app.services.api_key_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.api_key_service.generate_api_key_material",
            return_value=(full, prefix, digest),
        ),
    ):
        response = client.post(
            "/api/v1/api-keys",
            json={"label": "Production Integration", "rate_limit": 60},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] == full
    assert body["prefix"] == prefix
    assert "key_hash" not in body


def test_list_keys_hides_secret(client: TestClient) -> None:
    rows = [
        {
            "id": "key-1",
            "label": "Prod",
            "key_prefix": "ext_live_abcd1234",
            "rate_limit": 60,
            "last_used_at": None,
            "request_count": 12,
            "revoked_at": None,
            "created_at": "2026-09-03T00:00:00+00:00",
        }
    ]
    mock_client = MagicMock()
    mock_client.table.return_value = _chain(rows)
    with patch(
        "app.services.api_key_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/api-keys")
    assert response.status_code == 200
    item = response.json()["api_keys"][0]
    assert item["prefix"] == "ext_live_abcd1234"
    assert "api_key" not in item
    assert "key_hash" not in item


def test_authenticate_valid_and_invalid() -> None:
    full, prefix, digest = generate_api_key_material()
    row = {
        "id": "key-1",
        "user_id": None,
        "key_hash": digest,
        "key_prefix": prefix,
        "rate_limit": 60,
        "revoked_at": None,
        "expires_at": None,
        "permissions_json": ["extract:read", "extract:write"],
    }
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([row])
    with patch(
        "app.services.api_key_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        ctx = authenticate_api_key(full)
        assert ctx.key_id == "key-1"

    mock_client.table.return_value = _chain([])
    with (
        patch(
            "app.services.api_key_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        pytest.raises(DocumentServiceError) as exc,
    ):
        authenticate_api_key(full)
    assert exc.value.code == "INVALID_API_KEY"


def test_missing_key_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/extract")
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "API_KEY_REQUIRED"


def test_revoked_key_rejected() -> None:
    full, prefix, digest = generate_api_key_material()
    row = {
        "id": "key-1",
        "user_id": None,
        "key_hash": digest,
        "key_prefix": prefix,
        "rate_limit": 60,
        "revoked_at": "2026-09-01T00:00:00+00:00",
        "expires_at": None,
        "permissions_json": ["extract:read", "extract:write"],
    }
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([row])
    with (
        patch(
            "app.services.api_key_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        pytest.raises(DocumentServiceError) as exc,
    ):
        authenticate_api_key(full)
    assert exc.value.code == "API_KEY_REVOKED"


def test_revoke_endpoint(client: TestClient) -> None:
    existing = {
        "id": "key-1",
        "user_id": None,
        "revoked_at": None,
    }
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([existing])
    with patch(
        "app.services.api_key_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.delete("/api/v1/api-keys/key-1")
    assert response.status_code == 200
    assert response.json()["success"] is True

"""Tests for webhook SSRF protection, signing, and delivery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.document_service import DocumentServiceError
from app.services.webhook_security_service import validate_webhook_url
from app.services.webhook_service import (
    RETRY_DELAYS_SECONDS,
    deliver_webhook,
    sign_payload,
    verify_signature,
)


def test_https_url_accepted() -> None:
    with patch(
        "app.services.webhook_security_service.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ):
        assert validate_webhook_url("https://example.com/hook").startswith("https://")


def test_file_scheme_rejected() -> None:
    with pytest.raises(DocumentServiceError) as exc:
        validate_webhook_url("file:///etc/passwd")
    assert exc.value.code == "INVALID_WEBHOOK_URL"


def test_private_ip_blocked() -> None:
    with (
        patch(
            "app.services.webhook_security_service.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("10.0.0.5", 443))],
        ),
        pytest.raises(DocumentServiceError) as exc,
    ):
        validate_webhook_url("https://internal.example/hook")
    assert exc.value.code == "INVALID_WEBHOOK_URL"


def test_metadata_ip_blocked() -> None:
    with pytest.raises(DocumentServiceError):
        validate_webhook_url("https://169.254.169.254/latest/meta-data/")


def test_signature_roundtrip() -> None:
    body = b'{"event":"extraction.completed"}'
    secret = "whsec_test_secret"
    sig = sign_payload(secret, body)
    assert sig.startswith("sha256=")
    assert verify_signature(secret, body, sig)
    assert not verify_signature(secret, body, "sha256=deadbeef")


def test_deliver_webhook_success_recorded() -> None:
    delivery = {
        "id": "del-1",
        "event_id": "evt_abc",
        "job_id": "job-1",
        "status": "pending",
    }
    updated = {**delivery, "status": "delivered", "attempt": 1}

    mock_client = MagicMock()
    insert_q = MagicMock()
    for name in ("insert", "update", "eq"):
        getattr(insert_q, name).return_value = insert_q
    insert_q.execute.side_effect = [
        MagicMock(data=[delivery]),
        MagicMock(data=[updated]),
    ]
    mock_client.table.return_value = insert_q

    mock_resp = MagicMock(status_code=200)

    with (
        patch(
            "app.services.webhook_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.webhook_service.validate_webhook_url",
            return_value="https://example.com/hook",
        ),
        patch("app.services.webhook_service.httpx.Client") as http_cls,
    ):
        http_cls.return_value.__enter__.return_value.post.return_value = mock_resp
        result = deliver_webhook(
            job_id="job-1",
            event="extraction.completed",
            url="https://example.com/hook",
            payload_data={"data": {"total": "10.00"}},
            signing_secret="whsec_x",
            document_id="doc-1",
        )

    assert result["status"] == "delivered"
    call_kwargs = http_cls.return_value.__enter__.return_value.post.call_args
    headers = call_kwargs.kwargs["headers"]
    body = call_kwargs.kwargs["content"]
    assert headers["X-Extractly-Event"] == "extraction.completed"
    assert verify_signature("whsec_x", body, headers["X-Extractly-Signature"])
    assert b"evt_abc" in body
    assert b"event_id" in body


def test_deliver_webhook_failure_schedules_retry() -> None:
    delivery = {
        "id": "del-2",
        "event_id": "evt_fail",
        "job_id": "job-2",
        "status": "pending",
    }
    pending = {
        **delivery,
        "status": "pending",
        "attempt": 1,
        "next_retry_at": "2026-09-03T00:00:30+00:00",
    }
    mock_client = MagicMock()
    q = MagicMock()
    for name in ("insert", "update", "eq"):
        getattr(q, name).return_value = q
    q.execute.side_effect = [
        MagicMock(data=[delivery]),
        MagicMock(data=[pending]),
    ]
    mock_client.table.return_value = q
    mock_resp = MagicMock(status_code=500)

    with (
        patch(
            "app.services.webhook_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.webhook_service.validate_webhook_url",
            return_value="https://example.com/hook",
        ),
        patch("app.services.webhook_service.httpx.Client") as http_cls,
    ):
        http_cls.return_value.__enter__.return_value.post.return_value = mock_resp
        result = deliver_webhook(
            job_id="job-2",
            event="extraction.failed",
            url="https://example.com/hook",
            payload_data={"error": {"code": "EXTRACTION_FAILED", "message": "x"}},
            signing_secret="whsec_x",
            document_id="doc-2",
        )

    assert result["status"] == "pending"
    assert result.get("next_retry_at")
    assert RETRY_DELAYS_SECONDS[1] == 30


def test_no_blocking_sleep_in_webhook_module() -> None:
    import inspect

    import app.services.webhook_service as mod

    source = inspect.getsource(mod)
    assert "time.sleep" not in source

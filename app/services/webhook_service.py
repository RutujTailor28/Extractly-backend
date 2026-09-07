"""Outbound webhook delivery with HMAC signing and persisted retry state."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.services.api_key_service import hash_api_key
from app.services.document_service import DocumentServiceError
from app.services.webhook_security_service import validate_webhook_url

logger = logging.getLogger(__name__)

# Retry schedule (seconds) — persisted as next_retry_at; never sleep in request path
RETRY_DELAYS_SECONDS = [0, 30, 300, 1800]


def generate_webhook_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(24)}"


def hash_webhook_secret(secret: str) -> str:
    return hash_api_key(secret)


def sign_payload(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected, header or "")


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError("Supabase client is not configured", status_code=503)
    return client


def create_delivery_record(
    client,
    *,
    job_id: str,
    event: str,
    url: str,
) -> dict[str, Any]:
    event_id = f"evt_{secrets.token_hex(16)}"
    row = {
        "job_id": job_id,
        "event": event,
        "event_id": event_id,
        "url": url,
        "status": "pending",
        "attempt": 0,
    }
    response = client.table("webhook_deliveries").insert(row).execute()
    if not response.data:
        raise DocumentServiceError("Failed to create webhook delivery", status_code=500)
    return response.data[0]


def deliver_webhook(
    *,
    job_id: str,
    event: str,
    url: str,
    payload_data: dict[str, Any],
    signing_secret: str,
    document_id: str | None = None,
) -> dict[str, Any]:
    """
    Send webhook immediately (attempt 1). Persist result.
    Retries are scheduled via next_retry_at (no blocking sleep).
    """
    validate_webhook_url(url)
    client = _require_client()
    delivery = create_delivery_record(client, job_id=job_id, event=event, url=url)

    body_obj = {
        "event_id": delivery["event_id"],
        "event": event,
        "job_id": job_id,
        "document_id": document_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload_data,
    }
    raw = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = sign_payload(signing_secret, raw)
    headers = {
        "Content-Type": "application/json",
        "X-Extractly-Signature": signature,
        "X-Extractly-Event": event,
        "User-Agent": "Extractly-Webhooks/1.0",
    }

    attempt = 1
    try:
        with httpx.Client(timeout=settings.WEBHOOK_TIMEOUT_SECONDS) as http:
            resp = http.post(url, content=raw, headers=headers)
        if 200 <= resp.status_code < 300:
            now = datetime.now(timezone.utc).isoformat()
            updated = (
                client.table("webhook_deliveries")
                .update(
                    {
                        "status": "delivered",
                        "attempt": attempt,
                        "response_status": resp.status_code,
                        "delivered_at": now,
                        "error_message": None,
                        "next_retry_at": None,
                    }
                )
                .eq("id", delivery["id"])
                .execute()
            )
            return updated.data[0] if updated.data else delivery

        # Non-2xx → schedule retry
        next_delay = RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)]
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=next_delay)
        status = "pending" if attempt < len(RETRY_DELAYS_SECONDS) else "failed"
        updated = (
            client.table("webhook_deliveries")
            .update(
                {
                    "status": status,
                    "attempt": attempt,
                    "response_status": resp.status_code,
                    "error_message": f"HTTP {resp.status_code}",
                    "next_retry_at": next_retry.isoformat() if status == "pending" else None,
                }
            )
            .eq("id", delivery["id"])
            .execute()
        )
        return updated.data[0] if updated.data else delivery
    except Exception as exc:
        logger.warning("Webhook delivery failed for job %s: %s", job_id, exc)
        next_delay = RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)]
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=next_delay)
        updated = (
            client.table("webhook_deliveries")
            .update(
                {
                    "status": "pending",
                    "attempt": attempt,
                    "error_message": str(exc)[:500],
                    "next_retry_at": next_retry.isoformat(),
                }
            )
            .eq("id", delivery["id"])
            .execute()
        )
        return updated.data[0] if updated.data else delivery

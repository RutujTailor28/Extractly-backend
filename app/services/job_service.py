"""Extraction job lifecycle for the public API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.database import get_supabase_admin_client
from app.services.document_service import DocumentServiceError
from app.services.webhook_service import generate_webhook_secret, hash_webhook_secret

logger = logging.getLogger(__name__)


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError("Supabase client is not configured", status_code=503)
    return client


def create_job(
    *,
    document_id: str,
    api_key_id: str,
    user_id: str | None,
    webhook_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """
    Create extraction job.
    Returns (job_row, plaintext_webhook_secret_or_none).
    """
    client = _require_client()
    webhook_secret: str | None = None
    secret_hash: str | None = None
    if webhook_url:
        webhook_secret = generate_webhook_secret()
        secret_hash = hash_webhook_secret(webhook_secret)

    row = {
        "document_id": document_id,
        "api_key_id": api_key_id,
        "user_id": user_id,
        "status": "queued",
        "webhook_url": webhook_url,
        "webhook_secret_hash": secret_hash,
        "metadata_json": metadata or {},
        "attempt_count": 0,
    }
    response = client.table("extraction_jobs").insert(row).execute()
    if not response.data:
        raise DocumentServiceError("Failed to create extraction job", status_code=500)
    return response.data[0], webhook_secret


def get_job(job_id: str) -> dict[str, Any]:
    client = _require_client()
    response = (
        client.table("extraction_jobs")
        .select("*")
        .eq("id", job_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError(
            "Job not found",
            status_code=404,
            code="JOB_NOT_FOUND",
        )
    return response.data[0]


def mark_processing(job_id: str) -> dict[str, Any]:
    client = _require_client()
    now = datetime.now(timezone.utc).isoformat()
    response = (
        client.table("extraction_jobs")
        .update(
            {
                "status": "processing",
                "started_at": now,
                "attempt_count": 1,
            }
        )
        .eq("id", job_id)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Failed to update job", status_code=500)
    return response.data[0]


def mark_completed(job_id: str) -> dict[str, Any]:
    client = _require_client()
    now = datetime.now(timezone.utc).isoformat()
    response = (
        client.table("extraction_jobs")
        .update({"status": "completed", "completed_at": now, "error_message": None})
        .eq("id", job_id)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Failed to complete job", status_code=500)
    return response.data[0]


def mark_failed(
    job_id: str,
    *,
    code: str = "EXTRACTION_FAILED",
    message: str = "Unable to extract invoice data.",
) -> dict[str, Any]:
    client = _require_client()
    now = datetime.now(timezone.utc).isoformat()
    response = (
        client.table("extraction_jobs")
        .update(
            {
                "status": "failed",
                "completed_at": now,
                "error_code": code,
                "error_message": message[:1000],
            }
        )
        .eq("id", job_id)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Failed to fail job", status_code=500)
    return response.data[0]

"""Run existing process + extract pipeline for a public API job."""

from __future__ import annotations

import logging
from typing import Any

from app.services.document_service import DocumentServiceError
from app.services.extraction_service import extract_document
from app.services.job_service import get_job, mark_completed, mark_failed, mark_processing
from app.services.processing_service import process_document
from app.services.webhook_service import deliver_webhook

logger = logging.getLogger(__name__)


def process_extraction_job(
    job_id: str,
    *,
    webhook_signing_secret: str | None = None,
) -> None:
    """
    Execute preprocessing + AI extraction for a job.
    Safe to call from BackgroundTasks. Never leaves job stuck without terminal status.
    """
    try:
        job = get_job(job_id)
        mark_processing(job_id)
        document_id = str(job["document_id"])

        process_document(document_id)
        extract_document(document_id)

        mark_completed(job_id)
        webhook_data = _build_webhook_data(document_id)
        _maybe_webhook(
            job,
            event="extraction.completed",
            signing_secret=webhook_signing_secret,
            payload_data={"data": webhook_data},
        )
    except DocumentServiceError as exc:
        logger.warning("Job %s failed: %s", job_id, exc.message)
        try:
            job = get_job(job_id)
            mark_failed(
                job_id,
                code=exc.code or "EXTRACTION_FAILED",
                message=exc.message,
            )
            _maybe_webhook(
                job,
                event="extraction.failed",
                signing_secret=webhook_signing_secret,
                payload_data={
                    "error": {
                        "code": exc.code or "EXTRACTION_FAILED",
                        "message": "Unable to process document.",
                    }
                },
            )
        except Exception:
            logger.exception("Failed to mark job %s failed", job_id)
    except Exception as exc:
        logger.exception("Unexpected failure for job %s", job_id)
        try:
            job = get_job(job_id)
            mark_failed(
                job_id,
                code="EXTRACTION_FAILED",
                message="Unable to extract invoice data.",
            )
            _maybe_webhook(
                job,
                event="extraction.failed",
                signing_secret=webhook_signing_secret,
                payload_data={
                    "error": {
                        "code": "EXTRACTION_FAILED",
                        "message": "Unable to process document.",
                    }
                },
            )
        except Exception:
            logger.exception("Failed to mark job %s failed after crash", job_id)


def _build_webhook_data(document_id: str) -> dict[str, Any]:
    """Minimal public invoice summary for webhook payloads."""
    try:
        from app.core.database import get_supabase_admin_client
        from app.services.public_extract_service import build_public_result

        client = get_supabase_admin_client()
        if client is None:
            return {"document_id": document_id}
        result, _ = build_public_result(client, document_id, include_metadata=False)
        return {
            "vendor_name": result.vendor.name,
            "invoice_number": result.invoice.number,
            "total": result.amounts.total,
            "currency": result.amounts.currency,
        }
    except Exception:
        return {"document_id": document_id}


def _maybe_webhook(
    job: dict[str, Any],
    *,
    event: str,
    signing_secret: str | None,
    payload_data: dict[str, Any],
) -> None:
    url = job.get("webhook_url")
    if not url or not signing_secret:
        return
    try:
        deliver_webhook(
            job_id=str(job["id"]),
            event=event,
            url=url,
            payload_data=payload_data,
            signing_secret=signing_secret,
            document_id=str(job.get("document_id")),
        )
    except Exception as exc:
        logger.warning("Webhook skipped/failed for job %s: %s", job.get("id"), exc)

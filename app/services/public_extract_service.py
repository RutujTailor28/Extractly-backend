"""Public extract endpoint orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import BackgroundTasks, UploadFile

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.schemas.public_api import (
    ExtractionCompletedResponse,
    ExtractionJobAcceptedResponse,
    ExtractionJobStatusResponse,
    PublicAmountsResult,
    PublicErrorBody,
    PublicExtractionMetadata,
    PublicExtractionResult,
    PublicFieldMetadata,
    PublicInvoiceResult,
    PublicLineItem,
    PublicVendorResult,
    PublicWebhookInfo,
)
from app.services.api_key_service import APIKeyContext
from app.services.document_service import (
    DocumentServiceError,
    IncomingUpload,
    upload_single_document,
)
from app.services.job_processor import process_extraction_job
from app.services.job_service import create_job, get_job
from app.services.review_data_service import (
    build_effective_extraction,
    build_review_fields,
    build_review_line_items,
)
from app.services.webhook_security_service import validate_webhook_url
from app.utils.file_utils import FileValidationError, get_file_extension
from app.utils.normalization import field_value, parse_decimal

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _money_str(value: Any) -> str | None:
    parsed = parse_decimal(value)
    if parsed is None:
        return None if value is None else str(value)
    return format(parsed, "f")


def build_public_result(
    client,
    document_id: str,
    *,
    include_metadata: bool = False,
) -> tuple[PublicExtractionResult, PublicExtractionMetadata | None]:
    extraction_resp = (
        client.table("extractions")
        .select("*")
        .eq("document_id", document_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not extraction_resp.data:
        raise DocumentServiceError(
            "Extraction not found",
            status_code=404,
            code="EXTRACTION_FAILED",
        )
    effective = build_effective_extraction(
        client,
        document_id=document_id,
        extraction_row=extraction_resp.data[0],
    )
    fields = effective.get("fields") or {}
    result = PublicExtractionResult(
        vendor=PublicVendorResult(
            name=field_value(fields, "vendor_name"),
            address=field_value(fields, "vendor_address"),
            tax_id=field_value(fields, "vendor_tax_id"),
        ),
        invoice=PublicInvoiceResult(
            number=field_value(fields, "invoice_number"),
            date=field_value(fields, "invoice_date"),
            due_date=field_value(fields, "due_date"),
            po_number=field_value(fields, "po_number"),
        ),
        amounts=PublicAmountsResult(
            currency=field_value(fields, "currency"),
            subtotal=_money_str(field_value(fields, "subtotal")),
            tax_amount=_money_str(field_value(fields, "tax_amount")),
            discount=_money_str(field_value(fields, "discount")),
            shipping=_money_str(field_value(fields, "shipping")),
            total=_money_str(field_value(fields, "total")),
        ),
        line_items=[
            PublicLineItem(
                description=item.get("description"),
                quantity=_money_str(item.get("quantity")),
                unit=item.get("unit"),
                unit_price=_money_str(item.get("unit_price")),
                line_tax=_money_str(item.get("line_tax")),
                line_total=_money_str(item.get("line_total")),
            )
            for item in build_review_line_items(effective)
        ],
    )

    metadata = None
    if include_metadata:
        review_fields = build_review_fields(effective)
        metadata = PublicExtractionMetadata(
            overall_confidence=(
                float(effective["overall_confidence"])
                if effective.get("overall_confidence") is not None
                else None
            ),
            fields=[
                PublicFieldMetadata(
                    field_name=f["field_name"],
                    confidence=f.get("confidence"),
                    page_number=f.get("page_number"),
                    bbox=f.get("bbox") if isinstance(f.get("bbox"), dict) else None,
                )
                for f in review_fields
                if f.get("value") is not None
            ],
        )
    return result, metadata


def _parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError as exc:
        raise DocumentServiceError(
            "metadata must be valid JSON object",
            status_code=422,
            code="INVALID_METADATA",
        ) from exc
    raise DocumentServiceError(
        "metadata must be valid JSON object",
        status_code=422,
        code="INVALID_METADATA",
    )


async def create_public_extraction(
    *,
    file: UploadFile,
    api_key: APIKeyContext,
    background_tasks: BackgroundTasks,
    webhook_url: str | None = None,
    metadata: str | None = None,
    include_metadata: bool = False,
) -> ExtractionJobAcceptedResponse | ExtractionCompletedResponse:
    if "extract:write" not in api_key.permissions:
        raise DocumentServiceError(
            "API key lacks extract:write permission",
            status_code=403,
            code="FORBIDDEN",
        )

    if webhook_url:
        validate_webhook_url(webhook_url)

    content = await file.read()
    max_bytes = settings.public_api_max_file_size_bytes
    if len(content) > max_bytes:
        raise DocumentServiceError(
            "File too large",
            status_code=422,
            code="FILE_TOO_LARGE",
        )

    filename = file.filename or "upload.bin"
    try:
        upload = IncomingUpload(
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            content=content,
        )
        doc = upload_single_document(
            upload,
            source="api",
            user_id=api_key.user_id,
            max_bytes=settings.public_api_max_file_size_bytes,
        )
    except FileValidationError as exc:
        code = (
            "UNSUPPORTED_FILE_TYPE"
            if "type" in exc.message.lower() or "extension" in exc.message.lower()
            else exc.code
        )
        raise DocumentServiceError(exc.message, status_code=422, code=code) from exc
    except DocumentServiceError:
        raise

    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError("Supabase client is not configured", status_code=503)

    meta = _parse_metadata(metadata)
    job, webhook_secret = create_job(
        document_id=doc.id,
        api_key_id=api_key.key_id,
        user_id=api_key.user_id,
        webhook_url=webhook_url,
        metadata=meta,
    )
    job_id = str(job["id"])
    extension = get_file_extension(filename)
    sync_eligible = (
        extension in IMAGE_EXTENSIONS
        and len(content) <= settings.PUBLIC_SYNC_MAX_BYTES
    )

    webhook_info = None
    if webhook_url and webhook_secret:
        webhook_info = PublicWebhookInfo(url=webhook_url, signing_secret=webhook_secret)

    if sync_eligible:
        process_extraction_job(job_id, webhook_signing_secret=webhook_secret)
        refreshed = get_job(job_id)
        if refreshed.get("status") == "failed":
            raise DocumentServiceError(
                refreshed.get("error_message") or "Unable to extract invoice data.",
                status_code=422,
                code=refreshed.get("error_code") or "EXTRACTION_FAILED",
            )
        result, meta_out = build_public_result(
            client, doc.id, include_metadata=include_metadata
        )
        return ExtractionCompletedResponse(
            job_id=job_id,
            document_id=doc.id,
            result=result,
            metadata=meta_out,
            webhook=webhook_info,
        )

    background_tasks.add_task(
        process_extraction_job,
        job_id,
        webhook_signing_secret=webhook_secret,
    )
    return ExtractionJobAcceptedResponse(
        status="processing",
        job_id=job_id,
        document_id=doc.id,
        status_url=f"/api/v1/extract/{job_id}",
        webhook=webhook_info,
    )


def get_public_job_status(
    job_id: str,
    *,
    api_key: APIKeyContext,
    include_metadata: bool = False,
) -> ExtractionJobStatusResponse:
    if "extract:read" not in api_key.permissions and "extract:write" not in api_key.permissions:
        raise DocumentServiceError(
            "API key lacks extract:read permission",
            status_code=403,
            code="FORBIDDEN",
        )

    job = get_job(job_id)
    # Ownership: same user scope as key
    if api_key.user_id is not None and job.get("user_id") != api_key.user_id:
        raise DocumentServiceError(
            "Job not found",
            status_code=404,
            code="JOB_NOT_FOUND",
        )
    if api_key.user_id is None and job.get("user_id") is not None:
        raise DocumentServiceError(
            "Job not found",
            status_code=404,
            code="JOB_NOT_FOUND",
        )
    # Prefer key-level isolation when both null-user
    if job.get("api_key_id") and str(job.get("api_key_id")) != api_key.key_id:
        # Allow same-user keys to read each other's jobs only if user_id matches;
        # for null users, require same api_key_id
        if api_key.user_id is None:
            raise DocumentServiceError(
                "Job not found",
                status_code=404,
                code="JOB_NOT_FOUND",
            )

    status = job.get("status") or "queued"
    document_id = str(job["document_id"])
    response = ExtractionJobStatusResponse(
        job_id=job_id,
        status=status,
        document_id=document_id,
        created_at=job.get("created_at"),
    )

    if status == "failed":
        response.error = PublicErrorBody(
            code=job.get("error_code") or "EXTRACTION_FAILED",
            message=job.get("error_message") or "Unable to extract invoice data.",
        )
        return response

    if status == "completed":
        client = get_supabase_admin_client()
        if client is None:
            raise DocumentServiceError("Supabase client is not configured", status_code=503)
        result, meta_out = build_public_result(
            client, document_id, include_metadata=include_metadata
        )
        response.result = result
        response.metadata = meta_out
    return response

"""AI extraction service — structured invoice/receipt extraction from processed pages."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.schemas.extraction import (
    HEADER_FIELD_NAMES,
    DocumentExtraction,
    DocumentExtractionDetailResponse,
    ExtractionRunResponse,
    ExtractionSummary,
)
from app.services.ai.base import PageImage
from app.services.ai.provider import (
    AIProviderError,
    ExtractionProvider,
    get_extraction_provider,
)
from app.services.ai.prompts import EXTRACTION_SCHEMA_VERSION
from app.services.document_service import DocumentServiceError
from app.utils.extraction_normalize import normalize_extraction

logger = logging.getLogger(__name__)

EXTRACTABLE_STATUSES = {"ready", "needs_review", "failed"}
BLOCKED_STATUSES = {"queued", "preprocessing", "extracting", "validating"}


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def _fetch_document(client, document_id: str) -> dict:
    response = (
        client.table("documents")
        .select("*")
        .eq("id", document_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Document not found", status_code=404)
    return response.data[0]


def _update_document(client, document_id: str, updates: dict) -> dict:
    response = (
        client.table("documents")
        .update(updates)
        .eq("id", document_id)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Failed to update document", status_code=500)
    return response.data[0]


def get_processed_pages(client, document_id: str) -> list[dict]:
    response = (
        client.table("document_pages")
        .select("*")
        .eq("document_id", document_id)
        .order("page_number", desc=False)
        .execute()
    )
    return response.data or []


def download_page_images(client, pages: list[dict]) -> list[PageImage]:
    bucket = settings.PROCESSED_DOCUMENTS_BUCKET
    images: list[PageImage] = []
    for page in pages:
        storage_path = page.get("processed_storage_path")
        if not storage_path:
            raise DocumentServiceError(
                f"Page {page.get('page_number')} is missing storage path",
                status_code=500,
            )
        try:
            content = client.storage.from_(bucket).download(storage_path)
        except Exception as exc:
            raise DocumentServiceError(
                f"Failed to download processed page {page.get('page_number')}: {exc}",
                status_code=500,
            ) from exc
        if not content:
            raise DocumentServiceError(
                f"Processed page {page.get('page_number')} download was empty",
                status_code=500,
            )
        images.append(
            PageImage(
                page_number=int(page["page_number"]),
                content=content,
                mime_type=page.get("mime_type") or "image/png",
            )
        )
    return images


def validate_ai_response(payload: dict[str, Any]) -> DocumentExtraction:
    return DocumentExtraction.model_validate(payload)


def save_extraction(
    client,
    *,
    document_id: str,
    extraction: DocumentExtraction,
    raw_response: Any,
    model: str,
    latency_ms: int,
    cost_usd: float | None,
) -> dict:
    fields_payload = {
        key: (value.model_dump() if value is not None else None)
        for key, value in extraction.fields.items()
    }
    fields_payload["document_type"] = extraction.document_type

    record = {
        "document_id": document_id,
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "model": model,
        "raw_response_json": raw_response
        if isinstance(raw_response, (dict, list))
        else {"content": raw_response},
        "fields_json": fields_payload,
        "line_items_json": [item.model_dump() for item in extraction.line_items],
        "overall_confidence": float(extraction.overall_confidence),
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
    }
    response = client.table("extractions").insert(record).execute()
    if not response.data:
        raise DocumentServiceError("Failed to save extraction", status_code=500)
    return response.data[0]


def save_field_values(
    client,
    *,
    extraction_id: str,
    extraction: DocumentExtraction,
) -> None:
    rows: list[dict[str, Any]] = []
    for name in HEADER_FIELD_NAMES:
        field = extraction.fields.get(name)
        if field is None:
            continue
        value = field.value
        rows.append(
            {
                "extraction_id": extraction_id,
                "field_name": name,
                "value": None if value is None else str(value),
                "confidence": float(field.confidence),
                "page_number": field.page_number,
                "bbox_json": field.bbox.model_dump() if field.bbox else None,
                "corrected_value": None,
                "corrected_at": None,
                "corrected_by": None,
            }
        )

    if not rows:
        return

    response = client.table("field_values").insert(rows).execute()
    if response.data is None:
        raise DocumentServiceError("Failed to save field values", status_code=500)


def call_ai_provider_with_retries(
    provider: ExtractionProvider,
    pages: list[PageImage],
    *,
    vendor_hints: dict[str, Any] | None = None,
) -> tuple[DocumentExtraction, Any, str, int, float | None]:
    """
    Call the AI provider with validation retries.

    Returns: (normalized_extraction, raw_response, model, latency_ms, cost_usd)
    """
    max_retries = settings.AI_MAX_RETRIES
    total_attempts = max_retries + 1
    validation_error: str | None = None
    last_error: Exception | None = None
    total_latency = 0
    last_raw: Any = None
    if settings.ai_provider_name == "gemini":
        last_model = settings.GEMINI_MODEL or "gemini"
    else:
        last_model = settings.AI_MODEL or "unknown"
    last_cost: float | None = None

    for attempt in range(1, total_attempts + 1):
        try:
            result = provider.extract_document(
                pages,
                vendor_hints=vendor_hints,
                validation_error=validation_error,
            )
            total_latency += result.latency_ms
            last_raw = result.raw_response
            last_model = result.model
            last_cost = result.cost_usd

            try:
                validated = validate_ai_response(result.parsed_payload)
                normalized = normalize_extraction(validated)
                return normalized, last_raw, last_model, total_latency, last_cost
            except ValidationError as exc:
                validation_error = "; ".join(
                    err.get("msg", "validation error") for err in exc.errors()
                )
                last_error = exc
                logger.warning(
                    "Extraction attempt %s/%s failed validation: %s",
                    attempt,
                    total_attempts,
                    validation_error,
                )
                continue
            except ValueError as exc:
                validation_error = str(exc)
                last_error = exc
                logger.warning(
                    "Extraction attempt %s/%s failed parsing: %s",
                    attempt,
                    total_attempts,
                    validation_error,
                )
                continue
        except AIProviderError as exc:
            last_error = exc
            logger.warning(
                "Extraction attempt %s/%s provider error: %s",
                attempt,
                total_attempts,
                exc,
            )
            validation_error = str(exc)
            if attempt >= total_attempts:
                break
            continue

    raise DocumentServiceError(
        f"Extraction failed after {total_attempts} attempts: {last_error}",
        status_code=502,
    )


def _run_extraction(
    document_id: str,
    *,
    vendor_hints: dict[str, Any] | None = None,
    message: str,
) -> ExtractionRunResponse:
    client = _require_client()
    document = _fetch_document(client, document_id)
    status = document.get("status") or "queued"

    if status in BLOCKED_STATUSES:
        raise DocumentServiceError(
            f"Document cannot be extracted while status is '{status}'",
            status_code=409,
        )
    if status not in EXTRACTABLE_STATUSES:
        raise DocumentServiceError(
            f"Document status '{status}' is not eligible for extraction",
            status_code=409,
        )

    pages = get_processed_pages(client, document_id)
    if not pages:
        raise DocumentServiceError(
            "Document must be processed before extraction",
            status_code=400,
        )

    _update_document(
        client,
        document_id,
        {"status": "extracting", "processing_error": None},
    )

    try:
        images = download_page_images(client, pages)

        # Load existing vendor hints before AI call (re-extract / known vendor)
        from app.services.vendor_service import (
            associate_document_vendor,
            format_hints_for_prompt,
            load_vendor_hints_for_document,
        )

        hint_payload = vendor_hints
        if hint_payload is None:
            raw_hints = load_vendor_hints_for_document(client, document)
            hint_payload = format_hints_for_prompt(raw_hints)

        provider = get_extraction_provider()
        extraction, raw_response, model, latency_ms, cost_usd = call_ai_provider_with_retries(
            provider,
            images,
            vendor_hints=hint_payload,
        )
        saved = save_extraction(
            client,
            document_id=document_id,
            extraction=extraction,
            raw_response=raw_response,
            model=model,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
        save_field_values(
            client,
            extraction_id=str(saved["id"]),
            extraction=extraction,
        )
        # Auto-link vendor after extraction (unique document count maintained)
        try:
            refreshed = _fetch_document(client, document_id)
            associate_document_vendor(
                client,
                document_id=document_id,
                document=refreshed,
                extraction=extraction,
            )
        except Exception as exc:
            logger.warning("Vendor association skipped for %s: %s", document_id, exc)

        _update_document(
            client,
            document_id,
            {"status": "ready", "processing_error": None},
        )

        final_status = "ready"
        try:
            from app.services.validation_service import validate_document

            validation = validate_document(document_id)
            final_status = validation.status or "ready"
        except DocumentServiceError as exc:
            logger.warning(
                "Post-extraction validation skipped for %s: %s",
                document_id,
                exc.message,
            )
        except Exception as exc:
            logger.warning(
                "Post-extraction validation failed for %s: %s",
                document_id,
                exc,
            )

        return ExtractionRunResponse(
            message=message,
            document_id=document_id,
            extraction_id=str(saved["id"]),
            document_type=extraction.document_type,
            overall_confidence=float(extraction.overall_confidence),
            status=final_status,
            fields=extraction.fields,
            line_items=extraction.line_items,
        )
    except DocumentServiceError as exc:
        logger.exception("Extraction failed for document %s", document_id)
        try:
            _update_document(
                client,
                document_id,
                {"status": "failed", "processing_error": exc.message},
            )
        except Exception:
            logger.exception("Failed to mark document %s as failed", document_id)
        raise
    except Exception as exc:
        logger.exception("Unexpected extraction failure for %s", document_id)
        try:
            _update_document(
                client,
                document_id,
                {"status": "failed", "processing_error": str(exc)},
            )
        except Exception:
            logger.exception("Failed to mark document %s as failed", document_id)
        raise DocumentServiceError(
            f"Extraction failed: {exc}",
            status_code=500,
        ) from exc


def extract_document(
    document_id: str,
    *,
    vendor_hints: dict[str, Any] | None = None,
) -> ExtractionRunResponse:
    return _run_extraction(
        document_id,
        vendor_hints=vendor_hints,
        message="Document extracted successfully",
    )


def reextract_document(
    document_id: str,
    *,
    vendor_hints: dict[str, Any] | None = None,
) -> ExtractionRunResponse:
    """Run extraction again and preserve previous extraction history."""
    return _run_extraction(
        document_id,
        vendor_hints=vendor_hints,
        message="Document re-extracted successfully",
    )


def get_latest_extraction(document_id: str) -> DocumentExtractionDetailResponse:
    client = _require_client()
    _fetch_document(client, document_id)

    response = (
        client.table("extractions")
        .select("*")
        .eq("document_id", document_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError(
            "No extraction found for this document",
            status_code=404,
        )

    row = response.data[0]
    fields_json = row.get("fields_json") or {}
    document_type = "unknown"
    fields: dict[str, Any] = {}
    if isinstance(fields_json, dict):
        document_type = fields_json.get("document_type") or "unknown"
        fields = {
            key: value
            for key, value in fields_json.items()
            if key != "document_type"
        }

    line_items = row.get("line_items_json") or []
    if not isinstance(line_items, list):
        line_items = []

    return DocumentExtractionDetailResponse(
        document_id=document_id,
        extraction=ExtractionSummary(
            id=str(row["id"]),
            document_type=document_type,
            schema_version=row.get("schema_version"),
            model=row.get("model"),
            overall_confidence=row.get("overall_confidence"),
            latency_ms=row.get("latency_ms"),
            cost_usd=row.get("cost_usd"),
            created_at=row.get("created_at"),
        ),
        fields=fields,
        line_items=line_items,
        validation_flags=[],
    )

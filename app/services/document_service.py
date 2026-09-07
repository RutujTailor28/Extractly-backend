"""Document upload and management business logic."""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.schemas.document import (
    ALLOWED_STATUSES,
    DocumentCore,
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentFileResponse,
    DocumentListItem,
    DocumentListQuery,
    DocumentListResponse,
    DocumentUpdateRequest,
    DocumentUpdateResponse,
    DocumentUploadItem,
    DocumentUploadResponse,
    ExtractionSummary,
    FieldValueItem,
    PaginationMeta,
    UploadErrorItem,
    ValidationFlagItem,
)
from app.utils.file_utils import (
    FileValidationError,
    build_storage_path,
    compute_sha256,
    sanitize_filename,
    validate_extension,
    validate_file_size,
    validate_mime_type,
)

logger = logging.getLogger(__name__)

SORT_WHITELIST = {
    "created_at": "created_at",
    "filename": "filename",
    "status": "status",
}


@dataclass
class IncomingUpload:
    """Normalized upload payload passed from the API layer."""

    filename: str | None
    content_type: str | None
    content: bytes


class DocumentServiceError(Exception):
    """Raised for service-level failures that should map to HTTP errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        code: str | None = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def _upload_to_storage(
    *,
    client,
    bucket: str,
    storage_path: str,
    content: bytes,
    mime_type: str,
) -> None:
    try:
        client.storage.from_(bucket).upload(
            path=storage_path,
            file=content,
            file_options={
                "content-type": mime_type,
                "upsert": "false",
            },
        )
    except Exception as exc:
        raise DocumentServiceError(
            f"Storage upload failed: {exc}",
            status_code=500,
        ) from exc


def _delete_from_storage(
    *,
    client,
    bucket: str,
    storage_path: str,
    raise_on_error: bool = False,
) -> None:
    try:
        client.storage.from_(bucket).remove([storage_path])
    except Exception as exc:
        if raise_on_error:
            raise DocumentServiceError(
                f"Storage deletion failed: {exc}",
                status_code=500,
            ) from exc
        logger.warning("Best-effort storage cleanup failed for %s: %s", storage_path, exc)


def _insert_document_record(*, client, record: dict) -> dict:
    try:
        response = client.table("documents").insert(record).execute()
    except Exception as exc:
        raise DocumentServiceError(
            f"Database insert failed: {exc}",
            status_code=500,
        ) from exc

    if not response.data:
        raise DocumentServiceError(
            "Database insert returned no data",
            status_code=500,
        )
    return response.data[0]


def _fetch_document_row(client, document_id: str) -> dict:
    try:
        response = (
            client.table("documents")
            .select("*")
            .eq("id", document_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise DocumentServiceError(
            f"Failed to fetch document: {exc}",
            status_code=500,
        ) from exc

    if not response.data:
        raise DocumentServiceError("Document not found", status_code=404)
    return response.data[0]


def resolve_storage_path(document: dict) -> str:
    """Resolve the Storage object path for a document row."""
    stored = document.get("storage_path")
    if stored:
        return str(stored)
    document_id = str(document["id"])
    filename = document.get("filename") or "file"
    return build_storage_path(document_id, filename)


def upload_single_document(
    upload: IncomingUpload,
    *,
    source: str = "upload",
    user_id: str | None = None,
    max_bytes: int | None = None,
) -> DocumentUploadItem:
    """Validate, store, and persist a single uploaded document."""
    client = _require_client()

    safe_filename = sanitize_filename(upload.filename)
    extension = validate_extension(safe_filename)
    mime_type = validate_mime_type(upload.content_type, extension)

    content = upload.content
    validate_file_size(len(content), max_bytes=max_bytes)

    document_id = str(uuid.uuid4())
    file_hash = compute_sha256(content)
    storage_path = build_storage_path(document_id, safe_filename)
    bucket = settings.SUPABASE_DOCUMENTS_BUCKET
    created_at = datetime.now(timezone.utc)

    _upload_to_storage(
        client=client,
        bucket=bucket,
        storage_path=storage_path,
        content=content,
        mime_type=mime_type,
    )

    record = {
        "id": document_id,
        "filename": safe_filename,
        "file_hash": file_hash,
        "mime_type": mime_type,
        "file_size": len(content),
        "page_count": 1,
        "status": "queued",
        "source": source,
        "storage_path": storage_path,
        "created_at": created_at.isoformat(),
    }
    if user_id is not None:
        record["user_id"] = user_id

    try:
        saved = _insert_document_record(client=client, record=record)
    except DocumentServiceError:
        _delete_from_storage(
            client=client,
            bucket=bucket,
            storage_path=storage_path,
        )
        raise

    return DocumentUploadItem(
        id=str(saved.get("id", document_id)),
        filename=saved.get("filename", safe_filename),
        mime_type=saved.get("mime_type", mime_type),
        file_size=int(saved.get("file_size", len(content))),
        status=saved.get("status", "queued"),
        source=saved.get("source", source),
        created_at=saved.get("created_at", created_at),
    )


def upload_documents(uploads: list[IncomingUpload]) -> DocumentUploadResponse:
    """Upload one or more documents with per-file success/error results."""
    documents: list[DocumentUploadItem] = []
    errors: list[UploadErrorItem] = []

    for upload in uploads:
        display_name = upload.filename or "unknown"
        try:
            documents.append(upload_single_document(upload))
        except FileValidationError as exc:
            errors.append(UploadErrorItem(filename=display_name, error=exc.message))
        except DocumentServiceError as exc:
            errors.append(UploadErrorItem(filename=display_name, error=exc.message))
        except Exception as exc:
            errors.append(
                UploadErrorItem(
                    filename=display_name,
                    error=f"Unexpected upload failure: {exc}",
                )
            )

    return DocumentUploadResponse(
        message="Upload completed",
        successful=len(documents),
        failed=len(errors),
        documents=documents,
        errors=errors,
    )


def _unresolved_flag_document_ids(client) -> set[str]:
    try:
        response = (
            client.table("validation_flags")
            .select("document_id")
            .eq("resolved", False)
            .execute()
        )
    except Exception:
        return set()
    return {str(row["document_id"]) for row in (response.data or []) if row.get("document_id")}


def _has_flags_for_ids(client, document_ids: list[str]) -> set[str]:
    if not document_ids:
        return set()
    try:
        response = (
            client.table("validation_flags")
            .select("document_id")
            .eq("resolved", False)
            .in_("document_id", document_ids)
            .execute()
        )
    except Exception:
        return set()
    return {str(row["document_id"]) for row in (response.data or []) if row.get("document_id")}


def list_documents(query: DocumentListQuery) -> DocumentListResponse:
    """List documents with pagination, filters, search, and sorting."""
    client = _require_client()

    sort_column = SORT_WHITELIST[query.sort_by]
    descending = query.sort_order == "desc"
    start = (query.page - 1) * query.page_size
    end = start + query.page_size - 1

    builder = client.table("documents").select("*", count="exact")

    if query.status:
        if query.status not in ALLOWED_STATUSES:
            raise DocumentServiceError("Invalid status filter", status_code=422)
        builder = builder.eq("status", query.status)

    if query.search:
        builder = builder.ilike("filename", f"%{query.search}%")

    if query.date_from:
        builder = builder.gte("created_at", query.date_from.isoformat())

    if query.date_to:
        # Inclusive end-of-day for date-only filters
        builder = builder.lte("created_at", f"{query.date_to.isoformat()}T23:59:59.999999+00:00")

    if query.flagged is True:
        flagged_ids = list(_unresolved_flag_document_ids(client))
        if not flagged_ids:
            return DocumentListResponse(
                items=[],
                pagination=PaginationMeta(
                    page=query.page,
                    page_size=query.page_size,
                    total=0,
                    total_pages=0,
                ),
            )
        builder = builder.in_("id", flagged_ids)
    elif query.flagged is False:
        flagged_ids = list(_unresolved_flag_document_ids(client))
        if flagged_ids:
            # PostgREST: not.in — use filter string for exclusion
            builder = builder.not_.in_("id", flagged_ids)

    # Vendor filter is accepted for API compatibility. Documents are not linked
    # to vendors until extraction is implemented, so this is currently a no-op.
    _ = query.vendor

    builder = builder.order(sort_column, desc=descending).range(start, end)

    try:
        response = builder.execute()
    except Exception as exc:
        raise DocumentServiceError(
            f"Failed to list documents: {exc}",
            status_code=500,
        ) from exc

    rows = response.data or []
    total = int(response.count or 0)
    total_pages = math.ceil(total / query.page_size) if total else 0

    flagged_on_page = _has_flags_for_ids(client, [str(row["id"]) for row in rows])

    items = [
        DocumentListItem(
            id=str(row["id"]),
            filename=row.get("filename") or "",
            mime_type=row.get("mime_type") or "",
            file_size=row.get("file_size"),
            page_count=row.get("page_count") or 1,
            status=row.get("status") or "queued",
            source=row.get("source") or "upload",
            created_at=row.get("created_at"),
            vendor_name=None,
            invoice_number=None,
            total=None,
            overall_confidence=None,
            has_flags=str(row["id"]) in flagged_on_page,
        )
        for row in rows
    ]

    return DocumentListResponse(
        items=items,
        pagination=PaginationMeta(
            page=query.page,
            page_size=query.page_size,
            total=total,
            total_pages=total_pages,
        ),
    )


def get_document(document_id: str) -> DocumentDetailResponse:
    """Return document details with empty extraction placeholders when absent."""
    client = _require_client()
    row = _fetch_document_row(client, document_id)

    extraction: ExtractionSummary | None = None
    fields: list[FieldValueItem] = []
    flags: list[ValidationFlagItem] = []

    try:
        extraction_response = (
            client.table("extractions")
            .select("*")
            .eq("document_id", document_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if extraction_response.data:
            ext = extraction_response.data[0]
            extraction = ExtractionSummary(
                id=str(ext["id"]),
                schema_version=ext.get("schema_version"),
                model=ext.get("model"),
                overall_confidence=float(ext["overall_confidence"])
                if ext.get("overall_confidence") is not None
                else None,
                created_at=ext.get("created_at"),
            )
            fields_response = (
                client.table("field_values")
                .select("*")
                .eq("extraction_id", ext["id"])
                .execute()
            )
            fields = [
                FieldValueItem(
                    id=str(item["id"]),
                    field_name=item.get("field_name") or "",
                    value=item.get("value"),
                    confidence=float(item["confidence"])
                    if item.get("confidence") is not None
                    else None,
                    page_number=item.get("page_number"),
                    corrected_value=item.get("corrected_value"),
                )
                for item in (fields_response.data or [])
            ]
    except Exception:
        extraction = None
        fields = []

    try:
        flags_response = (
            client.table("validation_flags")
            .select("*")
            .eq("document_id", document_id)
            .order("created_at", desc=True)
            .execute()
        )
        flags = [
            ValidationFlagItem(
                id=str(item["id"]),
                rule_code=item.get("rule_code") or "",
                severity=item.get("severity") or "warning",
                message=item.get("message") or "",
                resolved=bool(item.get("resolved", False)),
                created_at=item.get("created_at"),
            )
            for item in (flags_response.data or [])
        ]
    except Exception:
        flags = []

    return DocumentDetailResponse(
        document=DocumentCore(
            id=str(row["id"]),
            filename=row.get("filename") or "",
            mime_type=row.get("mime_type") or "",
            file_size=row.get("file_size"),
            page_count=row.get("page_count") or 1,
            status=row.get("status") or "queued",
            source=row.get("source") or "upload",
            storage_path=row.get("storage_path"),
            file_hash=row.get("file_hash"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        ),
        extraction=extraction,
        fields=fields,
        validation_flags=flags,
    )


def update_document(
    document_id: str,
    payload: DocumentUpdateRequest,
) -> DocumentUpdateResponse:
    """Update allowed document metadata fields only."""
    client = _require_client()
    _fetch_document_row(client, document_id)

    updates: dict = {}
    if payload.status is not None:
        if payload.status not in ALLOWED_STATUSES:
            raise DocumentServiceError("Invalid status value", status_code=422)
        updates["status"] = payload.status
    if payload.source is not None:
        updates["source"] = payload.source

    try:
        response = (
            client.table("documents")
            .update(updates)
            .eq("id", document_id)
            .execute()
        )
    except Exception as exc:
        raise DocumentServiceError(
            f"Failed to update document: {exc}",
            status_code=500,
        ) from exc

    if not response.data:
        raise DocumentServiceError("Document not found", status_code=404)

    row = response.data[0]
    return DocumentUpdateResponse(
        id=str(row["id"]),
        filename=row.get("filename") or "",
        status=row.get("status") or "",
        updated_at=row.get("updated_at"),
    )


def delete_document(document_id: str) -> DocumentDeleteResponse:
    """Delete storage object first, then the database record."""
    client = _require_client()
    row = _fetch_document_row(client, document_id)
    storage_path = resolve_storage_path(row)
    bucket = settings.SUPABASE_DOCUMENTS_BUCKET

    _delete_from_storage(
        client=client,
        bucket=bucket,
        storage_path=storage_path,
        raise_on_error=True,
    )

    try:
        response = client.table("documents").delete().eq("id", document_id).execute()
    except Exception as exc:
        logger.error(
            "Database delete failed after storage removal for document %s path %s: %s",
            document_id,
            storage_path,
            exc,
        )
        raise DocumentServiceError(
            f"Database delete failed after storage removal: {exc}",
            status_code=500,
        ) from exc

    if response.data is None:
        # Some clients return empty data on delete; treat missing row as success if prior fetch worked
        pass

    return DocumentDeleteResponse(
        message="Document deleted successfully",
        id=document_id,
    )


def get_document_file_url(document_id: str) -> DocumentFileResponse:
    """Create a temporary signed URL for the original uploaded file."""
    client = _require_client()
    row = _fetch_document_row(client, document_id)
    storage_path = resolve_storage_path(row)
    expires_in = settings.FILE_URL_EXPIRY_SECONDS
    bucket = settings.SUPABASE_DOCUMENTS_BUCKET

    try:
        signed = client.storage.from_(bucket).create_signed_url(
            storage_path,
            expires_in,
        )
    except Exception as exc:
        raise DocumentServiceError(
            f"Failed to create signed URL: {exc}",
            status_code=500,
        ) from exc

    url = None
    if isinstance(signed, dict):
        url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url")
    if not url:
        raise DocumentServiceError(
            "Signed URL response was empty",
            status_code=500,
        )

    return DocumentFileResponse(
        document_id=str(row["id"]),
        filename=row.get("filename") or "",
        mime_type=row.get("mime_type") or "",
        url=url,
        expires_in=expires_in,
    )

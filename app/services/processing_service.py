"""Document preprocessing service — PDF/image to normalized page PNGs."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.schemas.processing import (
    DocumentPageItem,
    DocumentPageResponse,
    DocumentPagesResponse,
    DocumentProcessResponse,
)
from app.services.document_service import DocumentServiceError, resolve_storage_path
from app.utils.image_utils import preprocess_image
from app.utils.pdf_utils import get_pdf_page_count, render_pdf_pages

logger = logging.getLogger(__name__)

PROCESSABLE_STATUSES = {"queued", "failed", "ready"}
BLOCKED_STATUSES = {"preprocessing", "extracting", "validating"}
SUPPORTED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}


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


def download_original_file(client, storage_path: str) -> bytes:
    """Download the original uploaded file from the documents bucket."""
    bucket = settings.SUPABASE_DOCUMENTS_BUCKET
    try:
        data = client.storage.from_(bucket).download(storage_path)
    except Exception as exc:
        raise DocumentServiceError(
            f"Failed to download original file: {exc}",
            status_code=500,
        ) from exc

    if not data:
        raise DocumentServiceError(
            "Original file download returned empty content",
            status_code=500,
        )
    return data


def build_processed_page_path(document_id: str, page_number: int) -> str:
    return f"processed/{document_id}/page-{page_number}.png"


def upload_processed_page(
    client,
    *,
    storage_path: str,
    content: bytes,
) -> None:
    bucket = settings.PROCESSED_DOCUMENTS_BUCKET
    try:
        # Upsert so reprocessing replaces existing objects cleanly
        client.storage.from_(bucket).upload(
            path=storage_path,
            file=content,
            file_options={
                "content-type": "image/png",
                "upsert": "true",
            },
        )
    except Exception as exc:
        # Some supabase-py versions use upsert differently; retry after remove
        try:
            client.storage.from_(bucket).remove([storage_path])
            client.storage.from_(bucket).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": "image/png"},
            )
        except Exception as retry_exc:
            raise DocumentServiceError(
                f"Failed to upload processed page: {retry_exc}",
                status_code=500,
            ) from retry_exc


def save_page_metadata(client, record: dict) -> dict:
    response = client.table("document_pages").insert(record).execute()
    if not response.data:
        raise DocumentServiceError(
            "Failed to save page metadata",
            status_code=500,
        )
    return response.data[0]


def _list_existing_pages(client, document_id: str) -> list[dict]:
    response = (
        client.table("document_pages")
        .select("*")
        .eq("document_id", document_id)
        .order("page_number", desc=False)
        .execute()
    )
    return response.data or []


def cleanup_processed_pages(client, document_id: str) -> None:
    """Delete processed page rows and storage objects for a document."""
    pages = _list_existing_pages(client, document_id)
    paths = [
        page["processed_storage_path"]
        for page in pages
        if page.get("processed_storage_path")
    ]
    bucket = settings.PROCESSED_DOCUMENTS_BUCKET
    if paths:
        try:
            client.storage.from_(bucket).remove(paths)
        except Exception as exc:
            logger.warning(
                "Failed to remove processed storage objects for %s: %s",
                document_id,
                exc,
            )
    try:
        client.table("document_pages").delete().eq("document_id", document_id).execute()
    except Exception as exc:
        logger.warning(
            "Failed to delete document_pages rows for %s: %s",
            document_id,
            exc,
        )


def preprocess_page(content: bytes) -> tuple[bytes, dict, int, int]:
    png_bytes, metadata, width, height = preprocess_image(content)
    return png_bytes, metadata.to_dict(), width, height


def process_pdf(
    client,
    *,
    document_id: str,
    content: bytes,
    original_storage_path: str,
) -> list[dict]:
    page_images = render_pdf_pages(content)
    if not page_images:
        raise DocumentServiceError("PDF has no pages to process", status_code=400)

    saved_pages: list[dict] = []
    uploaded_paths: list[str] = []

    try:
        for index, page_png in enumerate(page_images, start=1):
            processed_bytes, metadata, width, height = preprocess_page(page_png)
            storage_path = build_processed_page_path(document_id, index)
            upload_processed_page(
                client,
                storage_path=storage_path,
                content=processed_bytes,
            )
            uploaded_paths.append(storage_path)
            record = save_page_metadata(
                client,
                {
                    "document_id": document_id,
                    "page_number": index,
                    "original_storage_path": original_storage_path,
                    "processed_storage_path": storage_path,
                    "width": width,
                    "height": height,
                    "mime_type": "image/png",
                    "processing_metadata": {
                        **metadata,
                        "source": "pdf",
                        "pdf_page_count": len(page_images),
                    },
                },
            )
            saved_pages.append(record)
    except Exception:
        # Clean partial uploads/rows for this attempt
        if uploaded_paths:
            try:
                client.storage.from_(settings.PROCESSED_DOCUMENTS_BUCKET).remove(
                    uploaded_paths
                )
            except Exception:
                pass
        client.table("document_pages").delete().eq("document_id", document_id).execute()
        raise

    return saved_pages


def process_image(
    client,
    *,
    document_id: str,
    content: bytes,
    original_storage_path: str,
) -> list[dict]:
    processed_bytes, metadata, width, height = preprocess_page(content)
    storage_path = build_processed_page_path(document_id, 1)
    upload_processed_page(
        client,
        storage_path=storage_path,
        content=processed_bytes,
    )
    try:
        record = save_page_metadata(
            client,
            {
                "document_id": document_id,
                "page_number": 1,
                "original_storage_path": original_storage_path,
                "processed_storage_path": storage_path,
                "width": width,
                "height": height,
                "mime_type": "image/png",
                "processing_metadata": {
                    **metadata,
                    "source": "image",
                },
            },
        )
    except Exception:
        try:
            client.storage.from_(settings.PROCESSED_DOCUMENTS_BUCKET).remove(
                [storage_path]
            )
        except Exception:
            pass
        raise
    return [record]


def process_document(document_id: str) -> DocumentProcessResponse:
    """
    Process an uploaded document into normalized page PNGs.

    Status flow: queued|failed|ready → preprocessing → ready|failed
    """
    client = _require_client()
    document = _fetch_document(client, document_id)
    status = document.get("status") or "queued"
    mime_type = (document.get("mime_type") or "").lower()

    if status in BLOCKED_STATUSES:
        raise DocumentServiceError(
            f"Document cannot be processed while status is '{status}'",
            status_code=409,
        )

    if status not in PROCESSABLE_STATUSES:
        raise DocumentServiceError(
            f"Document status '{status}' is not eligible for processing. "
            "Allowed: queued, failed, ready",
            status_code=409,
        )

    if mime_type not in SUPPORTED_MIME_TYPES:
        raise DocumentServiceError(
            f"Unsupported document type '{mime_type}'. "
            "Allowed: application/pdf, image/jpeg, image/png",
            status_code=415,
        )

    storage_path = resolve_storage_path(document)

    # Reprocessing: clear previous processed pages first
    cleanup_processed_pages(client, document_id)

    _update_document(
        client,
        document_id,
        {
            "status": "preprocessing",
            "processing_error": None,
        },
    )

    try:
        content = download_original_file(client, storage_path)

        if mime_type == "application/pdf":
            # Validate page count early
            page_count = get_pdf_page_count(content)
            if page_count < 1:
                raise DocumentServiceError("PDF has no pages", status_code=400)
            pages = process_pdf(
                client,
                document_id=document_id,
                content=content,
                original_storage_path=storage_path,
            )
        else:
            pages = process_image(
                client,
                document_id=document_id,
                content=content,
                original_storage_path=storage_path,
            )

        page_count = len(pages)
        _update_document(
            client,
            document_id,
            {
                "status": "ready",
                "page_count": page_count,
                "processing_error": None,
            },
        )

        return DocumentProcessResponse(
            message="Document processed successfully",
            document_id=document_id,
            status="ready",
            page_count=page_count,
            pages_processed=page_count,
        )
    except DocumentServiceError as exc:
        logger.exception("Processing failed for document %s", document_id)
        cleanup_processed_pages(client, document_id)
        try:
            _update_document(
                client,
                document_id,
                {
                    "status": "failed",
                    "processing_error": exc.message,
                },
            )
        except Exception:
            logger.exception(
                "Failed to mark document %s as failed after processing error",
                document_id,
            )
        raise
    except Exception as exc:
        logger.exception("Unexpected processing failure for document %s", document_id)
        cleanup_processed_pages(client, document_id)
        try:
            _update_document(
                client,
                document_id,
                {
                    "status": "failed",
                    "processing_error": str(exc),
                },
            )
        except Exception:
            logger.exception(
                "Failed to mark document %s as failed after unexpected error",
                document_id,
            )
        raise DocumentServiceError(
            f"Document processing failed: {exc}",
            status_code=500,
        ) from exc


def list_document_pages(document_id: str) -> DocumentPagesResponse:
    client = _require_client()
    _fetch_document(client, document_id)
    pages = _list_existing_pages(client, document_id)
    items = [
        DocumentPageItem(
            page_number=int(page["page_number"]),
            width=page.get("width"),
            height=page.get("height"),
            mime_type=page.get("mime_type") or "image/png",
            processing_metadata=page.get("processing_metadata"),
        )
        for page in pages
    ]
    return DocumentPagesResponse(
        document_id=document_id,
        total_pages=len(items),
        pages=items,
    )


def get_document_page(
    document_id: str,
    page_number: int,
) -> DocumentPageResponse:
    if page_number < 1:
        raise DocumentServiceError(
            "page_number must be >= 1",
            status_code=422,
        )

    client = _require_client()
    _fetch_document(client, document_id)

    response = (
        client.table("document_pages")
        .select("*")
        .eq("document_id", document_id)
        .eq("page_number", page_number)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError("Processed page not found", status_code=404)

    page = response.data[0]
    storage_path = page.get("processed_storage_path")
    if not storage_path:
        raise DocumentServiceError(
            "Processed page is missing storage path",
            status_code=500,
        )

    expires_in = settings.FILE_URL_EXPIRY_SECONDS
    try:
        signed = client.storage.from_(settings.PROCESSED_DOCUMENTS_BUCKET).create_signed_url(
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
        raise DocumentServiceError("Signed URL response was empty", status_code=500)

    return DocumentPageResponse(
        document_id=document_id,
        page_number=int(page["page_number"]),
        mime_type=page.get("mime_type") or "image/png",
        width=page.get("width"),
        height=page.get("height"),
        url=url,
        expires_in=expires_in,
        created_at=page.get("created_at"),
    )

"""Document upload and management API routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.document import (
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentFileResponse,
    DocumentListQuery,
    DocumentListResponse,
    DocumentStatus,
    DocumentUpdateRequest,
    DocumentUpdateResponse,
    DocumentUploadResponse,
    SortByField,
    SortOrder,
    UploadErrorItem,
)
from app.services.document_service import (
    DocumentServiceError,
    IncomingUpload,
    delete_document,
    get_document,
    get_document_file_url,
    list_documents,
    update_document,
    upload_documents,
)
from app.utils.file_utils import FileValidationError

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _raise_service_error(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


async def _read_upload_file(file: UploadFile) -> bytes:
    """Read an uploaded file in chunks, enforcing the configured size limit."""
    max_bytes = settings.max_file_size_bytes
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FileValidationError(
                f"File exceeds maximum allowed size of {settings.MAX_FILE_SIZE_MB} MB",
                code="FILE_TOO_LARGE",
            )
        chunks.append(chunk)

    return b"".join(chunks)


@router.post("", response_model=DocumentUploadResponse)
@router.post("/", response_model=DocumentUploadResponse, include_in_schema=False)
async def create_documents(
    files: list[UploadFile] = File(...),
) -> DocumentUploadResponse:
    """Upload one or more invoice/receipt files."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    uploads: list[IncomingUpload] = []
    read_errors: list[UploadErrorItem] = []

    for file in files:
        filename = file.filename or "unknown"
        try:
            content = await _read_upload_file(file)
            uploads.append(
                IncomingUpload(
                    filename=file.filename,
                    content_type=file.content_type,
                    content=content,
                )
            )
        except FileValidationError as exc:
            read_errors.append(UploadErrorItem(filename=filename, error=exc.message))
        finally:
            await file.close()

    response = upload_documents(uploads)
    if read_errors:
        response.errors.extend(read_errors)
        response.failed = len(response.errors)

    return response


@router.get("", response_model=DocumentListResponse)
@router.get("/", response_model=DocumentListResponse, include_in_schema=False)
def get_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: DocumentStatus | None = Query(None),
    search: str | None = Query(None),
    vendor: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    flagged: bool | None = Query(None),
    sort_by: SortByField = Query("created_at"),
    sort_order: SortOrder = Query("desc"),
) -> DocumentListResponse:
    """List documents with pagination, search, filters, and sorting."""
    try:
        query = DocumentListQuery(
            page=page,
            page_size=page_size,
            status=status,
            search=search,
            vendor=vendor,
            date_from=date_from,
            date_to=date_to,
            flagged=flagged,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValidationError as exc:
        messages = [err.get("msg", "Invalid query parameter") for err in exc.errors()]
        raise HTTPException(status_code=422, detail=messages) from exc

    try:
        return list_documents(query)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.get("/{document_id}/file", response_model=DocumentFileResponse)
def get_document_file(document_id: str) -> DocumentFileResponse:
    """Return a temporary signed URL for the original uploaded file."""
    try:
        return get_document_file_url(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document_by_id(document_id: str) -> DocumentDetailResponse:
    """Return document details; extraction may be null before AI phases."""
    try:
        return get_document(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.patch("/{document_id}", response_model=DocumentUpdateResponse)
def patch_document(
    document_id: str,
    payload: DocumentUpdateRequest,
) -> DocumentUpdateResponse:
    """Update allowed document metadata (status, optional source)."""
    try:
        return update_document(document_id, payload)
    except DocumentServiceError as exc:
        _raise_service_error(exc)


@router.delete("/{document_id}", response_model=DocumentDeleteResponse)
def remove_document(document_id: str) -> DocumentDeleteResponse:
    """Delete storage object and database record."""
    try:
        return delete_document(document_id)
    except DocumentServiceError as exc:
        _raise_service_error(exc)

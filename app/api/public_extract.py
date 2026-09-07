"""Public developer extraction API (X-API-Key authenticated)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile

from app.core.api_key_auth import ApiKeyDep
from app.schemas.public_api import (
    ExtractionCompletedResponse,
    ExtractionJobAcceptedResponse,
    ExtractionJobStatusResponse,
)
from app.services.document_service import DocumentServiceError
from app.services.public_extract_service import (
    create_public_extraction,
    get_public_job_status,
)

router = APIRouter(prefix="/api/v1/extract", tags=["Public API"])


def _public_http_error(exc: DocumentServiceError) -> None:
    detail = {
        "error": {
            "code": exc.code or "INTERNAL_ERROR",
            "message": exc.message,
        }
    }
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.post(
    "",
    response_model=ExtractionJobAcceptedResponse | ExtractionCompletedResponse,
    responses={
        200: {"description": "Synchronous completed extraction (small images)"},
        202: {"description": "Accepted async job (PDFs / large files)"},
    },
)
async def public_extract_endpoint(
    background_tasks: BackgroundTasks,
    api_key: ApiKeyDep,
    file: UploadFile = File(...),
    webhook_url: str | None = Form(None),
    metadata: str | None = Form(None),
    include_metadata: bool = Query(False),
) -> ExtractionJobAcceptedResponse | ExtractionCompletedResponse:
    """
    Upload an invoice/receipt for extraction using an API key.

    Small JPG/PNG files may complete synchronously (200).
    PDFs and larger files return 202 with a job_id for polling.
    """
    try:
        result = await create_public_extraction(
            file=file,
            api_key=api_key,
            background_tasks=background_tasks,
            webhook_url=webhook_url,
            metadata=metadata,
            include_metadata=include_metadata,
        )
    except DocumentServiceError as exc:
        _public_http_error(exc)

    # FastAPI: return 202 for async accepted responses
    if isinstance(result, ExtractionJobAcceptedResponse):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=202,
            content=result.model_dump(mode="json"),
        )
    return result


@router.get("/{job_id}", response_model=ExtractionJobStatusResponse)
def public_job_status_endpoint(
    job_id: str,
    api_key: ApiKeyDep,
    include_metadata: bool = Query(False),
) -> ExtractionJobStatusResponse:
    """Poll extraction job status / result (scoped to API key owner)."""
    try:
        return get_public_job_status(
            job_id,
            api_key=api_key,
            include_metadata=include_metadata,
        )
    except DocumentServiceError as exc:
        _public_http_error(exc)

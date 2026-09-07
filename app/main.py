import utils.session
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.ai import router as ai_router
from app.api.dev import router as dev_router
from app.api.documents import router as documents_router
from app.api.extraction import router as extraction_router
from app.api.health import router as health_router
from app.api.processing import router as processing_router
from app.api.review import router as review_router
from app.api.analytics import router as analytics_router
from app.api.vendors import router as vendors_router
from app.api.export import router as export_router
from app.api.integrations import router as integrations_router
from app.api.api_keys import router as api_keys_router
from app.api.public_extract import router as public_extract_router
from app.api.validation import (
    document_validation_router,
    rules_router as validation_rules_router,
)
from app.core.config import settings
from app.core.request_id import RequestIdMiddleware
from app.middleware.api_usage import ApiUsageMiddleware
import logging

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Invoice and Receipt Extraction API with Human Review.\n\n"
        "## Public API\n"
        "Authenticate developer requests with the `X-API-Key` header "
        "(`ext_live_...`). Create keys via `POST /api/v1/api-keys` "
        "(full secret shown once).\n\n"
        "Public extract: `POST /api/v1/extract` → poll `GET /api/v1/extract/{job_id}` "
        "or receive signed webhooks.\n\n"
        "## AI foundation\n"
        "Development endpoints under `/api/v1/ai/*` test Gemini connectivity "
        "(no invoice extraction in this phase)."
    ),
    version=settings.APP_VERSION,
)

app.add_middleware(ApiUsageMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(dev_router)
app.include_router(ai_router)
app.include_router(public_extract_router)
app.include_router(api_keys_router)
app.include_router(extraction_router)
app.include_router(processing_router)
app.include_router(document_validation_router)
app.include_router(validation_rules_router)
app.include_router(review_router)
app.include_router(vendors_router)
app.include_router(analytics_router)
app.include_router(export_router)
app.include_router(integrations_router)
app.include_router(documents_router)


@app.on_event("startup")
def validate_ai_settings_on_startup() -> None:
    """Validate AI env config without calling paid APIs."""
    try:
        settings.validate_ai_configuration()
        logger.info(
            "AI configuration OK provider=%s gemini_model=%s",
            settings.ai_provider_name,
            settings.GEMINI_MODEL if settings.ai_provider_name == "gemini" else None,
        )
    except ValueError as exc:
        # Do not crash the whole API for local/dev misconfig; log clearly.
        logger.error("AI configuration invalid: %s", exc)


@app.get("/")
def root():
    return {
        "message": "Extractly API is running",
        "version": settings.APP_VERSION,
    }

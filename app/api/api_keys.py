"""Internal API key management routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.api_key import (
    ApiKeyCreateResponse,
    ApiKeyListResponse,
    ApiKeyRevokeResponse,
    CreateApiKeyRequest,
)
from app.services.api_key_service import create_api_key, list_api_keys, revoke_api_key
from app.services.document_service import DocumentServiceError
from app.schemas.public_api import ApiUsageResponse
from app.services.usage_service import get_api_usage

router = APIRouter(tags=["API Keys"])


def _raise(exc: DocumentServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/api/v1/api-keys", response_model=ApiKeyCreateResponse)
def create_api_key_endpoint(payload: CreateApiKeyRequest) -> ApiKeyCreateResponse:
    """Create an API key. The full secret is returned ONLY once."""
    try:
        return create_api_key(payload, user_id=None)
    except DocumentServiceError as exc:
        _raise(exc)


@router.get("/api/v1/api-keys", response_model=ApiKeyListResponse)
def list_api_keys_endpoint() -> ApiKeyListResponse:
    """List API keys (prefix only — never the secret)."""
    try:
        return list_api_keys(user_id=None)
    except DocumentServiceError as exc:
        _raise(exc)


@router.delete("/api/v1/api-keys/{key_id}", response_model=ApiKeyRevokeResponse)
def revoke_api_key_endpoint(key_id: str) -> ApiKeyRevokeResponse:
    """Revoke an API key (soft revoke via revoked_at)."""
    try:
        return revoke_api_key(key_id, user_id=None)
    except DocumentServiceError as exc:
        _raise(exc)


@router.get("/api/v1/api-usage", response_model=ApiUsageResponse)
def api_usage_endpoint(days: int = 30) -> ApiUsageResponse:
    """Public API usage analytics for the application dashboard."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=422, detail="days must be between 1 and 365")
    try:
        return get_api_usage(days=days, user_id=None)
    except DocumentServiceError as exc:
        _raise(exc)

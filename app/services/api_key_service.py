"""API key generation, hashing, verification, and management."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.database import get_supabase_admin_client
from app.schemas.api_key import (
    ApiKeyCreateResponse,
    ApiKeyListItem,
    ApiKeyListResponse,
    ApiKeyRevokeResponse,
    CreateApiKeyRequest,
)
from app.services.document_service import DocumentServiceError

logger = logging.getLogger(__name__)

KEY_PREFIX = "ext_live_"
DEFAULT_PERMISSIONS = ["extract:read", "extract:write"]


@dataclass
class APIKeyContext:
    key_id: str
    user_id: str | None
    rate_limit: int
    permissions: list[str]
    prefix: str | None = None


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def hash_api_key(raw_key: str) -> str:
    """SHA-256 with application pepper (hex digest)."""
    material = f"{settings.API_KEY_PEPPER}:{raw_key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def generate_api_key_material() -> tuple[str, str, str]:
    """Return (full_key, prefix_for_display, key_hash)."""
    secret = secrets.token_urlsafe(32)
    full_key = f"{KEY_PREFIX}{secret}"
    # Show prefix + first 8 chars of secret for list UI
    display_prefix = f"{KEY_PREFIX}{secret[:8]}"
    return full_key, display_prefix, hash_api_key(full_key)


def verify_key_hash(raw_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), stored_hash)


def create_api_key(
    payload: CreateApiKeyRequest,
    *,
    user_id: str | None = None,
) -> ApiKeyCreateResponse:
    client = _require_client()
    full_key, display_prefix, key_hash = generate_api_key_material()
    rate_limit = payload.rate_limit or settings.API_KEY_DEFAULT_RATE_LIMIT
    row = {
        "user_id": user_id,
        "key_hash": key_hash,
        "key_prefix": display_prefix,
        "label": payload.label,
        "rate_limit": rate_limit,
        "permissions_json": DEFAULT_PERMISSIONS,
        "request_count": 0,
    }
    response = client.table("api_keys").insert(row).execute()
    if not response.data:
        raise DocumentServiceError("Failed to create API key", status_code=500)
    saved = response.data[0]
    return ApiKeyCreateResponse(
        id=str(saved["id"]),
        label=saved.get("label") or payload.label,
        api_key=full_key,
        prefix=display_prefix,
        rate_limit=int(saved.get("rate_limit") or rate_limit),
        created_at=saved.get("created_at"),
    )


def list_api_keys(*, user_id: str | None = None) -> ApiKeyListResponse:
    client = _require_client()
    query = client.table("api_keys").select(
        "id,label,key_prefix,rate_limit,last_used_at,request_count,revoked_at,created_at"
    )
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.is_("user_id", "null")
    response = query.order("created_at", desc=True).execute()
    items = [
        ApiKeyListItem(
            id=str(row["id"]),
            label=row.get("label") or "",
            prefix=row.get("key_prefix"),
            rate_limit=int(row.get("rate_limit") or 60),
            last_used_at=row.get("last_used_at"),
            request_count=int(row.get("request_count") or 0),
            revoked_at=row.get("revoked_at"),
            created_at=row.get("created_at"),
        )
        for row in (response.data or [])
    ]
    return ApiKeyListResponse(api_keys=items)


def revoke_api_key(key_id: str, *, user_id: str | None = None) -> ApiKeyRevokeResponse:
    client = _require_client()
    query = client.table("api_keys").select("*").eq("id", key_id).limit(1)
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.is_("user_id", "null")
    existing = query.execute()
    if not existing.data:
        raise DocumentServiceError("API key not found", status_code=404)
    if existing.data[0].get("revoked_at"):
        return ApiKeyRevokeResponse(message="API key was already revoked.")
    now = datetime.now(timezone.utc).isoformat()
    client.table("api_keys").update({"revoked_at": now}).eq("id", key_id).execute()
    return ApiKeyRevokeResponse()


def authenticate_api_key(raw_key: str) -> APIKeyContext:
    if not raw_key or not raw_key.strip():
        raise DocumentServiceError(
            "API key required",
            status_code=401,
            code="API_KEY_REQUIRED",
        )
    raw_key = raw_key.strip()
    if not raw_key.startswith(KEY_PREFIX):
        raise DocumentServiceError(
            "Invalid API key",
            status_code=401,
            code="INVALID_API_KEY",
        )

    client = _require_client()
    key_hash = hash_api_key(raw_key)
    response = (
        client.table("api_keys")
        .select("*")
        .eq("key_hash", key_hash)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise DocumentServiceError(
            "Invalid API key",
            status_code=401,
            code="INVALID_API_KEY",
        )
    row = response.data[0]
    if row.get("revoked_at"):
        raise DocumentServiceError(
            "API key has been revoked",
            status_code=401,
            code="API_KEY_REVOKED",
        )
    expires_at = row.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                raise DocumentServiceError(
                    "API key has expired",
                    status_code=401,
                    code="API_KEY_EXPIRED",
                )
        except DocumentServiceError:
            raise
        except Exception:
            pass

    perms = row.get("permissions_json") or DEFAULT_PERMISSIONS
    if not isinstance(perms, list):
        perms = DEFAULT_PERMISSIONS

    return APIKeyContext(
        key_id=str(row["id"]),
        user_id=row.get("user_id"),
        rate_limit=int(row.get("rate_limit") or settings.API_KEY_DEFAULT_RATE_LIMIT),
        permissions=[str(p) for p in perms],
        prefix=row.get("key_prefix"),
    )


def touch_api_key_usage(key_id: str, *, force: bool = False) -> None:
    """Update last_used_at / request_count, throttled unless force."""
    try:
        client = _require_client()
        existing = (
            client.table("api_keys")
            .select("last_used_at,request_count")
            .eq("id", key_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return
        row = existing.data[0]
        now = datetime.now(timezone.utc)
        last = row.get("last_used_at")
        should_update_ts = force
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                delta = (now - last_dt).total_seconds()
                if delta >= settings.API_KEY_LAST_USED_MIN_INTERVAL_SECONDS:
                    should_update_ts = True
            except Exception:
                should_update_ts = True
        else:
            should_update_ts = True

        updates: dict[str, Any] = {
            "request_count": int(row.get("request_count") or 0) + 1,
        }
        if should_update_ts:
            updates["last_used_at"] = now.isoformat()
        client.table("api_keys").update(updates).eq("id", key_id).execute()
    except Exception as exc:
        logger.warning("Failed to touch API key usage for %s: %s", key_id, exc)

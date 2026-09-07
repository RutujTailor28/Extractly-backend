"""API key Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=120)
    rate_limit: int = Field(default=60, ge=1)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("label is required")
        return cleaned

    @field_validator("rate_limit")
    @classmethod
    def max_rate(cls, value: int) -> int:
        if value > settings.API_KEY_MAX_RATE_LIMIT:
            raise ValueError(
                f"rate_limit cannot exceed {settings.API_KEY_MAX_RATE_LIMIT}"
            )
        return value


class ApiKeyCreateResponse(BaseModel):
    id: str
    label: str
    api_key: str
    prefix: str
    rate_limit: int
    created_at: datetime | None = None


class ApiKeyListItem(BaseModel):
    id: str
    label: str
    prefix: str | None = None
    rate_limit: int
    last_used_at: datetime | None = None
    request_count: int = 0
    revoked_at: datetime | None = None
    created_at: datetime | None = None


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeyListItem]


class ApiKeyRevokeResponse(BaseModel):
    success: bool = True
    message: str = "API key revoked successfully."

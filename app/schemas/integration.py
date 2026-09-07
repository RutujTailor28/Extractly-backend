"""Pydantic schemas for accounting integrations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IntegrationProvider = Literal["mock"]
IntegrationStatus = Literal["success", "failed"]


class SendToAccountingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: IntegrationProvider = "mock"


class IntegrationPreviewResponse(BaseModel):
    provider: str
    document_id: str
    ready: bool
    payload: dict[str, Any] = Field(default_factory=dict)


class IntegrationResultResponse(BaseModel):
    success: bool
    provider: str
    document_id: str
    external_reference: str | None = None
    message: str
    sent_at: datetime | None = None
    already_sent: bool = False


class IntegrationHistoryItem(BaseModel):
    id: str
    provider: str
    status: IntegrationStatus
    external_reference: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None


class IntegrationHistoryResponse(BaseModel):
    document_id: str
    integrations: list[IntegrationHistoryItem] = Field(default_factory=list)

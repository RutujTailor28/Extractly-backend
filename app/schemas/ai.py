"""Pydantic schemas for AI foundation / development test routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AIHealthResponse(BaseModel):
    provider: str
    configured: bool
    connected: bool
    model: str | None = None
    message: str


class AIModelItem(BaseModel):
    name: str
    display_name: str | None = None
    supported_generation_methods: list[str] = Field(default_factory=list)
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    description: str | None = None


class AIModelsResponse(BaseModel):
    provider: str
    models: list[AIModelItem]
    configured_model: str | None = None


class AITestRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        examples=["Reply with exactly: Gemini connection successful"],
    )
    model: str | None = Field(
        default=None,
        description="Optional model override; defaults to GEMINI_MODEL",
    )


class AITestResponse(BaseModel):
    provider: str
    model: str
    prompt: str
    response: str
    latency_ms: int
    note: str = "development-only endpoint"


class AIErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] | None = None

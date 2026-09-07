"""Development AI foundation routes (Gemini connectivity / model discovery)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.schemas.ai import (
    AIHealthResponse,
    AIModelItem,
    AIModelsResponse,
    AITestRequest,
    AITestResponse,
)
from app.services.ai.exceptions import AIProviderError
from app.services.ai.factory import get_ai_provider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/ai",
    tags=["AI"],
)


def _ensure_dev() -> None:
    if settings.ENVIRONMENT.lower() not in {"development", "dev", "local", "test"}:
        raise HTTPException(
            status_code=404,
            detail="AI development endpoints are not available in this environment.",
        )


def _raise_ai(exc: AIProviderError) -> None:
    # Never attach secrets; message is already sanitized in GeminiProvider
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    ) from exc


@router.get(
    "/health",
    response_model=AIHealthResponse,
    summary="AI provider health (development)",
)
def ai_health() -> AIHealthResponse:
    """
    Check whether the configured AI provider is set up and reachable.

    Development/testing only. Does not expose API keys.
    """
    _ensure_dev()
    provider_name = settings.ai_provider_name
    if provider_name != "gemini":
        return AIHealthResponse(
            provider=provider_name,
            configured=False,
            connected=False,
            model=None,
            message=(
                f"AI_PROVIDER is '{settings.AI_PROVIDER}'. "
                "Set AI_PROVIDER=gemini and GEMINI_API_KEY to use this endpoint."
            ),
        )
    if not (settings.GEMINI_API_KEY or "").strip():
        return AIHealthResponse(
            provider="gemini",
            configured=False,
            connected=False,
            model=settings.GEMINI_MODEL,
            message="GEMINI_API_KEY is not configured",
        )

    try:
        provider = get_ai_provider()
        result = provider.health_check()
    except AIProviderError as exc:
        return AIHealthResponse(
            provider="gemini",
            configured=True,
            connected=False,
            model=settings.GEMINI_MODEL,
            message=exc.message,
        )

    return AIHealthResponse(
        provider=result.provider,
        configured=result.configured,
        connected=result.connected,
        model=result.model,
        message=result.message,
    )


@router.get(
    "/models",
    response_model=AIModelsResponse,
    summary="List accessible Gemini models (development)",
)
def ai_models() -> AIModelsResponse:
    """List models available to the configured Gemini API key."""
    _ensure_dev()
    try:
        provider = get_ai_provider()
        models = provider.list_models()
    except AIProviderError as exc:
        _raise_ai(exc)

    return AIModelsResponse(
        provider=provider.name,
        configured_model=settings.GEMINI_MODEL,
        models=[
            AIModelItem(
                name=m.name,
                display_name=m.display_name,
                supported_generation_methods=m.supported_generation_methods,
                input_token_limit=m.input_token_limit,
                output_token_limit=m.output_token_limit,
                description=m.description,
            )
            for m in models
        ],
    )


@router.post(
    "/test",
    response_model=AITestResponse,
    summary="Send a simple Gemini test prompt (development)",
)
def ai_test(payload: AITestRequest) -> AITestResponse:
    """
    Smoke-test text generation against Gemini.

    Development/testing only — not part of the invoice extraction pipeline.
    """
    _ensure_dev()
    try:
        provider = get_ai_provider()
        result = provider.generate_text(payload.prompt, model=payload.model)
    except AIProviderError as exc:
        _raise_ai(exc)

    return AITestResponse(
        provider=result.provider,
        model=result.model,
        prompt=result.prompt,
        response=result.text,
        latency_ms=result.latency_ms,
    )

"""Factory for foundation AI providers (health / models / smoke tests)."""

from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import settings
from app.services.ai.exceptions import AIConfigurationError
from app.services.ai.foundation import AIProvider
from app.services.ai.gemini_provider import GeminiProvider

logger = logging.getLogger(__name__)


def get_ai_provider(*, force_new: bool = False) -> AIProvider:
    """
    Return the configured foundation AI provider.

    Does not implement invoice extraction — use get_extraction_provider for that.
    """
    if force_new:
        _cached_ai_provider.cache_clear()
    return _cached_ai_provider()


@lru_cache
def _cached_ai_provider() -> AIProvider:
    try:
        settings.validate_ai_configuration()
    except ValueError as exc:
        raise AIConfigurationError(str(exc)) from exc

    provider = settings.ai_provider_name
    if provider == "gemini":
        logger.info(
            "Initializing Gemini foundation provider model=%s",
            settings.GEMINI_MODEL,
        )
        return GeminiProvider(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
        )

    if provider in {"mock", "fake", "test", "openai", "gpt"}:
        raise AIConfigurationError(
            f"Foundation AI test endpoints require AI_PROVIDER=gemini "
            f"(current: '{settings.AI_PROVIDER}'). "
            "Extraction can still use mock/openai via get_extraction_provider()."
        )

    raise AIConfigurationError(
        f"Unsupported AI_PROVIDER '{settings.AI_PROVIDER}' for foundation routes"
    )


def clear_ai_provider_cache() -> None:
    _cached_ai_provider.cache_clear()

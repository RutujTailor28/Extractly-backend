"""Safe AI provider exceptions (never include secrets)."""

from __future__ import annotations


class AIProviderError(Exception):
    """Base error for AI provider operations."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "AI_PROVIDER_ERROR",
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class AIConfigurationError(AIProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            status_code=503,
            code="AI_CONFIGURATION_ERROR",
        )


class AIConnectionError(AIProviderError):
    def __init__(self, message: str = "Unable to connect to AI provider") -> None:
        super().__init__(
            message,
            status_code=502,
            code="AI_CONNECTION_ERROR",
        )


class AIModelError(AIProviderError):
    def __init__(self, message: str = "AI model request failed") -> None:
        super().__init__(
            message,
            status_code=422,
            code="AI_MODEL_ERROR",
        )


class AIRateLimitError(AIProviderError):
    def __init__(self, message: str = "AI provider rate limit exceeded") -> None:
        super().__init__(
            message,
            status_code=429,
            code="AI_RATE_LIMIT_ERROR",
        )

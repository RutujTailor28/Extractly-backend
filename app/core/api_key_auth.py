"""FastAPI dependency for X-API-Key authentication + rate limiting."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request, Response

from app.services.api_key_service import APIKeyContext, authenticate_api_key, touch_api_key_usage
from app.services.document_service import DocumentServiceError
from app.services.rate_limit_service import get_rate_limiter


def _public_error(exc: DocumentServiceError) -> dict:
    return {
        "error": {
            "code": exc.code or "UNAUTHORIZED",
            "message": exc.message,
        }
    }


async def require_api_key(
    request: Request,
    response: Response,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> APIKeyContext:
    from fastapi import HTTPException

    try:
        ctx = authenticate_api_key(x_api_key or "")
    except DocumentServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=_public_error(exc)) from exc

    limiter = get_rate_limiter()
    result = limiter.check(ctx.key_id, limit=ctx.rate_limit, window_seconds=60)
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset_at)
    if not result.allowed:
        if result.retry_after is not None:
            response.headers["Retry-After"] = str(result.retry_after)
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Rate limit exceeded",
                }
            },
            headers={
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(result.reset_at),
                "Retry-After": str(result.retry_after or 60),
            },
        )

    request.state.api_key_context = ctx
    touch_api_key_usage(ctx.key_id)
    return ctx


ApiKeyDep = Annotated[APIKeyContext, Depends(require_api_key)]

"""Middleware to persist public API usage metadata."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.services.usage_service import record_api_usage

logger = logging.getLogger(__name__)

PUBLIC_PREFIXES = ("/api/v1/extract",)


class ApiUsageMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        is_public = any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES)
        if not is_public:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = int((time.perf_counter() - start) * 1000)

        ctx = getattr(request.state, "api_key_context", None)
        request_id = getattr(request.state, "request_id", None)
        try:
            record_api_usage(
                api_key_id=getattr(ctx, "key_id", None) if ctx else None,
                user_id=getattr(ctx, "user_id", None) if ctx else None,
                request_id=request_id,
                endpoint=path,
                method=request.method,
                status_code=response.status_code,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            logger.warning("API usage middleware error: %s", exc)
        return response

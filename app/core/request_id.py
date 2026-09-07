"""Request ID middleware for correlation."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{secrets.token_hex(12)}"
        request.state.request_id = request_id
        request.state.start_time = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

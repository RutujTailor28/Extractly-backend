"""Rate limiting abstraction — in-memory for local; Redis-ready interface."""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # unix epoch seconds
    retry_after: int | None = None


class RateLimitService(ABC):
    @abstractmethod
    def check(self, key: str, *, limit: int, window_seconds: int = 60) -> RateLimitResult:
        raise NotImplementedError


class MemoryRateLimitService(RateLimitService):
    """Sliding-window counter in process memory (per-API-key)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_seconds: int = 60) -> RateLimitResult:
        now = time.time()
        window_start = now - window_seconds
        with self._lock:
            q = self._hits[key]
            while q and q[0] < window_start:
                q.popleft()
            reset_at = int(now + window_seconds)
            if len(q) >= limit:
                retry_after = max(1, int(q[0] + window_seconds - now) + 1)
                return RateLimitResult(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    reset_at=reset_at,
                    retry_after=retry_after,
                )
            q.append(now)
            remaining = max(0, limit - len(q))
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=remaining,
                reset_at=reset_at,
                retry_after=None,
            )


# Process-wide default store for local/dev
_default_rate_limiter: RateLimitService = MemoryRateLimitService()


def get_rate_limiter() -> RateLimitService:
    return _default_rate_limiter


def reset_rate_limiter() -> MemoryRateLimitService:
    """Replace the process-wide limiter (tests)."""
    global _default_rate_limiter
    _default_rate_limiter = MemoryRateLimitService()
    return _default_rate_limiter

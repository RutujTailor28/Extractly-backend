"""API usage logging and analytics."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.database import get_supabase_admin_client
from app.schemas.public_api import (
    ApiUsageDayPoint,
    ApiUsageEndpointPoint,
    ApiUsageResponse,
)
from app.services.document_service import DocumentServiceError

logger = logging.getLogger(__name__)


def record_api_usage(
    *,
    api_key_id: str | None,
    user_id: str | None,
    request_id: str | None,
    endpoint: str,
    method: str,
    status_code: int,
    latency_ms: int,
) -> None:
    try:
        client = get_supabase_admin_client()
        if client is None:
            return
        client.table("api_usage_logs").insert(
            {
                "api_key_id": api_key_id,
                "user_id": user_id,
                "request_id": request_id,
                "endpoint": endpoint,
                "method": method,
                "status_code": status_code,
                "latency_ms": latency_ms,
            }
        ).execute()
    except Exception as exc:
        logger.warning("Failed to record API usage: %s", exc)


def get_api_usage(*, days: int = 30, user_id: str | None = None) -> ApiUsageResponse:
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError("Supabase client is not configured", status_code=503)

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    query = (
        client.table("api_usage_logs")
        .select("endpoint,status_code,latency_ms,created_at")
        .gte("created_at", since)
    )
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.is_("user_id", "null")
    rows = query.execute().data or []

    total = len(rows)
    successful = sum(1 for r in rows if 200 <= int(r.get("status_code") or 0) < 400)
    rate_limited = sum(1 for r in rows if int(r.get("status_code") or 0) == 429)
    failed = sum(
        1
        for r in rows
        if int(r.get("status_code") or 0) >= 400 and int(r.get("status_code") or 0) != 429
    )
    latencies = [int(r["latency_ms"]) for r in rows if r.get("latency_ms") is not None]
    by_day: dict[str, int] = defaultdict(int)
    by_endpoint: dict[str, int] = defaultdict(int)
    for row in rows:
        day = str(row.get("created_at") or "")[:10]
        if day:
            by_day[day] += 1
        ep = row.get("endpoint") or "unknown"
        by_endpoint[ep] += 1

    return ApiUsageResponse(
        total_requests=total,
        successful_requests=successful,
        failed_requests=failed,
        rate_limited_requests=rate_limited,
        average_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
        requests_by_day=[
            ApiUsageDayPoint(date=d, count=c) for d, c in sorted(by_day.items())
        ],
        requests_by_endpoint=[
            ApiUsageEndpointPoint(endpoint=e, count=c)
            for e, c in sorted(by_endpoint.items(), key=lambda x: -x[1])
        ],
    )

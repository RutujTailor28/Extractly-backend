"""Analytics overview, accuracy, vendor comparison, and trends."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.database import get_supabase_admin_client
from app.schemas.analytics import (
    AccuracyAnalyticsResponse,
    AccuracyByFieldItem,
    AccuracySummary,
    AccuracyTrendPoint,
    AnalyticsOverviewResponse,
    DocumentStats,
    PeriodInfo,
    ProcessingStats,
    QualityStats,
    ReviewStats,
    TrendAnalyticsResponse,
    TrendPoint,
    VendorAnalyticsItem,
    VendorAnalyticsResponse,
)
from app.services.accuracy_service import collect_field_outcomes, summarize_outcomes
from app.services.document_service import DocumentServiceError
from app.services.vendor_service import get_hints_from_vendor

logger = logging.getLogger(__name__)

PROCESSING_STATUSES = {"queued", "preprocessing", "extracting", "validating"}


def _require_client():
    client = get_supabase_admin_client()
    if client is None:
        raise DocumentServiceError(
            "Supabase client is not configured",
            status_code=503,
        )
    return client


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _bucket_key(ts: str | None, group_by: str) -> str | None:
    if not ts:
        return None
    day = str(ts)[:10]
    if group_by == "day":
        return day
    try:
        dt = datetime.fromisoformat(day)
    except ValueError:
        return day
    if group_by == "week":
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if group_by == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    return day


def get_analytics_overview(*, days: int = 30) -> AnalyticsOverviewResponse:
    client = _require_client()
    since = _since_iso(days)

    docs = (
        client.table("documents")
        .select("id,status,created_at")
        .gte("created_at", since)
        .execute()
    )
    documents = docs.data or []
    status_counts: dict[str, int] = defaultdict(int)
    for doc in documents:
        status_counts[str(doc.get("status") or "")] += 1

    processing = sum(status_counts[s] for s in PROCESSING_STATUSES)

    doc_ids = [str(d["id"]) for d in documents]
    extractions: list[dict[str, Any]] = []
    if doc_ids:
        for i in range(0, len(doc_ids), 100):
            chunk = doc_ids[i : i + 100]
            response = (
                client.table("extractions")
                .select("document_id,latency_ms,overall_confidence,cost_usd,created_at")
                .in_("document_id", chunk)
                .execute()
            )
            extractions.extend(response.data or [])

    # Latest extraction per document for averages
    latest: dict[str, dict[str, Any]] = {}
    for ext in sorted(extractions, key=lambda e: str(e.get("created_at") or ""), reverse=True):
        doc_id = str(ext.get("document_id"))
        if doc_id not in latest:
            latest[doc_id] = ext

    latencies = [
        float(e["latency_ms"])
        for e in latest.values()
        if e.get("latency_ms") is not None
    ]
    confidences = [
        float(e["overall_confidence"])
        for e in latest.values()
        if e.get("overall_confidence") is not None
    ]
    costs = [
        float(e["cost_usd"])
        for e in latest.values()
        if e.get("cost_usd") is not None
    ]

    corrections = []
    if doc_ids:
        for i in range(0, len(doc_ids), 100):
            chunk = doc_ids[i : i + 100]
            response = (
                client.table("document_corrections")
                .select("id,document_id,correction_type")
                .in_("document_id", chunk)
                .execute()
            )
            corrections.extend(response.data or [])

    field_corrections = [
        c for c in corrections if c.get("correction_type") == "field_correction"
    ]
    docs_with_corrections = {str(c.get("document_id")) for c in field_corrections}
    approved_or_corrected = {
        str(d["id"])
        for d in documents
        if d.get("status") == "approved" or str(d["id"]) in docs_with_corrections
    }

    outcomes = collect_field_outcomes(client, days=days)
    quality = summarize_outcomes(outcomes)

    avg_corr = (
        (len(field_corrections) / len(approved_or_corrected))
        if approved_or_corrected
        else None
    )

    return AnalyticsOverviewResponse(
        period=PeriodInfo(days=days),
        documents=DocumentStats(
            total=len(documents),
            approved=status_counts.get("approved", 0),
            needs_review=status_counts.get("needs_review", 0),
            failed=status_counts.get("failed", 0),
            ready=status_counts.get("ready", 0),
            processing=processing,
        ),
        processing=ProcessingStats(
            average_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
            average_confidence=(sum(confidences) / len(confidences)) if confidences else None,
            average_cost_usd=(sum(costs) / len(costs)) if costs else None,
            total_cost_usd=sum(costs) if costs else None,
        ),
        review=ReviewStats(
            documents_reviewed=len(approved_or_corrected),
            total_corrections=len(field_corrections),
            average_corrections_per_document=avg_corr,
            documents_requiring_review=status_counts.get("needs_review", 0),
        ),
        quality=QualityStats(
            accuracy=quality["accuracy"],
            correction_rate=quality["correction_rate"],
            review_coverage=quality["coverage"],
        ),
    )


def get_accuracy_analytics(
    *,
    days: int = 30,
    vendor_id: str | None = None,
    field_name: str | None = None,
    group_by: str = "field",
) -> AccuracyAnalyticsResponse:
    client = _require_client()
    outcomes = collect_field_outcomes(
        client,
        days=days,
        vendor_id=vendor_id,
        field_name=field_name,
    )
    summary = summarize_outcomes(outcomes)

    by_field = [
        AccuracyByFieldItem(
            field_name=name,
            accuracy=(
                (stats["correct"] / (stats["correct"] + stats["incorrect"]))
                if (stats.get("correct", 0) + stats.get("incorrect", 0)) > 0
                else None
            ),
            correction_rate=(
                (stats["incorrect"] / (stats["correct"] + stats["incorrect"]))
                if (stats.get("correct", 0) + stats.get("incorrect", 0)) > 0
                else None
            ),
            samples=int(stats.get("samples") or 0),
        )
        for name, stats in sorted((outcomes.get("by_field") or {}).items())
    ]

    by_vendor: list[dict[str, Any]] = []
    if group_by == "vendor":
        for vid, stats in (outcomes.get("by_vendor") or {}).items():
            reviewed = stats.get("correct", 0) + stats.get("incorrect", 0)
            by_vendor.append(
                {
                    "vendor_id": vid,
                    "accuracy": (stats["correct"] / reviewed) if reviewed else None,
                    "correction_rate": (stats["incorrect"] / reviewed) if reviewed else None,
                    "samples": reviewed,
                }
            )

    trend: list[AccuracyTrendPoint] = []
    for day, stats in sorted((outcomes.get("by_day") or {}).items()):
        reviewed = stats.get("correct", 0) + stats.get("incorrect", 0)
        value = (stats["correct"] / reviewed) if reviewed else None
        # When accuracy unavailable, expose inverse correction rate as null accuracy
        trend.append(AccuracyTrendPoint(date=day, value=value))

    return AccuracyAnalyticsResponse(
        period=PeriodInfo(days=days),
        summary=AccuracySummary(
            accuracy=summary["accuracy"],
            correction_rate=summary["correction_rate"],
            coverage=summary["coverage"],
            fields_analyzed=summary["fields_analyzed"],
            fields_corrected=summary["fields_corrected"],
            fields_unchanged=summary["fields_unchanged"],
            unknown_fields=summary["unknown_fields"],
        ),
        by_field=by_field,
        by_vendor=by_vendor,
        trend=trend,
    )


def get_vendor_analytics(
    *,
    limit: int = 50,
    sort_by: str = "documents",
    sort_order: str = "desc",
) -> VendorAnalyticsResponse:
    client = _require_client()
    limit = min(max(1, limit), 200)
    vendors = client.table("vendors").select("*").execute().data or []

    items: list[VendorAnalyticsItem] = []
    for vendor in vendors:
        vid = str(vendor["id"])
        docs = (
            client.table("documents")
            .select("id")
            .eq("vendor_id", vid)
            .execute()
        )
        doc_ids = [str(d["id"]) for d in (docs.data or [])]
        corrections = []
        confidences: list[float] = []
        if doc_ids:
            for i in range(0, len(doc_ids), 100):
                chunk = doc_ids[i : i + 100]
                corr = (
                    client.table("document_corrections")
                    .select("id")
                    .in_("document_id", chunk)
                    .eq("correction_type", "field_correction")
                    .execute()
                )
                corrections.extend(corr.data or [])
                exts = (
                    client.table("extractions")
                    .select("document_id,overall_confidence,created_at")
                    .in_("document_id", chunk)
                    .order("created_at", desc=True)
                    .execute()
                )
                seen: set[str] = set()
                for ext in exts.data or []:
                    did = str(ext.get("document_id"))
                    if did in seen:
                        continue
                    seen.add(did)
                    if ext.get("overall_confidence") is not None:
                        confidences.append(float(ext["overall_confidence"]))

        from app.services.accuracy_service import compute_vendor_quality_metrics

        metrics = compute_vendor_quality_metrics(client, vid)
        hints = get_hints_from_vendor(vendor)
        items.append(
            VendorAnalyticsItem(
                vendor_id=vid,
                vendor_name=vendor.get("name") or "",
                documents=len(doc_ids),
                corrections=len(corrections),
                correction_rate=metrics.get("correction_rate"),
                accuracy=metrics.get("accuracy"),
                average_confidence=(sum(confidences) / len(confidences)) if confidences else None,
                hint_count=len(hints),
            )
        )

    reverse = sort_order != "asc"
    key_map = {
        "documents": lambda x: x.documents,
        "corrections": lambda x: x.corrections,
        "correction_rate": lambda x: x.correction_rate if x.correction_rate is not None else -1,
        "average_confidence": lambda x: x.average_confidence if x.average_confidence is not None else -1,
        "accuracy": lambda x: x.accuracy if x.accuracy is not None else -1,
    }
    key_fn = key_map.get(sort_by, key_map["documents"])
    items.sort(key=key_fn, reverse=reverse)
    return VendorAnalyticsResponse(vendors=items[:limit])


def get_trend_analytics(
    *,
    days: int = 30,
    metric: str = "corrections",
    group_by: str = "day",
) -> TrendAnalyticsResponse:
    client = _require_client()
    since = _since_iso(days)

    docs = (
        client.table("documents")
        .select("id,created_at,status")
        .gte("created_at", since)
        .execute()
    ).data or []
    doc_ids = [str(d["id"]) for d in docs]

    buckets: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)

    if metric == "documents":
        for doc in docs:
            key = _bucket_key(doc.get("created_at"), group_by)
            if key:
                counts[key] += 1
        data = [
            TrendPoint(date=k, value=float(v))
            for k, v in sorted(counts.items())
        ]
        return TrendAnalyticsResponse(metric=metric, group_by=group_by, data=data)

    corrections = []
    if doc_ids:
        for i in range(0, len(doc_ids), 100):
            chunk = doc_ids[i : i + 100]
            response = (
                client.table("document_corrections")
                .select("id,document_id,created_at,correction_type")
                .in_("document_id", chunk)
                .execute()
            )
            corrections.extend(response.data or [])

    field_corrections = [
        c for c in corrections if c.get("correction_type") == "field_correction"
    ]

    if metric == "corrections":
        for row in field_corrections:
            key = _bucket_key(row.get("created_at"), group_by)
            if key:
                counts[key] += 1
        data = [TrendPoint(date=k, value=float(v)) for k, v in sorted(counts.items())]
        return TrendAnalyticsResponse(metric=metric, group_by=group_by, data=data)

    if metric == "correction_rate":
        outcomes = collect_field_outcomes(client, days=days)
        for day, stats in sorted((outcomes.get("by_day") or {}).items()):
            key = _bucket_key(day, group_by) if group_by != "day" else day
            if not key:
                continue
            reviewed = stats.get("correct", 0) + stats.get("incorrect", 0)
            if reviewed:
                buckets[key].append(stats["incorrect"] / reviewed)
        data = [
            TrendPoint(
                date=k,
                value=(sum(vals) / len(vals)) if vals else None,
            )
            for k, vals in sorted(buckets.items())
        ]
        return TrendAnalyticsResponse(metric=metric, group_by=group_by, data=data)

    if metric == "accuracy":
        outcomes = collect_field_outcomes(client, days=days)
        for day, stats in sorted((outcomes.get("by_day") or {}).items()):
            key = day if group_by == "day" else _bucket_key(day, group_by)
            if not key:
                continue
            reviewed = stats.get("correct", 0) + stats.get("incorrect", 0)
            if reviewed:
                buckets[key].append(stats["correct"] / reviewed)
        data = [
            TrendPoint(date=k, value=(sum(v) / len(v)) if v else None)
            for k, v in sorted(buckets.items())
        ]
        return TrendAnalyticsResponse(metric=metric, group_by=group_by, data=data)

    # confidence / cost / processing_time from extractions
    extractions = []
    if doc_ids:
        for i in range(0, len(doc_ids), 100):
            chunk = doc_ids[i : i + 100]
            response = (
                client.table("extractions")
                .select("created_at,overall_confidence,cost_usd,latency_ms")
                .in_("document_id", chunk)
                .gte("created_at", since)
                .execute()
            )
            extractions.extend(response.data or [])

    field_map = {
        "confidence": "overall_confidence",
        "cost": "cost_usd",
        "processing_time": "latency_ms",
    }
    field = field_map.get(metric)
    if not field:
        raise DocumentServiceError(f"Unsupported metric '{metric}'", status_code=422)

    for ext in extractions:
        key = _bucket_key(ext.get("created_at"), group_by)
        if not key or ext.get(field) is None:
            continue
        buckets[key].append(float(ext[field]))

    data = [
        TrendPoint(date=k, value=(sum(v) / len(v)) if v else None)
        for k, v in sorted(buckets.items())
    ]
    return TrendAnalyticsResponse(metric=metric, group_by=group_by, data=data)

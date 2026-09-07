"""Honest accuracy / correction-rate metrics (confidence ≠ accuracy)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.extraction import HEADER_FIELD_NAMES
from app.utils.normalization import field_value

logger = logging.getLogger(__name__)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _period_start(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _values_differ(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return str(a).strip() != str(b).strip()


def collect_field_outcomes(
    client,
    *,
    days: int = 30,
    vendor_id: str | None = None,
    vendor_ids: list[str] | None = None,
    field_name: str | None = None,
) -> dict[str, Any]:
    """
    Outcome model (honest):

    - Approved document + field never corrected → correct (accepted via approval)
    - Field corrected at least once → incorrect (AI original differed)
    - Otherwise → unknown (excluded from accuracy denominator)

    accuracy = correct / (correct + incorrect) when denominator > 0 else null
    correction_rate = incorrect / (correct + incorrect)
    coverage = (correct + incorrect) / possible_fields_on_approved_and_corrected_docs
    """
    since = _period_start(days).isoformat()

    docs_q = (
        client.table("documents")
        .select("id,status,vendor_id,created_at,approved_at")
        .gte("created_at", since)
    )
    if vendor_ids:
        ids = [vid for vid in vendor_ids if vid]
        if not ids:
            return {
                "correct": 0,
                "incorrect": 0,
                "unknown": 0,
                "possible": 0,
                "by_field": {},
                "by_vendor": {},
                "by_day": {},
                "correction_count": 0,
                "documents_reviewed": 0,
            }
        docs_q = docs_q.in_("vendor_id", ids)
    elif vendor_id:
        docs_q = docs_q.eq("vendor_id", vendor_id)
    docs = docs_q.execute().data or []
    doc_ids = [str(d["id"]) for d in docs]
    docs_by_id = {str(d["id"]): d for d in docs}

    if not doc_ids:
        return {
            "correct": 0,
            "incorrect": 0,
            "unknown": 0,
            "possible": 0,
            "by_field": {},
            "by_vendor": {},
            "by_day": {},
            "correction_count": 0,
            "documents_reviewed": 0,
        }

    corrections: list[dict[str, Any]] = []
    for i in range(0, len(doc_ids), 100):
        chunk = doc_ids[i : i + 100]
        response = (
            client.table("document_corrections")
            .select("*")
            .in_("document_id", chunk)
            .eq("correction_type", "field_correction")
            .execute()
        )
        corrections.extend(response.data or [])

    corrected_fields: dict[str, set[str]] = defaultdict(set)
    for row in corrections:
        doc_id = str(row.get("document_id"))
        fname = row.get("field_name")
        if doc_id and fname:
            if field_name and fname != field_name:
                continue
            corrected_fields[doc_id].add(fname)

    # Latest extraction per document
    extractions: list[dict[str, Any]] = []
    for i in range(0, len(doc_ids), 100):
        chunk = doc_ids[i : i + 100]
        response = (
            client.table("extractions")
            .select("document_id,fields_json,created_at")
            .in_("document_id", chunk)
            .order("created_at", desc=True)
            .execute()
        )
        extractions.extend(response.data or [])

    latest_fields: dict[str, dict[str, Any]] = {}
    for ext in extractions:
        doc_id = str(ext.get("document_id"))
        if doc_id in latest_fields:
            continue
        fields = ext.get("fields_json") or {}
        if isinstance(fields, dict):
            latest_fields[doc_id] = fields

    correct = 0
    incorrect = 0
    unknown = 0
    possible = 0
    by_field: dict[str, dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "incorrect": 0, "unknown": 0, "samples": 0}
    )
    by_vendor: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "correct": 0,
            "incorrect": 0,
            "unknown": 0,
            "documents_reviewed": 0,
            "correction_count": 0,
        }
    )
    by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "incorrect": 0}
    )
    documents_reviewed = 0

    target_fields = [field_name] if field_name else list(HEADER_FIELD_NAMES)

    for doc_id, doc in docs_by_id.items():
        status = doc.get("status")
        fields = latest_fields.get(doc_id)
        if not fields:
            continue
        corrected = corrected_fields.get(doc_id, set())
        is_approved = status == "approved"
        has_corrections = bool(corrected)
        day_key = str(doc.get("created_at") or "")[:10]
        vendor_key = str(doc.get("vendor_id") or "none")
        if is_approved or has_corrections:
            documents_reviewed += 1
            by_vendor[vendor_key]["documents_reviewed"] += 1

        for fname in target_fields:
            if fname == "document_type":
                continue
            value = field_value(fields, fname)
            # Skip empty fields from possible reviewed set
            if value is None or (isinstance(value, str) and not value.strip()):
                if fname not in corrected:
                    continue

            possible += 1
            if fname in corrected:
                incorrect += 1
                by_field[fname]["incorrect"] += 1
                by_field[fname]["samples"] += 1
                by_vendor[vendor_key]["incorrect"] += 1
                by_vendor[vendor_key]["correction_count"] += 1
                if day_key:
                    by_day[day_key]["incorrect"] += 1
            elif is_approved:
                correct += 1
                by_field[fname]["correct"] += 1
                by_field[fname]["samples"] += 1
                by_vendor[vendor_key]["correct"] += 1
                if day_key:
                    by_day[day_key]["correct"] += 1
            else:
                unknown += 1
                by_field[fname]["unknown"] += 1
                by_vendor[vendor_key]["unknown"] += 1

    return {
        "correct": correct,
        "incorrect": incorrect,
        "unknown": unknown,
        "possible": possible,
        "by_field": dict(by_field),
        "by_vendor": dict(by_vendor),
        "by_day": dict(by_day),
        "correction_count": len(corrections),
        "documents_reviewed": documents_reviewed,
    }


def summarize_outcomes(outcomes: dict[str, Any]) -> dict[str, Any]:
    correct = int(outcomes.get("correct") or 0)
    incorrect = int(outcomes.get("incorrect") or 0)
    unknown = int(outcomes.get("unknown") or 0)
    possible = int(outcomes.get("possible") or 0)
    reviewed = correct + incorrect
    accuracy = (correct / reviewed) if reviewed > 0 else None
    correction_rate = (incorrect / reviewed) if reviewed > 0 else None
    coverage = (reviewed / possible) if possible > 0 else None
    return {
        "accuracy": accuracy,
        "correction_rate": correction_rate,
        "coverage": coverage,
        "fields_analyzed": reviewed,
        "fields_corrected": incorrect,
        "fields_unchanged": correct,
        "unknown_fields": unknown,
        "correction_count": outcomes.get("correction_count") or 0,
        "documents_reviewed": outcomes.get("documents_reviewed") or 0,
    }


def compute_vendor_quality_metrics(client, vendor_id: str) -> dict[str, Any]:
    outcomes = collect_field_outcomes(client, days=3650, vendor_id=vendor_id)
    summary = summarize_outcomes(outcomes)
    return {
        "accuracy": summary["accuracy"],
        "correction_rate": summary["correction_rate"],
        "coverage": summary["coverage"],
        "correction_count": summary["correction_count"],
        "documents_reviewed": summary["documents_reviewed"],
    }


def compute_vendor_quality_metrics_batch(
    client,
    vendor_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Compute quality metrics for many vendors in one DB pass.

    Avoids N+1 Supabase calls that exhaust HTTP/2 connections on list endpoints.
    """
    ids = [str(v) for v in vendor_ids if v]
    empty = {
        "accuracy": None,
        "correction_rate": None,
        "coverage": None,
        "correction_count": 0,
        "documents_reviewed": 0,
    }
    if not ids:
        return {}

    try:
        outcomes = collect_field_outcomes(client, days=3650, vendor_ids=ids)
    except Exception as exc:
        logger.warning("Batch vendor metrics failed: %s", exc)
        return {vid: dict(empty) for vid in ids}

    by_vendor = outcomes.get("by_vendor") or {}
    result: dict[str, dict[str, Any]] = {}
    for vid in ids:
        stats = by_vendor.get(vid) or {}
        correct = int(stats.get("correct") or 0)
        incorrect = int(stats.get("incorrect") or 0)
        reviewed = correct + incorrect
        result[vid] = {
            "accuracy": (correct / reviewed) if reviewed > 0 else None,
            "correction_rate": (incorrect / reviewed) if reviewed > 0 else None,
            "coverage": None,
            "correction_count": int(stats.get("correction_count") or incorrect),
            "documents_reviewed": int(stats.get("documents_reviewed") or 0),
        }
    return result


def compute_vendor_field_accuracy(client, vendor_id: str) -> list[dict[str, Any]]:
    outcomes = collect_field_outcomes(client, days=3650, vendor_id=vendor_id)
    items: list[dict[str, Any]] = []
    for field_name, stats in sorted((outcomes.get("by_field") or {}).items()):
        correct = int(stats.get("correct") or 0)
        incorrect = int(stats.get("incorrect") or 0)
        reviewed = correct + incorrect
        items.append(
            {
                "field_name": field_name,
                "accuracy": (correct / reviewed) if reviewed else None,
                "correction_rate": (incorrect / reviewed) if reviewed else None,
                "total_reviewed": reviewed,
                "corrected": incorrect,
            }
        )
    return items

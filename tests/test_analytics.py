"""Tests for Phase 9 analytics (honest metrics, mocked data)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.accuracy_service import collect_field_outcomes, summarize_outcomes


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _chain(data=None):
    q = MagicMock()
    for name in ("select", "eq", "order", "limit", "update", "insert", "is_", "in_", "gte", "range"):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(data=data if data is not None else [], count=len(data or []))
    return q


def test_summarize_outcomes_honest_accuracy() -> None:
    summary = summarize_outcomes(
        {
            "correct": 91,
            "incorrect": 9,
            "unknown": 50,
            "possible": 150,
            "correction_count": 9,
            "documents_reviewed": 10,
        }
    )
    assert summary["accuracy"] == pytest.approx(0.91)
    assert summary["correction_rate"] == pytest.approx(0.09)
    assert summary["coverage"] == pytest.approx(100 / 150)
    assert summary["unknown_fields"] == 50


def test_accuracy_null_when_no_reviewed_evidence() -> None:
    summary = summarize_outcomes(
        {
            "correct": 0,
            "incorrect": 0,
            "unknown": 20,
            "possible": 20,
            "correction_count": 0,
            "documents_reviewed": 0,
        }
    )
    assert summary["accuracy"] is None
    assert summary["correction_rate"] is None


def test_collect_field_outcomes_approved_vs_corrected() -> None:
    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain(
                [
                    {
                        "id": "d1",
                        "status": "approved",
                        "vendor_id": "v1",
                        "created_at": "2026-09-01T00:00:00+00:00",
                    },
                    {
                        "id": "d2",
                        "status": "needs_review",
                        "vendor_id": "v1",
                        "created_at": "2026-09-02T00:00:00+00:00",
                    },
                ]
            )
        if name == "document_corrections":
            return _chain(
                [
                    {
                        "document_id": "d2",
                        "correction_type": "field_correction",
                        "field_name": "invoice_number",
                    }
                ]
            )
        if name == "extractions":
            return _chain(
                [
                    {
                        "document_id": "d1",
                        "fields_json": {
                            "vendor_name": {"value": "Acme"},
                            "invoice_number": {"value": "INV-1"},
                            "total": {"value": "100"},
                        },
                        "created_at": "2026-09-01T01:00:00+00:00",
                    },
                    {
                        "document_id": "d2",
                        "fields_json": {
                            "vendor_name": {"value": "Acme"},
                            "invoice_number": {"value": "INV-2"},
                            "total": {"value": "200"},
                        },
                        "created_at": "2026-09-02T01:00:00+00:00",
                    },
                ]
            )
        return _chain([])

    mock_client.table.side_effect = table
    outcomes = collect_field_outcomes(mock_client, days=30)
    # d1 approved fields count as correct; d2 invoice_number incorrect; others unknown
    assert outcomes["incorrect"] >= 1
    assert outcomes["correct"] >= 1
    summary = summarize_outcomes(outcomes)
    assert summary["accuracy"] is not None
    # confidence is never used here
    assert "confidence" not in summary


def test_overview_and_trends_api(client: TestClient) -> None:
    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain(
                [
                    {"id": "d1", "status": "approved", "created_at": "2026-09-01T00:00:00+00:00"},
                    {"id": "d2", "status": "needs_review", "created_at": "2026-09-02T00:00:00+00:00"},
                ]
            )
        if name == "extractions":
            return _chain(
                [
                    {
                        "document_id": "d1",
                        "latency_ms": 1000,
                        "overall_confidence": 0.9,
                        "cost_usd": 0.01,
                        "created_at": "2026-09-01T01:00:00+00:00",
                    }
                ]
            )
        if name == "document_corrections":
            return _chain(
                [
                    {
                        "id": "c1",
                        "document_id": "d2",
                        "correction_type": "field_correction",
                        "created_at": "2026-09-02T02:00:00+00:00",
                    }
                ]
            )
        if name == "vendors":
            return _chain([])
        return _chain([])

    mock_client.table.side_effect = table
    with (
        patch(
            "app.services.analytics_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.analytics_service.collect_field_outcomes",
            return_value={
                "correct": 10,
                "incorrect": 2,
                "unknown": 5,
                "possible": 17,
                "by_field": {
                    "invoice_number": {
                        "correct": 8,
                        "incorrect": 2,
                        "unknown": 0,
                        "samples": 10,
                    }
                },
                "by_vendor": {},
                "by_day": {"2026-09-01": {"correct": 5, "incorrect": 1}},
                "correction_count": 2,
                "documents_reviewed": 2,
            },
        ),
    ):
        overview = client.get("/api/v1/analytics/overview?days=30")
        accuracy = client.get("/api/v1/analytics/accuracy?days=30")
        trends = client.get(
            "/api/v1/analytics/trends?days=30&metric=corrections&group_by=day"
        )

    assert overview.status_code == 200
    body = overview.json()
    assert body["documents"]["total"] == 2
    assert body["quality"]["accuracy"] == pytest.approx(10 / 12)
    assert body["processing"]["average_cost_usd"] == pytest.approx(0.01)
    # confidence is separate from accuracy
    assert body["processing"]["average_confidence"] == pytest.approx(0.9)

    assert accuracy.status_code == 200
    assert accuracy.json()["summary"]["correction_rate"] == pytest.approx(2 / 12)

    assert trends.status_code == 200
    assert trends.json()["metric"] == "corrections"


def test_invalid_query_params_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/analytics/overview?days=9999")
    assert response.status_code == 422
    response = client.get("/api/v1/analytics/trends?metric=not_a_metric")
    assert response.status_code == 422


def test_vendor_analytics_api(client: TestClient) -> None:
    mock_client = MagicMock()

    def table(name):
        if name == "vendors":
            return _chain(
                [
                    {
                        "id": "v1",
                        "name": "Acme",
                        "template_hints_json": {"hints": [{"id": "h1"}]},
                    }
                ]
            )
        if name == "documents":
            return _chain([{"id": "d1"}])
        if name == "document_corrections":
            return _chain([{"id": "c1"}])
        if name == "extractions":
            return _chain(
                [
                    {
                        "document_id": "d1",
                        "overall_confidence": 0.88,
                        "created_at": "2026-09-01T00:00:00Z",
                    }
                ]
            )
        return _chain([])

    mock_client.table.side_effect = table
    with (
        patch(
            "app.services.analytics_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.accuracy_service.compute_vendor_quality_metrics",
            return_value={
                "accuracy": None,
                "correction_rate": 0.1,
                "coverage": 0.5,
                "correction_count": 1,
                "documents_reviewed": 1,
            },
        ),
    ):
        response = client.get("/api/v1/analytics/vendors")
    assert response.status_code == 200
    item = response.json()["vendors"][0]
    assert item["vendor_name"] == "Acme"
    assert item["accuracy"] is None
    assert item["correction_rate"] == pytest.approx(0.1)

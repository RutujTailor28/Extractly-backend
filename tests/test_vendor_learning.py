"""Tests for Phase 9 vendor learning (mocked Supabase, no AI)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.ai.prompts import build_extraction_user_prompt
from app.services.vendor_learning_service import (
    _detect_date_format,
    _detect_invoice_prefix,
    _make_hint,
    _priority_for_count,
    analyze_vendor_corrections,
)
from app.services.vendor_service import format_hints_for_prompt, find_or_create_vendor
from app.utils.normalization import normalize_vendor_name


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _chain(data=None, count=None):
    q = MagicMock()
    for name in ("select", "eq", "order", "limit", "update", "insert", "is_", "in_", "ilike", "range", "gte"):
        getattr(q, name).return_value = q
    result = MagicMock(data=data if data is not None else [])
    result.count = count if count is not None else (len(data) if data is not None else 0)
    q.execute.return_value = result
    return q


def test_normalize_vendor_exact_match() -> None:
    assert normalize_vendor_name("  ACME   Corp ") == "acme corp"
    assert normalize_vendor_name("Acme Corp") == normalize_vendor_name("ACME Corp")


def test_priority_and_prefix_detection() -> None:
    assert _priority_for_count(5) == "high"
    assert _priority_for_count(3) == "medium"
    assert _detect_invoice_prefix(["INV-001", "INV-002", "INV-003", "INV-004"]) == "INV-"
    assert _detect_invoice_prefix(["A1", "B2"]) is None
    assert _detect_date_format(["15/03/2026", "16/03/2026", "17/04/2026"]) == "DD/MM/YYYY"


def test_find_or_create_reuses_existing() -> None:
    existing = {
        "id": "v1",
        "name": "Acme Corp",
        "name_normalized": "acme corp",
        "tax_id": None,
    }
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([existing])
    vendor = find_or_create_vendor(mock_client, vendor_name="ACME Corp")
    assert vendor["id"] == "v1"


def test_find_or_create_inserts_when_missing() -> None:
    created = {
        "id": "v2",
        "name": "New Vendor",
        "name_normalized": "new vendor",
    }
    mock_client = MagicMock()

    def table(name):
        q = _chain([])
        if name == "vendors":
            # first select empty, then insert returns created
            select_q = _chain([])
            insert_q = _chain([created])
            select_q.insert.return_value = insert_q
            return select_q
        return q

    mock_client.table.side_effect = table
    vendor = find_or_create_vendor(mock_client, vendor_name="New Vendor")
    assert vendor["id"] == "v2"


def test_analyze_generates_hint_at_threshold() -> None:
    vendor = {
        "id": "v1",
        "name": "Acme",
        "template_hints_json": {
            "version": 1,
            "hints": [
                {
                    "id": "manual-1",
                    "type": "manual_instruction",
                    "source": "manual",
                    "message": "Keep this",
                    "priority": "high",
                    "evidence_count": 0,
                }
            ],
        },
    }
    docs = [{"id": "d1"}, {"id": "d2"}, {"id": "d3"}]
    corrections = [
        {
            "document_id": "d1",
            "correction_type": "field_correction",
            "field_name": "invoice_number",
            "corrected_value_json": "INV-001",
            "original_value_json": "INV-000",
        },
        {
            "document_id": "d2",
            "correction_type": "field_correction",
            "field_name": "invoice_number",
            "corrected_value_json": "INV-002",
            "original_value_json": "X",
        },
        {
            "document_id": "d3",
            "correction_type": "field_correction",
            "field_name": "invoice_number",
            "corrected_value_json": "INV-003",
            "original_value_json": "Y",
        },
    ]

    mock_client = MagicMock()

    def table(name):
        if name == "vendors":
            q = _chain([vendor])
            u = _chain([vendor])
            q.update.return_value = u
            return q
        if name == "documents":
            return _chain(docs)
        if name == "document_corrections":
            return _chain(corrections)
        if name == "extractions":
            return _chain(
                [
                    {
                        "document_id": "d1",
                        "fields_json": {
                            "invoice_number": {"value": "x", "confidence": 0.4},
                            "currency": {"value": "INR"},
                        },
                        "created_at": "2026-09-01T00:00:00Z",
                    },
                    {
                        "document_id": "d2",
                        "fields_json": {
                            "invoice_number": {"value": "x", "confidence": 0.5},
                            "currency": {"value": "INR"},
                        },
                        "created_at": "2026-09-01T00:00:00Z",
                    },
                    {
                        "document_id": "d3",
                        "fields_json": {
                            "invoice_number": {"value": "x", "confidence": 0.6},
                            "currency": {"value": "INR"},
                        },
                        "created_at": "2026-09-01T00:00:00Z",
                    },
                ]
            )
        return _chain([])

    mock_client.table.side_effect = table
    with patch(
        "app.services.vendor_learning_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        result = analyze_vendor_corrections("v1")

    assert result.hints_generated >= 1
    types = {h.type for h in result.hints}
    assert "frequently_corrected_field" in types
    assert any(h.source == "manual" and h.message == "Keep this" for h in result.hints)
    assert any(h.type == "value_format_pattern" for h in result.hints)
    assert any(h.type == "preferred_currency" for h in result.hints)


def test_analyze_below_threshold_no_frequent_hint() -> None:
    vendor = {"id": "v1", "name": "Acme", "template_hints_json": {"hints": []}}
    mock_client = MagicMock()

    def table(name):
        if name == "vendors":
            q = _chain([vendor])
            q.update.return_value = _chain([vendor])
            return q
        if name == "documents":
            return _chain([{"id": "d1"}])
        if name == "document_corrections":
            return _chain(
                [
                    {
                        "document_id": "d1",
                        "correction_type": "field_correction",
                        "field_name": "total",
                        "corrected_value_json": "1",
                    }
                ]
            )
        if name == "extractions":
            return _chain([])
        return _chain([])

    mock_client.table.side_effect = table
    with patch(
        "app.services.vendor_learning_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        result = analyze_vendor_corrections("v1")
    assert not any(h.type == "frequently_corrected_field" for h in result.hints)


def test_prompt_includes_hints_safely() -> None:
    prompt = build_extraction_user_prompt(
        page_count=1,
        vendor_hints=format_hints_for_prompt(
            {
                "vendor_name": "Acme",
                "hints": [
                    {
                        "message": "Invoice number frequently requires careful review.",
                    }
                ],
            }
        ),
    )
    assert "Vendor-specific extraction guidance" in prompt
    assert "Do not invent values based on hints" in prompt
    assert "Invoice number frequently requires careful review" in prompt


def test_empty_hints_do_not_change_prompt_much() -> None:
    base = build_extraction_user_prompt(page_count=1, vendor_hints=None)
    empty = build_extraction_user_prompt(
        page_count=1,
        vendor_hints=format_hints_for_prompt({"vendor_name": "X", "hints": []}),
    )
    assert base == empty


def test_vendor_list_and_hints_api(client: TestClient) -> None:
    vendor = {
        "id": "v1",
        "name": "Acme",
        "tax_id": None,
        "invoice_count": 2,
        "last_invoice_at": None,
        "template_hints_json": {
            "hints": [
                {
                    "id": "h1",
                    "type": "preferred_currency",
                    "source": "generated",
                    "message": "Uses INR",
                    "priority": "medium",
                    "evidence_count": 3,
                }
            ]
        },
    }
    mock_client = MagicMock()

    def table(name):
        if name == "vendors":
            return _chain([vendor], count=1)
        if name == "documents":
            return _chain([{"id": "d1"}, {"id": "d2"}])
        if name == "document_corrections":
            return _chain([])
        if name == "extractions":
            return _chain([])
        return _chain([])

    mock_client.table.side_effect = table
    with (
        patch(
            "app.services.vendor_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.accuracy_service.collect_field_outcomes",
            return_value={
                "correct": 0,
                "incorrect": 0,
                "unknown": 0,
                "possible": 0,
                "by_field": {},
                "by_vendor": {},
                "by_day": {},
                "correction_count": 0,
                "documents_reviewed": 0,
            },
        ),
    ):
        response = client.get("/api/v1/vendors")
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Acme"

    with patch(
        "app.services.vendor_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/vendors/v1/hints")
    assert response.status_code == 200
    assert len(response.json()["hints"]) == 1


def test_manual_hint_patch(client: TestClient) -> None:
    vendor = {"id": "v1", "name": "Acme", "template_hints_json": {"version": 1, "hints": []}}
    mock_client = MagicMock()

    def table(name):
        q = _chain([vendor])
        q.update.return_value = _chain([vendor])
        return q

    mock_client.table.side_effect = table
    with patch(
        "app.services.vendor_learning_service.get_supabase_admin_client",
        return_value=mock_client,
    ), patch(
        "app.services.vendor_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        # After update, get_vendor_hints reads vendor again — return with hint
        stored = {"hints": []}

        def update_side_effect(*args, **kwargs):
            # capture via update call
            return _chain([vendor])

        response = client.patch(
            "/api/v1/vendors/v1/hints",
            json={
                "operation": "add",
                "hint": {
                    "type": "manual_instruction",
                    "field": "invoice_number",
                    "priority": "high",
                    "message": "Upper-right corner",
                },
            },
        )
    assert response.status_code == 200

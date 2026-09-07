"""Tests for Phase 7 validation rules engine (mocked Supabase, no AI calls)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.validation import ValidationRulesUpdateRequest
from app.services.validation.dates import (
    DueDateBeforeInvoiceDateRule,
    InvoiceDateInFutureRule,
)
from app.services.validation.duplicates import (
    DuplicateInvoiceRule,
    PossibleNearDuplicateRule,
)
from app.services.validation.line_items import LineItemsSubtotalRule
from app.services.validation.required_fields import RequiredFieldMissingRule
from app.services.validation.tax import LowConfidenceFieldRule, UnusualTaxRateRule
from app.services.validation.totals import TotalMismatchRule
from app.services.validation.base import merge_rule_settings
from app.utils.normalization import (
    is_missing_value,
    normalize_invoice_number,
    normalize_tax_rate,
    normalize_vendor_name,
    parse_date,
    parse_decimal,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _field(value, confidence=0.95):
    return {"value": value, "confidence": confidence, "page_number": 1}


def _extraction(**overrides):
    base = {
        "id": "ext-1",
        "document_id": "doc-1",
        "document_type": "invoice",
        "fields": {
            "vendor_name": _field("Acme Corp"),
            "invoice_number": _field("INV-10024"),
            "invoice_date": _field("2026-03-10"),
            "due_date": _field("2026-04-10"),
            "currency": _field("USD"),
            "subtotal": _field("1000.00"),
            "tax_amount": _field("180.00"),
            "shipping": _field("50.00"),
            "discount": _field("30.00"),
            "total": _field("1200.00"),
            "tax_rate": _field(18),
        },
        "line_items": [
            {"line_total": _field("500.00")},
            {"line_total": _field("500.00")},
        ],
    }
    fields = {**base["fields"], **(overrides.pop("fields", {}))}
    line_items = overrides.pop("line_items", base["line_items"])
    base.update(overrides)
    base["fields"] = fields
    base["line_items"] = line_items
    return base


def _doc(**overrides):
    base = {"id": "doc-1", "status": "ready", "user_id": None, "filename": "inv.pdf"}
    base.update(overrides)
    return base


# --- helpers ---


def test_parse_decimal_and_missing() -> None:
    assert parse_decimal("1,240.00") == Decimal("1240.00")
    assert parse_decimal(None) is None
    assert is_missing_value("  ") is True
    assert is_missing_value(0) is False
    assert normalize_vendor_name("  Acme   Corp ") == "acme corp"
    assert normalize_invoice_number(" INV 100 ") == "inv100"
    assert normalize_tax_rate(0.18) == Decimal("18")
    assert normalize_tax_rate("18%") == Decimal("18")
    assert parse_date("2026-03-15") == date(2026, 3, 15)
    assert parse_date("03/15/2026") is None


# --- line items ---


def test_line_items_matching_passes() -> None:
    rule = LineItemsSubtotalRule()
    results = rule.validate(
        extraction=_extraction(fields={"subtotal": _field("1000.00")}),
        document=_doc(),
        config={"tolerance": 0.02},
    )
    assert results == []


def test_line_items_mismatch_error() -> None:
    rule = LineItemsSubtotalRule()
    results = rule.validate(
        extraction=_extraction(
            fields={"subtotal": _field("1420.00")},
            line_items=[
                {"line_total": _field("500.00")},
                {"line_total": _field("740.00")},
            ],
        ),
        document=_doc(),
        config={"tolerance": 0.02},
    )
    assert len(results) == 1
    assert results[0].severity == "error"
    assert "1,240.00" in results[0].message
    assert "1,420.00" in results[0].message


def test_line_items_tolerance_respected() -> None:
    rule = LineItemsSubtotalRule()
    results = rule.validate(
        extraction=_extraction(
            fields={"subtotal": _field("1000.01")},
            line_items=[{"line_total": _field("1000.00")}],
        ),
        document=_doc(),
        config={"tolerance": 0.02},
    )
    assert results == []


def test_line_items_skips_missing_subtotal_or_items() -> None:
    rule = LineItemsSubtotalRule()
    assert (
        rule.validate(
            extraction=_extraction(fields={"subtotal": _field(None)}, line_items=[]),
            document=_doc(),
            config={},
        )
        == []
    )
    assert (
        rule.validate(
            extraction=_extraction(fields={"subtotal": None}, line_items=[]),
            document=_doc(),
            config={},
        )
        == []
    )


# --- totals ---


def test_total_correct_passes() -> None:
    rule = TotalMismatchRule()
    assert (
        rule.validate(extraction=_extraction(), document=_doc(), config={"tolerance": 0.02})
        == []
    )


def test_total_incorrect_error_with_shipping_discount() -> None:
    rule = TotalMismatchRule()
    results = rule.validate(
        extraction=_extraction(fields={"total": _field("1300.00")}),
        document=_doc(),
        config={"tolerance": 0.02},
    )
    assert len(results) == 1
    assert results[0].severity == "error"
    assert "1,200.00" in results[0].message
    assert "1,300.00" in results[0].message


def test_total_missing_tax_treated_as_zero() -> None:
    rule = TotalMismatchRule()
    results = rule.validate(
        extraction=_extraction(
            fields={
                "subtotal": _field("100.00"),
                "tax_amount": _field(None),
                "shipping": _field(None),
                "discount": _field(None),
                "total": _field("100.00"),
            }
        ),
        document=_doc(),
        config={"tolerance": 0.02},
    )
    assert results == []


def test_total_skips_when_subtotal_or_total_missing() -> None:
    rule = TotalMismatchRule()
    assert (
        rule.validate(
            extraction=_extraction(fields={"subtotal": _field(None)}),
            document=_doc(),
            config={},
        )
        == []
    )


# --- dates ---


def test_future_invoice_date_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 3, tzinfo=timezone.utc)

    monkeypatch.setattr("app.services.validation.dates.datetime", FixedDateTime)
    rule = InvoiceDateInFutureRule()
    results = rule.validate(
        extraction=_extraction(fields={"invoice_date": _field("2027-03-15")}),
        document=_doc(),
        config={},
    )
    assert len(results) == 1
    assert "2027-03-15" in results[0].message


def test_past_invoice_date_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 3, tzinfo=timezone.utc)

    monkeypatch.setattr("app.services.validation.dates.datetime", FixedDateTime)
    rule = InvoiceDateInFutureRule()
    assert (
        rule.validate(
            extraction=_extraction(fields={"invoice_date": _field("2026-03-15")}),
            document=_doc(),
            config={},
        )
        == []
    )


def test_due_date_before_invoice_flagged() -> None:
    rule = DueDateBeforeInvoiceDateRule()
    results = rule.validate(
        extraction=_extraction(
            fields={
                "invoice_date": _field("2026-03-10"),
                "due_date": _field("2026-02-10"),
            }
        ),
        document=_doc(),
        config={},
    )
    assert len(results) == 1
    assert results[0].severity == "error"


def test_missing_dates_do_not_crash() -> None:
    assert InvoiceDateInFutureRule().validate(
        extraction=_extraction(fields={"invoice_date": _field(None)}),
        document=_doc(),
        config={},
    ) == []
    assert DueDateBeforeInvoiceDateRule().validate(
        extraction=_extraction(fields={"due_date": _field(None)}),
        document=_doc(),
        config={},
    ) == []


# --- required ---


def test_required_fields_invoice_and_receipt() -> None:
    rule = RequiredFieldMissingRule()
    results = rule.validate(
        extraction=_extraction(
            document_type="invoice",
            fields={
                "vendor_name": _field(None),
                "invoice_number": _field(None),
                "invoice_date": _field("2026-01-01"),
                "currency": _field("USD"),
                "total": _field("10"),
            },
        ),
        document=_doc(),
        config={},
    )
    codes = {r.details["field_name"] for r in results}
    assert "vendor_name" in codes
    assert "invoice_number" in codes

    receipt = rule.validate(
        extraction=_extraction(
            document_type="receipt",
            fields={
                "vendor_name": _field("Shop"),
                "invoice_number": _field(None),
                "invoice_date": _field("2026-01-01"),
                "currency": _field("USD"),
                "total": _field("10"),
            },
        ),
        document=_doc(),
        config={},
    )
    assert receipt == []

    unknown = rule.validate(
        extraction=_extraction(
            document_type="unknown",
            fields={"vendor_name": _field(None), "total": _field(None)},
        ),
        document=_doc(),
        config={},
    )
    assert {r.details["field_name"] for r in unknown} == {"vendor_name", "total"}


# --- duplicates ---


def test_duplicate_invoice_detection() -> None:
    rule = DuplicateInvoiceRule()
    peer = {
        "id": "ext-2",
        "document_id": "doc-2",
        "fields_json": {
            "document_type": "invoice",
            "vendor_name": _field("Acme Corp"),
            "invoice_number": _field("INV-10024"),
        },
        "created_at": "2026-01-01T00:00:00Z",
    }
    results = rule.validate(
        extraction=_extraction(),
        document=_doc(),
        config={},
        context={"peer_extractions": [peer]},
    )
    assert len(results) == 1
    assert results[0].severity == "warning"
    assert "doc-2" in results[0].message

    # different vendor
    peer["fields_json"]["vendor_name"] = _field("Other")
    assert (
        rule.validate(
            extraction=_extraction(),
            document=_doc(),
            config={},
            context={"peer_extractions": [peer]},
        )
        == []
    )


def test_duplicate_excludes_current_document() -> None:
    rule = DuplicateInvoiceRule()
    peer = {
        "id": "ext-1",
        "document_id": "doc-1",
        "fields_json": {
            "vendor_name": _field("Acme Corp"),
            "invoice_number": _field("INV-10024"),
        },
        "created_at": "2026-01-01T00:00:00Z",
    }
    assert (
        rule.validate(
            extraction=_extraction(),
            document=_doc(),
            config={},
            context={"peer_extractions": [peer]},
        )
        == []
    )


def test_near_duplicate() -> None:
    rule = PossibleNearDuplicateRule()
    peer = {
        "id": "ext-2",
        "document_id": "doc-2",
        "fields_json": {
            "vendor_name": _field("Acme Corp"),
            "total": _field("1200.01"),
            "invoice_date": _field("2026-03-12"),
        },
        "created_at": "2026-01-01T00:00:00Z",
    }
    results = rule.validate(
        extraction=_extraction(),
        document=_doc(),
        config={"amount_tolerance": 0.02, "date_window_days": 7},
        context={"peer_extractions": [peer]},
    )
    assert len(results) == 1

    peer["fields_json"]["total"] = _field("1500.00")
    assert (
        rule.validate(
            extraction=_extraction(),
            document=_doc(),
            config={"amount_tolerance": 0.02, "date_window_days": 7},
            context={"peer_extractions": [peer]},
        )
        == []
    )


# --- tax / confidence ---


def test_tax_rate_plausibility() -> None:
    rule = UnusualTaxRateRule()
    assert (
        rule.validate(extraction=_extraction(), document=_doc(), config={"min_rate": 0, "max_rate": 30})
        == []
    )
    results = rule.validate(
        extraction=_extraction(fields={"tax_rate": _field(65)}),
        document=_doc(),
        config={"min_rate": 0, "max_rate": 30},
    )
    assert len(results) == 1
    assert results[0].severity == "warning"

    neg = rule.validate(
        extraction=_extraction(fields={"tax_rate": _field(-1)}),
        document=_doc(),
        config={"min_rate": 0, "max_rate": 30},
    )
    assert len(neg) == 1


def test_low_confidence() -> None:
    rule = LowConfidenceFieldRule()
    assert (
        rule.validate(
            extraction=_extraction(fields={"invoice_number": _field("INV-1", 0.95)}),
            document=_doc(),
            config={"threshold": 0.70},
        )
        == []
    )
    results = rule.validate(
        extraction=_extraction(fields={"invoice_number": _field("INV-1", 0.69)}),
        document=_doc(),
        config={"threshold": 0.70},
    )
    assert len(results) == 1
    assert "0.69" in results[0].message

    skipped = rule.validate(
        extraction=_extraction(fields={"invoice_number": _field(None, 0.40)}),
        document=_doc(),
        config={"threshold": 0.70},
    )
    assert skipped == []


# --- config ---


def test_default_rules_and_override() -> None:
    merged = merge_rule_settings(
        [{"rule_code": "TOTAL_MISMATCH", "enabled": False, "config_json": {"tolerance": 0.05}}]
    )
    assert merged["TOTAL_MISMATCH"]["enabled"] is False
    assert merged["TOTAL_MISMATCH"]["config"]["tolerance"] == 0.05
    assert merged["LINE_ITEMS_SUBTOTAL_MISMATCH"]["enabled"] is True


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValidationError):
        ValidationRulesUpdateRequest(
            rules=[{"rule_code": "LOW_CONFIDENCE_FIELD", "enabled": True, "config": {"threshold": 1.5}}]
        )
    with pytest.raises(ValidationError):
        ValidationRulesUpdateRequest(
            rules=[{"rule_code": "NOT_A_REAL_RULE", "enabled": True, "config": {}}]
        )


# --- API / flow ---


def _chain(data=None):
    q = MagicMock()
    for name in ("select", "eq", "order", "limit", "update", "delete", "insert", "is_", "in_"):
        getattr(q, name).return_value = q
    q.execute.return_value = MagicMock(data=data if data is not None else [])
    return q


def test_validate_missing_document(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([])
    with patch(
        "app.services.validation_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post("/api/v1/documents/missing/validate")
    assert response.status_code == 404


def test_validate_missing_extraction(client: TestClient) -> None:
    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            return _chain([_doc()])
        if name == "extractions":
            return _chain([])
        return _chain([])

    mock_client.table.side_effect = table
    with patch(
        "app.services.validation_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.post("/api/v1/documents/doc-1/validate")
    assert response.status_code == 400
    assert "extracted" in response.json()["detail"].lower()


def test_validate_creates_flags_and_routes_needs_review(client: TestClient) -> None:
    doc = _doc(status="ready")
    extraction_row = {
        "id": "ext-1",
        "document_id": "doc-1",
        "fields_json": {
            "document_type": "invoice",
            "vendor_name": _field("Acme"),
            "invoice_number": _field("INV-1"),
            "invoice_date": _field("2026-03-10"),
            "currency": _field("USD"),
            "subtotal": _field("1000.00"),
            "tax_amount": _field("0"),
            "total": _field("9999.00"),
        },
        "line_items_json": [{"line_total": _field("1000.00")}],
        "created_at": "2026-09-01T00:00:00Z",
    }
    flag_row = {
        "id": "flag-1",
        "document_id": "doc-1",
        "rule_code": "TOTAL_MISMATCH",
        "severity": "error",
        "message": "mismatch",
        "resolved": False,
        "details_json": {},
        "created_at": "2026-09-03T00:00:00Z",
    }

    calls = {"update_status": []}

    def table(name):
        q = _chain([])
        if name == "documents":
            select_q = _chain([doc])
            update_q = MagicMock()
            update_q.eq.return_value = update_q

            def execute_update():
                # last update payload captured via closure on chain — simplified
                return MagicMock(data=[{**doc, "status": "needs_review"}])

            update_q.execute.side_effect = execute_update
            select_q.update.return_value = update_q
            # select path
            select_q.select.return_value = select_q
            select_q.eq.return_value = select_q
            select_q.limit.return_value = select_q
            select_q.is_.return_value = select_q
            select_q.execute.return_value = MagicMock(data=[doc])
            return select_q
        if name == "extractions":
            return _chain([extraction_row])
        if name == "validation_rule_settings":
            return _chain([])
        if name == "validation_flags":
            insert_q = _chain([flag_row])
            update_q = _chain([])
            # both insert and update used
            q = MagicMock()
            q.update.return_value = update_q
            q.insert.return_value = insert_q
            q.eq.return_value = q
            q.select.return_value = q
            q.order.return_value = q
            q.limit.return_value = q
            q.execute.return_value = MagicMock(data=[])
            insert_q.execute.return_value = MagicMock(data=[flag_row])
            update_q.execute.return_value = MagicMock(data=[])
            return q
        return _chain([])

    mock_client = MagicMock()
    mock_client.table.side_effect = table

    with (
        patch(
            "app.services.validation_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.validation_service.fetch_peer_extractions",
            return_value=[],
        ),
        patch(
            "app.services.validation_service._update_document",
            side_effect=lambda c, did, updates: {**doc, **updates},
        ),
        patch(
            "app.services.validation_service.resolve_active_flags",
            return_value=None,
        ),
        patch(
            "app.services.validation_service.insert_flags",
            return_value=[flag_row],
        ),
    ):
        response = client.post("/api/v1/documents/doc-1/validate")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_review"
    assert body["summary"]["errors"] >= 1
    assert any(f["rule_code"] == "TOTAL_MISMATCH" for f in body["flags"])


def test_warning_only_keeps_ready() -> None:
    from app.services.validation_service import determine_status
    from app.services.validation.base import ValidationResult

    assert (
        determine_status(
            [
                ValidationResult(
                    rule_code="DUPLICATE_INVOICE",
                    severity="warning",
                    message="dup",
                    passed=False,
                )
            ]
        )
        == "ready"
    )
    assert determine_status([]) == "ready"
    assert (
        determine_status(
            [
                ValidationResult(
                    rule_code="TOTAL_MISMATCH",
                    severity="error",
                    message="err",
                    passed=False,
                )
            ]
        )
        == "needs_review"
    )


def test_get_validation_and_resolve_flag(client: TestClient) -> None:
    doc = _doc(status="needs_review")
    flag = {
        "id": "flag-1",
        "document_id": "doc-1",
        "rule_code": "TOTAL_MISMATCH",
        "severity": "error",
        "message": "bad total",
        "resolved": False,
        "details_json": {},
        "created_at": "2026-09-03T00:00:00Z",
    }

    mock_client = MagicMock()

    def table(name):
        if name == "documents":
            q = _chain([doc])
            # update for status recalc
            update_chain = _chain([{**doc, "status": "ready"}])
            q.update.return_value = update_chain
            return q
        if name == "validation_flags":
            q = MagicMock()
            for method in ("select", "eq", "order", "limit", "update"):
                getattr(q, method).return_value = q
            # get validation list
            q.execute.return_value = MagicMock(data=[flag])
            # resolve update then active select
            updated = {**flag, "resolved": True, "resolved_at": "2026-09-03T01:00:00Z"}

            def execute():
                return MagicMock(data=[updated])

            # We'll return different data based on call count
            q.execute.side_effect = [
                MagicMock(data=[flag]),  # get validation
            ]
            return q
        return _chain([])

    # Separate tests for GET and PATCH with clearer mocks
    mock_client.table.side_effect = lambda name: (
        _chain([doc]) if name == "documents" else _chain([flag])
    )
    with patch(
        "app.services.validation_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/documents/doc-1/validation")
    assert response.status_code == 200
    assert response.json()["summary"]["errors"] == 1

    # Resolve flag — table() is called multiple times; branch select vs update
    mock_client2 = MagicMock()
    updated_flag = {**flag, "resolved": True, "resolved_at": "2026-09-03T01:00:00Z"}
    flag_calls = {"n": 0}

    def table2(name):
        if name == "documents":
            q = _chain([doc])
            u = _chain([{**doc, "status": "ready"}])
            q.update.return_value = u
            return q
        if name == "validation_flags":
            flag_calls["n"] += 1
            q = MagicMock()
            for method in ("select", "eq", "limit", "update", "order"):
                getattr(q, method).return_value = q
            # 1=fetch, 2=update, 3=active unresolved query
            if flag_calls["n"] == 1:
                q.execute.return_value = MagicMock(data=[flag])
            elif flag_calls["n"] == 2:
                q.execute.return_value = MagicMock(data=[updated_flag])
            else:
                q.execute.return_value = MagicMock(data=[])
            return q
        return _chain([])

    mock_client2.table.side_effect = table2
    with patch(
        "app.services.validation_service.get_supabase_admin_client",
        return_value=mock_client2,
    ):
        response = client.patch(
            "/api/v1/documents/doc-1/validation-flags/flag-1",
            json={"resolved": True},
        )
    assert response.status_code == 200
    assert response.json()["resolved"] is True


def test_get_and_patch_rules(client: TestClient) -> None:
    mock_client = MagicMock()
    mock_client.table.return_value = _chain([])
    with patch(
        "app.services.validation_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.get("/api/v1/validation/rules")
    assert response.status_code == 200
    codes = {r["rule_code"] for r in response.json()["rules"]}
    assert "TOTAL_MISMATCH" in codes
    assert "LOW_CONFIDENCE_FIELD" in codes

    with patch(
        "app.services.validation_service.get_supabase_admin_client",
        return_value=mock_client,
    ):
        response = client.patch(
            "/api/v1/validation/rules",
            json={
                "rules": [
                    {
                        "rule_code": "TOTAL_MISMATCH",
                        "enabled": True,
                        "config": {"tolerance": 0.05},
                    }
                ]
            },
        )
    assert response.status_code == 200


def test_validation_crash_sets_failed() -> None:
    from app.services.document_service import DocumentServiceError
    from app.services.validation_service import validate_document

    mock_client = MagicMock()
    mock_client.table.return_value = _chain([_doc()])

    with (
        patch(
            "app.services.validation_service.get_supabase_admin_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.validation_service._fetch_document",
            return_value=_doc(),
        ),
        patch(
            "app.services.validation_service.get_latest_extraction_row",
            return_value={"id": "e1", "fields_json": {}, "line_items_json": []},
        ),
        patch(
            "app.services.validation_service._update_document",
            return_value=_doc(status="validating"),
        ) as upd,
        patch(
            "app.services.validation_service.load_rule_settings",
            side_effect=RuntimeError("boom"),
        ),
    ):
        with pytest.raises(DocumentServiceError):
            validate_document("doc-1")

    # final update should set failed
    assert any(
        call.args[2].get("status") == "failed" for call in upd.call_args_list if len(call.args) >= 3
    )

"""Effective extraction data: original AI extraction ⊕ chronological corrections."""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any

from app.schemas.extraction import HEADER_FIELD_NAMES, LINE_ITEM_FIELD_NAMES
from app.utils.normalization import field_value

logger = logging.getLogger(__name__)

LINE_ITEM_MUTATION_TYPES = {
    "line_item_update",
    "line_item_added",
    "line_item_deleted",
}


def _entry_value(entry: Any) -> Any:
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _set_field_value(fields: dict[str, Any], name: str, value: Any) -> None:
    existing = fields.get(name)
    if isinstance(existing, dict):
        updated = dict(existing)
        updated["value"] = value
        fields[name] = updated
    elif existing is None:
        fields[name] = {
            "value": value,
            "confidence": 1.0,
            "page_number": 1,
            "bbox": None,
        }
    else:
        fields[name] = value


def _flatten_line_item(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    flat: dict[str, Any] = {
        "index": index,
        "item_id": item.get("item_id"),
    }
    for name in LINE_ITEM_FIELD_NAMES:
        raw = item.get(name)
        flat[name] = _entry_value(raw)
    return flat


def _ensure_item_ids(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        cloned = copy.deepcopy(item) if isinstance(item, dict) else {}
        if not cloned.get("item_id"):
            cloned["item_id"] = str(uuid.uuid4())
        result.append(cloned)
    return result


def _set_line_field(item: dict[str, Any], name: str, value: Any) -> None:
    existing = item.get(name)
    if isinstance(existing, dict):
        updated = dict(existing)
        updated["value"] = value
        # Human correction: treat as high confidence
        updated["confidence"] = 1.0
        item[name] = updated
    else:
        item[name] = {
            "value": value,
            "confidence": 1.0,
            "page_number": 1,
            "bbox": None,
        }


def parse_original_extraction(row: dict[str, Any]) -> dict[str, Any]:
    fields_json = row.get("fields_json") or {}
    document_type = "unknown"
    fields: dict[str, Any] = {}
    if isinstance(fields_json, dict):
        document_type = fields_json.get("document_type") or "unknown"
        fields = {
            k: copy.deepcopy(v)
            for k, v in fields_json.items()
            if k != "document_type"
        }
    line_items = copy.deepcopy(row.get("line_items_json") or [])
    if not isinstance(line_items, list):
        line_items = []
    return {
        "id": row.get("id"),
        "document_id": row.get("document_id"),
        "document_type": document_type,
        "fields": fields,
        "line_items": line_items,
        "overall_confidence": row.get("overall_confidence"),
        "model": row.get("model"),
        "created_at": row.get("created_at"),
        "raw_row": row,
    }


def fetch_corrections(
    client,
    *,
    document_id: str,
    extraction_id: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        client.table("document_corrections")
        .select("*")
        .eq("document_id", document_id)
        .order("created_at", desc=False)
    )
    if extraction_id:
        query = query.eq("extraction_id", extraction_id)
    try:
        response = query.execute()
        return response.data or []
    except Exception as exc:
        logger.warning("Could not load document_corrections: %s", exc)
        return []


def apply_corrections(
    original: dict[str, Any],
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Apply chronological corrections onto a copy of the original extraction.

    Returns effective extraction plus metadata maps for review UI.
    """
    fields = copy.deepcopy(original.get("fields") or {})
    line_items = _ensure_item_ids(copy.deepcopy(original.get("line_items") or []))

    # Track latest correction per field for UI
    field_corrections: dict[str, Any] = {}
    # Original AI values (before any corrections)
    original_field_values: dict[str, Any] = {
        name: field_value(fields, name) for name in HEADER_FIELD_NAMES
    }

    for correction in corrections:
        ctype = correction.get("correction_type")
        if ctype == "field_correction":
            name = correction.get("field_name")
            if not name:
                continue
            new_value = correction.get("corrected_value_json")
            # JSONB may store primitives directly
            if isinstance(new_value, dict) and "value" in new_value and len(new_value) == 1:
                new_value = new_value.get("value")
            _set_field_value(fields, name, new_value)
            field_corrections[name] = new_value

        elif ctype == "line_item_update":
            idx = correction.get("line_item_index")
            patch = correction.get("corrected_value_json") or {}
            if idx is None or not isinstance(patch, dict):
                continue
            if idx < 0 or idx >= len(line_items):
                continue
            item = line_items[idx]
            for key, value in patch.items():
                if key in LINE_ITEM_FIELD_NAMES:
                    _set_line_field(item, key, value)

        elif ctype == "line_item_added":
            payload = correction.get("corrected_value_json") or {}
            if not isinstance(payload, dict):
                continue
            new_item: dict[str, Any] = {"item_id": payload.get("item_id") or str(uuid.uuid4())}
            for key in LINE_ITEM_FIELD_NAMES:
                if key in payload:
                    _set_line_field(new_item, key, payload.get(key))
                else:
                    new_item[key] = None
            line_items.append(new_item)

        elif ctype == "line_item_deleted":
            idx = correction.get("line_item_index")
            if idx is None or idx < 0 or idx >= len(line_items):
                continue
            line_items.pop(idx)

    return {
        "id": original.get("id"),
        "document_id": original.get("document_id"),
        "document_type": original.get("document_type"),
        "fields": fields,
        "line_items": line_items,
        "overall_confidence": original.get("overall_confidence"),
        "model": original.get("model"),
        "created_at": original.get("created_at"),
        "field_corrections": field_corrections,
        "original_field_values": original_field_values,
        "corrections": corrections,
    }


def build_effective_extraction(
    client,
    *,
    document_id: str,
    extraction_row: dict[str, Any],
) -> dict[str, Any]:
    original = parse_original_extraction(extraction_row)
    corrections = fetch_corrections(
        client,
        document_id=document_id,
        extraction_id=str(extraction_row.get("id")),
    )
    # Only data-affecting corrections for effective merge
    data_corrections = [
        c
        for c in corrections
        if c.get("correction_type")
        in {"field_correction", *LINE_ITEM_MUTATION_TYPES}
    ]
    return apply_corrections(original, data_corrections)


def build_review_fields(effective: dict[str, Any]) -> list[dict[str, Any]]:
    fields = effective.get("fields") or {}
    original_values = effective.get("original_field_values") or {}
    corrections_map = effective.get("field_corrections") or {}

    result: list[dict[str, Any]] = []
    for name in HEADER_FIELD_NAMES:
        entry = fields.get(name)
        original_ai = original_values.get(name)
        corrected = corrections_map.get(name) if name in corrections_map else None
        is_corrected = name in corrections_map

        confidence = None
        page_number = None
        bbox = None
        if isinstance(entry, dict):
            confidence = entry.get("confidence")
            page_number = entry.get("page_number")
            bbox = entry.get("bbox")
            current = entry.get("value")
            # Prefer original_value from extraction field metadata when uncorrected
            if not is_corrected and entry.get("original_value") is not None:
                display_original = entry.get("original_value")
            else:
                display_original = original_ai
        else:
            current = entry
            display_original = original_ai

        effective_value = corrected if is_corrected else current
        # If corrected, original_value in response is the AI original
        result.append(
            {
                "field_name": name,
                "value": effective_value,
                "original_value": display_original if display_original is not None else original_ai,
                "corrected_value": corrected if is_corrected else None,
                "confidence": float(confidence) if confidence is not None else None,
                "page_number": page_number,
                "bbox": bbox,
                "is_corrected": is_corrected,
            }
        )
    return result


def build_review_line_items(effective: dict[str, Any]) -> list[dict[str, Any]]:
    items = effective.get("line_items") or []
    return [_flatten_line_item(item, index=i) for i, item in enumerate(items)]


def insert_correction(
    client,
    *,
    document_id: str,
    extraction_id: str,
    correction_type: str,
    field_name: str | None = None,
    line_item_index: int | None = None,
    original_value: Any = None,
    corrected_value: Any = None,
    corrected_by: str | None = None,
) -> dict[str, Any]:
    row = {
        "document_id": document_id,
        "extraction_id": extraction_id,
        "correction_type": correction_type,
        "field_name": field_name,
        "line_item_index": line_item_index,
        "original_value_json": original_value,
        "corrected_value_json": corrected_value,
        "corrected_by": corrected_by,
    }
    response = client.table("document_corrections").insert(row).execute()
    if not response.data:
        raise RuntimeError("Failed to insert document correction")
    return response.data[0]

"""Prompt builder for invoice/receipt vision extraction."""

from __future__ import annotations

from typing import Any

EXTRACTION_SCHEMA_VERSION = "v1"

_SYSTEM_PROMPT = """You are an invoice and receipt data extraction system.

Extract information ONLY from the provided document page images.
DO NOT HALLUCINATE. Do not invent values. Do not guess missing fields.
Do not calculate missing totals or tax. Only extract visibly present information.

Missing vs uncertain:
- Missing (not present on the document): value = null, confidence low or omit certainty
- Uncertain (partially readable): return best reading with lower confidence (e.g. 0.4–0.69)
- Clear text: confidence 0.90–1.00
- Readable but imperfect: confidence 0.70–0.89

Return VALID JSON ONLY. No markdown. No explanations outside JSON.

For every extracted field return an object with:
- value
- confidence (number between 0 and 1)
- page_number (integer starting at 1)
- bbox: {x, y, width, height} using NORMALIZED coordinates from 0.0 to 1.0
  where x=left, y=top, relative to the page size
- original_value (optional): the exact text as seen
- original_format (optional): for dates, e.g. DD/MM/YYYY

Bounding box rules:
- Approximate boxes are acceptable (vision models are not pixel-perfect)
- 0 <= x,y,width,height <= 1
- x + width <= 1
- y + height <= 1
If a bbox cannot be determined confidently, set bbox to null (never invent invalid numbers).

Multi-page documents:
- Use all pages. Headers may be on page 1, line items on later pages, totals on the last page.
- Merge into one JSON object. Do not duplicate header fields.
- Set page_number to the page where that value appears.

Dates:
- Prefer ISO 8601 (YYYY-MM-DD) in value when format is confidently known
- Preserve original text in original_value

Currency:
- Prefer ISO 4217 codes (USD, EUR, INR, GBP) when identifiable
- Do not assume USD for "$" if context is ambiguous — return null

Numbers:
- Return clean numeric values without currency symbols or thousands separators
- Example: "$1,250.50" -> 1250.50

document_type must be one of: invoice, receipt, unknown

JSON shape:
{
  "document_type": "invoice",
  "fields": {
    "vendor_name": {"value": "...", "confidence": 0.0, "page_number": 1, "bbox": null},
    "vendor_address": null,
    "vendor_tax_id": null,
    "invoice_number": null,
    "invoice_date": null,
    "due_date": null,
    "po_number": null,
    "bill_to_name": null,
    "bill_to_address": null,
    "currency": null,
    "subtotal": null,
    "tax_amount": null,
    "tax_rate": null,
    "discount": null,
    "shipping": null,
    "total": null,
    "payment_terms": null,
    "bank_details": null
  },
  "line_items": [
    {
      "description": {"value": "...", "confidence": 0.0, "page_number": 1, "bbox": null},
      "quantity": null,
      "unit": null,
      "unit_price": null,
      "line_tax": null,
      "line_total": null
    }
  ],
  "overall_confidence": 0.0
}
"""


def build_extraction_system_prompt() -> str:
    return _SYSTEM_PROMPT


def build_extraction_user_prompt(
    *,
    page_count: int,
    vendor_hints: dict[str, Any] | None = None,
    validation_error: str | None = None,
) -> str:
    parts = [
        f"The document has {page_count} page(s). Images are provided in page order (Page 1..{page_count}).",
        "Extract all available invoice/receipt fields and line items.",
        "Assign the correct page_number for every field.",
        "Use every page. Do not skip pages.",
    ]
    if vendor_hints:
        guidance = vendor_hints.get("guidance") if isinstance(vendor_hints, dict) else None
        vendor_name = vendor_hints.get("vendor_name") if isinstance(vendor_hints, dict) else None
        parts.append("Vendor-specific extraction guidance:")
        if vendor_name:
            parts.append(f"Likely vendor context: {vendor_name}")
        if isinstance(guidance, list) and guidance:
            parts.extend(str(line) for line in guidance)
        else:
            parts.append(f"Optional vendor hints: {vendor_hints}")
        parts.extend(
            [
                "Use these as hints only.",
                "Do not invent values based on hints.",
                "Always extract from visible document evidence.",
            ]
        )
    if validation_error:
        parts.extend(
            [
                "Previous response failed validation.",
                "Correct the JSON structure and values.",
                f"Validation error: {validation_error}",
                "Return valid JSON matching the required schema.",
            ]
        )
    return "\n".join(parts)

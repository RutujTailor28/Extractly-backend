# Extractly API

Invoice and Receipt Extraction API with Human Review.

## Setup

1. Create a virtual environment

```bash
python -m venv .venv
```

2. Activate the environment

**macOS / Linux:**

```bash
source .venv/bin/activate
```

**Windows:**

```bash
.venv\Scripts\activate
```

3. Install dependencies

```bash
pip install -r requirements.txt
```

4. Create `.env` from the example file

```bash
cp .env.example .env
```

5. Add your Supabase credentials to `.env`

```env
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_anon_key
```

6. Run the server

```bash
uvicorn app.main:app --reload
```

## URLs

| Resource | URL |
|----------|-----|
| API | http://127.0.0.1:8000 |
| Swagger Docs | http://127.0.0.1:8000/docs |
| Health Check | http://127.0.0.1:8000/health |
| Dev DB Status | http://127.0.0.1:8000/api/v1/dev/database-status |

## DATABASE SETUP

Phase 2 creates the PostgreSQL schema in Supabase. FastAPI does **not** create tables automatically — run the SQL files manually.

### 1. Create a Supabase project

1. Go to [https://supabase.com](https://supabase.com) and create a project
2. Wait until the database is ready

### 2. Find your credentials

In the Supabase Dashboard:

1. Open **Project Settings → API**
2. Copy **Project URL** → `SUPABASE_URL`
3. Copy **anon / public** key → `SUPABASE_KEY`

Do not commit the service role key. Use only the configured env key for now.

### 3. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```env
APP_NAME=Extractly API
APP_VERSION=0.1.0
ENVIRONMENT=development

SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_KEY=YOUR_SUPABASE_ANON_KEY
```

### 4. Run `schema.sql`

1. Open your Supabase project
2. Go to **SQL Editor**
3. Create a new query
4. Copy the full contents of `database/schema.sql`
5. Run the query

This creates:

| Table | Purpose |
|-------|---------|
| `documents` | Uploaded invoice/receipt metadata and processing status |
| `vendors` | Vendor directory and future template hints |
| `extractions` | Extraction runs linked to documents |
| `field_values` | Individual extracted fields with confidence and bbox |
| `validation_flags` | Rule findings (error/warning) per document |
| `api_keys` | Hashed API keys only (no raw keys) |

Also creates:

- `pgcrypto` extension for `gen_random_uuid()`
- `update_updated_at_column()` trigger function
- `updated_at` triggers on `documents` and `vendors`
- Indexes for status, timestamps, foreign keys, and duplicate-detection prep

### 5. Run `seed.sql` (optional)

1. In **SQL Editor**, create another query
2. Copy the contents of `database/seed.sql`
3. Run the query

Seeds:

- 3 vendors: Acme Office Supplies, Vertex Technologies, Apex Courier & Freight
- 5 documents with statuses: `ready`, `needs_review`, `queued`, `approved`, `extracting`

Does **not** seed extractions, field values, flags, or API keys.

### 6. Test the connection

Start the API:

```bash
uvicorn app.main:app --reload
```

Then check:

```bash
curl http://127.0.0.1:8000/health
```

Expected when schema is applied and credentials are valid:

```json
{
  "status": "healthy",
  "service": "extractly-api",
  "database": "connected"
}
```

If Supabase is unreachable or tables are missing:

```json
{
  "status": "degraded",
  "service": "extractly-api",
  "database": "disconnected"
}
```

Development-only check (requires `ENVIRONMENT=development`):

```bash
curl http://127.0.0.1:8000/api/v1/dev/database-status
```

### Relationships

```
documents 1───* extractions 1───* field_values
documents 1───* validation_flags
vendors          (standalone for now; link in later phases)
api_keys         (standalone; auth in a later phase)
```

Foreign keys:

- `extractions.document_id` → `documents.id` (ON DELETE CASCADE)
- `field_values.extraction_id` → `extractions.id` (ON DELETE CASCADE)
- `validation_flags.document_id` → `documents.id` (ON DELETE CASCADE)

## PHASE 3 — DOCUMENT UPLOAD

Upload invoices and receipts to Supabase Storage and create `documents` rows.

### Supported file types

| Type | Extensions | MIME |
|------|------------|------|
| PDF | `.pdf` | `application/pdf` |
| JPEG | `.jpg`, `.jpeg` | `image/jpeg` |
| PNG | `.png` | `image/png` |

### Limits

- Maximum size: **10 MB** per file (`MAX_FILE_SIZE_MB`)
- Batch upload: multiple files in one request
- Partial failures: valid files succeed even if others fail

### Storage bucket setup

FastAPI does **not** create the bucket automatically.

1. Open Supabase Dashboard → **Storage**
2. Create a new bucket named **`documents`**
3. For development:
   - Keep the bucket **private**
   - Use `SUPABASE_SERVICE_ROLE_KEY` only on the backend (already in `.env`)
   - Do **not** put the service role key in the frontend
4. Object path format inside the bucket:

```text
{document_id}/{safe_filename}
```

Example:

```text
550e8400-e29b-41d4-a716-446655440000/invoice.pdf
```

### API endpoint

```http
POST /api/v1/documents
Content-Type: multipart/form-data
```

Form field name: `files` (repeat for batch uploads)

### Example cURL

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/documents" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "files=@invoice.pdf" \
  -F "files=@receipt.jpg"
```

### Example response

```json
{
  "message": "Upload completed",
  "successful": 2,
  "failed": 1,
  "documents": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "filename": "invoice.pdf",
      "mime_type": "application/pdf",
      "file_size": 245678,
      "status": "queued",
      "source": "upload",
      "created_at": "2026-09-03T10:00:00+00:00"
    }
  ],
  "errors": [
    {
      "filename": "invalid.exe",
      "error": "Unsupported file extension '.exe'. Allowed: .pdf, .jpg, .jpeg, .png"
    }
  ]
}
```

### Test with Swagger

1. Start the server: `uvicorn app.main:app --reload`
2. Open http://127.0.0.1:8000/docs
3. Expand **POST /api/v1/documents**
4. Click **Try it out**, choose one or more PDF/JPG/PNG files, then **Execute**
5. Confirm in Supabase:
   - **Storage** → `documents` → `{document_id}/...`
   - **Table Editor** → `documents` → `status = queued`, `source = upload`, `file_hash` set

### Config values

```env
MAX_FILE_SIZE_MB=10
SUPABASE_DOCUMENTS_BUCKET=documents
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
```

### Run unit tests

```bash
pip install pytest
pytest tests/test_documents.py -q
```

Unit tests mock Supabase. They do not upload to your live project.

## PHASE 4 — COMPLETE DOCUMENTS MODULE

Full document management APIs (list, detail, update, file access, delete).

### Database migration (required)

Phase 4 adds `storage_path` to `documents`.

1. Open Supabase → **SQL Editor**
2. Run:

`database/migrations/001_add_documents_storage_path.sql`

This:

- Adds `storage_path`
- Backfills existing rows as `{document_id}/{filename}`
- Creates an index

New installs using updated `database/schema.sql` already include the column.

### APIs

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/documents` | Upload (Phase 3) |
| `GET` | `/api/v1/documents` | List + pagination/filters |
| `GET` | `/api/v1/documents/{id}` | Document detail |
| `PATCH` | `/api/v1/documents/{id}` | Update status/source |
| `DELETE` | `/api/v1/documents/{id}` | Delete DB + storage file |
| `GET` | `/api/v1/documents/{id}/file` | Temporary signed URL |

### List query parameters

```text
page, page_size, status, search, vendor, date_from, date_to, flagged, sort_by, sort_order
```

Defaults: `page=1`, `page_size=20`, `sort_by=created_at`, `sort_order=desc`  
Max `page_size`: 100  
Allowed `sort_by`: `created_at`, `filename`, `status`

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/documents?page=1&page_size=20&status=needs_review&search=invoice"
```

### Get document

```bash
curl "http://127.0.0.1:8000/api/v1/documents/{id}"
```

Response always includes:

```json
{
  "document": { "...": "..." },
  "extraction": null,
  "fields": [],
  "validation_flags": []
}
```

### Update document

```bash
curl -X PATCH "http://127.0.0.1:8000/api/v1/documents/{id}" \
  -H "Content-Type: application/json" \
  -d '{"status":"needs_review"}'
```

Allowed body fields only: `status`, `source`.

### File access (signed URL)

```bash
curl "http://127.0.0.1:8000/api/v1/documents/{id}/file"
```

```json
{
  "document_id": "...",
  "filename": "invoice.pdf",
  "mime_type": "application/pdf",
  "url": "temporary-signed-url",
  "expires_in": 3600
}
```

Config: `FILE_URL_EXPIRY_SECONDS=3600`

### Delete document

```bash
curl -X DELETE "http://127.0.0.1:8000/api/v1/documents/{id}"
```

Deletes the Storage object first, then the database row (related rows cascade).

### Swagger test order

1. `POST /api/v1/documents` — upload  
2. `GET /api/v1/documents` — list  
3. `GET /api/v1/documents/{id}` — detail  
4. `PATCH /api/v1/documents/{id}` — update status  
5. `GET /api/v1/documents/{id}/file` — signed URL  
6. `DELETE /api/v1/documents/{id}` — remove  
7. `GET /api/v1/documents` — confirm gone  

## PHASE 5 — DOCUMENT PROCESSING

Prepare uploaded PDFs/images as normalized page PNGs for future AI extraction.

### Architecture

```text
documents bucket (original)
        ↓
POST /documents/{id}/process
        ↓
PDF render / image load
        ↓
EXIF rotate → deskew → contrast → resize
        ↓
processed-documents bucket (page-N.png)
        ↓
document_pages rows + status=ready
```

### Required Supabase setup

1. Run migration:

`database/migrations/002_create_document_pages.sql`

2. Create private Storage bucket:

**`processed-documents`**

Keep **`documents`** for originals. Do not overwrite originals.

Processed path:

```text
processed/{document_id}/page-{n}.png
```

### APIs

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/documents/{id}/process` | Process document |
| `GET` | `/api/v1/documents/{id}/pages` | List processed pages |
| `GET` | `/api/v1/documents/{id}/pages/{n}` | Signed URL for page |

### Status flow

```text
queued | failed | ready  →  preprocessing  →  ready
                                 ↓
                              failed
```

Blocked while status is `preprocessing`, `extracting`, or `validating`.

Reprocessing deletes previous page rows/files, then processes again.

### Environment variables

```env
PROCESSED_DOCUMENTS_BUCKET=processed-documents
PDF_RENDER_DPI=180
MAX_IMAGE_WIDTH=2500
MAX_IMAGE_HEIGHT=3500
MAX_DESKEW_ANGLE=10
FILE_URL_EXPIRY_SECONDS=3600
```

### Example

```bash
# Process
curl -X POST "http://127.0.0.1:8000/api/v1/documents/{id}/process"

# List pages
curl "http://127.0.0.1:8000/api/v1/documents/{id}/pages"

# Page signed URL
curl "http://127.0.0.1:8000/api/v1/documents/{id}/pages/1"
```

### Swagger test order

1. Upload with `POST /api/v1/documents`
2. `POST /api/v1/documents/{id}/process`
3. `GET /api/v1/documents/{id}/pages`
4. `GET /api/v1/documents/{id}/pages/1` → open `url`
5. Process again → confirm no duplicate pages

### Dependencies

`PyMuPDF`, `Pillow`, `opencv-python-headless`, `numpy`

## PHASE 6 — AI EXTRACTION MODULE

Extract structured invoice/receipt data from processed page images using a vision AI provider.

### Architecture

```text
processed pages
      ↓
POST /documents/{id}/extract
      ↓
AI provider abstraction (openai | mock)
      ↓
Pydantic validation + retry (max 3 attempts)
      ↓
Normalize dates / currency / decimals
      ↓
Save extractions + field_values
      ↓
GET /documents/{id}/extraction
```

### AI provider configuration

```env
AI_PROVIDER=mock          # mock | openai
AI_MODEL=gpt-4o
AI_API_KEY=               # required for openai
AI_TIMEOUT_SECONDS=60
AI_MAX_RETRIES=2
```

- `mock` — deterministic local responses for tests/dev without API cost
- `openai` — GPT-4o vision JSON extraction

### APIs

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/documents/{id}/extract` | Run extraction |
| `GET` | `/api/v1/documents/{id}/extraction` | Latest extraction |
| `POST` | `/api/v1/documents/{id}/reextract` | New extraction (keeps history) |

### Bounding boxes

Normalized 0–1 coordinates:

```json
{ "x": 0.10, "y": 0.08, "width": 0.35, "height": 0.04 }
```

### Confidence

Every field includes `confidence` in `[0, 1]` from the model.

### Retry strategy

Invalid JSON / Pydantic failures / transient provider errors retry up to `AI_MAX_RETRIES` (default 2 → 3 total attempts). Validation errors are fed back into the prompt.

### Swagger flow

1. Upload → Process → status `ready`
2. `POST /extract`
3. `GET /extraction`
4. `POST /reextract` → new extraction id, old rows preserved

### Example

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/documents/{id}/extract"
curl "http://127.0.0.1:8000/api/v1/documents/{id}/extraction"
curl -X POST "http://127.0.0.1:8000/api/v1/documents/{id}/reextract"
```

## PHASE 7 — VALIDATION RULES ENGINE

Independent Python validation of extracted invoice/receipt data. AI output is never blindly trusted.

### Architecture

```text
latest extraction ⊕ chronological corrections
      ↓
effective extraction data
      ↓
POST /documents/{id}/validate
      ↓
Load enabled rules (defaults ⊕ user overrides)
      ↓
Run modular rules (Decimal-safe arithmetic)
      ↓
Resolve previous active flags → insert new flags
      ↓
Any unresolved ERROR? → needs_review : ready
```

Validation is **explicit** (not auto-run after extract). Re-validation is supported.

### Migration

Run in Supabase SQL Editor:

`database/migrations/003_validation_engine.sql`

Adds:

- `validation_flags.details_json`
- `info` severity
- `validation_rule_settings` table

### Severity

| Severity | Effect on status |
|----------|------------------|
| `error` | Document → `needs_review` |
| `warning` | Document can stay `ready` |
| `info` | Informational only |

### Rules

| Rule code | Default | Notes |
|-----------|---------|-------|
| `LINE_ITEMS_SUBTOTAL_MISMATCH` | error | Σ line totals vs subtotal (tol 0.02) |
| `TOTAL_MISMATCH` | error | subtotal + tax + shipping − discount vs total |
| `INVOICE_DATE_IN_FUTURE` | error | UTC calendar date |
| `DUE_DATE_BEFORE_INVOICE_DATE` | error | Chronological order |
| `REQUIRED_FIELD_MISSING` | error | Per document_type |
| `DUPLICATE_INVOICE` | warning | Same vendor + invoice # |
| `POSSIBLE_NEAR_DUPLICATE` | warning | Vendor + amount + date window |
| `UNUSUAL_TAX_RATE` | warning | Default 0%–30% |
| `LOW_CONFIDENCE_FIELD` | warning | Default threshold 0.70 |

Tax rate normalization: values with `|x| ≤ 1` (e.g. `0.18`) are treated as fractions → `18%`.

### Status routing

```text
ready / needs_review / failed
        ↓
    validating
        ↓
   ERROR? ──yes──→ needs_review
      │
      no
      ↓
    ready
```

On crash: status → `failed` (never left stuck at `validating`).

Resolving the last unresolved error flag recalculates status to `ready` (does not approve).

### APIs

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/documents/{id}/validate` | Run validation |
| `GET` | `/api/v1/documents/{id}/validation` | Flags (`include_resolved`) |
| `PATCH` | `/api/v1/documents/{id}/validation-flags/{flag_id}` | Resolve flag |
| `GET` | `/api/v1/validation/rules` | Defaults ⊕ overrides |
| `PATCH` | `/api/v1/validation/rules` | Enable/config rules |

### Swagger testing

1. Upload → Process → Extract
2. `POST /validate` (use mismatched totals to force `needs_review`)
3. `GET /validation`
4. `PATCH .../validation-flags/{flag_id}` with `{"resolved": true}`
5. `GET /validation/rules` then `PATCH` tolerance e.g. `0.05`

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/documents/{id}/validate"
curl "http://127.0.0.1:8000/api/v1/documents/{id}/validation"
curl -X PATCH "http://127.0.0.1:8000/api/v1/documents/{id}/validation-flags/{flag_id}" \
  -H "Content-Type: application/json" \
  -d '{"resolved": true}'
curl "http://127.0.0.1:8000/api/v1/validation/rules"
curl -X PATCH "http://127.0.0.1:8000/api/v1/validation/rules" \
  -H "Content-Type: application/json" \
  -d '{"rules":[{"rule_code":"TOTAL_MISMATCH","enabled":true,"config":{"tolerance":0.05}}]}'
```

### Tests

```bash
pytest tests/test_validation.py -q
```

## PHASE 8 — HUMAN REVIEW & CORRECTION APIs

Backend support for the Human Review workspace: correct fields/line items, re-validate, approve.

### Workflow

```text
needs_review
    ↓
GET /review  (document + pages + fields + flags)
    ↓
PATCH /fields  /  line-items CRUD
    ↓
Append-only document_corrections
    ↓
Re-run Phase 7 validation on effective data
    ↓
ready (no ERROR flags)
    ↓
POST /approve → approved
```

### Architecture

- **AI extraction is immutable** (`fields_json`, `line_items_json`, `raw_response_json` never overwritten)
- Corrections stored in append-only `document_corrections`
- `review_data_service` replays corrections chronologically → **effective extraction**
- Validation (Phase 7) runs against effective data
- `field_values.corrected_value` updated when a matching row exists
- Reviewer identity (no auth yet): `system-reviewer`

### Migration

Run in Supabase SQL Editor:

`database/migrations/004_review_and_corrections.sql`

Adds:

- `documents.approved_at`, `documents.approved_by`
- `document_corrections` table

### APIs

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/documents/{id}/review` | Full review payload |
| `PATCH` | `/api/v1/documents/{id}/fields` | Bulk field corrections |
| `PATCH` | `/api/v1/documents/{id}/line-items` | Update line items |
| `POST` | `/api/v1/documents/{id}/line-items` | Add line item |
| `DELETE` | `/api/v1/documents/{id}/line-items/{index}` | Delete line item |
| `POST` | `/api/v1/documents/{id}/approve` | Final approval |
| `POST` | `/api/v1/documents/{id}/unapprove` | Reopen for edits |
| `GET` | `/api/v1/documents/{id}/review-history` | Audit trail |

### Edit protection

| Status | Edit | Approve |
|--------|------|---------|
| processing (`queued`…`validating`) | 409 | 409 |
| `approved` | 409 (unapprove first) | no-op |
| unresolved ERROR flags | allowed | 409 |
| warnings only | allowed | allowed |

### Field display logic

`value` = corrected value if present, else original AI value. `original_value` always preserved.

### Line items

Mutations apply to the corrected representation only. Each item gets a stable `item_id`. Responses include recalculated `line_items_subtotal` and `expected_total` (Decimal). Totals are **not** auto-overwritten on the invoice.

### History types

`field_correction`, `line_item_update`, `line_item_added`, `line_item_deleted`, `validation_resolved`, `document_approved`, `document_unapproved`

### Swagger testing

1. Extract + validate → `needs_review`
2. `GET /review`
3. `PATCH /fields` (fix totals/subtotals)
4. Line item update / add / delete
5. `GET /review-history`
6. Approve with errors → 409; fix → `POST /approve`
7. Edit approved → 409; `POST /unapprove` → edit again

```bash
curl "http://127.0.0.1:8000/api/v1/documents/{id}/review"
curl -X PATCH "http://127.0.0.1:8000/api/v1/documents/{id}/fields" \
  -H "Content-Type: application/json" \
  -d '{"fields":[{"field_name":"subtotal","value":"1240.00"}]}'
curl -X POST "http://127.0.0.1:8000/api/v1/documents/{id}/approve"
curl -X POST "http://127.0.0.1:8000/api/v1/documents/{id}/unapprove"
curl "http://127.0.0.1:8000/api/v1/documents/{id}/review-history"
```

### Tests

```bash
pytest tests/test_review.py -q
```

## PHASE 9 — VENDOR LEARNING + ACCURACY ANALYTICS

Correction-feedback loop (not model fine-tuning): human corrections → vendor hints → future extraction context → measurable quality metrics.

### Architecture

```text
Extraction → vendor auto-link (normalized name)
      ↓
Human corrections (document_corrections)
      ↓
POST /vendors/{id}/analyze-corrections
      ↓
Deterministic patterns → template_hints_json
      ↓
Extract / re-extract injects hints into prompt
      ↓
Analytics: correction_rate, coverage, accuracy (when evidenced)
```

### Migration

Run in Supabase SQL Editor:

`database/migrations/005_vendor_learning_and_analytics.sql`

Adds:

- `documents.vendor_id`
- `vendors.name_normalized`, `vendors.last_invoice_at`
- helpful indexes

### Vendor association

After each successful extraction:

1. Normalize `vendor_name` (lowercase, trim, collapse spaces)
2. Exact-match existing vendor or create one
3. Set `documents.vendor_id`
4. Recount unique documents → `invoice_count` / `last_invoice_at`

### Hints

Stored in `vendors.template_hints_json`.

| Source | Behavior |
|--------|----------|
| `generated` | Replaced on each analyze-corrections run |
| `manual` | Preserved forever unless edited/deleted |

Patterns (min evidence usually 3): frequently corrected field, low-confidence+correction, invoice prefix format, date format, preferred currency.

Hints are advisory only — prompts instruct the model not to invent values from hints.

### Accuracy vs confidence

| Metric | Meaning |
|--------|---------|
| **Confidence** | Model self-score (prediction) |
| **Accuracy** | Correct fields / reviewed fields — only when review evidence exists |
| **Correction rate** | Incorrect / reviewed |
| **Coverage** | Reviewed / possible fields |

Review evidence:

- Field corrected → incorrect
- Document **approved** and field never corrected → correct (accepted)
- Otherwise → unknown (excluded from accuracy denominator)

If no reviewed evidence: `accuracy = null` (never invented).

### APIs

| Method | Path |
|--------|------|
| `GET` | `/api/v1/vendors` |
| `GET` | `/api/v1/vendors/{id}` |
| `GET` | `/api/v1/vendors/{id}/hints` |
| `PATCH` | `/api/v1/vendors/{id}/hints` |
| `POST` | `/api/v1/vendors/{id}/analyze-corrections` |
| `GET` | `/api/v1/analytics/overview` |
| `GET` | `/api/v1/analytics/accuracy` |
| `GET` | `/api/v1/analytics/vendors` |
| `GET` | `/api/v1/analytics/trends` |

### Swagger testing

1. Extract several same-vendor invoices
2. Correct `invoice_number` ≥ 3 times
3. `POST /vendors/{id}/analyze-corrections` → generated hint
4. `GET /vendors/{id}/hints`
5. Re-extract → hints appear in AI prompt context
6. `GET /analytics/overview?days=30`
7. `GET /analytics/accuracy`, `/analytics/vendors`, `/analytics/trends`

```bash
curl "http://127.0.0.1:8000/api/v1/vendors"
curl -X POST "http://127.0.0.1:8000/api/v1/vendors/{id}/analyze-corrections"
curl "http://127.0.0.1:8000/api/v1/analytics/overview?days=30"
curl "http://127.0.0.1:8000/api/v1/analytics/trends?days=30&metric=corrections&group_by=day"
```

### Tests

```bash
pytest tests/test_vendor_learning.py tests/test_analytics.py -q
```

## PHASE 10 — EXPORT & ACCOUNTING INTEGRATIONS

Export **approved** invoices (effective/corrected values) and simulate accounting handoff.

### Architecture

```text
Approved documents (filter by approved_at)
        ↓
Effective extraction (Phase 8 corrections applied)
        ↓
CSV / XLSX / JSON  OR  QuickBooks-compatible CSV
```

```text
Approved document → accounting payload → mock send → integration_logs
```

Date filters use **`approved_at`** (not `created_at`).

### Migration

Run in Supabase SQL Editor:

`database/migrations/006_export_and_integrations.sql`

Creates `integration_logs` + `documents(approved_at)` index.

Also install: `pip install openpyxl`

### Export rules

- **Status:** only `approved` (other statuses → 422)
- **Source of truth:** effective values after human corrections
- **Limit:** `EXPORT_MAX_DOCUMENTS` (default 1000)
- **Date range:** max `EXPORT_MAX_DATE_RANGE_DAYS` (default 365)
- Empty result → 404

### Formats

| Format | Behavior |
|--------|----------|
| `csv` + line items | ZIP: `invoices.csv` + `line_items.csv` |
| `csv` header only | Single `invoices.csv` |
| `xlsx` | Sheets: Invoices (+ Line Items) |
| `json` | Nested invoice payload (no raw AI / prompts) |

### QuickBooks-compatible mapping

**Not an official QuickBooks API.** CSV columns suited for AP import; one row per line item (invoice fields repeated). Preview capped at 100 rows.

### Mock accounting integration

| Endpoint | Purpose |
|----------|---------|
| `GET .../integration-preview` | Payload that would be sent |
| `POST .../send-to-accounting` | Mock send (`provider=mock` only) |
| `GET .../integration-history` | Prior attempts |

**Idempotency:** successful `(document_id, provider)` returns the existing result and does **not** insert a duplicate log.

### APIs

| Method | Path |
|--------|------|
| `GET` | `/api/v1/export` |
| `GET` | `/api/v1/export/quickbooks` |
| `GET` | `/api/v1/export/quickbooks/preview` |
| `GET` | `/api/v1/documents/{id}/integration-preview` |
| `POST` | `/api/v1/documents/{id}/send-to-accounting` |
| `GET` | `/api/v1/documents/{id}/integration-history` |

### Swagger testing

1. Approve at least one document
2. `GET /export?format=csv&status=approved`
3. `GET /export?format=xlsx` / `json`
4. `GET /export/quickbooks/preview` then `/export/quickbooks`
5. `GET /documents/{id}/integration-preview`
6. `POST /documents/{id}/send-to-accounting` `{"provider":"mock"}`
7. Send again → `already_sent: true`
8. `GET /documents/{id}/integration-history`

```bash
curl -OJ "http://127.0.0.1:8000/api/v1/export?format=csv&include_line_items=true"
curl "http://127.0.0.1:8000/api/v1/export/quickbooks/preview"
curl -X POST "http://127.0.0.1:8000/api/v1/documents/{id}/send-to-accounting" \
  -H "Content-Type: application/json" \
  -d '{"provider":"mock"}'
```

### Tests

```bash
pytest tests/test_export.py tests/test_integrations.py -q
```

## PHASE 11 — PUBLIC API PLATFORM

Transforms Extractly into a developer platform: API keys, rate limits, public extract, async jobs, signed webhooks, and usage analytics.

### Architecture

```text
External app
    → X-API-Key
    → Rate limit (per key)
    → POST /api/v1/extract
    → Upload + extraction_jobs
    → Existing process + extract pipeline
    → Poll GET /api/v1/extract/{job_id}
       OR signed webhook (extraction.completed / failed)
```

Internal app APIs (`/documents`, `/vendors`, `/export`, …) remain separate from the public developer surface.

### Migration

Run in Supabase SQL Editor:

`database/migrations/007_public_api_and_jobs.sql`

Also: `pip install httpx` (already in `requirements.txt`).

Optional env:

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY_PEPPER` | dev default | Pepper for SHA-256 key hashing (**change in prod**) |
| `API_KEY_DEFAULT_RATE_LIMIT` | `60` | Requests/minute |
| `API_KEY_MAX_RATE_LIMIT` | `1000` | Max allowed on create |
| `PUBLIC_API_MAX_FILE_SIZE_MB` | `25` | Public upload cap |
| `PUBLIC_SYNC_MAX_BYTES` | `10MB` | Sync threshold for small images |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | Outbound webhook HTTP timeout |
| `WEBHOOK_ALLOW_LOCALHOST` | `true` | Allow `http://localhost` in non-prod |
| `EXTRACTION_JOB_TIMEOUT_SECONDS` | `300` | Design timestamp for stuck-job detection |

### API key lifecycle

1. `POST /api/v1/api-keys` → returns **full** `ext_live_…` secret **once**
2. Store only `key_hash` (SHA-256 + pepper); list shows `prefix` only
3. Authenticate public routes with `X-API-Key`
4. Soft revoke via `DELETE /api/v1/api-keys/{id}` (`revoked_at`)

Default permissions: `extract:read`, `extract:write`.

### Rate limiting

In-memory sliding window (`MemoryRateLimitService`), abstracted for future Redis.

On exceed: **429** + `X-RateLimit-*` + `Retry-After`.

### Public extract

| Mode | When | Status |
|------|------|--------|
| Sync | JPG/PNG ≤ sync threshold | **200** completed + result |
| Async | PDF / large files | **202** + `job_id` + `status_url` |

Background work uses FastAPI `BackgroundTasks` calling `job_processor` (same process/extract pipeline). Replaceable later with Celery/RQ without changing business logic.

`include_metadata=true` adds confidence / bbox / page numbers.

### Webhooks

- Optional `webhook_url` on extract
- HTTPS only (localhost HTTP allowed in development)
- SSRF blocked (private / loopback / link-local / metadata hosts)
- HMAC-SHA256 header: `X-Extractly-Signature: sha256=…`
- `X-Extractly-Event` + unique `event_id`
- Signing secret `whsec_…` shown **once** on job create
- Delivery persisted in `webhook_deliveries`; retries scheduled via `next_retry_at` (no `sleep` in request path)

### Usage tracking

Middleware logs public `/api/v1/extract*` only (metadata: key, endpoint, status, latency, request id — never bodies or secrets).

`GET /api/v1/api-usage?days=30` for dashboard analytics.

Every response includes `X-Request-ID`.

### CORS note

CORS remains limited to the internal frontend origins. The public API is **server-to-server**; API keys are not a browser security boundary. Do not open `*` for public routes casually.

### APIs

| Method | Path | Auth |
|--------|------|------|
| `POST` | `/api/v1/api-keys` | App (internal) |
| `GET` | `/api/v1/api-keys` | App |
| `DELETE` | `/api/v1/api-keys/{id}` | App |
| `GET` | `/api/v1/api-usage` | App |
| `POST` | `/api/v1/extract` | `X-API-Key` |
| `GET` | `/api/v1/extract/{job_id}` | `X-API-Key` |

### cURL examples

```bash
# Create API key (copy api_key immediately)
curl -s -X POST "http://127.0.0.1:8000/api/v1/api-keys" \
  -H "Content-Type: application/json" \
  -d '{"label":"Production Integration","rate_limit":60}'

# Extract (async for PDF)
curl -X POST "http://127.0.0.1:8000/api/v1/extract" \
  -H "X-API-Key: ext_live_xxxxx" \
  -F "file=@invoice.pdf" \
  -F "webhook_url=https://example.com/webhook"

# Poll job
curl "http://127.0.0.1:8000/api/v1/extract/JOB_ID?include_metadata=true" \
  -H "X-API-Key: ext_live_xxxxx"

# Usage
curl "http://127.0.0.1:8000/api/v1/api-usage?days=30"
```

### Swagger testing (`/docs`)

1. `POST /api/v1/api-keys` → copy `api_key`
2. `GET /api/v1/api-keys` → prefix only
3. Authorize / pass `X-API-Key` on `POST /api/v1/extract` with a JPG
4. For PDF → 202 → poll `GET /api/v1/extract/{job_id}`
5. `?include_metadata=true`
6. Create key with `rate_limit: 2` → hammer extract → 429
7. `DELETE` key → public call → 401
8. `GET /api/v1/api-usage`

### Tests

```bash
pytest tests/test_api_keys.py tests/test_rate_limit.py \
  tests/test_public_api.py tests/test_webhooks.py \
  tests/test_usage_tracking.py -q
```

### Known limitations (Phase 11)

- Rate limiter is **in-process** (not shared across multiple uvicorn workers)
- Webhook retries are **scheduled in DB** but not auto-drained by a worker yet
- Jobs use **BackgroundTasks** (lost on process restart mid-job)
- No OAuth / billing / org multi-tenancy (out of scope)

### Recommended production improvements

- Redis sliding-window rate limits
- Celery/Arq worker for jobs + webhook retry drain
- Stronger `API_KEY_PEPPER` from secrets manager
- Stuck-job sweeper using `EXTRACTION_JOB_TIMEOUT_SECONDS`


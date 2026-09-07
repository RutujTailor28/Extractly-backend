-- Extractly — Phase 2 seed data
-- Run manually in Supabase Dashboard → SQL Editor AFTER schema.sql
-- Seeds vendors and documents only (no extractions yet)

-- ---------------------------------------------------------------------------
-- Vendors (3)
-- ---------------------------------------------------------------------------

INSERT INTO vendors (id, name, tax_id, template_hints_json, invoice_count)
VALUES
    (
        'a1000000-0000-4000-8000-000000000001',
        'Acme Office Supplies',
        'XX-XXXXX4921',
        '{"invoice_number_region": "top_right", "date_format": "MM/DD/YYYY"}'::jsonb,
        0
    ),
    (
        'a1000000-0000-4000-8000-000000000002',
        'Vertex Technologies',
        'XX-XXXXX7710',
        '{"invoice_number_region": "top_left", "date_format": "YYYY-MM-DD"}'::jsonb,
        0
    ),
    (
        'a1000000-0000-4000-8000-000000000003',
        'Apex Courier & Freight',
        'XX-XXXXX3344',
        '{"invoice_number_region": "header", "date_format": "DD/MM/YYYY"}'::jsonb,
        0
    )
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Documents (5)
-- ---------------------------------------------------------------------------

INSERT INTO documents (
    id,
    filename,
    file_hash,
    mime_type,
    file_size,
    page_count,
    status,
    source
)
VALUES
    (
        'b1000000-0000-4000-8000-000000000001',
        'acme-office-invoice-2041.pdf',
        'sha256:acme2041placeholderhash00000000000000000000000000000001',
        'application/pdf',
        1474560,
        1,
        'ready',
        'upload'
    ),
    (
        'b1000000-0000-4000-8000-000000000002',
        'vertex-tech-invoice-8821.pdf',
        'sha256:vertex8821placeholderhash000000000000000000000000000002',
        'application/pdf',
        2097152,
        2,
        'needs_review',
        'upload'
    ),
    (
        'b1000000-0000-4000-8000-000000000003',
        'apex-courier-receipt-901.jpg',
        'sha256:apex901placeholderhash000000000000000000000000000000003',
        'image/jpeg',
        524288,
        1,
        'queued',
        'upload'
    ),
    (
        'b1000000-0000-4000-8000-000000000004',
        'acme-office-supplies-sept-2026.pdf',
        'sha256:acme0926placeholderhash00000000000000000000000000000004',
        'application/pdf',
        1310720,
        1,
        'approved',
        'api'
    ),
    (
        'b1000000-0000-4000-8000-000000000005',
        'vertex-technologies-po-4412.pdf',
        'sha256:vertex4412placeholderhash000000000000000000000000000005',
        'application/pdf',
        983040,
        1,
        'extracting',
        'email'
    )
ON CONFLICT (id) DO NOTHING;

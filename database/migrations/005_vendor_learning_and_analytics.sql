-- Phase 9: Vendor learning + analytics support
-- Run in Supabase Dashboard → SQL Editor

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS vendor_id UUID
        REFERENCES vendors (id)
        ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_documents_vendor_id
    ON documents (vendor_id);

ALTER TABLE vendors
    ADD COLUMN IF NOT EXISTS last_invoice_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS name_normalized TEXT;

-- Backfill normalized names for existing vendors
UPDATE vendors
SET name_normalized = lower(trim(regexp_replace(name, '\s+', ' ', 'g')))
WHERE name_normalized IS NULL;

CREATE INDEX IF NOT EXISTS idx_vendors_name_normalized
    ON vendors (name_normalized);

CREATE INDEX IF NOT EXISTS idx_document_corrections_created_at_desc
    ON document_corrections (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_document_corrections_type_field
    ON document_corrections (correction_type, field_name);

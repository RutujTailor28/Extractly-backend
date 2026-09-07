-- Phase 8: Human review & corrections
-- Run in Supabase Dashboard → SQL Editor

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS approved_by TEXT;

CREATE TABLE IF NOT EXISTS document_corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    document_id UUID NOT NULL
        REFERENCES documents (id)
        ON DELETE CASCADE,

    extraction_id UUID NOT NULL
        REFERENCES extractions (id)
        ON DELETE CASCADE,

    correction_type TEXT NOT NULL
        CHECK (
            correction_type IN (
                'field_correction',
                'line_item_update',
                'line_item_added',
                'line_item_deleted',
                'validation_resolved',
                'document_approved',
                'document_unapproved'
            )
        ),

    field_name TEXT,
    line_item_index INTEGER,

    original_value_json JSONB,
    corrected_value_json JSONB,

    corrected_by TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_document_corrections_document_id
    ON document_corrections (document_id);

CREATE INDEX IF NOT EXISTS idx_document_corrections_extraction_id
    ON document_corrections (extraction_id);

CREATE INDEX IF NOT EXISTS idx_document_corrections_created_at
    ON document_corrections (document_id, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_document_corrections_type
    ON document_corrections (correction_type);

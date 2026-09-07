-- Phase 5 migration: document_pages + processing_error on documents
-- Run in Supabase Dashboard → SQL Editor

-- Optional error message for failed preprocessing jobs
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS processing_error TEXT;

-- Processed page images metadata
CREATE TABLE IF NOT EXISTS document_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    document_id UUID NOT NULL
        REFERENCES documents (id)
        ON DELETE CASCADE,

    page_number INTEGER NOT NULL
        CHECK (page_number >= 1),

    original_storage_path TEXT,
    processed_storage_path TEXT NOT NULL,

    width INTEGER,
    height INTEGER,
    mime_type TEXT NOT NULL DEFAULT 'image/png',

    processing_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_document_pages_document_page UNIQUE (document_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_document_pages_document_id
    ON document_pages (document_id);

CREATE INDEX IF NOT EXISTS idx_document_pages_document_page
    ON document_pages (document_id, page_number);

DROP TRIGGER IF EXISTS trg_document_pages_updated_at ON document_pages;
CREATE TRIGGER trg_document_pages_updated_at
    BEFORE UPDATE ON document_pages
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();

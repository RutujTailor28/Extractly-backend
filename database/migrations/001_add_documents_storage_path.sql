-- Phase 4 migration: add storage_path for signed URL + delete support
-- Run in Supabase Dashboard → SQL Editor AFTER Phase 2 schema.sql

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS storage_path TEXT;

-- Backfill existing rows using Phase 3 path convention: {document_id}/{filename}
UPDATE documents
SET storage_path = id::text || '/' || filename
WHERE storage_path IS NULL
  AND filename IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_documents_storage_path ON documents (storage_path);

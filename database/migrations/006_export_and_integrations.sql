-- Phase 10: Export & accounting integration logs
-- Run in Supabase Dashboard → SQL Editor

CREATE TABLE IF NOT EXISTS integration_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID,

    document_id UUID NOT NULL
        REFERENCES documents (id)
        ON DELETE CASCADE,

    provider TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('success', 'failed')),

    request_payload_json JSONB,
    response_payload_json JSONB,

    external_reference TEXT,
    error_message TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_integration_logs_document_id
    ON integration_logs (document_id);

CREATE INDEX IF NOT EXISTS idx_integration_logs_provider
    ON integration_logs (provider);

CREATE INDEX IF NOT EXISTS idx_integration_logs_created_at
    ON integration_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_integration_logs_doc_provider_status
    ON integration_logs (document_id, provider, status);

CREATE INDEX IF NOT EXISTS idx_documents_approved_at
    ON documents (approved_at DESC);

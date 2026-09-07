-- Extractly — Phase 2 database schema
-- Run manually in Supabase Dashboard → SQL Editor
-- PostgreSQL / Supabase compatible

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Reusable updated_at trigger function
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- TABLE 1: documents
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    filename TEXT NOT NULL,
    file_hash TEXT,
    mime_type TEXT NOT NULL,
    file_size BIGINT,
    page_count INTEGER DEFAULT 1,

    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (
            status IN (
                'queued',
                'preprocessing',
                'extracting',
                'validating',
                'ready',
                'needs_review',
                'approved',
                'failed'
            )
        ),

    source TEXT NOT NULL DEFAULT 'upload'
        CHECK (
            source IN (
                'upload',
                'api',
                'email'
            )
        ),

    -- Object path inside the documents storage bucket: {document_id}/{filename}
    storage_path TEXT,

    processing_error TEXT,

    approved_at TIMESTAMPTZ,
    approved_by TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents (user_id);
CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents (file_hash);

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();

-- ---------------------------------------------------------------------------
-- TABLE 2: vendors
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    name TEXT NOT NULL,
    name_normalized TEXT,
    tax_id TEXT,
    template_hints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    invoice_count INTEGER NOT NULL DEFAULT 0,
    last_invoice_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vendors_user_id ON vendors (user_id);
CREATE INDEX IF NOT EXISTS idx_vendors_name ON vendors (name);
CREATE INDEX IF NOT EXISTS idx_vendors_name_normalized ON vendors (name_normalized);

DROP TRIGGER IF EXISTS trg_vendors_updated_at ON vendors;
CREATE TRIGGER trg_vendors_updated_at
    BEFORE UPDATE ON vendors
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();

-- Link documents to vendors (added after vendors exists)
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS vendor_id UUID
        REFERENCES vendors (id)
        ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_documents_vendor_id ON documents (vendor_id);
-- ---------------------------------------------------------------------------
-- TABLE 3: extractions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS extractions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    document_id UUID NOT NULL
        REFERENCES documents (id)
        ON DELETE CASCADE,

    schema_version TEXT,
    model TEXT,
    raw_response_json JSONB,
    fields_json JSONB,
    line_items_json JSONB,
    overall_confidence NUMERIC(5, 4),
    latency_ms INTEGER,
    cost_usd NUMERIC(12, 6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extractions_document_id ON extractions (document_id);

-- ---------------------------------------------------------------------------
-- TABLE 4: field_values
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS field_values (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    extraction_id UUID NOT NULL
        REFERENCES extractions (id)
        ON DELETE CASCADE,

    field_name TEXT NOT NULL,
    value TEXT,
    confidence NUMERIC(5, 4),
    page_number INTEGER,
    bbox_json JSONB,
    corrected_value TEXT,
    corrected_at TIMESTAMPTZ,
    corrected_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_field_values_extraction_id ON field_values (extraction_id);
-- Supports future duplicate detection via extracted invoice_number (and similar fields)
CREATE INDEX IF NOT EXISTS idx_field_values_field_name ON field_values (field_name);

-- ---------------------------------------------------------------------------
-- TABLE 5: validation_flags
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS validation_flags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    document_id UUID NOT NULL
        REFERENCES documents (id)
        ON DELETE CASCADE,

    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL
        CHECK (severity IN ('error', 'warning', 'info')),
    message TEXT NOT NULL,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_validation_flags_document_id ON validation_flags (document_id);
CREATE INDEX IF NOT EXISTS idx_validation_flags_resolved ON validation_flags (resolved);

-- ---------------------------------------------------------------------------
-- TABLE 6: api_keys
-- ---------------------------------------------------------------------------
-- Store only hashed keys — never store raw API keys.

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT,
    label TEXT NOT NULL,
    rate_limit INTEGER NOT NULL DEFAULT 60,
    permissions_json JSONB NOT NULL DEFAULT '["extract:read","extract:write"]'::jsonb,
    request_count BIGINT NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys (user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_prefix ON api_keys (key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_revoked_at ON api_keys (revoked_at);

-- ---------------------------------------------------------------------------
-- TABLE 7: document_pages (processed page images)
-- ---------------------------------------------------------------------------

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

-- ---------------------------------------------------------------------------
-- TABLE 8: validation_rule_settings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS validation_rule_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    rule_code TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_validation_rule_settings_user_rule
        UNIQUE NULLS NOT DISTINCT (user_id, rule_code)
);

CREATE INDEX IF NOT EXISTS idx_validation_rule_settings_user_id
    ON validation_rule_settings (user_id);

CREATE INDEX IF NOT EXISTS idx_validation_rule_settings_rule_code
    ON validation_rule_settings (rule_code);

DROP TRIGGER IF EXISTS trg_validation_rule_settings_updated_at ON validation_rule_settings;
CREATE TRIGGER trg_validation_rule_settings_updated_at
    BEFORE UPDATE ON validation_rule_settings
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();

-- ---------------------------------------------------------------------------
-- TABLE 9: document_corrections (append-only audit + effective-data source)
-- ---------------------------------------------------------------------------

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

-- ---------------------------------------------------------------------------
-- TABLE 10: integration_logs
-- ---------------------------------------------------------------------------

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

-- ---------------------------------------------------------------------------
-- TABLE 11: extraction_jobs (public API)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS extraction_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    api_key_id UUID
        REFERENCES api_keys (id)
        ON DELETE SET NULL,
    document_id UUID NOT NULL
        REFERENCES documents (id)
        ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    webhook_url TEXT,
    webhook_secret_hash TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extraction_jobs_user_id ON extraction_jobs (user_id);
CREATE INDEX IF NOT EXISTS idx_extraction_jobs_api_key_id ON extraction_jobs (api_key_id);
CREATE INDEX IF NOT EXISTS idx_extraction_jobs_status ON extraction_jobs (status);
CREATE INDEX IF NOT EXISTS idx_extraction_jobs_document_id ON extraction_jobs (document_id);

-- ---------------------------------------------------------------------------
-- TABLE 12: webhook_deliveries
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL
        REFERENCES extraction_jobs (id)
        ON DELETE CASCADE,
    event TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'failed')),
    attempt INTEGER NOT NULL DEFAULT 0,
    response_status INTEGER,
    error_message TEXT,
    next_retry_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_job_id ON webhook_deliveries (job_id);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status ON webhook_deliveries (status);

-- ---------------------------------------------------------------------------
-- TABLE 13: api_usage_logs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS api_usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id UUID
        REFERENCES api_keys (id)
        ON DELETE SET NULL,
    user_id UUID,
    request_id TEXT,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_usage_logs_api_key_id ON api_usage_logs (api_key_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_logs_user_id ON api_usage_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_logs_created_at ON api_usage_logs (created_at DESC);

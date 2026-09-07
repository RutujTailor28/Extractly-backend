-- Phase 11: Public API, jobs, webhooks, usage tracking
-- Run in Supabase Dashboard → SQL Editor

-- Extend api_keys
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS key_prefix TEXT,
    ADD COLUMN IF NOT EXISTS permissions_json JSONB NOT NULL DEFAULT '["extract:read","extract:write"]'::jsonb,
    ADD COLUMN IF NOT EXISTS request_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_api_keys_key_prefix ON api_keys (key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_revoked_at ON api_keys (revoked_at);

-- Extraction jobs (public API)
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
CREATE INDEX IF NOT EXISTS idx_extraction_jobs_created_at ON extraction_jobs (created_at DESC);

-- Webhook delivery attempts
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
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_next_retry ON webhook_deliveries (next_retry_at);

-- API usage metadata (no bodies / secrets)
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
CREATE INDEX IF NOT EXISTS idx_api_usage_logs_endpoint ON api_usage_logs (endpoint);

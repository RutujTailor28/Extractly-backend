-- Phase 7: validation engine support
-- Run in Supabase Dashboard → SQL Editor

-- Allow info severity + optional details on flags
ALTER TABLE validation_flags
    DROP CONSTRAINT IF EXISTS validation_flags_severity_check;

ALTER TABLE validation_flags
    ADD CONSTRAINT validation_flags_severity_check
    CHECK (severity IN ('error', 'warning', 'info'));

ALTER TABLE validation_flags
    ADD COLUMN IF NOT EXISTS details_json JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Configurable rule overrides (defaults live in application code)
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

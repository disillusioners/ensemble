-- Migration: add paused_at column to instances table
-- Created: 2026-05-16
-- Author: system
-- Description: Add paused_at column to track when instance was paused for TTL expiration

-- UP

ALTER TABLE instances ADD COLUMN paused_at VARCHAR;

CREATE INDEX IF NOT EXISTS ix_instances_paused_at ON instances(paused_at);

-- DOWN

DROP INDEX IF EXISTS ix_instances_paused_at;
ALTER TABLE instances DROP COLUMN paused_at;
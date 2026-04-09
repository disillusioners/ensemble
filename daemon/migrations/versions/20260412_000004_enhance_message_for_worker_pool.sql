-- Migration: enhance message for worker pool
-- Created: 2026-04-12
-- Author: system
-- Description: Add columns for worker pool support to message_queue table.
--              - type (message type: human/agent/system/completion_report/error_report)
--              - root_source (root cause source)
--              - processing_task_id (FK to task table)
--              - last_error (last error message)
--              Reuses existing processing_started_at (don't add new started_at).

-- UP

-- Add type column (message type)
ALTER TABLE message_queue ADD COLUMN type TEXT DEFAULT 'agent';

-- Add root_source column (nullable)
ALTER TABLE message_queue ADD COLUMN root_source TEXT;

-- Add processing_task_id column (FK to task table)
ALTER TABLE message_queue ADD COLUMN processing_task_id TEXT;

-- Add last_error column (nullable)
ALTER TABLE message_queue ADD COLUMN last_error TEXT;

-- Index on processing_task_id for efficient lookups
CREATE INDEX IF NOT EXISTS idx_message_queue_task ON message_queue(processing_task_id);

-- Make source nullable (existing data will have NULL for old rows)
-- SQLite doesn't support ALTER COLUMN, but we can note this for PostgreSQL migration
-- For SQLite, the model will handle nullable source

-- DOWN
-- Note: SQLite doesn't support DROP COLUMN or DROP INDEX, so DOWN is a no-op.

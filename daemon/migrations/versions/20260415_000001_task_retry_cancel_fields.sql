-- Migration: add retry and cancellation fields to task
-- Created: 2026-04-15
-- Description: Add retry tracking and cancellation fields to task table.

-- UP

ALTER TABLE task ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task ADD COLUMN next_retry_at TEXT;
ALTER TABLE task ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task ADD COLUMN cancel_requested_at TEXT;
ALTER TABLE task ADD COLUMN retry_scheduled INTEGER NOT NULL DEFAULT 0;

-- Indexes for retry scheduling queries
CREATE INDEX IF NOT EXISTS idx_task_status_next_retry ON task(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_task_cancel_status ON task(cancel_requested, status);

-- DOWN
-- (Not supporting down migration for safety — columns with defaults are harmless)

-- Migration: add soft delete to jobs
-- Created: 2026-04-19
-- Description: Add deleted_at column to job_queue_items for soft delete support.

-- UP
ALTER TABLE job_queue_items ADD COLUMN deleted_at TEXT DEFAULT NULL;

-- Create index for efficient filtering of non-deleted jobs
CREATE INDEX IF NOT EXISTS idx_job_queue_deleted_at ON job_queue_items(deleted_at);

-- DOWN
DROP INDEX IF EXISTS idx_job_queue_deleted_at;
-- Note: SQLite doesn't support DROP COLUMN before 3.35.0
-- For older SQLite, the column will remain but be unused

-- Migration: add job_type to job_queue_items
-- Created: 2026-05-24
-- Description: Add job_type column to job_queue_items table for task vs message distinction.

-- UP

-- Add job_type column: "task" (serial) or "message" (parallel)
ALTER TABLE job_queue_items ADD COLUMN job_type TEXT NOT NULL DEFAULT 'task';

-- Add index for job_type queries
CREATE INDEX IF NOT EXISTS idx_job_queue_items_job_type ON job_queue_items(job_type);

-- DOWN
-- Note: SQLite doesn't support DROP COLUMN, so column remains but is ignored by code.
-- We can only drop the index we created.
DROP INDEX IF EXISTS idx_job_queue_items_job_type;

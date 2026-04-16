-- Migration: add job system improvements
-- Created: 2026-04-20
-- Description: Add job locks table, retry/idempotency columns to job_queue_items,
--              and default_max_retries to job_queues for improved job processing.

-- UP

-- Create job_locks table for distributed locking
CREATE TABLE IF NOT EXISTS job_locks (
    lock_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    queue_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    instance_id TEXT,
    acquired_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_locks_project_id ON job_locks(project_id);
CREATE INDEX IF NOT EXISTS idx_job_locks_queue_id ON job_locks(queue_id);
CREATE INDEX IF NOT EXISTS idx_job_locks_instance_id ON job_locks(instance_id);

-- Add retry tracking columns to job_queue_items
ALTER TABLE job_queue_items ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE job_queue_items ADD COLUMN max_retries INTEGER DEFAULT NULL;
ALTER TABLE job_queue_items ADD COLUMN idempotency_key TEXT DEFAULT NULL;
ALTER TABLE job_queue_items ADD COLUMN failed_at TEXT DEFAULT NULL;
ALTER TABLE job_queue_items ADD COLUMN next_retry_at TEXT DEFAULT NULL;

-- Add default_max_retries to job_queues
ALTER TABLE job_queues ADD COLUMN default_max_retries INTEGER DEFAULT NULL;

-- Partial unique index for idempotency (only applies when key is set)
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_idempotency 
ON job_queue_items(idempotency_key) 
WHERE idempotency_key IS NOT NULL;

-- DOWN
-- Note: SQLite doesn't support DROP COLUMN, so columns remain but are ignored by code.
-- We can only drop the index we created.
DROP INDEX IF EXISTS idx_job_idempotency;
DROP TABLE IF EXISTS job_locks;

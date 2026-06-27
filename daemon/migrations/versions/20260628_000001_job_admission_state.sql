-- Migration: add admission_state column to job_queue_items
-- Created: 2026-06-28
-- Description: Phase 2 of Job-as-Queue-Proxy. Adds admission_state column
--   alongside the existing status column. Dual-write in Phase 2; status
--   becomes write-only mirror, then dropped in Phase 5.

-- UP

ALTER TABLE job_queue_items ADD COLUMN admission_state TEXT NOT NULL DEFAULT 'queued';

-- Backfill from status: pending→queued, processing→active, paused→active,
-- completed/failed/cancelled→done, dead_letter→dead.
-- Idempotency guard (admission_state = 'queued'): mirrors PG backfill in
-- _ensure_postgres_columns() so a manual SQL replay doesn't clobber rows
-- already dual-written under the new column.
UPDATE job_queue_items SET admission_state = 'queued' WHERE status = 'pending' AND admission_state = 'queued';
UPDATE job_queue_items SET admission_state = 'active' WHERE status = 'processing' AND admission_state = 'queued';
UPDATE job_queue_items SET admission_state = 'active' WHERE status = 'paused' AND admission_state = 'queued';
UPDATE job_queue_items SET admission_state = 'done' WHERE status = 'completed' AND admission_state = 'queued';
UPDATE job_queue_items SET admission_state = 'done' WHERE status = 'failed' AND admission_state = 'queued';
UPDATE job_queue_items SET admission_state = 'done' WHERE status = 'cancelled' AND admission_state = 'queued';
UPDATE job_queue_items SET admission_state = 'dead' WHERE status = 'dead_letter' AND admission_state = 'queued';

CREATE INDEX IF NOT EXISTS idx_job_queue_admission_state ON job_queue_items(admission_state);

-- DOWN

DROP INDEX IF EXISTS idx_job_queue_admission_state;
-- SQLite cannot DROP COLUMN easily; leave the column in place.
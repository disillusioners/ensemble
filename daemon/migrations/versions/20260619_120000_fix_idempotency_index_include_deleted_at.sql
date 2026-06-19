-- Migration: Fix idempotency unique index to also exclude soft-deleted rows.
--
-- The original ``idx_job_idempotency`` partial unique index
-- (created in 20260420_000001_add_job_system_improvements) had the
-- predicate ``WHERE idempotency_key IS NOT NULL``. This prevented
-- the soft-delete → recreate pattern: after soft-deleting a job,
-- a caller could not enqueue a fresh job with the same
-- idempotency_key because the old (soft-deleted) row still held
-- the unique slot.
--
-- ``find_by_idempotency_key`` already filters on
-- ``deleted_at IS NULL`` (see JobRepository.find_by_idempotency_key
-- in daemon/repositories/job_queue/repository.py), so the index
-- should match: a key is "free" for a new INSERT whenever
-- no non-deleted row holds it.
--
-- This migration drops the old index and recreates it with the
-- refined predicate ``WHERE idempotency_key IS NOT NULL AND
-- deleted_at IS NULL``. The model definition in
-- ``daemon/repositories/job_queue/models.py::JobItem.__table_args__``
-- is updated to match, so fresh databases created via
-- ``SQLModel.metadata.create_all`` get the correct index without
-- running this migration.

-- Drop the old index (both dialects use the same DROP syntax).
DROP INDEX IF EXISTS idx_job_idempotency;

-- Recreate with the refined partial predicate.
-- PostgreSQL supports the ``CREATE UNIQUE INDEX ... WHERE`` syntax
-- natively. SQLite supports the same syntax (added in SQLite 3.8.0).
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_idempotency
ON job_queue_items(idempotency_key)
WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;

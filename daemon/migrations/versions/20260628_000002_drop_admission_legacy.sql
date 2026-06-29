-- Migration: drop admission legacy columns (Phase 5)
-- Created: 2026-06-28
-- Author: system
-- MANUAL: TRUE
-- Description:
--   Phase 5 (final cleanup) of the Job-as-Queue-Proxy architecture.
--   Drops the 7 legacy columns that were replaced by the
--   ``admission_state`` column and Instance-side execution state:
--
--     * ``job_queue_items.status``           (7-state JobStatus enum)
--     * ``job_queue_items.started_at``       (moved to Instance.started_at)
--     * ``job_queue_items.completed_at``     (moved to Instance.completed_at)
--     * ``job_queue_items.result_summary``   (moved to Instance.result_summary)
--     * ``job_queue_items.error_message``    (moved to Instance.error_message)
--     * ``job_queue_items.cancelled_at``     (moved to Instance.cancelled_at)
--     * ``job_queue_items.failed_at``        (retry marker, now on Instance)
--
--   NOTE: ``failed_at`` was the LIVE retry marker with 5 read sites.
--   Phase 5b deferred its drop until retry/DLQ paths were fully migrated
--   to Instance-side state. This migration attempts the drop for
--   idempotency; the retry engine must already be using Instance.
--
--   After Phase 4 write cutover, ``admission_state`` is the sole
--   authority for queue gating. ``status`` is frozen at INSERT default
--   and never written. All 9 gating/count queries now filter on
--   ``admission_state IN ('queued', 'active')``.
--
--   ============================================================================
--   WARNING: THIS MIGRATION IS IRREVERSIBLE AND DATA-DESTRUCTIVE.
--   ============================================================================
--   All data in the dropped columns is PERMANENTLY LOST
--   after this migration runs. The DOWN section below recreates the
--   schema (empty columns) but the data is gone --
--   there is no recovery path.
--
--   This migration is NOT auto-applied. Per the reviewer's §7.2
--   recommendation, it exists as a file so operators can apply it
--   manually AFTER 2+ weeks of clean Job-as-Queue-Proxy operation in
--   production. Do NOT apply until:
--
--     1. The admission_state column has been the sole write authority
--        in production for 2+ weeks with no rollbacks.
--     2. A pre-migration snapshot/backup of the database has been
--        taken and verified.
--     3. Operators are on-call and prepared to roll forward (the
--        DOWN section below does NOT restore data).
--
--   ============================================================================
--   Dialect notes:
--     - PostgreSQL: supports ``ALTER TABLE ... DROP COLUMN IF EXISTS``
--       natively. This is the recommended target environment. The
--       migration runner NO-OPs all .sql files on PostgreSQL
--       (runner.py lines 455-482), so this file is INERT on
--       PostgreSQL until an operator applies it manually (e.g.
--       via psql) -- which is the intended behavior.
--     - SQLite 3.35.0+ supports ``ALTER TABLE ... DROP COLUMN`` but
--       does NOT support the ``IF EXISTS`` variant. The migration
--       runner DOES pick up .sql files on SQLite. If you intend to
--       run this on SQLite, either (a) skip the file via the
--       runner's filter mechanism, (b) hand-edit the UP section
--       to drop the IF EXISTS clauses, or (c) upgrade SQLite and
--       accept the first-run syntax error on a one-time manual
--       retry.
--
--     For SQLite versions < 3.35 (no DROP COLUMN support), implement
--     the table-rebuild approach:
--       1. CREATE TABLE job_queue_items_new AS SELECT ... (all kept cols)
--       2. INSERT INTO job_queue_items_new SELECT ... FROM job_queue_items
--       3. DROP TABLE job_queue_items
--       4. ALTER TABLE job_queue_items_new RENAME TO job_queue_items
--       5. Recreate indexes and constraints
--
--   Schema reference: daemon/repositories/job_queue/models.py
--     - ``JobItem.status``           line 251 (str, default PENDING)
--     - ``JobItem.started_at``       line 271 (str | None)
--     - ``JobItem.completed_at``     line 272 (str | None)
--     - ``JobItem.result_summary``   line 277 (str | None)
--     - ``JobItem.error_message``    line 276 (str | None)
--     - ``JobItem.cancelled_at``     line 286 (str | None)
--     - ``JobItem.failed_at``        line 298 (str | None, Field default=None)

-- UP

-- Drop indexes that reference the legacy status column.
-- These indexes are no longer needed; all queries now use admission_state.
DROP INDEX IF EXISTS idx_job_queue_status;
DROP INDEX IF EXISTS idx_job_queue_items_project_status_deleted;
DROP INDEX IF EXISTS idx_job_queue_items_status_type_instance;

-- Drop the 7 legacy columns.
-- Order does not matter; each is independent.
-- SQLite 3.35+ supports DROP COLUMN; older versions require table rebuild.
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS status;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS started_at;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS completed_at;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS result_summary;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS error_message;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS cancelled_at;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS failed_at;

-- NOTE: The ``admission_state`` column and its supporting index
--       ``idx_job_queue_admission_state`` remain as the canonical
--       queue-admission authority.

-- DOWN
-- ============================================================================
-- DATA LOSS WARNING
-- ============================================================================
-- This DOWN section recreates the columns as empty containers. The
-- data that was in those columns at the time the UP migration ran is
-- PERMANENTLY LOST. Rolling back does NOT restore the data -- it only
-- restores the ability for new jobs to write to those columns.
-- Schema recreation is NOT data recovery.
--
-- The recreated columns will be empty (NULL/default). Any historical
-- status/timing/result data is gone.

-- Recreate the 7 legacy columns with their original types/defaults.
-- Matches the SQLModel definitions in daemon/repositories/job_queue/models.py.
ALTER TABLE job_queue_items ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE job_queue_items ADD COLUMN started_at TEXT;
ALTER TABLE job_queue_items ADD COLUMN completed_at TEXT;
ALTER TABLE job_queue_items ADD COLUMN result_summary TEXT;
ALTER TABLE job_queue_items ADD COLUMN error_message TEXT;
ALTER TABLE job_queue_items ADD COLUMN cancelled_at TEXT;
ALTER TABLE job_queue_items ADD COLUMN failed_at TEXT;

-- Recreate the 3 legacy indexes.
-- Note: These indexes will be empty after UP; they exist only for
--       schema compatibility with code that may still reference them.
CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue_items(status);
CREATE INDEX IF NOT EXISTS idx_job_queue_items_project_status_deleted
    ON job_queue_items(project_id, status, deleted_at);
CREATE INDEX IF NOT EXISTS idx_job_queue_items_status_type_instance
    ON job_queue_items(status, job_type, instance_id);

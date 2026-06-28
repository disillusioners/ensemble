-- Migration: drop job_queue_items legacy columns (Phase 5)
-- Created: 2026-06-28
-- Author: system
-- MANUAL: TRUE
-- Description:
--   Phase 5 (Job-as-Queue-Proxy) drops the seven legacy
--   ``job_queue_items`` columns that ``admission_state`` replaced as
--   the sole authority in Phase 4 cleanup (commit 4eb1758a):
--
--     * ``status``         (TEXT, frozen at INSERT default 'pending')
--     * ``started_at``     (TEXT, nullable)
--     * ``completed_at``   (TEXT, nullable)
--     * ``result_summary`` (TEXT, nullable)
--     * ``error_message``  (TEXT, nullable)
--     * ``cancelled_at``   (TEXT, nullable)
--     * ``failed_at``      (TEXT, nullable)
--
--   After Phase 4, ``admission_state`` (queued/active/done/dead) is the
--   single source of truth for the admission lifecycle. The ``status``
--   column was frozen at its INSERT default and never written. The six
--   timing/result columns were maintained only for backward
--   compatibility (serialized via ``JobItem.to_dict()``) and are now
--   dead artifacts.
--
--   Three legacy indexes that reference ``status`` are also dropped
--   because ``status`` no longer exists after this migration:
--
--     * ``idx_job_queue_status``                       (status)
--     * ``idx_job_queue_items_status_type_instance``   (status, job_type, instance_id)
--     * ``idx_job_queue_items_project_status_deleted`` (project_id, status, deleted_at)
--
--   The replacement index ``idx_job_queue_admission_state`` (created
--   in Phase 2) is NOT touched — it is the live index backing the
--   ``WHERE admission_state IN ('queued', 'active')`` predicates.
--
--   ============================================================================
--   WARNING: THIS MIGRATION IS IRREVERSIBLE AND DATA-DESTRUCTIVE.
--   ============================================================================
--   All data in the dropped columns is PERMANENTLY LOST
--   after this migration runs. The DOWN section below recreates the
--   schema (empty columns) but the data is gone --
--   there is no recovery path.
--
--   This migration is NOT auto-applied. It exists as a file so
--   operators can apply it manually AFTER 2+ weeks of clean
--   ``admission_state`` operation in production. Do NOT apply until:
--
--     1. Phase 4 cleanup has been live in production for 2+ weeks
--        with no rollbacks to the dual-write code path.
--     2. All 19 production reads of ``JobItem.status`` have been
--        converted to ``admission_state`` queries and the JobItem
--        SQLModel no longer maps these seven columns.
--     3. A pre-migration snapshot/backup of the database has been
--        taken and verified.
--     4. Operators are on-call and prepared to roll forward (the
--        DOWN section below does NOT restore data).
--
--   ============================================================================
--   WARNING: RUNNING THIS DESTROYS THE DUAL-WRITE ROLLBACK PATH.
--   ============================================================================
--   The ``status`` column holds the frozen mirror that a Phase 4
--   rollback (re-enabling dual-write) would need to re-seed. After
--   this migration runs, any code that reads ``status`` will fail.
--   Verify ``admission_state`` is fully clean before applying.
--
--   ============================================================================
--   Dialect notes:
--     - PostgreSQL: supports ``ALTER TABLE ... DROP COLUMN IF EXISTS``
--       natively. This is the recommended target environment. The
--       migration runner NO-OPs all .sql files on PostgreSQL
--       (runner.py), so this file is INERT on PostgreSQL -- the
--       equivalent drops are performed at startup by
--       ``InstanceManager._ensure_postgres_drop_admission_legacy()``
--       in daemon/manager.py.
--     - SQLite 3.35.0+ supports ``ALTER TABLE ... DROP COLUMN`` but
--       does NOT support the ``IF EXISTS`` variant. The migration
--       runner DOES pick up .sql files on SQLite. If you intend to
--       run this on SQLite, either (a) skip the file via the
--       runner's filter mechanism, (b) hand-edit the UP section to
--       drop the IF EXISTS clauses, or (c) if your SQLite version is
--       older than 3.35.0 (which has no DROP COLUMN support at all),
--       use the table-rebuild approach: CREATE the table under a new
--       name without the legacy columns, copy data, DROP the old
--       table, and RENAME.
--
--   Schema reference: daemon/repositories/job_queue/models.py
--     - ``JobItem.status``          line 238
--     - ``JobItem.started_at``      line 258
--     - ``JobItem.completed_at``    line 259
--     - ``JobItem.result_summary``  line 264
--     - ``JobItem.error_message``   line 263
--     - ``JobItem.cancelled_at``    line 273
--     - ``JobItem.failed_at``       line 285

-- UP

-- Drop legacy indexes that reference the ``status`` column first
-- (must precede the column drop, otherwise the column is still
-- referenced by an index). IF EXISTS keeps this idempotent.
DROP INDEX IF EXISTS idx_job_queue_status;
DROP INDEX IF EXISTS idx_job_queue_items_status_type_instance;
DROP INDEX IF EXISTS idx_job_queue_items_project_status_deleted;

-- Drop the seven legacy columns.
-- NOTE: SQLite 3.35.0+ is required for DROP COLUMN support. On older
-- SQLite, use the table-rebuild approach described in the dialect
-- notes above. IF EXISTS is NOT supported by SQLite's DROP COLUMN;
-- on PostgreSQL the IF EXISTS variant is used by the runtime helper.
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS status;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS started_at;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS completed_at;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS result_summary;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS error_message;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS cancelled_at;
ALTER TABLE job_queue_items DROP COLUMN IF EXISTS failed_at;

-- DOWN
-- ============================================================================
-- DATA LOSS WARNING
-- ============================================================================
-- This DOWN section recreates the columns as empty containers. The
-- data that was in those columns at the time the UP migration ran is
-- PERMANENTLY LOST. Rolling back does NOT restore the data -- it only
-- restores the ability for new rows to write to those columns.
-- Schema recreation is NOT data recovery.

-- Recreate status as TEXT (frozen at INSERT default 'pending').
-- Matches the SQLModel definition at
-- daemon/repositories/job_queue/models.py:238.
ALTER TABLE job_queue_items ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';

-- Recreate started_at as TEXT (nullable; set when a job starts).
ALTER TABLE job_queue_items ADD COLUMN started_at TEXT;

-- Recreate completed_at as TEXT (nullable; set when a job completes).
ALTER TABLE job_queue_items ADD COLUMN completed_at TEXT;

-- Recreate result_summary as TEXT (nullable; human-readable result).
ALTER TABLE job_queue_items ADD COLUMN result_summary TEXT;

-- Recreate error_message as TEXT (nullable; failure detail).
ALTER TABLE job_queue_items ADD COLUMN error_message TEXT;

-- Recreate cancelled_at as TEXT (nullable; cancellation timestamp).
ALTER TABLE job_queue_items ADD COLUMN cancelled_at TEXT;

-- Recreate failed_at as TEXT (nullable; failure timestamp for retry).
ALTER TABLE job_queue_items ADD COLUMN failed_at TEXT;

-- Recreate the three legacy indexes on the restored ``status`` column.
CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue_items(status);
CREATE INDEX IF NOT EXISTS idx_job_queue_items_status_type_instance ON job_queue_items(status, job_type, instance_id);
CREATE INDEX IF NOT EXISTS idx_job_queue_items_project_status_deleted ON job_queue_items(project_id, status, deleted_at);

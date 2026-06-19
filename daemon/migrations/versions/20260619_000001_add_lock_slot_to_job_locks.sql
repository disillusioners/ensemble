-- Migration: add lock_slot column and UNIQUE constraint to job_locks
-- Created: 2026-06-19
-- Description:
--   Closes two related lock-lifecycle gaps (C5 + C12) on the per-queue
--   job_locks table.
--
--   C5 — Cross-process acquire race:
--     JobLockManager.acquire_queue_lock() previously did COUNT-then-INSERT
--     under only an in-process asyncio.Lock. Two daemons both passing the
--     COUNT check could both INSERT, exceeding concurrency_limit.
--     The fix (per review) is a UNIQUE constraint on
--     (project_id, queue_id, lock_slot) so acquire can claim a slot
--     atomically via INSERT OR IGNORE (SQLite) / INSERT ... ON CONFLICT
--     DO NOTHING (Postgres). At most concurrency_limit rows can exist
--     per queue because there are exactly that many distinct slot
--     values — the invariant is enforced by the DB engine itself, which
--     is the only entity both processes trust.
--
--   C12 — Startup sweep for orphaned job locks:
--     job_locks had no recover_stale_job_locks() counterpart to
--     instance_execution_leases' recover_stale_leases(). Crashes
--     between lock acquire and job completion permanently orphaned
--     lock rows. The fix is a startup sweep (added in
--     JobLockManager) and this migration's pre-flight DELETE so any
--     existing orphans (which all share lock_slot=0 from the new
--     column default and would otherwise deadlock the UNIQUE index)
--     are cleared at migration time. The startup sweep is now
--     idempotent and handles future orphans.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine
--   dialect is sqlite (runner.py skips non-sqlite). For PostgreSQL:
--     - Fresh DBs: SQLModel.metadata.create_all() picks up the new
--       lock_slot column + UniqueConstraint from JobLock.__table_args__
--       automatically.
--     - Existing DBs: apply manually:
--         DELETE FROM job_locks;
--         ALTER TABLE job_locks ADD COLUMN lock_slot INTEGER NOT NULL DEFAULT 0;
--         CREATE UNIQUE INDEX uq_job_locks_slot
--             ON job_locks(project_id, queue_id, lock_slot);
--       This matches the execution_lease precedent (its .sql migration
--       is also skipped on PG; PG users got the leases table via
--       create_all()).

-- UP

-- STEP 1: Clear any existing job_locks rows. They are stale by definition
-- (migration is run at startup, before the daemon processes jobs). If we
-- left them in place, the new lock_slot DEFAULT 0 would give every
-- existing row slot=0, and the UNIQUE INDEX in step 3 would reject the
-- very first INSERT that uses slot 0.
DELETE FROM job_locks;

-- STEP 2: Add lock_slot column. DEFAULT 0 is required because ALTER TABLE
-- ADD COLUMN on a table with existing rows is more portable when a
-- DEFAULT is supplied. After the DELETE in step 1, no rows exist, so
-- the default's actual value is moot — every row inserted from this
-- point on picks its slot explicitly via try_acquire_slot().
ALTER TABLE job_locks ADD COLUMN lock_slot INTEGER NOT NULL DEFAULT 0;

-- STEP 3: Enforce at most one lock per (project_id, queue_id, lock_slot).
-- Together with the slot-try loop in acquire_queue_lock (which attempts
-- slots 0..concurrency_limit-1 via INSERT OR IGNORE), this makes
-- acquire atomic across processes: two daemons that both pass the
-- in-process acquire path cannot both end up holding the same slot,
-- so they cannot both end up holding the queue. If they both try slot
-- 0, one wins. The loser advances to slot 1. And so on until either
-- one of them gets a slot or all slots are taken.
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_locks_slot
    ON job_locks(project_id, queue_id, lock_slot);

-- DOWN
-- Reverses both column and index. Note: SQLite doesn't support DROP COLUMN
-- on older versions so the column will remain but is unused by code.
DROP INDEX IF EXISTS uq_job_locks_slot;
-- ALTER TABLE job_locks DROP COLUMN lock_slot  -- SQLite <3.35 cannot drop columns

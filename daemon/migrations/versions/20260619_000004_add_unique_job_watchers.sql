-- Migration: Add unique constraint on job_watchers(job_id, instance_id)
-- Created: 2026-06-19
-- Author: system
-- Description:
--   H13 fix: JobWatcherRepository.add_watch previously did
--   SELECT-then-INSERT/UPDATE, which produced duplicate job_watchers rows
--   under concurrent calls from the same (job_id, instance_id) pair
--   (PostgreSQL concurrency audit MEDIUM-severity finding). The repository
--   now executes a dialect-aware INSERT ... ON CONFLICT DO UPDATE keyed on
--   (job_id, instance_id).
--
--   For PostgreSQL, SQLModel.metadata.create_all() picks up the
--   UniqueConstraint on JobWatcher.__table_args__ automatically (fresh DBs)
--   same precedent as the lock_slot, version, and instance_mappings
--   migrations. Existing PostgreSQL DBs need to add the constraint manually:
--       ALTER TABLE job_watchers
--           ADD CONSTRAINT uq_job_watchers_job_instance
--           UNIQUE (job_id, instance_id)
--   This .sql migration is therefore skipped by the runner on PostgreSQL
--   and applied only on SQLite. SQLite does not honor __table_args__ on
--   existing tables, so we add a unique INDEX (functionally equivalent for
--   ON CONFLICT purposes on SQLite).
--
--   PRE-FLIGHT DEDUP:
--   Before creating the UNIQUE INDEX, any pre-existing duplicate rows
--   must be removed, otherwise the CREATE UNIQUE INDEX would fail with
--   "UNIQUE constraint failed: job_watchers.job_id, job_watchers.instance_id"
--   and the migration runner would (incorrectly) treat that as a real
--   error rather than the idempotent "already exists" case. We keep the
--   row with the lexicographically smallest watch_id per (job_id,
--   instance_id) group. The exact survivor is deterministic but not
--   insertion-order dependent; any consistent pick works because the
--   upsert's `watch_events` semantics don't require preserving a
--   particular duplicate.
--
--   The migration runner (daemon/migrations/runner.py) treats "already
--   exists" errors as idempotent, so re-running this file is safe.
--
--   NOTE: this migration file deliberately avoids semicolons inside
--   SQL comments because runner.py executes the UP section via
--   ``migration.up_sql.split`` with ``;`` as separator and naively
--   treats every semicolon as a statement boundary regardless of
--   whether it sits inside a ``--`` comment line.

-- UP

-- STEP 1: Dedupe pre-existing duplicates. SQLite has no ALTER TABLE
-- ADD CONSTRAINT syntax, so we use a unique INDEX (equivalent for ON
-- CONFLICT DO UPDATE purposes). The DELETE must run before the index
-- is created, otherwise the CREATE UNIQUE INDEX would fail.
-- Use the primary key (watch_id) instead of SQLite's implicit `rowid`
-- so the same dedup runs unchanged on PostgreSQL once the runner is
-- ever taught to execute UP SQL on PG. The exact survivor is
-- deterministic (lexicographically smallest watch_id per group) but
-- not insertion-order dependent (all duplicate rows are equivalent
-- for the eventual UNIQUE constraint).
DELETE FROM job_watchers
WHERE watch_id NOT IN (
    SELECT MIN(watch_id) FROM job_watchers GROUP BY job_id, instance_id
);

-- STEP 2: Enforce at most one watch per (job_id, instance_id). Together
-- with the ON CONFLICT DO UPDATE upsert in JobWatcherRepository.add_watch,
-- this makes add_watch atomic across processes. Two concurrent callers
-- attempting to add a watch for the same pair both reach the INSERT,
-- the loser gets a UNIQUE constraint violation, and the dialect-specific
-- INSERT compiles that into a re-write of the loser's `watch_events`.
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_watchers_job_instance
    ON job_watchers (job_id, instance_id);

-- DOWN
DROP INDEX IF EXISTS uq_job_watchers_job_instance;
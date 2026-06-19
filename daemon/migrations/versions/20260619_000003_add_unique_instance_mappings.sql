-- Migration: Add unique constraint on instance_mappings(source_id, external_user_id)
-- Created: 2026-06-19
-- Author: system
-- Description:
--   C9 fix: SQLModelSourceRepository.create_instance_mapping used to perform
--   SELECT-then-INSERT/UPDATE, which produced duplicate instance_mappings
--   rows under concurrent first-message access from the same external user
--   (PostgreSQL concurrency audit TOP CRITICAL finding). The repository now
--   executes a dialect-aware INSERT ... ON CONFLICT DO UPDATE keyed on
--   (source_id, external_user_id).
--
--   For PostgreSQL, SQLModel.metadata.create_all() picks up the
--   UniqueConstraint on InstanceMapping.__table_args__ automatically (fresh
--   DBs) — same precedent as the lock_slot and version columns. Existing
--   PostgreSQL DBs need to add the constraint manually by running the
--   following three-line ALTER TABLE (trailing semicolon on the last line
--   omitted on purpose: runner.py splits UP SQL on `;` and would treat
--   an in-comment semicolon as a statement boundary):
--       ALTER TABLE instance_mappings
--           ADD CONSTRAINT uq_instance_mappings_source_user
--           UNIQUE (source_id, external_user_id)
--   This .sql migration is therefore skipped by the runner on PostgreSQL and
--   applied only on SQLite. SQLite does not honor __table_args__ on existing
--   tables, so we add a unique INDEX (functionally equivalent for ON
--   CONFLICT purposes on SQLite).
--
--   The migration runner (daemon/migrations/runner.py) treats "duplicate
--   index name" / "already exists" errors as idempotent, so re-running this
--   file is safe.

-- UP

-- STEP 1: Dedupe pre-existing duplicates. SQLite has no ALTER TABLE
-- ADD CONSTRAINT syntax, so we use a unique INDEX (equivalent for ON
-- CONFLICT DO UPDATE purposes). The DELETE must run before the index
-- is created, otherwise the CREATE UNIQUE INDEX would fail.
-- This dedup is needed because legacy C9 race may have produced
-- duplicate (source_id, external_user_id) rows before the repository
-- was switched to dialect-aware INSERT ... ON CONFLICT DO UPDATE.
-- Use the primary key (mapping_id) instead of SQLite's implicit `rowid`
-- so the same dedup runs unchanged on PostgreSQL once the runner is
-- ever taught to execute UP SQL on PG. The exact survivor is
-- deterministic (lexicographically smallest mapping_id per group) but
-- not insertion-order dependent (all duplicate rows are equivalent
-- for the eventual UNIQUE constraint).
DELETE FROM instance_mappings
WHERE mapping_id NOT IN (
    SELECT MIN(mapping_id) FROM instance_mappings GROUP BY source_id, external_user_id
);

-- STEP 2: Enforce at most one mapping per (source_id, external_user_id).
-- Together with the ON CONFLICT DO UPDATE upsert in
-- SQLModelSourceRepository.create_instance_mapping, this makes the
-- upsert atomic across processes. Two concurrent callers attempting
-- to create a mapping for the same pair both reach the INSERT, the
-- loser gets a UNIQUE constraint violation, and the dialect-specific
-- INSERT compiles that into a re-write of the loser's row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_instance_mappings_source_user
    ON instance_mappings (source_id, external_user_id);

-- DOWN
DROP INDEX IF EXISTS uq_instance_mappings_source_user;
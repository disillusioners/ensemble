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
CREATE UNIQUE INDEX IF NOT EXISTS uq_instance_mappings_source_user
    ON instance_mappings (source_id, external_user_id);

-- DOWN
DROP INDEX IF EXISTS uq_instance_mappings_source_user;
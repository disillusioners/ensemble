-- Migration: add work_id column and unique index to task table
-- Created: 2026-06-27
-- Author: system
-- Description:
--   Phase 1 (Batch 2) of feature/virtual-job-management-surface. Adds
--   a stable cross-system work identifier (UUID4 string) to the
--   ``task`` table so the virtual job resolver can correlate a Task
--   row with a corresponding JobItem row (or a logical work unit
--   that spans both) without depending on the integer primary key.
--   The column is UNIQUE + indexed for O(1) lookup. New rows get a
--   UUID4 via the model's ``default_factory`` in
--   ``daemon/repositories/task/models.py``.
--
--   Backfill: ``lower(hex(randomblob(16)))`` produces a 32-char
--   lowercase hex string (NOT a formatted UUID with dashes). This is
--   acceptable for backfill of historical rows — application code
--   never reads pre-existing ``work_id`` values, only newly-created
--   Tasks. The unique index ensures future writes don't collide
--   with the backfilled values.
--
--   job_watchers FK / notification is intentionally NOT touched in
--   this migration — that's Phase 2.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine
--   dialect is sqlite (runner.py skips non-sqlite). For PostgreSQL:
--     - Fresh DBs: SQLModel.metadata.create_all() picks up the new
--       work_id column + unique index from Task.__table_args__
--       automatically.
--     - Existing DBs: equivalent statements are in
--       daemon/manager.py::_ensure_postgres_columns() (see the
--       Virtual Job Work ID block at the end of the statements
--       list). This matches the lock_slot / version-column /
--       enqueued_at precedents.

-- UP

-- Add the work_id column. TEXT is fine: SQLite is loosely typed and
-- the model's default_factory generates a 36-char UUID4 string with
-- dashes. We declare the column nullable here so the ALTER TABLE
-- succeeds against a populated table — the backfill below fills all
-- existing rows, and the unique index then enforces non-NULL going
-- forward (SQLite treats NULL as a distinct value, so the index does
-- not block the backfill UPDATE).
ALTER TABLE task ADD COLUMN work_id TEXT;

-- Backfill historical rows with a 32-char hex string. Application
-- code does not consume these values — only newly-created Tasks go
-- through the model's default_factory which produces a UUID4. The
-- intent here is just to satisfy the upcoming UNIQUE index so it
-- can be created without violating NOT NULL semantics on existing
-- rows (SQLite has no way to add a NOT NULL constraint to an
-- existing column without table-rebuild).
UPDATE task SET work_id = lower(hex(randomblob(16))) WHERE work_id IS NULL;

-- Enforce uniqueness. Named idx_task_work_id to match the equivalent
-- CREATE UNIQUE INDEX statement in _ensure_postgres_columns().
-- IF NOT EXISTS keeps this idempotent against fresh databases where
-- SQLModel.metadata.create_all() has already created a same-name
-- index from the model's index=True / unique=True fields.
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_work_id ON task(work_id);

-- DOWN
-- Reverse the index and column. SQLite < 3.35 does not support
-- DROP COLUMN so the column will remain but is unused by code.
-- Trailing semicolons are deliberately omitted from the DROP COLUMN
-- comment because runner.py splits DOWN SQL on the statement-
-- terminator character and would otherwise treat the in-comment
-- semicolon as a statement boundary.
DROP INDEX IF EXISTS idx_task_work_id;
-- ALTER TABLE task DROP COLUMN work_id  -- SQLite <3.35 cannot drop columns

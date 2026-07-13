-- Migration: add is_background column to task table
-- Created: 2026-06-27
-- Author: system
-- Description:
--   Phase 3 Part B2 (2026-06-27) of
--   feature/virtual-job-management-surface. Adds the ``is_background``
--   boolean to the ``task`` table so a Task row can be flagged as
--   belonging to the background-queue lane. Mirrors the
--   ``is_deferred`` migration pattern exactly. The worker pool's
--   idle gate (see ``daemon/services/worker_pool.py``) only claims
--   a background task once every non-background queue is empty,
--   which lets the orchestrator stage long-running / housekeeping
--   work behind "real" traffic without competing for claim slots.
--
--   The column is NOT NULL with DEFAULT 0 so existing rows backfill
--   cleanly. SQLite is loosely typed and treats ``BOOLEAN`` as a
--   synonym for ``INTEGER`` (0/1); the model field
--   ``Task.is_background`` is declared ``bool`` and SQLAlchemy coerces
--   on read/write, so the storage type is interchangeable.
--
--   Backfill is a no-op (DEFAULT 0 = non-background, which matches the
--   pre-migration behaviour where no task was background). No UPDATE
--   statement is needed.
--
--   The plain non-unique index ``ix_task_is_background`` matches the
--   model's ``index=True`` declaration in
--   ``daemon/repositories/task/models.py``. Fresh databases get the
--   index automatically from ``SQLModel.metadata.create_all()``;
--   here on existing SQLite databases we add it explicitly so the
--   background-queue idle-gate predicate
--   (``WHERE status='pending' AND is_background=...``) stays O(log n).
--   ``IF NOT EXISTS`` keeps the statement idempotent on re-run.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine
--   dialect is sqlite (runner.py skips non-sqlite). For PostgreSQL:
--     - Fresh DBs: SQLModel.metadata.create_all() picks up the new
--       ``is_background`` column + ``ix_task_is_background`` index
--       from ``Task.__table_args__`` automatically.
--     - Existing DBs: equivalent statement lives in
--       daemon/manager.py::_ensure_postgres_columns() (see the
--       Background Queue marker block at the end of the statements
--       list, immediately after the Defer Queue marker block).
--       Same dual-driver pattern as ``last_heartbeat_at`` /
--       ``work_id`` / ``is_deferred``.

-- UP

-- Add the column. SQLite is loosely typed — ``BOOLEAN`` is stored as
-- INTEGER 0/1, matching how the model field's ``bool`` is persisted
-- by SQLAlchemy. NOT NULL DEFAULT 0 ensures the existing rows
-- backfill cleanly and matches the Python default on the model.
ALTER TABLE task ADD COLUMN is_background BOOLEAN DEFAULT 0 NOT NULL;

-- Plain index matching the model's ``index=True`` on Task.is_background.
-- Name mirrors what ``SQLModel.metadata.create_all()`` emits on fresh
-- databases so both paths converge on the same index name. IF NOT
-- EXISTS makes this a no-op on re-run and on fresh databases where
-- create_all already created it.
CREATE INDEX IF NOT EXISTS ix_task_is_background ON task(is_background);

-- DOWN
-- Reverses both the index and the column. SQLite < 3.35 does not
-- support DROP COLUMN so the column will remain but is unused by
-- code. Trailing semicolons are deliberately omitted below because
-- runner.py splits DOWN SQL on the statement-terminator character
-- and would otherwise treat in-comment semicolons as statement
-- boundaries.
DROP INDEX IF EXISTS ix_task_is_background;
-- ALTER TABLE task DROP COLUMN is_background  -- SQLite <3.35 cannot drop columns
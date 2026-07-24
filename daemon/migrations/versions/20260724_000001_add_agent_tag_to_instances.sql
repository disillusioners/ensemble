-- Migration: add agent_tag column to instances table
-- Created: 2026-07-24
-- Author: system
-- Description:
--   Agent Versioning Phase 2 data layer. ``agent_tag`` records the version
--   tag the instance was spawned with (None = base). The Phase 3 frontend
--   surfaces this as a version badge next to the agent name in the instance
--   list. The column is nullable, so no backfill is required — existing
--   instances default to the base version.
--
--   Schema mirrors daemon/repositories/instance/models.py
--   (Instance.agent_tag, default None).
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine
--   dialect is sqlite (runner.py skips non-sqlite). For PostgreSQL:
--     - Fresh DBs: SQLModel.metadata.create_all() picks up the new
--       ``agent_tag`` column from the Instance SQLModel automatically
--       (nullable=True, default=None).
--     - Existing DBs: handled by the ``ALTER TABLE instances ADD
--       COLUMN IF NOT EXISTS agent_tag VARCHAR`` statement registered
--       in ``daemon/manager.py::_ensure_postgres_columns``. W9: no
--       index is created — agent_tag filtering is rare and the column
--       is only used for read-after-write (badge display).
--
--   The runner treats "duplicate column name" errors as idempotent,
--   so re-running this file is safe on SQLite.

-- UP

ALTER TABLE instances ADD COLUMN agent_tag VARCHAR;

-- DOWN
-- Reverse the column addition. SQLite 3.35+ supports DROP COLUMN; older
-- versions will leave the column in place but it is unused by code.
-- Trailing semicolons are deliberately omitted below because runner.py
-- splits DOWN SQL on the statement-terminator character.
-- ALTER TABLE instances DROP COLUMN agent_tag  -- SQLite <3.35 cannot drop columns

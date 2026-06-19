-- Migration: add version column to infra_assets for optimistic locking
-- Created: 2026-06-19
-- Author: system
-- Description:
--   M5 fix — defense-in-depth for concurrent updates to the same
--   infra_assets row. Adds a ``version INTEGER NOT NULL DEFAULT 1``
--   column so the SQLModel model can carry an optimistic-lock
--   counter. ``SQLModelInfraRepository.update_asset`` uses this
--   counter for atomic check-and-increment when callers opt in
--   via the ``expected_version`` parameter; concurrent edits to
--   the same asset then raise ``ValueError`` instead of silently
--   clobbering each other.
--
--   Existing rows backfill to ``1`` via ``NOT NULL DEFAULT 1``.
--   The migration runner (daemon/migrations/runner.py) treats
--   "duplicate column name" errors as idempotent, so re-running
--   this file is safe on a DB that already has the column.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine
--   dialect is sqlite (runner.py skips non-sqlite). For PostgreSQL:
--     - Fresh DBs: SQLModel.metadata.create_all() picks up the new
--       ``version`` column from the InfraAsset model automatically.
--     - Existing DBs: apply manually:
--           ALTER TABLE infra_assets
--             ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
--       This matches the lock_slot / version-column precedents:
--       those .sql migrations are also skipped on PG; PG users got
--       the column via create_all() on fresh databases.

-- UP

ALTER TABLE infra_assets ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

-- DOWN
-- Reverses the column. Note: SQLite < 3.35 does not support DROP COLUMN;
-- the column will remain but is unused by code.
-- ALTER TABLE infra_assets DROP COLUMN version;  -- SQLite <3.35 cannot drop columns

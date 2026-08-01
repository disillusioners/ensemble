-- Migration: add turn suspension handles to task table
-- Created: 2026-08-01
-- Author: system
-- Description:
--   Increment 4 turn-reconciler schema phase. Adds nullable
--   ``suspension_reason`` and ``resume_target_turn_id`` columns to the
--   ``task`` table, plus the composite lookup index used by explicit
--   resume routing.
--
--   B3 IDEMPOTENCY GUARD:
--   MigrationRunner executes each statement inside a Python try/except
--   guard and treats SQLite's "duplicate column name" error as an
--   idempotent no-op. This is the project's guarded ALTER convention for
--   fresh databases, where SQLModel.metadata.create_all() has already
--   created both columns before pending migrations run. On an existing
--   database, the same ALTER statements add whichever columns are absent.
--   The following CREATE INDEX uses SQLite's native IF NOT EXISTS guard,
--   and the backfill is naturally idempotent through its WHERE predicate.
--
--   B2 LEGACY BACKFILL:
--   A legacy paused Task did not record why or which turn was suspended.
--   Mark it as externally paused and self-target it by authoritative
--   ``work_id`` so later explicit-handle routing can still resume it.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine dialect
--   is SQLite. Fresh PostgreSQL databases receive the fields and index
--   from SQLModel.metadata.create_all(). Existing PostgreSQL databases
--   receive equivalent idempotent column adds, index creation, and
--   backfill in daemon/manager.py::_ensure_postgres_columns().

-- UP

-- Guarded by MigrationRunner's per-statement duplicate-column handler.
ALTER TABLE task ADD COLUMN suspension_reason TEXT;
ALTER TABLE task ADD COLUMN resume_target_turn_id TEXT;

CREATE INDEX IF NOT EXISTS idx_task_resume_target
    ON task (resume_target_turn_id, suspension_reason);

UPDATE task
   SET suspension_reason = 'paused_external',
       resume_target_turn_id = work_id
 WHERE status = 'paused'
   AND suspension_reason IS NULL;

-- DOWN
-- Additive rollback: remove the lookup index first. SQLite versions older
-- than 3.35 cannot safely drop columns, so application reads and writes
-- must be reverted before leaving the nullable columns unused.
DROP INDEX IF EXISTS idx_task_resume_target;
-- ALTER TABLE task DROP COLUMN resume_target_turn_id  -- SQLite <3.35 cannot drop columns
-- ALTER TABLE task DROP COLUMN suspension_reason  -- SQLite <3.35 cannot drop columns

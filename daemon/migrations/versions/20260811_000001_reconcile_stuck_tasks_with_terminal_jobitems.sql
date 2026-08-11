-- Migration: reconcile stuck tasks whose linked JobItem is terminal
-- Created: 2026-08-11
-- Author: system
-- Description:
--   Reconciles Task rows stuck in 'paused'/'pending' status when their
--   linked JobItem (via task.work_id = job_queue_items.job_id) has
--   already transitioned to a terminal admission_state ('done'/'dead').
--   Without this backfill, the orphaned Task blocks the defer/background
--   idle-gate indefinitely. See
--   .agents/shared/planning/task-job-reconciliation/phase3-plan.md
--
--   The UPDATE uses the portable ANSI ``WHERE EXISTS`` subquery form so
--   the same SQL works on both SQLite and PostgreSQL. The
--   ``status IN ('paused', 'pending')`` guard makes the migration
--   idempotent: a second run matches 0 rows.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine dialect
--   is SQLite. Existing PostgreSQL databases receive the equivalent
--   UPDATE statement from ``EnsembleManager._ensure_postgres_columns``
--   in ``daemon/manager.py`` (the .sql runner is a NO-OP on PostgreSQL).
--   The statement is kept byte-identical so the two paths converge on
--   the same final state.

-- UP

-- Reconcile tasks whose linked JobItem is already terminal.
UPDATE task
SET status = 'cancelled',
    cancel_requested = 1,
    cancel_requested_at = CURRENT_TIMESTAMP,
    completed_at = CURRENT_TIMESTAMP
WHERE status IN ('paused', 'pending')
  AND EXISTS (
      SELECT 1 FROM job_queue_items ji
      WHERE ji.job_id = task.work_id
        AND ji.admission_state IN ('done', 'dead')
        AND ji.deleted_at IS NULL
  );

-- DOWN
-- No-op: this migration is a forward-only backfill. Reverting would
-- re-introduce the stuck state. Same convention as the reference
-- migration (20260810_000001_fix_idle_gate_stuck_task_flags.sql).
SELECT 1;

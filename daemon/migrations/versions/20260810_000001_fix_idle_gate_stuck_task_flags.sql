-- Migration: repair stuck task flags on defer/background queues
-- Created: 2026-08-10
-- Author: system
-- Description:
--   Idle-gate deadlock repair. Backfills ``task.is_deferred`` and
--   ``task.is_background`` for tasks whose linked JobItem sits on a
--   defer or background queue but whose task flag was never stamped
--   (the pre-fix bug: enqueue_message_job forwarded the caller's
--   flags verbatim, so a defer/background queue's task often carried
--   ``is_deferred=False`` / ``is_background=False``). The Task-side
--   and Job-side idle-gate predicates then counted the offending task
--   as non-deferred / non-background work and the queue's
--   JobItems never got activated — permanent deadlock.
--
--   The migration backfills the flag on the Task row by joining the
--   task table through job_queue_items and job_queues. Uses the
--   portable ``WHERE EXISTS`` subquery form so the same SQL works on
--   both SQLite and PostgreSQL.
--
--   Post-migration, enqueue_message_job derives the flag from the
--   resolved queue's queue_type at Task creation time, so no further
--   backfill is needed for new tasks.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine dialect
--   is SQLite. Existing PostgreSQL databases receive the equivalent
--   UPDATE statements from ``EnsembleManager._ensure_postgres_columns``
--   in ``daemon/manager.py`` (the .sql runner is a NO-OP on PostgreSQL).
--   The two statements are kept byte-identical so the two paths converge
--   on the same final state.

-- UP

-- Backfill is_deferred for tasks on defer queues.
UPDATE task
SET is_deferred = TRUE
WHERE task.is_deferred = FALSE
  AND EXISTS (
      SELECT 1 FROM job_queue_items ji
      JOIN job_queues q ON ji.queue_id = q.queue_id
      WHERE ji.job_id = task.work_id
        AND q.queue_type = 'defer'
        AND ji.deleted_at IS NULL
  );

-- Backfill is_background for tasks on background queues.
UPDATE task
SET is_background = TRUE
WHERE task.is_background = FALSE
  AND EXISTS (
      SELECT 1 FROM job_queue_items ji
      JOIN job_queues q ON ji.queue_id = q.queue_id
      WHERE ji.job_id = task.work_id
        AND q.queue_type = 'background'
        AND ji.deleted_at IS NULL
  );

-- DOWN
-- No-op: reverting the flag to its pre-migration value would risk
-- re-introducing the deadlock. Operators who need to undo this should
-- set the flag back to FALSE manually for tasks that were enqueued
-- before the fix landed.

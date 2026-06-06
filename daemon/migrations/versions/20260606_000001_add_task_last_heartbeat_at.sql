-- Migration: add last_heartbeat_at column to task table
-- Created: 2026-06-06
-- Author: system
-- Description: Add per-task heartbeat timestamp used by StaleTaskRecovery
--              to distinguish a live long-running task from a crashed one.
--              A live task has its last_heartbeat_at updated periodically by
--              the worker (see TaskHeartbeat in worker_pool.py). A crashed
--              task's last_heartbeat_at stops being updated, so the recovery
--              service can flag it within the configured threshold
--              (default 5 min) instead of waiting for task_timeout_minutes
--              (default 60 min).
--
--              The recovery predicate in find_cancellable_tasks() and
--              find_stale_running_tasks() is migrated from
--                  started_at < threshold
--              to
--                  last_heartbeat_at < threshold
--              (falling back to started_at for rows where last_heartbeat_at
--              is NULL, e.g. rows inserted by old code paths). The fallback
--              preserves the old behavior for any code path that inserts
--              RUNNING tasks without going through claim_pending_task.
--
--              Backfill: on startup, the daemon sets
--              last_heartbeat_at = started_at for any RUNNING row where
--              last_heartbeat_at is NULL. This prevents in-flight tasks
--              from being immediately flagged as stale after a deploy.
--
--              See docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md
--              §9.1 (StaleTaskRecovery verification) and the follow-up to
--              the per-instance guard fix (Option 1: liveness signal).
--
--              NOTE: this file is auto-applied for SQLite only (the
--              migration runner skips non-SQLite engines). For Postgres
--              production, the manager.py startup hook calls
--              _ensure_heartbeat_column() which adds the column with
--              IF NOT EXISTS — see the inline comment there for the
--              reasoning and the manual one-time migration to run if
--              upgrading from a version that pre-dates the hook.

-- UP

ALTER TABLE task ADD COLUMN last_heartbeat_at TIMESTAMP;

-- Partial index on RUNNING tasks: the recovery predicate filters by
-- status='running' AND last_heartbeat_at < threshold, so a partial
-- index keeps the lookup O(log n) even as completed/old rows accumulate.
-- (SQLite ignores the WHERE clause; the index is still useful on Postgres.)
CREATE INDEX IF NOT EXISTS idx_task_running_heartbeat
    ON task(last_heartbeat_at)
    WHERE status = 'running';

-- DOWN

DROP INDEX IF EXISTS idx_task_running_heartbeat;
ALTER TABLE task DROP COLUMN IF EXISTS last_heartbeat_at;

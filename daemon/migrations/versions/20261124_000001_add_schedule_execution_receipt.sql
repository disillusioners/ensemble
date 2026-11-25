-- Migration: add execution-receipt columns to schedule_executions
-- Created: 2026-11-24
-- Commission: fix/post-outage-recovery SCOPE b3 — Addendum A execution receipt.
--
-- The linkage column ``instance_id`` ALREADY exists (models.py:170); this
-- migration adds ONLY the new receipt handles:
--   * task_id / job_item_id — durable handles to the spawned Task/JobItem
--     (job_item_id == Task.work_id == JobItem.job_id). NULL on a
--     never-started / failed-to-dispatch fire.
--   * outcome — terminal outcome carried on an ``executed`` row.
--   * redriven_at / redrive_count / redrive_reason — supervisor CAS stamp +
--     bounded re-drive bookkeeping (A.4).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): applied by the .sql runner (ADD COLUMN guarded by
--     the applied-version ledger).
--   - PostgreSQL: schema comes from SQLModel create_all (the ScheduleExecution
--     SQLModel carries the fields) plus the probe/idempotent ALTER in
--     daemon/manager.py::_ensure_postgres_columns. The .sql runner is a NO-OP
--     on PG.
--   - All TEXT/INTEGER — same naive frame as the existing TEXT columns.
--
-- ROLLBACK POSTURE: additive only. DOWN leaves the columns in place (SQLite
-- cannot DROP COLUMN easily); the code ignores them when the lane is fenced.

-- UP

ALTER TABLE schedule_executions ADD COLUMN task_id VARCHAR;
ALTER TABLE schedule_executions ADD COLUMN job_item_id VARCHAR;
ALTER TABLE schedule_executions ADD COLUMN outcome VARCHAR;
ALTER TABLE schedule_executions ADD COLUMN redriven_at VARCHAR;
ALTER TABLE schedule_executions ADD COLUMN redrive_count INTEGER DEFAULT 0;
ALTER TABLE schedule_executions ADD COLUMN redrive_reason VARCHAR;

-- DOWN
-- SQLite cannot DROP COLUMN easily; leave the columns in place (additive-only).

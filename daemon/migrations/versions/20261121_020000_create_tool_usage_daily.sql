-- Migration: create the tool_usage_daily aggregated counter ledger
-- Created: 2026-11-21
-- Phase: Tool-Usage Metrics Phase 1 (ENSEMBLE-37 measurement slice; plan:
--   .agents/shared/planning/tool-usage-metrics-p1/phase1-plan.md).
--
-- What lives here:
--   * ``tool_usage_daily`` — ONE row per
--     (day, project_id, tool_name, agent_id) with an additive call_count.
--     NAMES ONLY: no argument / argv / content / tool_call_id / instance /
--     session column, ever (frozen schema invariant).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). This file is the
--     persistent record for the brand-new table.
--   - PostgreSQL: the schema comes from SQLModel.metadata.create_all with
--     ``daemon/repositories/tool_usage/models.py`` as canonical source;
--     the model is registered by the late-import block in
--     daemon/manager.py BEFORE create_all. Brand-new table ⇒ NO
--     _ensure_postgres_columns mirror. The PG runner is a NO-OP for this
--     file (runner.py early-returns on non-SQLite).
--   - EVERY STRING COLUMN IS TEXT (never VARCHAR). call_count is INTEGER
--     here (SQLite int64); the model renders BIGINT on PostgreSQL.
--   - 3-site index lockstep: the index names AND column lists below are
--     byte-identical to the ``__table_args__`` of the model.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path create_all runs FIRST and the migration runner applies
--   this file IMMEDIATELY AFTER it, so the table + model indexes already
--   exist by the time this file runs. IF NOT EXISTS removes both the
--   "already exists" error and the silent idempotent-skip that would
--   degrade the file into a no-op.
--
-- ROLLBACK POSTURE: additive only. The DOWN section drops only the new
-- table + its two indexes; the OLD binary references none of them, so
-- rollback is FIX-LOSS, never an incompatibility. No DROP/ALTER of any
-- existing table appears here.
--
-- No PRECONDITION header: no version-sensitive DDL. No semicolon in any
-- comment (the runner strips full-line comments but not inline ones).

-- UP

CREATE TABLE IF NOT EXISTS tool_usage_daily (
    day TEXT NOT NULL,
    project_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (day, project_id, tool_name, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_usage_daily_project_day
    ON tool_usage_daily (project_id, day);

CREATE INDEX IF NOT EXISTS idx_tool_usage_daily_tool_day
    ON tool_usage_daily (tool_name, day);

-- DOWN

DROP INDEX IF EXISTS idx_tool_usage_daily_tool_day;
DROP INDEX IF EXISTS idx_tool_usage_daily_project_day;
DROP TABLE IF EXISTS tool_usage_daily;

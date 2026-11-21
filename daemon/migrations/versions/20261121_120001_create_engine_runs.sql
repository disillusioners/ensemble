-- Migration: create engine_runs
--           (Engine Arc Phase 1 — central polling/event engine: the
--           APPEND-ONLY run audit)
-- Created: 2026-11-21
-- Phase: engine-arc-phase1 P1 (plan:
--   .agents/shared/planning/engine-arc-phase1/phase1-plan.md, T1.2).
--
-- What lives here:
--   * ``engine_runs`` — one row per attempted tick, APPEND-ONLY. Rows are
--     never updated and never deleted by the substrate (retention is a
--     later-phase concern, AMD-1(e)). The audit half of the DB
--     source-of-truth contract in decision-report.md section B.2
--     (lines 110-131): ``engine_jobs`` holds the mutable last-outcome,
--     this table holds the history.
--   * ``boot_id`` + ``host_pid`` are the discriminator that lets an
--     operator separate runs across daemon restarts (precedent:
--     ``bash_spawn_registry.boot_id``). A NULL ``boot_id`` row is
--     attributable to no boot in particular and must be read as
--     historical.
--   * ``error_digest`` is a JSON payload typed with the house
--     dual-driver JSONBType -- TEXT in this file so the DDL stays
--     SQLite-valid.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: the schema comes from SQLModel.metadata.create_all
--     with ``daemon/repositories/engine/models.py`` as the canonical
--     source (registered by the late-import block in daemon/manager.py
--     BEFORE create_all). Brand-new table, so there is NO
--     _ensure_postgres_columns mirror. The PG-side runner is a NO-OP.
--   - EVERY STRING COLUMN IS TEXT (never VARCHAR) on either driver.
--     ``id`` is an INTEGER autoincrement primary key on both drivers
--     (SQLite AUTOINCREMENT keyword; the model renders SERIAL /
--     IDENTITY on PostgreSQL via SQLModel's int primary-key handling).
--   - 3-site index lockstep: the index names AND column lists below are
--     byte-identical to the ``__table_args__`` of
--     ``daemon/repositories/engine/models.py`` and to the substrate
--     test pack's parity assertion.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   create_all runs FIRST on the boot path and this file is applied
--   IMMEDIATELY AFTER, so the objects already exist. IF NOT EXISTS is
--   what keeps the file from degrading into a silent no-op on every
--   boot (see 20261109_021154_create_memoir_v2_tables.sql).
--
-- ROLLBACK POSTURE: additive only. DOWN drops only this new table and
-- its indexes -- fix-loss for run history, never an incompatibility.
--
-- No semicolon inside any comment (the runner strips full-line comments
-- but not inline ones, and it splits statements on semicolons).

-- UP

CREATE TABLE IF NOT EXISTS engine_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    service_tag TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL,
    skip_reason TEXT,
    duration_seconds REAL,
    attempt INTEGER NOT NULL DEFAULT 1,
    error_class TEXT,
    error_digest TEXT,
    boot_id TEXT,
    host_pid INTEGER
);

CREATE INDEX IF NOT EXISTS idx_engine_runs_job_started
    ON engine_runs (job_id, started_at);

CREATE INDEX IF NOT EXISTS idx_engine_runs_outcome
    ON engine_runs (outcome);

-- DOWN

DROP INDEX IF EXISTS idx_engine_runs_outcome;
DROP INDEX IF EXISTS idx_engine_runs_job_started;
DROP TABLE IF EXISTS engine_runs;

-- Migration: create engine_jobs
--           (Engine Arc Phase 1 — central polling/event engine: the
--           mutable registration + last-outcome substrate)
-- Created: 2026-11-21
-- Phase: engine-arc-phase1 P1 (plan:
--   .agents/shared/planning/engine-arc-phase1/phase1-plan.md, T1.1).
--
-- What lives here:
--   * ``engine_jobs`` — ONE row per registered job. The DB is the
--     source of truth; the engine keeps a RAM cache for hot reads
--     (frozen design: central-polling-engine-study/decision-report.md
--     section B.2, lines 110-131).
--   * ``id`` is the stable ops SLUG (e.g. ``orphan-watcher``), not a
--     uuid: a re-registration upserts BY ID, so the slug makes that
--     literal and keeps operator greps direct (D1.4).
--   * ``version`` is bumped on every successful re-registration (D1.5)
--     so "did my re-registration land?" is answerable from the DB alone.
--   * The self-describing registration metadata required by the Emperor
--     addendum (2026-11-21) is carried as DURABLE columns:
--     ``service_tag`` (stable ops identity, metrics label, UNIQUE per
--     AMD-7), ``service_name``, ``category`` (open TEXT vocabulary with
--     a documented starter set), ``module_info`` (origin module path),
--     ``description``, and an extensible ``metadata`` JSON blob. Adding
--     a category later forces NO migration.
--   * Terminal state is stamped by ``stamp_job_outcome`` writing
--     ``last_run_at`` / ``last_outcome`` / ``last_skip_reason`` /
--     ``consecutive_skips``. The table deliberately has NO
--     ``action_by`` / ``acted_at`` column and never combines
--     ``resource_pid_map`` with an action string (fence F-3).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). This file is the
--     persistent record for the brand-new table.
--   - PostgreSQL: the schema comes from SQLModel.metadata.create_all
--     with ``daemon/repositories/engine/models.py`` as the canonical
--     source; the models are registered by the late-import block in
--     daemon/manager.py BEFORE create_all. Brand-new table, so there is
--     NO _ensure_postgres_columns mirror (that hook is for COLUMN
--     evolution on pre-existing tables). The PG-side runner is a NO-OP
--     for this file.
--   - EVERY STRING COLUMN IS TEXT (never VARCHAR) on either driver.
--     The JSON payloads (``metadata``) are TEXT here so the file stays
--     SQLite-valid; the model types them with the house dual-driver
--     JSONBType (JSONB on PostgreSQL, JSON on SQLite).
--   - 3-site index lockstep: the index names AND column lists below are
--     byte-identical to the ``__table_args__`` of
--     ``daemon/repositories/engine/models.py`` and to the substrate
--     test pack's parity assertion.
--
-- AMD-7 NOTE (service_tag UNIQUE): the plan's section 5 index list named
-- a plain ``idx_engine_jobs_service_tag``. The architect's AMD-7
-- hardening rules that ``service_tag`` must be UNIQUE FROM DAY ONE (one
-- tag maps to exactly one registered service -- it protects the future
-- maintenancer inspect/steer table and the /metrics UI table, and
-- adding UNIQUE later is the breaking change this review exists to
-- prevent). The plain index is therefore REPLACED by the named UNIQUE
-- index ``uq_engine_jobs_service_tag`` -- same column, unique semantics,
-- no redundant duplicate index. Documented here and in the model so the
-- module / migration / pack stay byte-identical.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path create_all runs FIRST and the migration runner
--   applies this file IMMEDIATELY AFTER it, so the table and its indexes
--   already exist by the time this file runs. A bare CREATE would raise
--   "already exists", the runner would swallow it on its idempotent-skip
--   branch, and the file would silently degrade into a no-op on every
--   boot (documented in 20261109_021154_create_memoir_v2_tables.sql).
--
-- ROLLBACK POSTURE: additive only. The DOWN section drops only this new
-- table and its indexes; the OLD binary references none of them, so
-- rollback is FIX-LOSS, never an incompatibility. No DROP or ALTER of
-- any existing table appears in the UP section.
--
-- No semicolon in any TRAILING (inline) comment: the runner strips
-- full-line comments but not inline ones, and it splits statements on
-- semicolons, so a ';' in a trailing comment would split a statement early.
-- (A ';' inside a FULL-LINE comment is harmless -- those are stripped.)

-- UP

CREATE TABLE IF NOT EXISTS engine_jobs (
    id TEXT NOT NULL PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 1,
    service_tag TEXT NOT NULL,
    service_name TEXT,
    category TEXT NOT NULL,
    module_info TEXT,
    description TEXT,
    metadata TEXT,
    trigger_kind TEXT NOT NULL,
    interval_seconds REAL,
    jitter_seconds REAL,
    first_tick_kind TEXT,
    first_tick_offset_seconds REAL,
    event_kind TEXT,
    event_filter TEXT,
    max_runtime_s REAL NOT NULL,
    timeout_policy TEXT NOT NULL,
    retry_max INTEGER NOT NULL DEFAULT 0,
    retry_backoff_base_s REAL,
    retry_backoff_cap_s REAL,
    priority INTEGER NOT NULL DEFAULT 5,
    concurrency_class TEXT NOT NULL,
    kill_switch_key TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_outcome TEXT,
    last_skip_reason TEXT,
    consecutive_skips INTEGER NOT NULL DEFAULT 0,
    dead INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_engine_jobs_service_tag
    ON engine_jobs (service_tag);

CREATE INDEX IF NOT EXISTS idx_engine_jobs_category
    ON engine_jobs (category);

CREATE INDEX IF NOT EXISTS idx_engine_jobs_trigger
    ON engine_jobs (trigger_kind);

-- DOWN

DROP INDEX IF EXISTS idx_engine_jobs_trigger;
DROP INDEX IF EXISTS idx_engine_jobs_category;
DROP INDEX IF EXISTS uq_engine_jobs_service_tag;
DROP TABLE IF EXISTS engine_jobs;

-- Migration: create resource_daemon_bucket
--           (resource-cost-control Phase 1 — sampler bucket storage)
-- Created: 2026-10-18
-- Phase: resource-cost-control P1 (plan:
--   .agents/shared/planning/resource-cost-control/phase1-plan.md).
--   Backing store for T1 ``resource_daemon_bucket``: 1 row / tick.
--
-- What lives here:
--   * Per-tick wall-clock anchor ``bucket_ts`` (Naive-UTC TEXT per D9;
--     Python-side ``daemon.services.timestamps.now_utc_naive`` is the
--     authoritative producer — see service_tracking A12 precedent).
--   * Sampler self-cost counters (D12 — alarmable).
--   * Attribution columns are written NULL/0 in P1 (D13 — P2 fills
--     them); the schema carries them now so P2 doesn't re-P1.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: brand-new table — created by
--     ``SQLModel.metadata.create_all`` from the
--     :class:`ResourceDaemonBucket` model
--     (``daemon/models/resource_monitor.py``), which registers into
--     the metadata via the one-line late-import block in
--     ``daemon/manager.py:738-748`` BEFORE the create_all call
--     (app_settings / snapshot / maintenance_runs / drift_events
--     precedent). Brand-new tables need NO
--     ``_ensure_postgres_columns`` mirror.
--   - Column names/types stay byte-identical to the
--     ResourceDaemonBucket model (canonical source for PG);
--     BIGINT in the model maps to BigInteger on PG, INTEGER on SQLite
--     (the maintenance_runs BigInteger precedent — incident 2026-09-28).
--   - ``bucket_ts`` carries an UNIQUE table-constraint; the implicit
--     SQLite index name is ``sqlite_autoindex_resource_daemon_bucket_1``
--     (no named index in §2.4 T1 — UNIQUE(bucket_ts) only).
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   ``daemon/migrations/runner.py:543-557`` strips full-line ``--``
--   comments before splitting on ``;``. Inline comments that contain
--   a ``;`` character are NOT stripped. This file uses no inline
--   semicolons. The 20260627_000003_task_is_deferred file documented
--   the same trap in reverse (semicolons inside comments previously
--   corrupted the split).

-- UP

CREATE TABLE IF NOT EXISTS resource_daemon_bucket (
    -- SQLite-friendly autoincrement serial. PG mirror uses BIGSERIAL.
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_ts TEXT NOT NULL UNIQUE,
    interval_s INTEGER,
    rss_kb INTEGER,
    pss_kb INTEGER,
    anon_kb INTEGER,
    swap_kb INTEGER,
    vsize_kb INTEGER,
    hw_peak_kb INTEGER,
    threads INTEGER,
    fds INTEGER,
    -- BIGINT in the SQLModel (PG int8); INTEGER on SQLite which is a
    -- superset binding via the maintenance_runs BigInteger precedent.
    cpu_user_usec INTEGER,
    cpu_sys_usec INTEGER,
    cg_current_kb INTEGER,
    cg_anon_kb INTEGER,
    cg_file_kb INTEGER,
    cg_slab_kb INTEGER,
    cg_peak_kb INTEGER,
    child_rss_total_kb INTEGER,
    attributed_bytes INTEGER,
    unattributed_kb INTEGER,
    coverage_pct REAL,
    sample_cost_ms REAL,
    sampler_lag_ms REAL,
    missed_samples INTEGER
);

-- DOWN

DROP TABLE IF EXISTS resource_daemon_bucket;
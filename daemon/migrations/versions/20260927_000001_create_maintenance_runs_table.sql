-- MANUAL: TRUE
--
-- Canonical DDL for the ``maintenance_runs`` audit table (Section 1
-- Maintenance Console, AM-15). CANONICAL DOC ONLY — the runner is a
-- NO-OP on PG (daemon/migrations/runner.py:693-730) and the table is
-- built by ``SQLModel.metadata.create_all`` on BOTH drivers at boot
-- (daemon/manager.py:530-547, model-registration block).
--
-- Rollback section below is informational — never executed by the
-- runner. A manual rollback uses ``DROP TABLE IF EXISTS
-- maintenance_runs;``.
--
-- This file's existence + structure is part of the conventions-
-- validated audit trail — the AM-15 ruling says: "no
-- ``_ensure_postgres_columns`` entries, brand-new tables need none,
-- but the canonical DDL stays in the migrations tree as the
-- human-readable contract".

CREATE TABLE IF NOT EXISTS maintenance_runs (
    run_id               TEXT PRIMARY KEY,   -- ckpt-<YYYYMMDD_HHMMSSffffff>-<hex8>
    section              TEXT NOT NULL,      -- 'checkpoint-cleanup' v1
    kind                 TEXT NOT NULL,      -- 'auto' | 'manual_dry_run' | 'manual_execute'
    started_at           TEXT NOT NULL,      -- now_utc_iso()
    completed_at         TEXT,               -- NULL while running
    status               TEXT NOT NULL,      -- 'running'|'succeeded'|'failed'|'interrupted'
    triggered_by         TEXT NOT NULL,      -- 'system' | 'user'
    requester_json       JSON,               -- {peer_ip, user_agent, origin}; NULL for auto
    dry_run_run_id       TEXT,               -- soft ref, no FK
    expected_bytes       INTEGER,            -- echoed promise
    dry_run_summary_json JSON,               -- full dry-run snapshot (incl. skipped[])
    confirm              BOOLEAN,            -- execute confirm flag
    advisory             TEXT,               -- 'system_busy' | NULL
    env_flags_json       JSON,               -- {blob_prune_dry_run, blob_prune_destructive, destructive_override}
    summary_json         JSON,               -- outcome: BlobPruneSummary + Op D counts + duration_ms
    error_json           JSON                -- {code, message}
);

-- The DB single-flight claim (AM-5): at most ONE ``running`` row per
-- ``section``. Postgres renders the where-clause via
-- ``postgresql_where``; SQLite via ``sqlite_where``. ``create_all``
-- emits both — Phase 1 verification gate is case 64.
CREATE UNIQUE INDEX IF NOT EXISTS uq_maintenance_runs_running_section
    ON maintenance_runs(section) WHERE status = 'running';

-- Composite for ``latest_completed_for_section`` queries
-- (kind filter + ORDER BY completed_at DESC).
CREATE INDEX IF NOT EXISTS ix_maintenance_runs_section_completed
    ON maintenance_runs(section, completed_at);

-- ─────────────────────────────────────────────────────────────────
-- Rollback (informational; never executed by the runner)
-- ─────────────────────────────────────────────────────────────────
--
-- DROP TABLE IF EXISTS maintenance_runs;
-- Migration: create resource_pid_map
--           (resource-cost-control Phase 1 — subprocess registry)
-- Created: 2026-10-18
-- Phase: resource-cost-control P1 (plan:
--   .agents/shared/planning/resource-cost-control/phase1-plan.md).
--   Backing store for T3 ``resource_pid_map``: the generalized
--   ``service_tracking`` registry (A11 pattern).
--
-- What lives here:
--   * Live-pid uniqueness enforced by the PARTIAL UNIQUE index
--     ``uq_resource_pid_map_pid_active`` over ``pid WHERE
--     exited_at IS NULL`` (dual-dialect WHERE via sqlite_where /
--     postgresql_where — the ``uq_report_injections_oblig_triple``
--     precedent). Makes pid-recycle a non-issue for live rows: after
--     a row is marked exited, a fresh pid can claim the slot.
--   * PID-recycle defense via ``start_time`` (kernel jiffies field
--     22) — same as ``service_tracking``.
--   * ``kind`` discriminates ``bash`` | ``chart_render`` |
--     ``mcp_child`` | ``service`` | ``proc`` (the §2.3 seams).
--   * Dormant v2 columns ``action`` / ``action_by`` / ``acted_at``
--     are declared NOW so P6 (control seam) does NOT re-create
--     schema (phase1-plan T3 requirement).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new table — created by
--     ``SQLModel.metadata.create_all`` from the :class:`ResourcePidMap`
--     model (``daemon/models/resource_monitor.py``) via the
--     late-import block in ``daemon/manager.py:738-748``.
--   - Partial UNIQUE indexes with a WHERE clause are supported on
--     both PG (always) and SQLite (since 3.8.0, 2013 — every
--     supported deployment has it). The service_tracking migration
--     20260915_212810 documents the same dual-dialect posture.
--   - 3-site index lockstep (D7): the four named indexes below are
--     byte-identical to ``__table_args__`` in
--     ``daemon/models/resource_monitor.py:ResourcePidMap`` and to the
--     storage test pack (AC3).
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner; inline
--   comments that contain a ``;`` character are NOT stripped. This
--   file uses no inline semicolons.

-- UP

CREATE TABLE IF NOT EXISTS resource_pid_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid INTEGER,
    pgid INTEGER,
    start_time INTEGER,
    instance_id TEXT,
    agent_id TEXT,
    kind TEXT,
    command TEXT,
    detail TEXT,
    parent_pid INTEGER,
    spawned_at TEXT NOT NULL,
    exited_at TEXT,
    exit_code INTEGER,
    exit_kind TEXT,
    peak_rss_kb INTEGER,
    cpu_user_usec INTEGER,
    -- Dormant v2 columns (P6 control seam).
    action TEXT,
    action_by TEXT,
    acted_at TEXT
);

-- Partial UNIQUE over live rows only. Dialect-neutral WHERE clause
-- (SQLite honors sqlite_where at create_all on SQLite; PG honors
-- postgresql_where). The literal ``exited_at IS NULL`` predicate is
-- byte-identical to the SQLModel Index(...) declaration.
CREATE UNIQUE INDEX IF NOT EXISTS uq_resource_pid_map_pid_active
    ON resource_pid_map (pid)
    WHERE exited_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_resource_pid_map_instance_spawned
    ON resource_pid_map (instance_id, spawned_at);

CREATE INDEX IF NOT EXISTS idx_resource_pid_map_kind_spawned
    ON resource_pid_map (kind, spawned_at);

CREATE INDEX IF NOT EXISTS idx_resource_pid_map_pid
    ON resource_pid_map (pid);

-- DOWN

DROP INDEX IF EXISTS idx_resource_pid_map_pid;
DROP INDEX IF EXISTS idx_resource_pid_map_kind_spawned;
DROP INDEX IF EXISTS idx_resource_pid_map_instance_spawned;
DROP INDEX IF EXISTS uq_resource_pid_map_pid_active;
DROP TABLE IF EXISTS resource_pid_map;
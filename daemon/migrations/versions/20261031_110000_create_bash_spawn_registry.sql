-- Migration: create bash_spawn_registry
--           (bash-tool orphan reaper Phase 1 — durable bash-spawn
--           tombstone registry)
-- Created: 2026-10-31
-- Phase: bash-tool orphan reaper P1 (plan:
--   .agents/shared/planning/bash-tool-orphan-reaper/plan.md, task 1.1).
--
-- What lives here:
--   * One row per bash-tool spawn carrying an instance_id, written
--     synchronously at the bash spawn seam (F4 call-site in
--     daemon/tools/bash.py). The row is the tombstone that keeps
--     (pid, pgid, tracking_id) reclaimable AFTER the direct child exits
--     (Path A) and across a daemon restart (Path F).
--   * Partial indexes over the OPEN subset: (instance_id) WHERE
--     reaped_at IS NULL and (boot_id) WHERE reaped_at IS NULL, plus a
--     plain (pgid) for kill-site dedup. Open rows are the small hot
--     subset every lane scans; a (boot_id, reaped_at) composite cannot
--     serve the ``reaped_at IS NULL AND boot_id != X`` reconcile
--     predicate anyway (plan m6).
--   * tracking_id is globally unique (uuid4 hex minted pre-spawn).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new table — created by
--     SQLModel.metadata.create_all from the BashSpawnRecord model
--     (daemon/repositories/bash_spawn/models.py) via the late-import
--     block in daemon/manager.py. Brand-new table ⇒ NO
--     _ensure_postgres_columns mirror (api.py:975-976 convention).
--   - Index names are byte-identical to __table_args__ in
--     daemon/repositories/bash_spawn/models.py (3-site lockstep:
--     model / migration / storage test pack).
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner; inline
--   comments that contain a ``;`` character are NOT stripped. This
--   file uses no inline semicolons.

-- UP

CREATE TABLE IF NOT EXISTS bash_spawn_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid INTEGER NOT NULL,
    pgid INTEGER NOT NULL,
    start_time INTEGER,
    tracking_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    command_sanitized TEXT NOT NULL,
    boot_id TEXT,
    daemon_pid INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reaped_at TEXT,
    reaped_by TEXT,
    reap_reason TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bash_spawn_registry_tracking_id
    ON bash_spawn_registry (tracking_id);

CREATE INDEX IF NOT EXISTS idx_bash_spawn_registry_instance_open
    ON bash_spawn_registry (instance_id)
    WHERE reaped_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_bash_spawn_registry_boot_open
    ON bash_spawn_registry (boot_id)
    WHERE reaped_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_bash_spawn_registry_pgid
    ON bash_spawn_registry (pgid);

-- DOWN

DROP INDEX IF EXISTS idx_bash_spawn_registry_pgid;
DROP INDEX IF EXISTS idx_bash_spawn_registry_boot_open;
DROP INDEX IF EXISTS idx_bash_spawn_registry_instance_open;
DROP INDEX IF EXISTS uq_bash_spawn_registry_tracking_id;
DROP TABLE IF EXISTS bash_spawn_registry;

-- Migration: create resource_limit_policy
--           (resource-cost-control Phase 1 — control seam)
-- Created: 2026-10-18
-- Phase: resource-cost-control P1 (plan:
--   .agents/shared/planning/resource-cost-control/phase1-plan.md).
--   Backing store for T4 ``resource_limit_policy``: the control seam
--   for v2 enforcement. EMPTY in v1 (observe-only); the chromium
--   concurrency cap (O2) lands later as a
--   ``scope='child_kind', target='chart_render'`` policy consumed at
--   the chart spawn seam.
--
-- What lives here:
--   * ``enabled`` defaults FALSE — every row is observe-only until
--     an operator enables it.
--   * ``user_gated`` is NOT NULL DEFAULT TRUE — every policy must be
--     gated by default; the un-gated form only lands via the
--     user-ceremony nonce gate (architecture §2.4 T4).
--   * v1 ships the table EMPTY (the FE control surface is P6).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new table — created by
--     ``SQLModel.metadata.create_all`` from the
--     :class:`ResourceLimitPolicy` model
--     (``daemon/models/resource_monitor.py``) via the late-import
--     block in ``daemon/manager.py:738-748``.
--   - 3-site index lockstep (D7): ``idx_resource_limit_policy_scope_target_metric``
--     is byte-identical to ``__table_args__`` in
--     ``daemon/models/resource_monitor.py:ResourceLimitPolicy`` and
--     to the storage test pack (AC3).
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner; inline
--   comments that contain a ``;`` character are NOT stripped. This
--   file uses no inline semicolons.

-- UP

CREATE TABLE IF NOT EXISTS resource_limit_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT,
    target TEXT,
    metric TEXT,
    op TEXT,
    threshold REAL,
    window_s INTEGER,
    enabled INTEGER NOT NULL DEFAULT 0,
    user_gated INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_resource_limit_policy_scope_target_metric
    ON resource_limit_policy (scope, target, metric);

-- DOWN

DROP INDEX IF EXISTS idx_resource_limit_policy_scope_target_metric;
DROP TABLE IF EXISTS resource_limit_policy;
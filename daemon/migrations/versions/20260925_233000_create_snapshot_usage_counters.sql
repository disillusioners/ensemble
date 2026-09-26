-- Migration: create snapshot_usage_counters (1 table)
--
-- Created: 2026-09-25
-- Author: worker (agent-snapshot-v1 Wave 3 R16 review follow-up)
-- Description:
--   Creates the ``snapshot_usage_counters`` table (R16 usage
--   monitoring — MONITORING ONLY, R10 forbids usage-ranking in v1).
--   Dual-driver behavior: see notes below.
-- DUAL-DRIVER NOTES:
--   For PostgreSQL: SQLModel.metadata.create_all() in manager.py creates
--     this table on every boot (brand-new table needs NO
--     _ensure_postgres_columns mirror — same contract as the PR3
--     20260925_194500_create_snapshot_tables.sql sibling).
--   For SQLite: This migration creates the table (the migration runner
--     is SQLite-only by design — runner.py:719-727).
--
-- Agent Snapshot v1 — Wave 3 R16 review follow-up (MONITORING ONLY).
--
--   snapshot_usage_counters  light usage counters: per-agent capture
--                            counts + per-snapshot spawn-warm counts
--                            (R16 rider j). Observational only — R10
--                            forbids usage-ranking in v1; ranking
--                            modules never read this table (pinned by
--                            TestMonitoringOnlyPin in
--                            tests/unit/tools/test_snapshot_v3.py).
--
-- Mirrors SnapshotUsageCounter exactly
-- (daemon/repositories/snapshot/models.py): TEXT ids/ISO-8601 stamps
-- for cross-driver consistency, INTEGER value defaulting to 0, and
-- the UNIQUE composite index on (scope, key) — the natural key that
-- makes the metrics service's INSERT ... ON CONFLICT upsert
-- race-free.

-- UP

CREATE TABLE IF NOT EXISTS snapshot_usage_counters (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_snapshot_usage_counters_scope_key ON snapshot_usage_counters (scope, key);

-- DOWN
DROP INDEX IF EXISTS ix_snapshot_usage_counters_scope_key;
DROP TABLE IF EXISTS snapshot_usage_counters;

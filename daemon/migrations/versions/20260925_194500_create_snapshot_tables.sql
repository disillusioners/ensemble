-- Migration: create agent-snapshot tables (2 tables)
--
-- Created: 2026-09-25
-- Author: worker (agent-snapshot-v1 PR3 storage layer)
-- Description:
--   Creates the ``snapshots`` + ``snapshot_embeddings`` tables for the
--   Agent Snapshot v1 storage layer (design-exploration §3.2, Rev 5
--   per-instance pivot). Dual-driver behavior: see notes below.
-- DUAL-DRIVER NOTES:
--   For PostgreSQL: SQLModel.metadata.create_all() in manager.py creates
--     these tables on every boot (brand-new tables need NO
--     _ensure_postgres_columns mirror — design-exploration §3.3).
--   For SQLite: This migration creates the tables (the migration runner
--     is SQLite-only by design — runner.py:719-727).
--
-- Agent Snapshot v1 — PR3 (design-exploration §3.2, Rev 5 per-instance pivot).
--
--   snapshots            header + digest + search metadata + R8 tags.
--   snapshot_embeddings  per-snapshot trigger-query vectors (JSONB floats,
--                        mirrors skill_embeddings).
--
-- The Rev 4 `snapshot_nodes` table is DELETED (per-instance capture has
-- no ordered 1:N tree to persist). `target_instance_id` and
-- `supersedes_snapshot_id` are SOFT references (plain TEXT, no FK) per
-- the superseded_by_id precedent. No truncation-marker column (Rev 5
-- deletion — fresh|stale|expired are computed at query time, never
-- stored).

-- UP

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    created_by_agent_id TEXT NOT NULL,
    target_instance_id TEXT NOT NULL,
    title TEXT NOT NULL,
    task_summary TEXT NOT NULL DEFAULT '',
    domain_tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_snapshot_id TEXT,
    repo_path TEXT,
    vcs_type TEXT,
    git_sha TEXT,
    git_branch TEXT,
    git_dirty INTEGER NOT NULL DEFAULT 0,
    runtime_version TEXT NOT NULL,
    effective_model TEXT,
    digest TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects (project_id)
);

CREATE INDEX IF NOT EXISTS ix_snapshots_project_status ON snapshots (project_id, status);
CREATE INDEX IF NOT EXISTS ix_snapshots_target_instance ON snapshots (target_instance_id);
CREATE INDEX IF NOT EXISTS ix_snapshots_supersedes ON snapshots (supersedes_snapshot_id);

CREATE TABLE IF NOT EXISTS snapshot_embeddings (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    trigger_query TEXT NOT NULL,
    embedding TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_snapshot_embeddings_snapshot_id ON snapshot_embeddings (snapshot_id);

-- DOWN
DROP TABLE IF EXISTS snapshot_embeddings;
DROP TABLE IF EXISTS snapshots;

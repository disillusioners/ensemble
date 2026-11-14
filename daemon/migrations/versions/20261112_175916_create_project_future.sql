-- Migration: create_project_future
-- Created: 2026-11-12
-- Author: feature/project-future implementation lane
-- Description: ProjectFutureEntry — per-project forward-improvement wishlist (MAX 32, FIFO eviction).
--   On PostgreSQL the table is created by ``SQLModel.metadata.create_all()`` (the model is
--   registered before create_all at manager boot — mission-records precedent for brand-new
--   tables, NO ``_ensure_postgres_columns`` mirror); the migration runner is a NO-OP there.
--   Schema mirrors daemon/repositories/project/models.py ``ProjectFutureEntry``.
--   STRING COLUMNS ARE TEXT IN THIS FILE (memoir doctrine, 20261109_021154_create_memoir_v2_tables.sql:42). Scope: the SQLite-side
--   .sql — TEXT here, never VARCHAR. On PostgreSQL, create_all emits VARCHAR(300)/(5000)
--   from the model's max_length=300/5000 (sibling precedent ProjectHistoryEntry.summary/
--   details, models.py:488-489); that PG-side VARCHAR reality is accepted (equivalent
--   semantics; two-layer truncation makes the length check unreachable). Do NOT claim
--   "never VARCHAR on either driver" — it is false on PG.
--   WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY): create_all runs FIRST on the boot
--   path; on a SQLite dev DB where create_all already made the table, a bare CREATE would
--   raise "already exists" and the runner would swallow it on its idempotent-skip branch.

-- UP
CREATE TABLE IF NOT EXISTS project_future (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    details TEXT,
    source_agent TEXT NOT NULL,
    source_instance_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_project_future_project_id ON project_future(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_future_project_seq ON project_future(project_id, seq);

-- DOWN
DROP INDEX IF EXISTS uq_project_future_project_seq;
DROP INDEX IF EXISTS ix_project_future_project_id;
DROP TABLE IF EXISTS project_future;

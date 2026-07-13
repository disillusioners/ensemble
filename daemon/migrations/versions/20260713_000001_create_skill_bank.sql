-- Migration: create skill_bank table
-- Created: 2026-07-13
-- Description:
--   Creates the ``skill_bank`` table for the isolated Skill Bank CRUD
--   feature. This is NOT part of the skill evolution system — it is
--   a standalone user-facing template store. No FK to ``skills``.
--
--   NOTE: This .sql migration is a NO-OP on PostgreSQL (the .sql
--   runner skips non-SQLite engines). PostgreSQL table creation is
--   handled by raw DDL in ``daemon/manager.py``
--   ``_ensure_postgres_columns()`` (line 2460). On SQLite, this
--   provides idempotent CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS skill_bank (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'workflow',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_bank_project ON skill_bank(project_id);

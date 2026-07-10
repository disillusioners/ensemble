-- Migration: create skill evolution tables (6 tables)
-- DUAL-DRIVER NOTES:
--   For PostgreSQL: _ensure_postgres_columns() in manager.py handles creation.
--   For SQLite: This migration creates the tables.

-- UP

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'workflow',
    is_active INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    lineage_origin TEXT NOT NULL DEFAULT 'imported',
    generation INTEGER NOT NULL DEFAULT 0,
    ab_test_group TEXT,
    total_selections INTEGER NOT NULL DEFAULT 0,
    total_applied INTEGER NOT NULL DEFAULT 0,
    total_completions INTEGER NOT NULL DEFAULT 0,
    total_fallbacks INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    UNIQUE(project_id, name, generation)
);

CREATE INDEX IF NOT EXISTS idx_skills_project ON skills(project_id);
CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active);
CREATE INDEX IF NOT EXISTS idx_skills_ab_group ON skills(ab_test_group);

CREATE TABLE IF NOT EXISTS skill_lineage (
    skill_id TEXT NOT NULL,
    parent_skill_id TEXT NOT NULL,
    change_summary TEXT NOT NULL DEFAULT '',
    content_diff TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (skill_id, parent_skill_id)
);

CREATE TABLE IF NOT EXISTS skill_usage_records (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_message TEXT,
    selected INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    task_succeeded INTEGER NOT NULL DEFAULT 0,
    iterations INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    fallback INTEGER NOT NULL DEFAULT 0,
    feedback_applied INTEGER,
    feedback_note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_usage_skill ON skill_usage_records(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_usage_instance ON skill_usage_records(instance_id);
CREATE INDEX IF NOT EXISTS idx_skill_usage_applied ON skill_usage_records(instance_id, feedback_applied);

CREATE TABLE IF NOT EXISTS skill_triggers (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    condition_json JSON NOT NULL DEFAULT '{}',
    action TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_embeddings (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    trigger_query TEXT NOT NULL,
    embedding JSON NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_embeddings_skill ON skill_embeddings(skill_id);

CREATE TABLE IF NOT EXISTS skill_ab_tests (
    id TEXT PRIMARY KEY,
    ab_test_group TEXT NOT NULL,
    skill_id_old TEXT NOT NULL,
    skill_id_new TEXT NOT NULL,
    extension_count INTEGER NOT NULL DEFAULT 0,
    comparisons INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    winner_skill_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_skill_ab_tests_group ON skill_ab_tests(ab_test_group);

-- DOWN
DROP TABLE IF EXISTS skill_ab_tests;
DROP TABLE IF EXISTS skill_embeddings;
DROP TABLE IF EXISTS skill_triggers;
DROP TABLE IF EXISTS skill_usage_records;
DROP TABLE IF EXISTS skill_lineage;
DROP TABLE IF EXISTS skills;
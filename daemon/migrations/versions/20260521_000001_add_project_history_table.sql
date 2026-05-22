-- Migration: add project_history table
-- Created: 2026-05-21
-- Author: system
-- Description: Add project_history table for tracking project events, decisions, and learnings

-- UP

CREATE TABLE IF NOT EXISTS project_history (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    entry_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT,
    source_agent TEXT,
    source_instance_id TEXT,
    entry_metadata JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_project_history_project_created
    ON project_history(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_project_history_entry_type
    ON project_history(project_id, entry_type);

-- DOWN

DROP TABLE IF EXISTS project_history;

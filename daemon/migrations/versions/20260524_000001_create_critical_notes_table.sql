-- Migration: create critical_notes table
-- Created: 2026-05-24
-- Author: system
-- Description: Create dedicated critical_notes table and drop JSON column from projects

-- UP

CREATE TABLE IF NOT EXISTS critical_notes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_agent TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    reference TEXT
);

CREATE INDEX IF NOT EXISTS ix_critical_notes_project_id
    ON critical_notes(project_id);

-- Note: SQLite DROP COLUMN requires special handling. The column will be ignored
-- by the application code since it now uses the dedicated critical_notes table.
-- SQLite doesn't require explicit column removal - unused columns have no effect.

-- DOWN

-- SQLite doesn't support adding columns back easily
-- For DOWN, we recreate the column for downgrade compatibility
ALTER TABLE projects ADD COLUMN critical_notes JSON DEFAULT '[]';

DROP TABLE IF EXISTS critical_notes;

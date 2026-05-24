-- Migration: create project_metadata_records table
-- Created: 2026-05-24
-- Author: system
-- Description: Create dedicated project_metadata_records table for key-value metadata

-- UP

CREATE TABLE IF NOT EXISTS project_metadata_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    meta_key TEXT NOT NULL,
    meta_value JSON,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_project_metadata_records_project_id
    ON project_metadata_records(project_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_project_metadata_project_key
    ON project_metadata_records(project_id, meta_key);

-- DOWN

DROP TABLE IF EXISTS project_metadata_records;

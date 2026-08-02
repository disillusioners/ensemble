-- Migration: create Project Blueprint tables (3 tables)
-- DUAL-DRIVER NOTES:
--   For PostgreSQL: SQLModel.metadata.create_all() creates these new tables.
--   For SQLite: This migration creates the tables.

-- UP

CREATE TABLE IF NOT EXISTS project_blueprints (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'area',
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    tags TEXT NOT NULL DEFAULT '[]',
    file_refs TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    embedding_model TEXT,
    source TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_reviewed_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(project_id, slug)
);

CREATE INDEX IF NOT EXISTS ix_project_blueprints_project_id
    ON project_blueprints(project_id);
CREATE INDEX IF NOT EXISTS ix_project_blueprints_kind
    ON project_blueprints(kind);
CREATE INDEX IF NOT EXISTS ix_project_blueprints_status
    ON project_blueprints(status);
CREATE INDEX IF NOT EXISTS ix_project_blueprints_project_kind_active
    ON project_blueprints(project_id, kind, is_active);

CREATE TABLE IF NOT EXISTS project_blueprint_triggers (
    id TEXT PRIMARY KEY,
    blueprint_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    embedding TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (blueprint_id) REFERENCES project_blueprints(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_project_blueprint_triggers_blueprint_id
    ON project_blueprint_triggers(blueprint_id);

CREATE TABLE IF NOT EXISTS project_blueprint_revisions (
    id TEXT PRIMARY KEY,
    blueprint_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    content_snapshot TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    revision_summary TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (blueprint_id) REFERENCES project_blueprints(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_project_blueprint_revisions_blueprint_id
    ON project_blueprint_revisions(blueprint_id);
CREATE INDEX IF NOT EXISTS ix_project_blueprint_revisions_blueprint_version
    ON project_blueprint_revisions(blueprint_id, version);

-- DOWN
DROP TABLE IF EXISTS project_blueprint_revisions;
DROP TABLE IF EXISTS project_blueprint_triggers;
DROP TABLE IF EXISTS project_blueprints;

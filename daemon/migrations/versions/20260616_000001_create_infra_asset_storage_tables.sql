-- Migration: create infra asset storage tables
-- Created: 2026-06-16
-- Author: system
-- Description:
--   Phase 0 (Infra Asset Storage) of the Infrastructure Tool Category.
--   Flexible per-project document model for storing infrastructure
--   resources (compute, networking, storage, services, etc.) with
--   built-in versioning via a history table.
--
--   Three tables back the storage:
--
--     1. infra_asset_types — GLOBAL type registry shared across all
--        projects. No project_id column by design: type definitions
--        are cross-project so the same "vm" or "postgres" schema is
--        reused everywhere.
--
--     2. infra_assets — per-project documents keyed by
--        (project_id, type, name). The document payload lives in two
--        JSONB columns (attributes, relationships) to keep the
--        relational schema stable while the type system evolves.
--        parent_asset_id is a self-referential FK for hierarchy.
--
--     3. infra_asset_history — append-only history of every change.
--        snapshot holds the full pre-change document; change_type,
--        changed_fields, old_values, and new_values give targeted
--        diff metadata for UI rendering and audit.
--
--   JSONB columns use the SQLAlchemy JSONBType TypeDecorator (defined
--   in Phase 1 models) which maps to PostgreSQL JSONB at runtime and
--   JSON/TEXT on SQLite. GIN indexes for JSONB containment queries
--   are added in Phase 1 via SQLModel __table_args__ — they are NOT
--   part of this raw SQL migration (dialect-specific and not needed
--   for the SQLite baseline).
--
--   Schema mirrors daemon/repositories/infra/{models.py} (Phase 1).

-- UP

-- 1. Global type registry (no project_id, shared across all projects)
CREATE TABLE IF NOT EXISTS infra_asset_types (
    name TEXT PRIMARY KEY,
    schema_json JSON NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_infra_asset_types_updated_at
    ON infra_asset_types(updated_at);

-- 2. Main per-project asset storage
CREATE TABLE IF NOT EXISTS infra_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_asset_id TEXT REFERENCES infra_assets(id) ON DELETE SET NULL,
    attributes JSON NOT NULL,
    relationships JSON NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    updated_by TEXT
);

CREATE INDEX IF NOT EXISTS ix_infra_assets_project_id
    ON infra_assets(project_id);

CREATE INDEX IF NOT EXISTS ix_infra_assets_type
    ON infra_assets(type);

CREATE INDEX IF NOT EXISTS ix_infra_assets_parent_asset_id
    ON infra_assets(parent_asset_id);

CREATE INDEX IF NOT EXISTS ix_infra_assets_updated_at
    ON infra_assets(updated_at);

CREATE INDEX IF NOT EXISTS ix_infra_assets_name
    ON infra_assets(name);

CREATE UNIQUE INDEX IF NOT EXISTS uq_infra_assets_project_type_name
    ON infra_assets(project_id, type, name);

-- 3. Append-only history of every change to an asset
CREATE TABLE IF NOT EXISTS infra_asset_history (
    id TEXT PRIMARY KEY,
    -- ``ON DELETE SET NULL`` (not CASCADE) so the ``deleted`` history row
    -- written by ``delete_asset`` survives the asset's removal. The snapshot
    -- column preserves the asset state including its ID, so the row remains
    -- reconstructable. The FK only enforces referential integrity while the
    -- asset still exists. Mirrors ``InfraAssetHistory.asset_id`` in
    -- daemon/repositories/infra/models.py. The column is nullable for the
    -- same reason.
    asset_id TEXT REFERENCES infra_assets(id) ON DELETE SET NULL,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    snapshot JSON,
    change_type TEXT NOT NULL,
    changed_fields JSON,
    old_values JSON,
    new_values JSON,
    changed_by TEXT,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_asset_id
    ON infra_asset_history(asset_id);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_project_id
    ON infra_asset_history(project_id);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_timestamp
    ON infra_asset_history(timestamp);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_change_type
    ON infra_asset_history(change_type);

-- DOWN

-- Drop in reverse dependency order: history → assets → types
DROP INDEX IF EXISTS ix_infra_asset_history_change_type;
DROP INDEX IF EXISTS ix_infra_asset_history_timestamp;
DROP INDEX IF EXISTS ix_infra_asset_history_project_id;
DROP INDEX IF EXISTS ix_infra_asset_history_asset_id;
DROP TABLE IF EXISTS infra_asset_history;

DROP INDEX IF EXISTS uq_infra_assets_project_type_name;
DROP INDEX IF EXISTS ix_infra_assets_name;
DROP INDEX IF EXISTS ix_infra_assets_updated_at;
DROP INDEX IF EXISTS ix_infra_assets_parent_asset_id;
DROP INDEX IF EXISTS ix_infra_assets_type;
DROP INDEX IF EXISTS ix_infra_assets_project_id;
DROP TABLE IF EXISTS infra_assets;

DROP INDEX IF EXISTS ix_infra_asset_types_updated_at;
DROP TABLE IF EXISTS infra_asset_types;

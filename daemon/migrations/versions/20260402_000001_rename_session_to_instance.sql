-- Migration: rename session to instance (table and column names)
-- Created: 2026-04-02
-- Author: system
-- Description: Rename all tables and columns from 'session' to 'instance'
--              to align with the new terminology used in the codebase.
-- 
-- This migration handles:
-- - Table renames: sessions→instances, session_hierarchy→instance_hierarchy, session_mappings→instance_mappings
-- - Column renames: session_id→instance_id, session_metadata→instance_metadata, etc.
-- - Index creation with new names
--
-- Note: SQLite does not support RENAME COLUMN directly, so we use the
-- create-copy-drop-rename pattern for column renames.
--
-- Updated: 2026-04-03 - The migration runner now detects if old schema exists
-- and skips this migration entirely on fresh databases where create_all() has
-- already created tables with new names. This migration only runs when old
-- 'session'-named tables or columns are detected.

-- UP

-- ============================================================================
-- PHASE 1: Rename tables
-- ============================================================================

-- Rename session_hierarchy to instance_hierarchy (no column changes needed)
ALTER TABLE session_hierarchy RENAME TO instance_hierarchy;

-- Rename sessions to instances
-- Note: We need to rename columns session_id→instance_id and session_metadata→instance_metadata
-- SQLite doesn't support RENAME COLUMN, so we use the temp table pattern
PRAGMA foreign_keys=off;

CREATE TABLE instances (
    instance_id TEXT PRIMARY KEY,
    agent_id TEXT,
    agent_dir TEXT NOT NULL,
    agent_name TEXT,
    parent_id TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    instance_metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO instances (instance_id, agent_id, agent_dir, agent_name, parent_id, status, created_at, updated_at, instance_metadata)
SELECT session_id, agent_id, agent_dir, agent_name, parent_id, status, created_at, updated_at, session_metadata
FROM sessions;

DROP TABLE sessions;

PRAGMA foreign_keys=on;

-- Rename session_mappings to instance_mappings
-- Note: We need to rename column agent_session_id→agent_instance_id
PRAGMA foreign_keys=off;

CREATE TABLE instance_mappings (
    mapping_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    agent_instance_id TEXT NOT NULL,
    agent_id TEXT,
    agent_dir TEXT NOT NULL,
    mapping_metadata TEXT DEFAULT '{}',
    last_message_at TEXT,
    created_at TEXT NOT NULL
);

INSERT INTO instance_mappings (mapping_id, source_id, external_user_id, agent_instance_id,
                                agent_id, agent_dir, mapping_metadata, last_message_at,
                                created_at)
SELECT mapping_id, source_id, external_user_id, agent_session_id,
       agent_id, agent_dir, mapping_metadata, last_message_at, created_at
FROM session_mappings;

DROP TABLE session_mappings;

PRAGMA foreign_keys=on;

-- ============================================================================
-- PHASE 2: Rename columns in other tables
-- ============================================================================

-- schedule_executions: session_id → instance_id
PRAGMA foreign_keys=off;

CREATE TABLE schedule_executions_new (
    execution_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    instance_id TEXT,
    status TEXT DEFAULT 'triggered',
    error_message TEXT,
    completed_at TEXT
);

INSERT INTO schedule_executions_new 
SELECT execution_id, schedule_id, triggered_at, session_id, status, 
       error_message, completed_at
FROM schedule_executions;

DROP TABLE schedule_executions;
ALTER TABLE schedule_executions_new RENAME TO schedule_executions;

PRAGMA foreign_keys=on;

-- projects: creator_session_id → creator_instance_id
PRAGMA foreign_keys=off;

CREATE TABLE projects_new (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    project_type TEXT DEFAULT 'general',
    status TEXT DEFAULT 'active',
    main_directory TEXT,
    related_directories TEXT DEFAULT '[]',
    description TEXT,
    job_queue_paused INTEGER DEFAULT 0,
    project_metadata TEXT DEFAULT '{}',
    relationships TEXT DEFAULT '{}',
    creator_instance_id TEXT,
    creator_agent_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO projects_new
SELECT project_id, name, project_type, status, main_directory,
       related_directories, description, job_queue_paused,
       project_metadata, relationships, 
       creator_session_id, creator_agent_id,
       created_at, updated_at
FROM projects;

DROP TABLE projects;
ALTER TABLE projects_new RENAME TO projects;

PRAGMA foreign_keys=on;

-- job_queue_items: session_id → instance_id
PRAGMA foreign_keys=off;

CREATE TABLE job_queue_items_new (
    job_id TEXT PRIMARY KEY,
    agent_id TEXT,
    agent_dir TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT DEFAULT 'api',
    project_id TEXT,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    instance_id TEXT,
    error_message TEXT,
    result_summary TEXT,
    job_metadata TEXT DEFAULT '{}',
    cancelled_at TEXT
);

INSERT INTO job_queue_items_new
SELECT job_id, agent_id, agent_dir, message, source, project_id,
       priority, status, created_at, started_at, completed_at,
       session_id, error_message, result_summary, job_metadata,
       cancelled_at
FROM job_queue_items;

DROP TABLE job_queue_items;
ALTER TABLE job_queue_items_new RENAME TO job_queue_items;

PRAGMA foreign_keys=on;

-- message_queue: session_id → instance_id
PRAGMA foreign_keys=off;

CREATE TABLE message_queue_new (
    message_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT DEFAULT 'api',
    status TEXT DEFAULT 'ready',
    priority INTEGER DEFAULT 1,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 5,
    error_message TEXT,
    message_metadata TEXT DEFAULT '{}',
    enqueued_at TEXT NOT NULL,
    processing_started_at TEXT,
    last_activity_at TEXT,
    completed_at TEXT,
    next_retry_at TEXT
);

INSERT INTO message_queue_new
SELECT message_id, session_id, content, source, status, priority,
       retry_count, max_retries, error_message, message_metadata,
       enqueued_at, processing_started_at, last_activity_at,
       completed_at, next_retry_at
FROM message_queue;

DROP TABLE message_queue;
ALTER TABLE message_queue_new RENAME TO message_queue;

PRAGMA foreign_keys=on;

-- ============================================================================
-- PHASE 3: Recreate indexes with new names
-- ============================================================================

CREATE INDEX IF NOT EXISTS ix_instances_agent_id ON instances(agent_id);
CREATE INDEX IF NOT EXISTS ix_instances_agent_dir ON instances(agent_dir);
CREATE INDEX IF NOT EXISTS ix_instances_agent_name ON instances(agent_name);
CREATE INDEX IF NOT EXISTS ix_instances_parent_id ON instances(parent_id);
CREATE INDEX IF NOT EXISTS ix_instances_status ON instances(status);

CREATE INDEX IF NOT EXISTS ix_instance_mappings_source ON instance_mappings(source_id);
CREATE INDEX IF NOT EXISTS ix_instance_mappings_instance ON instance_mappings(agent_instance_id);
CREATE INDEX IF NOT EXISTS ix_instance_mappings_cleanup ON instance_mappings(last_message_at);

CREATE INDEX IF NOT EXISTS ix_schedule_executions_schedule_id ON schedule_executions(schedule_id);
CREATE INDEX IF NOT EXISTS ix_schedule_executions_instance ON schedule_executions(instance_id);

CREATE INDEX IF NOT EXISTS ix_job_queue_status ON job_queue_items(status);
CREATE INDEX IF NOT EXISTS ix_job_queue_instance ON job_queue_items(instance_id);
CREATE INDEX IF NOT EXISTS ix_job_queue_project ON job_queue_items(project_id);

CREATE INDEX IF NOT EXISTS ix_message_queue_instance ON message_queue(instance_id);
CREATE INDEX IF NOT EXISTS ix_message_queue_status ON message_queue(status);

-- ============================================================================
-- DOWN (rollback)
-- ============================================================================
-- Note: This migration is NOT safely reversible because SQLite doesn't support
-- DROP COLUMN and we dropped original tables. For rollback, restore from backup.

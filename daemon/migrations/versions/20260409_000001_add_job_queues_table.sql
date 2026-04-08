-- Migration: add job queues table
-- Created: 2026-04-09
-- Author: system
-- Description: Create job_queues table for named per-project job queues with
--              system queues seeded for all existing projects.

-- UP

-- STEP 1: Delete all existing jobs (clean slate)
DELETE FROM job_queue_items;

-- STEP 2: Create job_queues table
CREATE TABLE IF NOT EXISTS job_queues (
    queue_id          TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(project_id),
    queue_name        TEXT NOT NULL,
    queue_name_lower  TEXT NOT NULL,
    queue_type        TEXT NOT NULL DEFAULT 'fifo'
                      CHECK(queue_type IN ('fifo', 'parallel')),
    concurrency_limit INTEGER NOT NULL DEFAULT 1,
    is_paused         BOOLEAN NOT NULL DEFAULT 0,
    is_system         BOOLEAN NOT NULL DEFAULT 0,
    description       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(project_id, queue_name_lower)
);

-- STEP 3: Create index on project_id
CREATE INDEX IF NOT EXISTS idx_job_queues_project ON job_queues(project_id);

-- STEP 4: Add queue_id column to job_queue_items
ALTER TABLE job_queue_items ADD COLUMN queue_id TEXT REFERENCES job_queues(queue_id);
CREATE INDEX IF NOT EXISTS idx_job_queue_items_queue ON job_queue_items(queue_id);

-- STEP 5: Seed system queues for all existing projects
-- System FIFO queue (one job at a time)
INSERT INTO job_queues (
    queue_id, project_id, queue_name, queue_name_lower,
    queue_type, concurrency_limit, is_paused, is_system,
    description, created_at, updated_at
)
SELECT 
    'sys-fifo-' || project_id,
    project_id,
    'system_fifo_queue',
    'system_fifo_queue',
    'fifo',
    1,
    0,
    1,
    'System FIFO queue - default, one job at a time',
    datetime('now'),
    datetime('now')
FROM projects;

-- System parallel queue (configurable concurrency)
INSERT INTO job_queues (
    queue_id, project_id, queue_name, queue_name_lower,
    queue_type, concurrency_limit, is_paused, is_system,
    description, created_at, updated_at
)
SELECT 
    'sys-parallel-' || project_id,
    project_id,
    'system_parallel_queue',
    'system_parallel_queue',
    'parallel',
    3,
    0,
    1,
    'System parallel queue - configurable concurrency',
    datetime('now'),
    datetime('now')
FROM projects;

-- DOWN
-- Note: SQLite doesn't support DROP COLUMN, so the queue_id column on job_queue_items will remain.
DROP TABLE IF EXISTS job_queues;

-- Migration: create task table
-- Created: 2026-04-12
-- Author: system
-- Description: Create task table for worker pool tasks.

-- UP

CREATE TABLE IF NOT EXISTS task (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    type                TEXT NOT NULL DEFAULT 'process_message'
                          CHECK(type IN ('process_message', 'send_report', 'cleanup')),
    instance_id         TEXT NOT NULL,
    message_id          TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                          CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    worker_id           TEXT,
    result              TEXT,
    error               TEXT,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT
);

-- Index for efficient polling (status, created_at)
CREATE INDEX IF NOT EXISTS idx_task_status_created ON task(status, created_at);

-- Index on instance_id for instance-specific queries
CREATE INDEX IF NOT EXISTS idx_task_instance ON task(instance_id);

-- Index on worker_id for worker queries
CREATE INDEX IF NOT EXISTS idx_task_worker ON task(worker_id);

-- DOWN
DROP TABLE IF EXISTS task;

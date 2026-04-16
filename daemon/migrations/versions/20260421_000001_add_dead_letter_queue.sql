-- Migration: add dead letter queue table
-- Created: 2026-04-17
-- Description: Create dead_letter_items table for storing failed jobs that exceeded retry limits.

-- UP

-- Create dead_letter_items table
CREATE TABLE IF NOT EXISTS dead_letter_items (
    dlq_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_dir TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL,
    project_id TEXT NOT NULL,
    queue_id TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 5,
    error_message TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    failed_at TEXT NOT NULL,
    moved_to_dlq_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    metadata TEXT
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_dead_letter_job_id ON dead_letter_items(job_id);
CREATE INDEX IF NOT EXISTS idx_dead_letter_project ON dead_letter_items(project_id);
CREATE INDEX IF NOT EXISTS idx_dead_letter_queue ON dead_letter_items(queue_id);

-- DOWN
DROP INDEX IF EXISTS idx_dead_letter_queue;
DROP INDEX IF EXISTS idx_dead_letter_project;
DROP INDEX IF EXISTS idx_dead_letter_job_id;
DROP TABLE IF EXISTS dead_letter_items;

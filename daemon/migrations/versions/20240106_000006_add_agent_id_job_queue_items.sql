-- Migration: add agent_id to job_queue_items
-- Created: 2024-01-06 (retrospective)
-- Author: system
-- Description: Add agent_id column to job_queue_items table (new table name), populating from agent_dir

-- UP
-- Add agent_id column (populated from agent_dir by the migration runner)
ALTER TABLE job_queue_items ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
-- To rollback, recreate table without the column or use a no-op approach
-- Note: This migration also populates existing rows - rolling back loses that data

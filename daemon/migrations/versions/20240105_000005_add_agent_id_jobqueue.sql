-- Migration: add agent_id to jobqueue (legacy table)
-- Created: 2024-01-05 (retrospective)
-- Author: system
-- Description: Add agent_id column to jobqueue table (legacy table name), populating from agent_dir

-- UP
-- Add agent_id column (populated from agent_dir by the migration runner)
ALTER TABLE jobqueue ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
-- To rollback, recreate table without the column or use a no-op approach
-- Note: This migration also populates existing rows - rolling back loses that data

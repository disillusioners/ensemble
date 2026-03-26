-- Migration: add agent_id to session_mappings
-- Created: 2024-01-04 (retrospective)
-- Author: system
-- Description: Add agent_id column to session_mappings table, populating from agent_dir

-- UP
-- Add agent_id column (populated from agent_dir by the migration runner)
ALTER TABLE session_mappings ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
-- To rollback, recreate table without the column or use a no-op approach
-- Note: This migration also populates existing rows - rolling back loses that data

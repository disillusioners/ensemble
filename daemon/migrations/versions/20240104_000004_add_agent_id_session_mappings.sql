-- Migration: add agent_id to instance_mappings (originally session_mappings)
-- Created: 2024-01-04 (retrospective)
-- Author: system
-- Description: Add agent_id column to instance_mappings table, populating from agent_dir
-- Updated: 2026-04-03 - Updated table name from 'session_mappings' to 'instance_mappings' after session→instance rename

-- UP
-- Add agent_id column (populated from agent_dir by the migration runner)
ALTER TABLE instance_mappings ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
-- To rollback, recreate table without the column or use a no-op approach
-- Note: This migration also populates existing rows - rolling back loses that data

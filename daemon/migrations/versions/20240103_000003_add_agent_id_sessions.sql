-- Migration: add agent_id to instances (originally sessions)
-- Created: 2024-01-03 (retrospective)
-- Author: system
-- Description: Add agent_id column to instances table, populating from agent_dir
-- Updated: 2026-04-03 - Updated table name from 'sessions' to 'instances' after session→instance rename

-- UP
-- Add agent_id column (populated from agent_dir by the migration runner)
-- Handles both old table name (sessions) and new table name (instances)
ALTER TABLE instances ADD COLUMN agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
-- To rollback, recreate table without the column or use a no-op approach
-- Note: This migration also populates existing rows - rolling back loses that data

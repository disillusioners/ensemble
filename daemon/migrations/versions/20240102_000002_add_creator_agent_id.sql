-- Migration: add creator_agent_id to projects
-- Created: 2024-01-02 (retrospective)
-- Author: system
-- Description: Add creator_agent_id column to track which agent created the project

-- UP
ALTER TABLE projects ADD COLUMN creator_agent_id TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
-- To rollback, recreate table without the column or use a no-op approach
-- This migration only adds a nullable column, so DOWN is a no-op

-- Migration: add job_queue_paused to projects
-- Created: 2024-01-01 (retrospective)
-- Author: system
-- Description: Add job_queue_paused column to projects table for pausing job queues

-- UP
ALTER TABLE projects ADD COLUMN job_queue_paused BOOLEAN DEFAULT 0;

-- DOWN
-- SQLite does not support DROP COLUMN
-- To rollback, recreate table without the column or use a no-op approach
-- This migration only adds a nullable column with a default, so DOWN is a no-op

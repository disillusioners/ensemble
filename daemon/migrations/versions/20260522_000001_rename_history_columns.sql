-- Migration: rename project_history columns recorded_by_agent and recorded_by_instance
-- Created: 2026-05-22
-- Author: system
-- Description: Rename columns in project_history table to match updated model.
--              recorded_by_agent → source_agent
--              recorded_by_instance → source_instance_id
--
-- Idempotent: If columns were already renamed (fresh DBs created with correct
-- names from create_all()), the ALTER statements will fail with "no such column"
-- and the runner will skip them gracefully and mark migration as applied.

-- UP

-- Rename recorded_by_agent → source_agent
ALTER TABLE project_history RENAME COLUMN recorded_by_agent TO source_agent;

-- Rename recorded_by_instance → source_instance_id
ALTER TABLE project_history RENAME COLUMN recorded_by_instance TO source_instance_id;

-- DOWN

-- Rename back: source_agent → recorded_by_agent
ALTER TABLE project_history RENAME COLUMN source_agent TO recorded_by_agent;

-- Rename back: source_instance_id → recorded_by_instance
ALTER TABLE project_history RENAME COLUMN source_instance_id TO recorded_by_instance;

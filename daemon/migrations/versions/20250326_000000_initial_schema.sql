-- Migration: initial schema baseline
-- Created: 2025-03-26
-- Author: system
-- Description: Baseline migration capturing initial schema state

-- UP
-- This is the baseline - tables are created via SQLModel.metadata.create_all()
-- This migration records that we've captured the initial state

-- DOWN
-- Drop all tables (rarely needed for baseline)
DROP TABLE IF EXISTS schema_migrations;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS projects;

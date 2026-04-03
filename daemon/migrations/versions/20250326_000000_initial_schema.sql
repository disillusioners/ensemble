-- Migration: initial schema baseline
-- Created: 2025-03-26
-- Author: system
-- Description: Baseline migration capturing initial schema state
-- Updated: 2026-04-03 - Updated table names after session→instance rename

-- UP
-- This is the baseline - tables are created via SQLModel.metadata.create_all()
-- This migration records that we've captured the initial state

-- DOWN
-- Drop all tables (order matters for foreign key constraints)
-- Drop tables with foreign keys first, then independent tables
DROP TABLE IF EXISTS instance_mappings;
DROP TABLE IF EXISTS schedule_executions;
DROP TABLE IF EXISTS project_tags;
DROP TABLE IF EXISTS project_shortnames;
DROP TABLE IF EXISTS instances;
DROP TABLE IF EXISTS instance_hierarchy;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS source_configs;
DROP TABLE IF EXISTS processed_external_messages;
DROP TABLE IF EXISTS job_queue_items;
DROP TABLE IF EXISTS message_queue;
DROP TABLE IF EXISTS schema_migrations;

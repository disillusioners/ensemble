-- Migration: add autostart column to source_configs table
-- Created: 2026-06-14
-- Description: Add autostart flag controlling whether a source auto-starts
--              when the service boots (delayed by 1 minute).

-- UP

ALTER TABLE source_configs ADD COLUMN autostart BOOLEAN DEFAULT TRUE;

-- DOWN

-- SQLite cannot DROP COLUMN cleanly before 3.35; leave it as-is on rollback.

-- Migration: enhance instance for worker pool
-- Created: 2026-04-12
-- Author: system
-- Description: Add columns for worker pool support to instances table.
--              - version (optimistic locking)
--              - children (denormalized cache)
--              - waiting_for (pending children count)
--              - last_activity_at (watchdog timeout)

-- UP

-- Add version column (optimistic locking)
ALTER TABLE instances ADD COLUMN version INTEGER DEFAULT 1;

-- Add children column (denormalized cache of child instance IDs)
ALTER TABLE instances ADD COLUMN children TEXT DEFAULT '[]';

-- Add waiting_for column (count of pending children)
ALTER TABLE instances ADD COLUMN waiting_for INTEGER DEFAULT 0;

-- Add last_activity_at column (for watchdog timeout)
ALTER TABLE instances ADD COLUMN last_activity_at TEXT;

-- DOWN
-- Note: SQLite doesn't support DROP COLUMN, so DOWN is a no-op.

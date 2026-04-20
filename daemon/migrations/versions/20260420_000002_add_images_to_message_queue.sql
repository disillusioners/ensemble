-- Migration: add images column to message_queue
-- Created: 2026-04-20
-- Description: Add images JSON column to message_queue for vision/image support.

-- UP
ALTER TABLE message_queue ADD COLUMN images JSON DEFAULT NULL;

-- DOWN
-- Note: SQLite doesn't support DROP COLUMN before 3.35.0
-- For older SQLite, the column will remain but be unused

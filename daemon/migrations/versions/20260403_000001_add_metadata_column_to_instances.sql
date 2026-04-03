-- Migration: add metadata column to instances
-- Created: 2026-04-03
-- Author: system
-- Description: Add missing 'metadata' column to instances table.
--              The SQLModel Instance.instance_metadata field uses sa_column=Column("metadata", JSON)
--              but the rename_session_to_instance migration created it as 'instance_metadata'.
--              This migration adds the 'metadata' column if it doesn't exist.

-- UP

ALTER TABLE instances ADD COLUMN metadata TEXT DEFAULT '{}';

-- DOWN
-- Note: SQLite doesn't support DROP COLUMN, so DOWN is a no-op.
-- To rollback, you would need to drop and recreate the table.

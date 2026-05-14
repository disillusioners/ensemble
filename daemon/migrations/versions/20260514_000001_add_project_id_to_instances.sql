-- Migration: add project_id column to instances table
-- Created: 2026-05-14
-- Author: system
-- Description: Add project_id column to instances table with backfill from metadata JSON

-- UP

ALTER TABLE instances ADD COLUMN project_id VARCHAR;

-- Backfill from existing metadata JSON (idempotent: only affects rows with valid project_id)
UPDATE instances SET project_id = json_extract(metadata, '$.project_id')
WHERE json_extract(metadata, '$.project_id') IS NOT NULL;

-- DOWN

ALTER TABLE instances DROP COLUMN project_id;

-- Migration: rename critical_experience column to critical_notes
-- Created: 2026-05-23
-- Author: system
-- Description: Rename critical_experience column to critical_notes in projects table to match updated model naming.
--
-- Idempotent: If column was already renamed (fresh DBs created with correct
-- name from create_all()), the ALTER statement will fail with "no such column"
-- and the runner will skip it gracefully and mark migration as applied.

-- UP

ALTER TABLE projects RENAME COLUMN critical_experience TO critical_notes;

-- DOWN

ALTER TABLE projects RENAME COLUMN critical_notes TO critical_experience;

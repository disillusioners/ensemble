-- Migration: add critical_experience column to projects table
-- Created: 2026-05-20
-- Author: system
-- Description: Add critical_experience JSON column to projects table for storing concise, high-value knowledge entries

-- UP

ALTER TABLE projects ADD COLUMN critical_experience TEXT DEFAULT '[]';

-- DOWN

ALTER TABLE projects DROP COLUMN critical_experience;

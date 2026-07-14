-- Migration: add auto_load and source_skill_bank_id columns to skills table
-- Created: 2026-07-14
-- Phase 2 of tester-skill-evolution: clone-side counterpart of the
-- skill_bank template flags. ``auto_load`` is the per-skill loader
-- flag (loaded into system prompt before every task vs on-demand).
-- ``source_skill_bank_id`` is the soft FK back to the skill_bank
-- template this row was cloned from (NULL for manually-created or
-- evolved skills — soft FK only, never enforced at the DB level).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every startup.
--   - PostgreSQL: equivalent idempotent statements live in
--     ``daemon/manager.py::_ensure_postgres_columns`` because the .sql runner
--     is a NO-OP for non-SQLite (runner.py lines 446-448).
--   - Fresh databases of either flavor get the columns automatically via
--     ``SQLModel.metadata.create_all`` from the Skill model
--     (``daemon/repositories/skill/models.py``).
--
--   - Booleans are stored as INTEGER NOT NULL DEFAULT 0 on SQLite (SQLite
--     has no native BOOLEAN type — the convention is 0/1). On PostgreSQL
--     the equivalent column is BOOLEAN NOT NULL DEFAULT false.
--   - ``source_skill_bank_id`` is nullable TEXT (soft FK).
--   - ix_skills_auto_load matches the model __table_args__ index name so
--     both dialects converge on a single index.

-- UP

ALTER TABLE skills ADD COLUMN auto_load INTEGER NOT NULL DEFAULT 0;
ALTER TABLE skills ADD COLUMN source_skill_bank_id TEXT;
CREATE INDEX IF NOT EXISTS ix_skills_auto_load ON skills(auto_load);

-- DOWN
-- WARNING: drops the two columns and the index. Any data in
-- auto_load / source_skill_bank_id is permanently lost.
-- DROP INDEX IF EXISTS ix_skills_auto_load;
-- ALTER TABLE skills DROP COLUMN source_skill_bank_id;
-- ALTER TABLE skills DROP COLUMN auto_load;
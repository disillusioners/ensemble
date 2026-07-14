-- Migration: add template_version, agent_id, and auto_load columns to skill_bank
-- Created: 2026-07-14
-- Phase 2 of tester-skill-evolution: schema evolution for the skill bank
-- template versioning + agent_id scoping + auto_load flag propagation.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every startup.
--   - PostgreSQL: equivalent idempotent statements live in
--     ``daemon/manager.py::_ensure_postgres_columns`` because the .sql runner
--     is a NO-OP for non-SQLite (runner.py lines 446-448).
--   - Fresh databases of either flavor get the columns automatically via
--     ``SQLModel.metadata.create_all`` from the SkillBankItem model
--     (``daemon/repositories/skill/models.py``).
--
--   - Booleans are stored as INTEGER NOT NULL DEFAULT 0 on SQLite (SQLite
--     has no native BOOLEAN type — the convention is 0/1). On PostgreSQL
--     the equivalent column is BOOLEAN NOT NULL DEFAULT false.
--   - ``agent_id`` is nullable TEXT — ``NULL`` means generic/shared template.
--   - ``template_version`` is non-null TEXT with default ``'1.0.0'`` so any
--     pre-existing row backfills to a known sentinel.

-- UP

ALTER TABLE skill_bank ADD COLUMN template_version TEXT NOT NULL DEFAULT '1.0.0';
ALTER TABLE skill_bank ADD COLUMN agent_id TEXT;
ALTER TABLE skill_bank ADD COLUMN auto_load INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_skill_bank_agent_id ON skill_bank(agent_id);

-- DOWN
-- WARNING: drops the three columns and the index. Any data in
-- template_version / agent_id / auto_load is permanently lost.
-- ALTER TABLE skill_bank DROP INDEX IF EXISTS ix_skill_bank_agent_id;
-- ALTER TABLE skill_bank DROP COLUMN auto_load;
-- ALTER TABLE skill_bank DROP COLUMN agent_id;
-- ALTER TABLE skill_bank DROP COLUMN template_version;
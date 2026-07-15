-- Migration: add ab_test_group and superseded columns + 2 indexes to skill_usage_records
-- Created: 2026-07-15
-- Phase: Skill-worker milestone prerequisite. ``ab_test_group`` enables
-- A/B test period isolation (NULL = not under test). ``superseded`` marks
-- usage records as superseded when a worker is reused with a new skill
-- (excluded from completion-rate aggregation, retained for audit).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every startup.
--   - PostgreSQL: equivalent idempotent statements live in
--     ``daemon/manager.py::_ensure_postgres_columns`` because the .sql runner
--     is a NO-OP for non-SQLite (runner.py lines 446-448).
--   - Fresh databases of either flavor get the columns automatically via
--     ``SQLModel.metadata.create_all`` from the SkillUsageRecord model
--     (``daemon/repositories/skill/models.py``).
--
--   - Booleans are stored as INTEGER NOT NULL DEFAULT 0 on SQLite (SQLite
--     has no native BOOLEAN type — the convention is 0/1). On PostgreSQL
--     the equivalent column is BOOLEAN NOT NULL DEFAULT false.
--   - ``ab_test_group`` is nullable TEXT — NULL means "not under test".
--     matches the Python Optional[str] type on the model.
--   - Index names match the model ``__table_args__`` declaration exactly so
--     both dialects converge on a single index.

-- UP

ALTER TABLE skill_usage_records ADD COLUMN ab_test_group TEXT;
ALTER TABLE skill_usage_records ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_skill_usage_records_ab_group ON skill_usage_records(ab_test_group);
CREATE INDEX IF NOT EXISTS ix_skill_usage_records_skill_created ON skill_usage_records(skill_id, created_at);

-- DOWN
-- WARNING: drops the two columns and the indexes. Any data in
-- ab_test_group / superseded is permanently lost.
-- DROP INDEX IF EXISTS ix_skill_usage_records_skill_created;
-- DROP INDEX IF EXISTS ix_skill_usage_records_ab_group;
-- ALTER TABLE skill_usage_records DROP COLUMN superseded;
-- ALTER TABLE skill_usage_records DROP COLUMN ab_test_group;
-- Migration: add feedback_usefulness and feedback_improvement columns to skill_usage_records
-- Created: 2026-07-21
-- Phase: skill_feedback usefulness + improvement scoring. ``feedback_usefulness``
-- is an INTEGER holding the agent-judged quality score 1-10 (NULL = not
-- recorded). ``feedback_improvement`` is a TEXT column for actionable
-- suggestions about the skill content itself (distinct from
-- ``feedback_note`` which is general context observation). Together they
-- feed the skill-keeper evolution loop and the per-skill usefulness rollup.
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
--   - ``feedback_usefulness`` is nullable INTEGER — matches the Python
--     Optional[int] type on the model. No DEFAULT clause so a NULL is
--     preserved on existing rows (an explicit 0 would corrupt the
--     "not recorded" signal that the rollup distinguishes from "rated 0").
--   - ``feedback_improvement`` is nullable TEXT — matches the Python
--     Optional[str] type on the model. No DEFAULT clause for the same
--     reason.

-- UP

ALTER TABLE skill_usage_records ADD COLUMN feedback_usefulness INTEGER;
ALTER TABLE skill_usage_records ADD COLUMN feedback_improvement TEXT;

-- DOWN
-- WARNING: drops the two columns. Any data in feedback_usefulness /
-- feedback_improvement is permanently lost.
-- ALTER TABLE skill_usage_records DROP COLUMN feedback_improvement;
-- ALTER TABLE skill_usage_records DROP COLUMN feedback_usefulness;

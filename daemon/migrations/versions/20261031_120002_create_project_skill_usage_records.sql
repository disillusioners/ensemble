-- Migration: create the project_skill_usage_records companion table (D2)
-- Created: 2026-10-31
-- Phase: Phase 1 of the project-skill-source-loader commission
--        (branch feature/project-skill-loader, base b56444244).
--
-- What lives here:
--   * ``project_skill_usage_records`` — usage metrics rows for FILE
--     skills under their ``pf-`` pseudo-ids: the column set of
--     ``skill_usage_records`` minus the ``ab_test_group`` pair (file
--     skills never enter A/B — P7; the duck-typed object carries
--     ``ab_test_group=None`` which skips the A/B branch) and minus THE
--     FK.
--
-- WHY THIS TABLE EXISTS (the D2 census fork — leader ruling D2 +
-- architect A2, decided BY CENSUS EVIDENCE in Phase 1):
--   The static write census
--   (.agents/shared/planning/project-skill-loader/d2-write-census.md)
--   found the file path RIDES the pre-existing injection-tracking write:
--   ``inject_skills``/STEP-0 return ``injected_skill_ids`` (pf- ids for
--   file skills), the caller merges them into
--   ``instance_metadata["last_injected_skill_ids"]``
--   (daemon/services/instance_messaging.py, _dedup_merge_skill_ids),
--   and ``SkillMetricsService.record_task_completion`` iterates those
--   ids at task completion. The usage-ROW insert itself is guarded
--   (``_record_one`` returns 0 on the skill-row miss — a pf- id has no
--   ``skills`` row), so today the usage signal for file skills is
--   silently dropped. The census rule — the file path performs ZERO
--   writes into FK-constrained child tables — HOLDS (verified; the
--   skill_usage_records FK would reject a pf- row anyway), and the fork
--   default applied: a usage write EXISTS on the file path ⇒ the
--   companion table keeps D-Q5's metrics value ("which skills agents
--   actually use" is exactly the signal a git-PR evolution decision
--   needs). The Phase-3 feedback surface writes HERE, never into
--   ``skill_usage_records``.
--
-- ROLLBACK POSTURE (standing-rule adjudication input):
--   Additive CREATE TABLE IF NOT EXISTS + indexes; NO FK, no ALTER/DROP;
--   zero references in the pre-lane tree — inert post-rollback (the
--   board/notify_audit class).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): one .sql per table (house rule) — the
--     persistent record alongside
--     daemon/repositories/project_skill_file/models.py (A10: the
--     pre-create_all model registration is the operative fresh-PG
--     artifact; registration lands in Phase 4).
--   - PostgreSQL: schema from SQLModel.metadata.create_all; the PG-side
--     runner is a NO-OP for this file.
--   - ``feedback_applied`` is a NULLABLE BOOLEAN (NULL = not yet
--     recorded) — same semantics as the legacy table.
--
-- RUNTIME-SPLITTER NOTE (defensive): semicolons only in full-line
--   comments (stripped before the runner's split).

-- UP

CREATE TABLE IF NOT EXISTS project_skill_usage_records (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_message TEXT,
    selected BOOLEAN NOT NULL DEFAULT 0,
    applied BOOLEAN NOT NULL DEFAULT 0,
    task_succeeded BOOLEAN NOT NULL DEFAULT 0,
    iterations INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    fallback BOOLEAN NOT NULL DEFAULT 0,
    feedback_applied BOOLEAN,
    feedback_note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_psur_skill_id
    ON project_skill_usage_records (skill_id);

CREATE INDEX IF NOT EXISTS idx_psur_instance_id
    ON project_skill_usage_records (instance_id);

CREATE INDEX IF NOT EXISTS idx_psur_instance_feedback
    ON project_skill_usage_records (instance_id, feedback_applied);

CREATE INDEX IF NOT EXISTS idx_psur_skill_created
    ON project_skill_usage_records (skill_id, created_at);

-- DOWN

DROP INDEX IF EXISTS idx_psur_skill_created;
DROP INDEX IF EXISTS idx_psur_instance_feedback;
DROP INDEX IF EXISTS idx_psur_instance_id;
DROP INDEX IF EXISTS idx_psur_skill_id;
DROP TABLE IF EXISTS project_skill_usage_records;

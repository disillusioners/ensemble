-- Migration: create the project_skill_embeddings vector table (A1)
-- Created: 2026-10-31
-- Phase: Phase 1 of the project-skill-source-loader commission
--        (branch feature/project-skill-loader, base b56444244).
--
-- What lives here:
--   * ``project_skill_embeddings`` — the DEDICATED vector home for file
--     skills: one row per (pf- pseudo-id, generated trigger query), the
--     column shape of the legacy skill_embeddings minus the FK.
--
-- WHY A DEDICATED TABLE (the frozen A1 ruling, architect 2026-10-31):
--   The originally planned storage vehicle — reusing ``skill_embeddings``
--   with ``skill_id = pf-<sha16>`` — is structurally broken on
--   PostgreSQL: ``skill_embeddings.skill_id`` carries
--   ``ForeignKey("skills.id", ondelete="CASCADE")``
--   (daemon/repositories/skill/models.py), and on PG the constraint is
--   materialized STRICTLY (create_all runs before any column-ensure
--   pass), so EVERY ``pf-`` insert raises ForeignKeyViolation. The
--   per-row catch in the embedding service would swallow it into
--   ``written=0`` — file skills would be silently, PERMANENTLY BM25-only
--   on fresh installs and remote replicas, plus retry/log churn. The
--   rejected alternative (dropping the FK from skill_embeddings) was
--   refused: schema mutation of an evolution-lane table, breaks rollback
--   posture, diverges fresh vs long-lived DBs. THE ABSENCE OF THE FK
--   HERE IS THE POINT — the real-PG integration pack pins the legacy
--   wall RED (a pf- insert into skill_embeddings is REJECTED) as
--   documentation of this ruling.
--
-- WHY NO per-skill UNIQUE KEY: rows are (trigger_query, vector) pairs;
--   regeneration follows A4 delete-AFTER-success (replace-in-one-
--   transaction) so duplicates cannot accumulate; the opaqueness of the
--   hash is why orphan cleanup is the A4 gc sweep (skill_id NOT IN
--   project_skill_files), not a per-project LIKE pattern.
--
-- ROLLBACK POSTURE (standing-rule adjudication input):
--   Additive CREATE TABLE IF NOT EXISTS + index; NO FK, no ALTER/DROP;
--   zero references in the pre-lane tree — inert post-rollback (old
--   binary has no model; the board/notify_audit class).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): one .sql per table (house rule) — the
--     persistent record alongside
--     daemon/repositories/project_skill_file/models.py (A10: the
--     pre-create_all model registration is the operative fresh-PG
--     artifact; registration lands in Phase 4).
--   - PostgreSQL: schema from SQLModel.metadata.create_all; the
--     ``embedding`` column is the JSONBType JSON-array storage (same
--     shape as the legacy table — plain JSON array of floats, never
--     BYTEA/numpy/pickle). The PG-side runner is a NO-OP here.
--
-- RUNTIME-SPLITTER NOTE (defensive): semicolons only in full-line
--   comments (stripped before the runner's split).

-- UP

CREATE TABLE IF NOT EXISTS project_skill_embeddings (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    trigger_query TEXT NOT NULL,
    embedding JSON NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pse_skill_id
    ON project_skill_embeddings (skill_id);

-- DOWN

DROP INDEX IF EXISTS idx_pse_skill_id;
DROP TABLE IF EXISTS project_skill_embeddings;

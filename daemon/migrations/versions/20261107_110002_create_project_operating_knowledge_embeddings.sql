-- Migration: create the project_operating_knowledge_embeddings table
-- (POK Phase 1, T1 — the ranking-only vector companion)
-- Created: 2026-11-07
-- Phase: Phase 1 core surface of the project-operating-knowledge (POK)
--        commission (Emperor GO 2026-11-07; branch
--        feature/project-operating-knowledge, base 6b571771d).
--
-- What lives here:
--   * ``project_operating_knowledge_embeddings`` — one row per POK
--     entry id, holding the entry's query-embedding vector for the
--     BM25→vector fusion re-rank. RANKING-ONLY (arch §4.1/§4.6): the
--     row is NEVER authoritative content; a missing row degrades that
--     entry to BM25-only scoring with the lag flag surfaced
--     (AC14) — the lazy-mint pipeline replenishes it. Same shape and
--     contract as the critical-notes / project-skill embeddings
--     companions (plain JSON array of floats, never BYTEA/numpy/
--     pickle).
--
-- WHY NO FOREIGN KEY: mirrors the main table (§4.1 additive-only
--   posture) and the ``project_skill_embeddings`` precedent (A1) —
--   the absence keeps the table rollback-inert and lets entries be
--   re-embedded without constraint churn. Orphan rows are harmless
--   (the join filters on live entry ids) and the delete path removes
--   them with the entry in the same transaction.
--
-- ROLLBACK POSTURE (standing-rule adjudication; recorded in the
--   commit message per readiness constraint 3): additive-only CREATE
--   TABLE IF NOT EXISTS + index; NO FK, no ALTER/DROP; zero
--   references in the pre-lane tree — inert post-rollback.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): one .sql per table (house rule).
--   - PostgreSQL: schema from SQLModel.metadata.create_all (the
--     pre-create_all registration in ``daemon/manager.py`` is the
--     operative fresh-PG artifact, B7). The PG-side runner is a
--     NO-OP here.
--
-- RUNTIME-SPLITTER NOTE (defensive): semicolons only in full-line
--   comments (stripped before the runner's split).

-- UP

CREATE TABLE IF NOT EXISTS project_operating_knowledge_embeddings (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    embedding JSON NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_poke_entry_id
    ON project_operating_knowledge_embeddings (entry_id);

-- DOWN

-- Never executes (runner is SQLite-only; PG tables arrive via
-- create_all). Kept for house symmetry — see the main table's DOWN
-- note for the full rollback-inert adjudication.
DROP INDEX IF EXISTS idx_poke_entry_id;
DROP TABLE IF EXISTS project_operating_knowledge_embeddings;

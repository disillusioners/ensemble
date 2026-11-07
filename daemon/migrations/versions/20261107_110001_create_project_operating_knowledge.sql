-- Migration: create the project_operating_knowledge table (POK Phase 1, T1)
-- Created: 2026-11-07
-- Phase: Phase 1 core surface of the project-operating-knowledge (POK)
--        commission (Emperor GO 2026-11-07; branch
--        feature/project-operating-knowledge, base 6b571771d).
--
-- What lives here:
--   * ``project_operating_knowledge`` — the DB-canon store of ordered
--     operating procedures per project (architecture-recommendation
--     v3.4 §4.1, column set VERBATIM). Every row is one fenced,
--     single-paragraph fact; the UNIQUE PARTIAL INDEX
--     ``uq_pok_active_slug`` makes a dual-active slug STRUCTURALLY
--     impossible — append-with-supersede is the only mutation path.
--
-- AUDIENCE DIMENSION (Emperor ruling ER-1, 2026-11-07):
--   ``audience`` ∈ {operation, implementation, both} — stated at
--   capture (default ``operation``), curator-adjustable via
--   ``pok_curate``. ``audience_agents`` (nullable) is the curator-set
--   NARROW override: when present it REPLACES the default audience
--   set for ACTIVE injection. The dimension gates ACTIVE injection
--   ONLY — retrieval (``pok_query``) is UNIVERSAL (ER-1.4: reading is
--   free; audience never filters a read).
--
-- WHY NO FOREIGN KEY (design §4.1 / readiness constraint 1):
--   ``project_id`` is a plain TEXT column with NO FK and the table
--   references NO other table. Combined with the additive-only body
--   below this makes the migration ROLLBACK-INERT (see the standing
--   rollback-safety adjudication in the commit message and in
--   ``20261107_110002_create_project_operating_knowledge_embeddings.sql``).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): one .sql per table (house rule) — the
--     persistent record alongside
--     daemon/repositories/pok/models.py. SQLite supports partial
--     indexes natively (WHERE clause below).
--   - PostgreSQL: schema from SQLModel.metadata.create_all (the
--     pre-create_all model registration in ``daemon/manager.py`` is
--     the operative fresh-PG artifact, B7). The PG-side runner is a
--     NO-OP here.
--
-- CHECK-CLOSED VOCABULARIES (mirrored in BOTH this file AND the
-- SQLModel tables — readiness constraint 1; keep both sides in sync):
--   domain    ∈ artifact|deploy|verify|environment|catalog|other
--   audience  ∈ operation|implementation|both   [Emperor ruling]
--   provenance ∈ declared|observed
--   status    ∈ active|superseded|archived     (archived REACHABLE
--                                               via pok_curate only)
--
-- RUNTIME-SPLITTER NOTE (defensive): semicolons only in full-line
--   comments (stripped before the runner's split).

-- UP

CREATE TABLE IF NOT EXISTS project_operating_knowledge (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'other'
        CHECK (domain IN ('artifact', 'deploy', 'verify', 'environment', 'catalog', 'other')),
    audience TEXT NOT NULL DEFAULT 'operation'
        CHECK (audience IN ('operation', 'implementation', 'both')),
    audience_agents TEXT,
    body TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT 'observed'
        CHECK (provenance IN ('declared', 'observed')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'archived')),
    disputed BOOLEAN NOT NULL DEFAULT FALSE,
    superseded_by_id TEXT,
    content_sha256 TEXT NOT NULL,
    created_by_agent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_verified TEXT,
    verify_count INTEGER NOT NULL DEFAULT 0,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT,
    pinned BOOLEAN NOT NULL DEFAULT FALSE
);

-- The structurally-impossible-dual-active guard (§4.1): ONE active row
-- per (project_id, slug). Append-with-supersede flips the prior active
-- row to 'superseded' in the SAME transaction, so the predicate
-- status='active' keeps exactly one survivor per slug.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pok_active_slug
    ON project_operating_knowledge (project_id, slug)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_pok_project_id
    ON project_operating_knowledge (project_id);

CREATE INDEX IF NOT EXISTS idx_pok_project_status
    ON project_operating_knowledge (project_id, status);

-- DOWN

-- ROLLBACK-INERT adjudication (standing rule; recorded per readiness
-- constraint 3): additive-only CREATE TABLE/INDEX IF NOT EXISTS, no
-- FK, zero references anywhere in the pre-lane tree — an old binary
-- rolled back onto this schema neither reads nor writes these tables
-- (the board/notify_audit class). The DOWN section never executes in
-- production: the migration runner is SQLite-only and PG tables
-- arrive via create_all (no alembic versioning on PG). Kept for
-- house symmetry with the other lane migrations.
DROP INDEX IF EXISTS idx_pok_project_status;
DROP INDEX IF EXISTS idx_pok_project_id;
DROP INDEX IF EXISTS uq_pok_active_slug;
DROP TABLE IF EXISTS project_operating_knowledge;

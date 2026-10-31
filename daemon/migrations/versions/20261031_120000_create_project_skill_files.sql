-- Migration: create the project_skill_files derived-index-metadata table
-- Created: 2026-10-31
-- Phase: Phase 1 of the project-skill-source-loader commission
--        (branch feature/project-skill-loader, base b56444244).
--
-- What lives here:
--   * ``project_skill_files`` — ONE row per indexed file skill: identity
--     (pseudo-id PK + A3 natural key), digests, health (valid/last_error),
--     embedding state (embedded_at), index timestamps. DERIVED METADATA
--     ONLY — there is deliberately NO body column (P1: files are canon,
--     DB is a view; bodies are read from disk at call time and the P1
--     sentinel test greps every skill table to zero body bytes).
--
-- WHY THE NATURAL KEY CARRIES root_path (A3, architect 2026-10-31):
--   Two roots can bind to one project (live today via
--   ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS). Keying on
--   (project_id, namespace, name) alone would make the second root's
--   scan last-writer-wins over the first AND share embedding rows
--   (stage-2 vectors computed from root A's body served to root B
--   callers). The unique key is therefore
--   (root_path, project_id, namespace, name); the PK id is the
--   pf-<sha256(root_path|project_id|ns|name)[:16]> pseudo-id
--   (per-host-per-root index identity — D-Q5/A3). The human address
--   pf:<ns>:<name> is UNCHANGED and disambiguated at resolve time by
--   the caller's root.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path SQLModel.metadata.create_all runs FIRST and the
--   migration runner applies this file IMMEDIATELY AFTER it. A bare
--   CREATE would raise "already exists" on every boot.
--
-- ROLLBACK POSTURE (standing-rule adjudication input):
--   Additive CREATE TABLE IF NOT EXISTS + indexes; NO FK, no ALTER/DROP;
--   zero references anywhere in the pre-lane tree (grep
--   project_skill_files at the base = 0 hits) — the old binary has no
--   model for the table, so it is INERT post-rollback (the
--   board/notify_audit adjudication class).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup. One .sql per table — the house two-artifact rule (this
--     file + the SQLModel in
--     daemon/repositories/project_skill_file/models.py). The OPERATIVE
--     fresh-PG artifact is the model registration BEFORE create_all
--     (A10) — the manager-side registration lands in Phase 4; until
--     then nothing in the boot path reads this table.
--   - PostgreSQL: schema from SQLModel.metadata.create_all with the
--     model as the canonical source. Brand-new table — NO
--     _ensure_postgres_columns mirror required for creation (the belt
--     mirror entry is a Phase-4 deliverable). The PG-side runner is a
--     NO-OP for this file.
--   - Every string column is TEXT on both drivers — never VARCHAR.
--     ``valid`` is BOOLEAN (create_all owns a brand-new table's DDL on
--     PG; SQLite stores 0/1 under NUMERIC affinity — identical reads).
--
-- RUNTIME-SPLITTER NOTE (defensive): semicolons appear ONLY inside
--   full-line ``--`` comments, which the runner strips before splitting
--   the remainder on ';' (daemon/migrations/runner.py).

-- UP

CREATE TABLE IF NOT EXISTS project_skill_files (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    root_path TEXT NOT NULL,
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT,
    body_path TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    body_char_len INTEGER NOT NULL DEFAULT 0,
    manifest_json TEXT,
    valid BOOLEAN NOT NULL DEFAULT 1,
    last_error TEXT,
    embedded_at TEXT,
    indexed_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_psf_root_project_ns_name
    ON project_skill_files (root_path, project_id, namespace, name);

CREATE INDEX IF NOT EXISTS idx_psf_project_id
    ON project_skill_files (project_id);

CREATE INDEX IF NOT EXISTS idx_psf_root_path
    ON project_skill_files (root_path);

-- DOWN

DROP INDEX IF EXISTS idx_psf_root_path;
DROP INDEX IF EXISTS idx_psf_project_id;
DROP INDEX IF EXISTS uq_psf_root_project_ns_name;
DROP TABLE IF EXISTS project_skill_files;

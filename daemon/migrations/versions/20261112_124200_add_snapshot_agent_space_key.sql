-- Migration: add the vsss agent-space column + search index to snapshots
--           (version-scoped snapshot spaces — REVIEW-1 MAJOR-1 fix)
-- Created: 2026-11-12
-- Lane: version-scoped snapshot spaces (vsss) on
--       feature/version-scoped-snapshot-spaces (core landed at
--       ac19e40ce; docs at a1f66da38). The core commit shipped the
--       column for FRESH databases (create_all) and for PostgreSQL
--       (_ensure_postgres_columns) but had NO column-add path for
--       databases created by OLDER binaries — an existing SQLite DB
--       was broken at first snapshot access (capture INSERT failed,
--       GET /snapshots 500'd). This file is that missing path.
--
-- What changes here (ADDITIVE-ONLY — one column + one index):
--   * ``snapshots.agent_space_key`` TEXT (NULL). Canonical
--     versioned agent identity of the TARGET instance at capture
--     time (``registry.compose_agent_space_key`` — e.g.
--     ``'developer'`` / ``'developer[v2]'``). NULL =
--     version-unattributable (legacy rows / deleted-instance
--     captures) and NEVER matches any version's automatic
--     warm-start (fail-closed, no fallback tier). Nullable — no
--     backfill is required for correctness; legacy rows are
--     stamped opportunistically by
--     ``scripts/backfill_snapshot_agent_space.py`` (only when the
--     target instance row still exists).
--   * ``ix_snapshots_project_status_space`` on
--     (project_id, status, agent_space_key) — the candidate-filter
--     index for the automatic warm-start path's Stage-0 predicate.
--     Name mirrors the SQLModel ``__table_args__`` entry in
--     daemon/repositories/snapshot/models.py exactly (keep both
--     sides in sync).
--
-- DUAL-DRIVER NOTES (mirrors 20261107_110003 / 20260724_000001):
--   - SQLite (this file): plain ADD COLUMN + CREATE INDEX (no
--     IF NOT EXISTS — the runner ledger records this file as
--     applied exactly once). On a database that already carries
--     the schema (fresh boots create the column via create_all
--     BEFORE the runner runs), the runner's idempotent handling
--     skips the duplicate column / existing index statements and
--     still records the ledger row — both boot orders converge.
--   - PostgreSQL: already handled by the additive entries in
--     ``EnsembleManager._ensure_postgres_columns``
--     (``ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS
--     agent_space_key TEXT`` + ``CREATE INDEX IF NOT EXISTS
--     ix_snapshots_project_status_space`` — daemon/manager.py,
--     the v0.25.4-era ensure precedent). The PG-side runner is a
--     NO-OP here (runner.py skips non-sqlite engines). Fresh PG
--     databases get the column + index from the model via
--     create_all.
--
-- RUNTIME-SPLITTER NOTE (defensive): semicolons only in full-line
--   comments (stripped before the runner's split).

-- UP

ALTER TABLE snapshots ADD COLUMN agent_space_key TEXT;

CREATE INDEX ix_snapshots_project_status_space
    ON snapshots (project_id, status, agent_space_key);

-- DOWN

-- ROLLBACK-INERT adjudication (standing rule; recorded per the
-- 20261107_110003 precedent): the UP is ONE nullable column plus
-- one derived index — an old binary neither reads nor writes the
-- column (unknown columns are ignored by both drivers' SELECT *
-- paths and the ORM maps only declared fields), so rollback
-- safety does NOT depend on the DOWN statements ever running.
-- The DROP COLUMN needs SQLite >= 3.35.0 and, like every DOWN in
-- this lane, never executes in production (the runner is
-- SQLite-only and applies UP-only; PG evolves via create_all +
-- _ensure_postgres_columns). Kept for house symmetry.
DROP INDEX ix_snapshots_project_status_space;
ALTER TABLE snapshots DROP COLUMN agent_space_key;

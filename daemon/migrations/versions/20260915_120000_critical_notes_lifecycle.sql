-- Migration: critical notes lifecycle columns (pin / supersede / review clock / detail)
-- Created: 2026-09-15
-- Author: coder (critical-notes-retrieval Phase 1)
-- Description:
--   Additive lifecycle columns on ``critical_notes`` per
--   architecture-recommendation §5.1 (six columns — NOTE: §5.2 says
--   "five ADD COLUMN statements" but the §5.1 table lists SIX; §5.1 is
--   authoritative and this migration implements all six. The miscount
--   is flagged in the implementer report):
--
--     * pinned BOOLEAN NOT NULL DEFAULT 0   — leader-explicit core-tier
--       pin (D2: core tier = pinned rows only, cap 8, reject-don't-evict).
--     * pinned_at TEXT                       — audit: last pin timestamp.
--     * pinned_by TEXT                       — audit: pinning instance id.
--     * superseded_by_id TEXT                — SOFT self-reference to the
--       superseding row (deliberately NO DB-level FK: a hard FK would
--       give dialect-divergent ON DELETE behavior; integrity is enforced
--       leader-only in the tool layer). NULL = active row.
--     * last_reviewed_at TEXT                — staleness numerator; NULL
--       backfilled to ``created_at`` by the explicit NULL-guarded UPDATE
--       below (idempotent per #7: the guard preserves any value set
--       between DDL and backfill). NOT declared NOT NULL because SQLite
--       cannot ADD COLUMN NOT NULL without a constant default; every
--       write path populates it and read surfaces fall back to
--       created_at for NULL, so the drift is behaviorally inert.
--     * detail_ref TEXT                      — unbounded detail text;
--       NEVER injected into context blocks (list/router reads only).
--
--   DUAL-DRIVER NOTES (mirrors the convention in
--   ``20260905_000001_attestation_ledger_columns.sql``):
--
--     * This .sql is applied by MigrationRunner ONLY on SQLite
--       (``daemon/migrations/runner.py`` no-ops on non-SQLite; PG schema
--       evolution is handled by
--       ``daemon/manager.py::_ensure_postgres_columns``).
--     * Fresh databases (both drivers) get the columns from
--       ``SQLModel.metadata.create_all()`` via
--       ``daemon/repositories/project/models.py::CriticalNoteModel`` —
--       the "duplicate column name" errors below are swallowed
--       idempotently by the runner on those lineages.
--     * Existing PG databases get the columns AND the two PARTIAL
--       indexes via the matching idempotent statements in
--       ``_ensure_postgres_columns`` (partial indexes with WHERE clauses
--       are NOT expressible via SQLModel/create_all — reviewer #5 — so
--       without the mirror PG would never get them).
--     * Fresh-SQLite boot hazard class (LESSONS/2026-09-04-fresh-sqlite-
--       boot-migration-20260714-pg-only): every statement below is
--       portable SQLite DDL — plain ``ADD COLUMN`` (no ``IF NOT EXISTS``;
--       idempotency comes from the ordered ledger + the runner's
--       duplicate-column swallow), a portable UPDATE, and two partial
--       indexes (valid on BOTH drivers — verified for SQLite >= 3.35
--       partial-index support; partial indexes have been supported since
--       SQLite 3.8.0).
--
--   The partial indexes mirror the access predicates introduced in
--   Phase 1: core-tier counting (``WHERE pinned``) and the R20
--   active-rows-only collision/render scans
--   (``WHERE superseded_by_id IS NOT NULL``).

-- UP

ALTER TABLE critical_notes ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE critical_notes ADD COLUMN pinned_at TEXT;
ALTER TABLE critical_notes ADD COLUMN pinned_by TEXT;
ALTER TABLE critical_notes ADD COLUMN superseded_by_id TEXT;
ALTER TABLE critical_notes ADD COLUMN last_reviewed_at TEXT;
ALTER TABLE critical_notes ADD COLUMN detail_ref TEXT;

-- Backfill the review clock for pre-lifecycle rows. The explicit
-- NULL-guard makes the backfill idempotent AND preserves any value set
-- between the DDL above and this statement (reviewer #7).
UPDATE critical_notes SET last_reviewed_at = created_at WHERE last_reviewed_at IS NULL;

-- Partial index: core-tier counting (pinned ACTIVE rows per project).
CREATE INDEX ix_critical_notes_pinned ON critical_notes(project_id, pinned) WHERE pinned = TRUE;

-- Partial index: superseded-pointer lookups (remove cascade guard,
-- R20 active-rows-only scans).
CREATE INDEX ix_critical_notes_superseded_by_id ON critical_notes(superseded_by_id) WHERE superseded_by_id IS NOT NULL;

-- DOWN
-- Symmetric drop. SQLite 3.35+ supports DROP COLUMN (older versions
-- will error on the column drops; the columns are unused by older
-- builds, which is the documented rollback limitation). Trailing
-- semicolons omitted on nothing — this file relies on the runner's
-- semicolon split, so statements below ARE semicolon-terminated.
DROP INDEX ix_critical_notes_pinned;
DROP INDEX ix_critical_notes_superseded_by_id;
ALTER TABLE critical_notes DROP COLUMN pinned;
ALTER TABLE critical_notes DROP COLUMN pinned_at;
ALTER TABLE critical_notes DROP COLUMN pinned_by;
ALTER TABLE critical_notes DROP COLUMN superseded_by_id;
ALTER TABLE critical_notes DROP COLUMN last_reviewed_at;
ALTER TABLE critical_notes DROP COLUMN detail_ref;

-- Migration: alter prompt_versions add artifact_class column
--           (agent-prompt-immutability commission, RESOLUTION-CORE — Ud#3)
-- Created: 2026-10-14
-- Phase: agent-prompt-immutability Phase A extension (Ud#3 binding blocks 1-4)
--   Adds a single TEXT NOT NULL DEFAULT 'CORE' column to ``prompt_versions``
--   carrying the resolution class for each row:
--
--     * 'CORE' — the conservative artifact set: soul / rule / workflow /
--       tools_note / tools / skill. State-machine governed; pending-counted;
--       R-B2 auto-reset applies on template drift.
--     * 'MEMORY' — the carve-out row class: memory / user / growth / memories
--       families. TRANSITION-FREE (no pending/approve); born-effective;
--       R-B2 auto-reset does NOT apply (MEMORY rows own the artifact).
--
--   The class DISTINCT identity is critical: a MEMORY write must bust ONLY
--   the MEMORY fingerprint slot (a CORE write must bust ONLY the CORE
--   fingerprint slot); both have to land in the synthetic
--   ``db_versions_fingerprint`` so the loader's cache-key check discriminates
--   (see ``daemon/loader.py:_compute_db_versions_fingerprint`` post-Ud#3).
--
--   BACKFILL rule (for existing dev rows — empty-start directive means no
--   production rows will ever exist; the system never shipped; the
--   user-directive §7 binding block swept every accumulated ``memory.md``
--   body to legacy/, but the SQLite dev DBs used in tests may carry rows):
--
--     * artifact IN ('memory', 'user', 'growth')  -> 'MEMORY'
--     * artifact LIKE 'memories/%'                -> 'MEMORY'
--     * everything else                          -> 'CORE'
--
--   One-file convention per corpus (memory table for skill is 6 grouped
--   tables; one ALTER = one migration here, matching
--   ``20240101_000001_add_job_queue_paused.sql`` precedent).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent — ``ALTER TABLE prompt_versions ADD COLUMN`` is
--     safe on SQLite when the column is absent). Fresh dev DBs get the
--     column via this runner; existing dev DBs get it on next boot.
--   - PostgreSQL: brand-new tables — the column is added by the model's
--     declaration on ``PromptVersion.artifact_class`` (default='CORE')
--     via ``SQLModel.metadata.create_all(manager.engine)`` on boot next
--     to the existing ``PromptVersion.metadata.create_all(...)`` call
--     at api.py. Existing PG databases receive the column from the
--     equivalent ``ALTER TABLE prompt_versions ADD COLUMN IF NOT EXISTS
--     artifact_class TEXT NOT NULL DEFAULT 'CORE'`` statement appended to
--     ``daemon/manager.py::_ensure_postgres_columns`` (the dual-driver
--     table-evolution pattern — see the work_id / last_heartbeat_at /
--     mcp_server.instance_metadata references in that method's comments).
--
--   The dev backfill rule on existing rows is encoded here as a
--   single UPDATE so a populated dev DB gets the right default without
--   a separate migration step. Fresh PG databases have no rows to backfill.
--
--   Index / unique-constraint names MUST stay byte-identical to the
--   model's ``__table_args__`` declarations (the canonical source).
--   This ALTER adds no new index beyond what the model declaration
--   already carries; the index parity test at
--   ``tests/migration/test_prompt_versions_index_parity.py`` continues
--   to hold (one ADD COLUMN, no new index — parity preserved).
--
--   IF NOT EXISTS on the ALTER is INVALID SQLite syntax — the runner
--   detects ``prompt_versions.artifact_class`` via the engine's
--   dialect inspection (schema_columns probe) and only applies the
--   ADD COLUMN when the column is absent; this stays idempotent.

-- UP

-- Idempotent guard: the SQLite runner applies this file on every boot,
-- so probe for the column's absence before running the ALTER. Wrapped
-- in a CREATE TRIGGER-style guard via the dialect's columns() probe is
-- not portable — the runner handles the guard externally; the file
-- itself contains the plain statements below.
--
-- 1) Add the column. Existing rows pick up DEFAULT 'CORE' so the
--    backfill below can re-classify MEMORY rows without a separate
--    pass.
ALTER TABLE prompt_versions ADD COLUMN artifact_class TEXT NOT NULL DEFAULT 'CORE';

-- 2) Backfill: classify existing rows by artifact name. Production has
--    no rows (clean-start directive §7) so the UPDATE is a no-op on a
--    production DB; on populated dev DBs it stamps the right class.
UPDATE prompt_versions
SET artifact_class = CASE
    WHEN artifact IN ('memory', 'user', 'growth') THEN 'MEMORY'
    WHEN artifact LIKE 'memories/%' THEN 'MEMORY'
    ELSE 'CORE'
END
WHERE artifact_class = 'CORE'
  AND (
    artifact IN ('memory', 'user', 'growth')
    OR artifact LIKE 'memories/%'
  );

-- DOWN

-- SQLite <3.35 cannot DROP COLUMN. The runner is forward-only; the
-- DOWN is a no-op marker so the file parses both halves of the
-- corpus's split-statement convention.
-- Reversibility for the backfill: artifact_class is purely a class
-- stamp; revoking it loses the class disambiguation, not the rows
-- themselves. Reversing the boot would require restoring the column
-- with DEFAULT 'CORE' (the body above), which the runner does on
-- re-apply automatically.

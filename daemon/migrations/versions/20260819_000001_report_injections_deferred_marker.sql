-- Migration: pause-report-recovery Phase 1 — DEFERRED marker schema
-- Created: 2026-08-19
-- Author: system
-- Description:
--   Phase 1 of the pause-report-recovery plan. Adds the DEFERRED
--   marker schema on ``report_injections``:
--
--     * ``deferred_reason TEXT`` (open-ended rationale for a DEFERRED
--       marker — one of the ``DEFERRED_REASON_*`` constants from
--       ``daemon/constants.py``).
--     * ``recovery_attempted_at TEXT`` (ISO-8601 stamp on
--       ``DEFERRED → PENDING`` — Phase 2 recovery sweep re-processes
--       mid-sweep-crash rows; FM-13).
--     * ``report_message_id`` → DROP NOT NULL (Phase 1 C4: NULL
--       = pre-artifact Site-1 marker shape).
--     * Partial unique index ``uq_report_injections_oblig_triple``
--       on the obligation triple
--       ``(parent_instance_id, child_instance_id, child_message_id)``
--       ``WHERE state IN ('PENDING','DEFERRED')`` — the write-once
--       gate that prevents duplicate markers across the three
--       concurrent recovery actors (router / sweep / Site 1).
--     * Partial index ``ix_report_injections_recovery_attempted``
--       on ``recovery_attempted_at`` ``WHERE state = 'PENDING'``
--       for the recovery sweep predicate.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine
--   dialect is SQLite. Fresh PostgreSQL databases receive the
--   fields and indexes from ``SQLModel.metadata.create_all()`` (the
--   ReportInjection model declares all four columns and both indexes).
--   Existing PostgreSQL databases receive equivalent idempotent
--   column adds, DROP NOT NULL, and index creation in
--   ``daemon/manager.py::_ensure_postgres_columns()``. **Index name
--   MUST match across both DDL paths** — see the literal
--   ``uq_report_injections_oblig_triple`` below and the matching
--   DDL in ``_ensure_postgres_columns``.
--
-- C1 CASE-LOCKSTEP CONTRACT:
--   The partial-index predicate literals ``('PENDING','DEFERRED')``
--   MUST stay uppercase and verbatim across: the
--   ``ReportInjectionState`` enum in
--   ``daemon/repositories/report_injection/models.py``, the
--   SQLAlchemy ``postgresql_where`` / ``sqlite_where`` expression,
--   this migration's CREATE INDEX, and the matching PG DDL in
--   ``_ensure_postgres_columns``. A case drift between storage and
--   app would silently break the write-once gate.
--
-- W3 PRE-CHECK (SQLite variant):
--   Before building the partial unique index, dedup any existing
--   non-terminal duplicates via the same GROUP BY / HAVING >1 query
--   the PG ``_ensure_postgres_columns`` runs. Oldest row wins;
--   duplicates are transitioned to a terminal disposition
--   (``TASK_DELIVERED`` with a sentinel ``delivered_at``) so the
--   index can build cleanly.
--
-- W8 ROLLBACK RUNBOOK:
--   To revert this migration:
--     1. DROP INDEX uq_report_injections_oblig_triple;  -- FIRST
--     2. DROP INDEX ix_report_injections_recovery_attempted;
--     3. Restore ``report_message_id`` NOT NULL via table rebuild
--        (SQLite <3.35) or ``ALTER TABLE ... DROP COLUMN ... ADD
--        COLUMN ... NOT NULL`` (SQLite 3.35+); verify all rows have
--        ``report_message_id`` set with ``SELECT COUNT(*) WHERE
--        report_message_id IS NULL`` first.
--     4. DROP COLUMN recovery_attempted_at (or table rebuild).
--     5. DROP COLUMN deferred_reason (or table rebuild).
--   Reverse order matters: column drops WITH the partial unique
--   index still present are blocked (PostgreSQL rejects) or leave
--   an orphaned index (SQLite). Always DROP indexes first, then
--   columns.

-- UP

-- Guarded by MigrationRunner's per-statement duplicate-column handler.
-- ``deferred_reason`` TEXT — open-ended vocabulary (not VARCHAR).
ALTER TABLE report_injections ADD COLUMN deferred_reason TEXT;
-- ``recovery_attempted_at`` ISO-8601 stamp (TEXT) on recovery.
ALTER TABLE report_injections ADD COLUMN recovery_attempted_at TEXT;

-- ``report_message_id`` → nullable (Phase 1 C4).
-- SQLite <3.35 has no ``ALTER TABLE ... DROP NOT NULL``. Use the
-- supported pattern: add a nullable ``report_message_id_new``, copy
-- values across, drop the original, rename. Each step is idempotent
-- under MigrationRunner's duplicate-column / no-such-column
-- handlers. On a fresh DB ``create_all`` already created the column
-- as nullable, so the ADD of ``report_message_id_new`` succeeds and
-- the column swap is a no-op rename.
ALTER TABLE report_injections ADD COLUMN report_message_id_new VARCHAR(64);
UPDATE report_injections SET report_message_id_new = report_message_id;
ALTER TABLE report_injections DROP COLUMN report_message_id;
ALTER TABLE report_injections RENAME COLUMN report_message_id_new TO report_message_id;

-- W3 PRE-CHECK (SQLite variant): the partial unique index will
-- reject pre-existing duplicate non-terminal rows. The PG path
-- resolves duplicates via ``_ensure_postgres_columns``; on SQLite
-- we resolve here with the same oldest-wins rule. Transition any
-- duplicate non-terminal rows to ``TASK_DELIVERED`` with a sentinel
-- ``delivered_at`` (oldest row of each duplicate group survives).
UPDATE report_injections
   SET state = 'TASK_DELIVERED',
       delivered_at = COALESCE(delivered_at, created_at)
 WHERE injection_id NOT IN (
    SELECT MIN(injection_id)
      FROM report_injections
     WHERE state IN ('PENDING', 'DEFERRED')
     GROUP BY parent_instance_id, child_instance_id, child_message_id
 )
   AND state IN ('PENDING', 'DEFERRED');

-- Write-once gate on the obligation triple. Name MUST match the PG
-- DDL emitted by ``_ensure_postgres_columns`` and the SQLAlchemy
-- model definition at
-- ``daemon/repositories/report_injection/models.py``.
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_injections_oblig_triple
    ON report_injections (
        parent_instance_id,
        child_instance_id,
        child_message_id
    )
    WHERE state IN ('PENDING','DEFERRED');

-- Partial index for the Phase 2 recovery-sweep predicate.
-- ``state = 'PENDING'`` keeps the index sparse (only stamps survive).
CREATE INDEX IF NOT EXISTS ix_report_injections_recovery_attempted
    ON report_injections (recovery_attempted_at)
    WHERE state = 'PENDING';

-- DOWN
-- Reverse order: DROP indexes FIRST, then columns. See W8 rollback
-- runbook in the comment block above.
DROP INDEX IF EXISTS ix_report_injections_recovery_attempted;
DROP INDEX IF EXISTS uq_report_injections_oblig_triple;
-- Drop the new columns. SQLite <3.35 cannot drop columns; on those
-- versions the inverse is a table rebuild (out of scope here). The
-- runner's per-statement duplicate-column handler tolerates the
-- no-such-column idempotent path on re-runs.
ALTER TABLE report_injections DROP COLUMN recovery_attempted_at;
ALTER TABLE report_injections DROP COLUMN deferred_reason;
-- Restoring NOT NULL on ``report_message_id`` requires
-- ``ALTER TABLE ... DROP COLUMN report_message_id`` + ADD COLUMN
-- ``report_message_id VARCHAR(64) NOT NULL`` after verifying no
-- NULL rows exist. Out of scope for a DOWN path; document and
-- require manual verification.

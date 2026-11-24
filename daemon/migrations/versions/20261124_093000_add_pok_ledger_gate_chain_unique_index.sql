-- Migration: gate-chain partial UNIQUE index on pok_write_gate_ledger
--           (POK write gate — Wave 2 Phase 8, T8.16)
-- Created: 2026-11-24
-- Phase: pok-write-gate Phase 8 (plan:
--   .agents/shared/planning/pok-write-gate/phase8-plan.md, T8.16).
--
-- What lives here:
--   * ``uq_pok_ledger_gate_chain`` — a PARTIAL UNIQUE index on
--     ``(project_id, gate_id, phase)`` WHERE ``gate_id IS NOT NULL``.
--     At most ONE ledger row per gate-chain step.
--
-- WHY (review advisory 2 — the DB-level idempotency backstop):
--   The ledger appender is APPEND-ONLY and its de-duplication was
--   APP-LEVEL read-then-append only (``daemon/repositories/pok/
--   repository.py`` — ``append_ledger`` / ``record_standing_defects``).
--   Two concurrent callers can therefore write the SAME gate-chain
--   step twice: the read sees "no such row", both append, both
--   commit. The single-writer discipline is DOCUMENTATION, not
--   enforcement — this index is the enforcement. The appender now
--   surfaces a duplicate as the typed ``POKLedgerDuplicateError``.
--
-- PARTIAL on ``gate_id IS NOT NULL``:
--   A RETRIEVAL row belongs to no gate chain (its ``gate_id`` is
--   NULL) and MANY retrieval rows legitimately share one project —
--   they must never be constrained. Only rows that carry a chain key
--   are unique per (project, gate, phase).
--
-- 3-SITE INDEX LOCKSTEP (byte-identical name + predicate):
--   * this migration (SQLite),
--   * ``daemon/repositories/pok/models.py`` (``__table_args__`` —
--     fresh databases via ``SQLModel.metadata.create_all``),
--   * ``daemon/manager.py::_ensure_postgres_columns`` (the PostgreSQL
--     mirror — the .sql runner is a NO-OP on PG, and ``create_all``
--     with ``checkfirst=True`` does NOT add an index to an EXISTING
--     table).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): the runner is the persistent record.
--   - PostgreSQL: the schema comes from SQLModel.metadata.create_all
--     with ``daemon/repositories/pok/models.py`` as the canonical
--     source (registered by the late-import block in daemon/manager.py
--     BEFORE create_all), plus the manager's PG mirror statement for
--     databases that already carry the table.
--   - EVERY STRING COLUMN IS TEXT (never VARCHAR) on either driver.
--
-- ROLLBACK POSTURE: additive only (one index, no column, no table, no
-- destructive DDL). Dropping the index restores the pre-Wave-2
-- app-level-only de-duplication — it never destroys a ledger row.
--
-- No semicolon in any TRAILING (inline) comment: the runner strips
-- full-line comments but not inline ones, and it splits statements on
-- semicolons, so a ';' in a trailing comment would split a statement
-- early.

-- UP

CREATE UNIQUE INDEX IF NOT EXISTS uq_pok_ledger_gate_chain
    ON pok_write_gate_ledger (project_id, gate_id, phase)
    WHERE gate_id IS NOT NULL;

-- DOWN

DROP INDEX IF EXISTS uq_pok_ledger_gate_chain;

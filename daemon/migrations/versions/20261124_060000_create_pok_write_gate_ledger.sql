-- Migration: create the pok_write_gate_ledger table
--           (POK write gate — Wave 1 Phase 1, T1.2)
-- Created: 2026-11-24
-- Phase: pok-write-gate Phase 1 (plan:
--   .agents/shared/planning/pok-write-gate/phase1-plan.md, T1.2).
--
-- What lives here:
--   * ``pok_write_gate_ledger`` — the WRITE-GATE LEDGER. One
--     APPEND-ONLY row per gate event: the gate's ADMISSION decision,
--     the RETRIEVAL events, the TOOL's EXECUTION, and the BASIS on
--     which anything was archived. It is the decision substrate for
--     every later cut of the gate and the sink for BOTH the gate's
--     verdict AND the tool's execution (authority split —
--     design-amendments-v2 §8, BINDING).
--
-- WHY THE MERGE-PROPOSAL COLUMNS LAND NOW (design-amendments-v2 §13.5,
-- "no-pick-foreclosed rule"): the ledger is additive and cheap today;
-- a schema that cannot represent a proposal forces a MIGRATION at P1.
-- ``target_slug`` + ``merge_kind`` + ``proposed_body`` + the three
-- sha256 columns + ``executed_by`` let the librarian measure the
-- edit-distance between what the judge PROPOSED and what the tool
-- EXECUTED (rubber-stamp detection).
--
-- ROW-SHAPE VOCABULARY (mirrored in BOTH this file AND
-- daemon/repositories/pok/models.py — readiness constraint 1; keep
-- both sides in sync):
--   phase      ∈ ADMISSION|RETRIEVAL|EXECUTION|ASK_MINTED|ASK_ANSWERED|
--               ASK_EXECUTED|ASK_EXPIRED|ASK_SHADOW
--   verdict    ∈ ACCEPT|MERGE|ASK|REJECT          (NULL on a retrieval)
--   merge_kind ∈ no_delta|delta                    (NULL unless a merge)
--   basis      ∈ covered-by-surviving-row|relocated-to-routed-home|
--               NOW-FALSE|gate-rejection            (NULL unless archived)
--   source     ∈ injection|query                  (NULL unless a retrieval)
--
-- CHAIN SHAPE (design-amendments-v2 §13.2 fork G): rows of one gate
-- event are keyed by ``gate_id`` + ``phase``. A PLAIN chain
-- (ACCEPT/MERGE/REJECT) keeps TWO rows — ADMISSION then EXECUTION. An
-- ASK chain keeps THREE — ASK_MINTED, ASK_ANSWERED, ASK_EXECUTED.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): one .sql per table (house rule) — the
--     persistent record alongside daemon/repositories/pok/models.py.
--   - PostgreSQL: the schema comes from SQLModel.metadata.create_all
--     with ``daemon/repositories/pok/models.py`` as the canonical
--     source (registered by the late-import block in daemon/manager.py
--     BEFORE create_all). Brand-new table, so there is NO
--     _ensure_postgres_columns mirror. The PG-side runner is a NO-OP.
--   - EVERY STRING COLUMN IS TEXT (never VARCHAR) on either driver.
--
-- 3-SITE INDEX LOCKSTEP: the index names AND column lists below are
-- byte-identical to the ``__table_args__`` of
-- ``daemon/repositories/pok/models.py``.
--
-- WHY NO FOREIGN KEY (house posture, matching the sibling POK tables):
--   ``project_id`` / ``entry_id`` / ``gate_id`` are plain TEXT and the
--   table references NO other table. Combined with the additive-only
--   body below this makes the migration ROLLBACK-INERT.
--
-- EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY): create_all runs
-- FIRST on the boot path and this file is applied IMMEDIATELY AFTER,
-- so the objects already exist. IF NOT EXISTS is what keeps the file
-- from degrading into a silent no-op on every boot.
--
-- ROLLBACK POSTURE: additive only. DOWN drops only this new table and
-- its indexes -- fix-loss for gate audit history, never an
-- incompatibility.
--
-- No semicolon in any TRAILING (inline) comment: the runner strips
-- full-line comments but not inline ones, and it splits statements on
-- semicolons, so a ';' in a trailing comment would split a statement
-- early. (A ';' inside a FULL-LINE comment is harmless -- those are
-- stripped.)

-- UP

CREATE TABLE IF NOT EXISTS pok_write_gate_ledger (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    gate_id TEXT,
    phase TEXT NOT NULL CHECK (phase IN ('ADMISSION','RETRIEVAL','EXECUTION','ASK_MINTED','ASK_ANSWERED','ASK_EXECUTED','ASK_EXPIRED','ASK_SHADOW')),
    entry_id TEXT,
    candidate_slug TEXT,
    verdict TEXT CHECK (verdict IN ('ACCEPT','MERGE','ASK','REJECT')),
    reason TEXT,
    judge_explanation TEXT,
    confidence REAL,
    basis TEXT CHECK (basis IN ('covered-by-surviving-row','relocated-to-routed-home','NOW-FALSE','gate-rejection')),
    source TEXT CHECK (source IN ('injection','query')),
    answer_excerpt TEXT,
    answered_by TEXT,
    executed_by TEXT,
    executed BOOLEAN NOT NULL DEFAULT FALSE,
    target_slug TEXT,
    merge_kind TEXT CHECK (merge_kind IN ('no_delta','delta')),
    proposed_body TEXT,
    proposed_body_sha256 TEXT,
    target_body_sha256 TEXT,
    executed_body_sha256 TEXT,
    agent_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pok_ledger_project_id
    ON pok_write_gate_ledger (project_id);

CREATE INDEX IF NOT EXISTS idx_pok_ledger_project_phase
    ON pok_write_gate_ledger (project_id, phase);

CREATE INDEX IF NOT EXISTS idx_pok_ledger_project_created
    ON pok_write_gate_ledger (project_id, created_at);

CREATE INDEX IF NOT EXISTS idx_pok_ledger_gate_id
    ON pok_write_gate_ledger (gate_id);

CREATE INDEX IF NOT EXISTS idx_pok_ledger_entry_id
    ON pok_write_gate_ledger (entry_id);

-- DOWN

DROP INDEX IF EXISTS idx_pok_ledger_entry_id;
DROP INDEX IF EXISTS idx_pok_ledger_gate_id;
DROP INDEX IF EXISTS idx_pok_ledger_project_created;
DROP INDEX IF EXISTS idx_pok_ledger_project_phase;
DROP INDEX IF EXISTS idx_pok_ledger_project_id;
DROP TABLE IF EXISTS pok_write_gate_ledger;

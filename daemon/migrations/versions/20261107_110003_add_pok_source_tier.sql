-- Migration: add the trust-tier column to project_operating_knowledge
--           (POK Phase 2 — trust-tier addendum T-1, D3 un-park)
-- Created: 2026-11-07
-- Phase: Phase 2 of the project-operating-knowledge (POK) commission
--        (branch feature/project-operating-knowledge; Phase-1 core
--        + cap landed at 7207a6360; this lane continues from there).
--
-- What changes here (ADDITIVE-ONLY — one column):
--   * ``project_operating_knowledge.source_tier`` TEXT NOT NULL
--     DEFAULT 'agent' CHECK (source_tier IN ('agent','curated')).
--     The WRITE/MUTATION AUTHORITY class of the row (trust-tier
--     addendum T-1): ``curated`` = the dev-curated immutable
--     reference (the parked guidelines); writes/flips touching it
--     are curator-only (leader/PM per A-9). Named ``source_tier``
--     NOT ``tier`` — the admission vocabulary already owns "tier"
--     (pinned tier / ranked actives, arch §4.5:107).
--
-- FIVE-AXIS ORTHOGONALITY (addendum T-1, adjudicated): provenance
--   (epistemic status, A-10) ⊥ pinned (delivery placement, §4.7) ⊥
--   audience (view membership, ER-1) ⊥ status (lifecycle) ⊥
--   source_tier (authority class). Deriving tier from
--   ``created_by_agent`` is FORBIDDEN (A-5: attribution ≠
--   authorization).
--
-- WHY DEFAULT 'agent' IS SAFE (proven empirically on a throwaway
--   SQLite before landing — the fresh-SQLite breaker lesson): the
--   DEFAULT satisfies the CHECK for every pre-existing row, so the
--   ALTER backfills all Phase-1 rows to 'agent' IN PLACE — every
--   Phase-1 dogfood row reads byte-unchanged and every Phase-1 call
--   signature is unchanged (zero behavior change without opt-in).
--
-- CHECK-CLOSED VOCABULARIES (mirrored in BOTH this file AND the
-- SQLModel table — readiness constraint 1; keep both sides in sync):
--   source_tier ∈ agent|curated   [Phase 2 — addendum T-1]
--   (the Phase-1 vocabularies — domain/audience/provenance/status —
--   are UNTOUCHED; see 20261107_110001.)
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): one additive .sql (house rule); plain
--     ADD COLUMN (no IF NOT EXISTS — SQLite lacks it and the
--     ledger records this file as applied exactly once).
--   - PostgreSQL: the live PG table already exists (Phase 2 lands
--     POST-Phase-1-promote), and ``create_all`` cannot add columns
--     to existing tables — the operative PG path is the idempotent
--     ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` entry in
--     ``EnsembleManager._ensure_postgres_columns`` (daemon/manager.py;
--     the v0.25.4-era ensure precedent). Fresh PG databases get the
--     column from the model field via create_all. The PG-side runner
--     is a NO-OP here. The CHECK is carried inline in BOTH paths.
--
-- ZERO GUIDELINE CONTENT (addendum T-5): seeding is the TOOL lane
--   (``pok_upsert(source_tier='curated')`` per slug, curator-only)
--   — there is NO SQL backfill, now or later. This file carries
--   DDL only.
--
-- RUNTIME-SPLITTER NOTE (defensive): semicolons only in full-line
--   comments (stripped before the runner's split).

-- UP

ALTER TABLE project_operating_knowledge ADD COLUMN source_tier
    TEXT NOT NULL DEFAULT 'agent'
    CHECK (source_tier IN ('agent', 'curated'));

-- DOWN

-- ROLLBACK-INERT adjudication (standing rule; recorded per readiness
-- constraint 3): the UP is ONE additive column with a constant
-- DEFAULT and a self-contained CHECK that references ONLY the new
-- column — an old binary neither reads nor writes it (unknown
-- columns are ignored by both drivers' SELECT * paths and the ORM
-- maps only declared fields). Rollback safety therefore does NOT
-- depend on the DOWN statement ever running. The DROP COLUMN below
-- needs SQLite >= 3.35.0 and, like every DOWN in this lane, never
-- executes in production (the runner is SQLite-only and applies
-- UP-only; PG evolves via create_all + _ensure_postgres_columns).
-- Kept for house symmetry with the other lane migrations.
ALTER TABLE project_operating_knowledge DROP COLUMN source_tier;

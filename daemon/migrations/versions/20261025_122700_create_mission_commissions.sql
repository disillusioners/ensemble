-- Migration: create mission_commissions table (completion-subsystem Proposal B PR1)
-- Created: 2026-10-25
-- Author: coder
-- Description:
--   Proposal B — Mission-Record Anchor, PR1 of 3. One row per
--   COMMISSIONED worker / work item: the census substrate (D2) and the
--   routing key (D1) for the PR2/PR3 read flips.
--
--   Lifecycle: ``outstanding`` → ``satisfied`` | ``abandoned`` |
--   ``superseded`` — every transition is a guarded
--   ``UPDATE ... WHERE state='outstanding'`` (the dependency_watchers
--   PENDING→FIRED exactly-once shape). Multiple OUTSTANDING rows for
--   the same worker are ALLOWED by design (adoption-delta (a): the
--   guarded-state lifecycle is the discriminator; routing authority
--   stays with the A-resolver until the flip) — hence NO unique
--   constraint on (mission_id, worker_instance_id).
--
--   Indexes (proposal-b §4): (mission_id, state) — the D1 routing key
--   and mission-scoped census; (worker_instance_id, state) — the
--   ladder's rung-2 lookup and the eternal-mission reconciliation.
--
--   ``advisory_backfill`` marks adoption-delta (b) backfill rows —
--   EXCLUDED from routing authority forever (the "if dropped, the
--   ledger is worse than no ledger" rule, comparison.md §4c). Backfill
--   rows carry DETERMINISTIC UUID5 ``commission_id`` values so the
--   backfill's INSERT ... ON CONFLICT DO NOTHING idempotency keys on
--   the PK; live mints use random UUID4s.
--
--   Additive-only and rollback-safe. On PostgreSQL the table is created
--   by ``SQLModel.metadata.create_all()`` (model registered before
--   create_all at manager boot — snapshot precedent); the migration
--   runner is a NO-OP there. Schema mirrors
--   daemon/repositories/mission_record/models.py.

-- UP

CREATE TABLE IF NOT EXISTS mission_commissions (
    commission_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    commissioner_instance_id TEXT NOT NULL,
    worker_instance_id TEXT NOT NULL,
    work_id TEXT,
    lane TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'outstanding',
    created_at TEXT NOT NULL,
    satisfied_at TEXT,
    abandon_reason TEXT,
    advisory_backfill INTEGER NOT NULL DEFAULT 0
);

-- D1 routing key (PR3) + mission-scoped commission census (D2).
CREATE INDEX IF NOT EXISTS ix_mission_commissions_mission_state
    ON mission_commissions(mission_id, state);

-- Rung-2 ladder lookup + the eternal-mission reconciliation sweep.
CREATE INDEX IF NOT EXISTS ix_mission_commissions_worker_state
    ON mission_commissions(worker_instance_id, state);

-- DOWN

DROP INDEX IF EXISTS ix_mission_commissions_worker_state;
DROP INDEX IF EXISTS ix_mission_commissions_mission_state;
DROP TABLE IF EXISTS mission_commissions;

-- Migration: create mission_records table (completion-subsystem Proposal B PR1)
-- Created: 2026-10-25
-- Author: coder
-- Description:
--   Proposal B — Mission-Record Anchor, PR1 of 3 (dry-mints substrate).
--   One row per mission EPOCH: the durable written record of "what is
--   this mission, who commissioned it, where is the anchor" that every
--   settle/gate/routing decision will READ in PR2/PR3 (until then the
--   rows are write-only — mints write, NOTHING reads for decisions,
--   zero behavior change).
--
--   INSERT-per-epoch; closed rows are immutable; only ``status`` /
--   attestation fields on OPEN rows mutate — the dependency_watchers
--   guarded-transition precedent. ``mission_id`` is a fresh UUID4 per
--   epoch (a deliberate break from the read-model's
--   ``mission_id == instance_id`` lean — the record is write-side
--   truth; ``MissionResolver`` later joins it).
--
--   Indexes:
--     * (anchor_instance_id, status) — the resolution ladder's rung-1
--       lookup ("does the actor have an OPEN epoch as anchor?"). The
--       status suffix mirrors the dependency_watchers composite
--       trade-off: most rows become non-open over time, and every
--       resolve/mint gates on the open prefix.
--     * UNIQUE (anchor_instance_id, founding_message_id)
--       WHERE founding_message_id IS NOT NULL — the mint idempotency
--       key (anchor, founding_message_id), the mint-storm guard
--       (proposal-b §8). PARTIAL: NULL-founding rows (boot sweep /
--       backfill) are excluded — they dedupe by resolve-if-none.
--       SQLite has supported partial indexes natively since 3.8.
--
--   This migration is additive-only and rollback-safe (CREATE TABLE +
--   CREATE INDEX; the DOWN drops only what UP created). On PostgreSQL
--   the table is created by ``SQLModel.metadata.create_all()`` (the
--   model is registered before create_all at manager boot — snapshot
--   precedent for brand-new tables, no _ensure_postgres_columns
--   mirror); the migration runner is a NO-OP there. Schema mirrors
--   daemon/repositories/mission_record/models.py.
--
--   Deliberately NOT part of this migration: any change to
--   instance_ops_audit (an ops-safety ledger — mission semantics are a
--   different concern), and any instance-status / admission-state
--   writer (the terminal-write census and the admission-writer census
--   are untouched — mission records write ONLY their own tables).

-- UP

CREATE TABLE IF NOT EXISTS mission_records (
    mission_id TEXT PRIMARY KEY,
    anchor_instance_id TEXT NOT NULL,
    epoch_seq INTEGER NOT NULL DEFAULT 1,
    founding_message_id TEXT,
    founding_source TEXT NOT NULL,
    commissioner_instance_id TEXT,
    work_id TEXT,
    parent_mission_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    attested_at TEXT,
    attest_method TEXT,
    attestation_bypassed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    settled_at TEXT,
    provenance TEXT NOT NULL DEFAULT 'live'
);

-- Rung-1 resolution lookup: the anchor's OPEN epoch.
CREATE INDEX IF NOT EXISTS ix_mission_records_anchor_status
    ON mission_records(anchor_instance_id, status);

-- Mint idempotency keyed (anchor, founding_message_id) — partial on
-- non-NULL founding messages; boot/backfill mints dedupe by
-- resolve-if-none instead (honest-NULL foundings).
CREATE UNIQUE INDEX IF NOT EXISTS ux_mission_records_anchor_founding_msg
    ON mission_records(anchor_instance_id, founding_message_id)
    WHERE founding_message_id IS NOT NULL;

-- DOWN

DROP INDEX IF EXISTS ux_mission_records_anchor_founding_msg;
DROP INDEX IF EXISTS ix_mission_records_anchor_status;
DROP TABLE IF EXISTS mission_records;

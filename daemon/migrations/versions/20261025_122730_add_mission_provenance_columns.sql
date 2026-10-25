-- Migration: add mission-provenance columns (completion-subsystem Proposal B PR1)
-- Created: 2026-10-25
-- Author: coder
-- Description:
--   Proposal B — Mission-Record Anchor, PR1 of 3. The THREE additive
--   provenance columns (proposal-b §2.1 "Extended existing tables"):
--
--     * report_injections.mission_id            — which mission the
--       delivered report belongs to. WRITTEN BY THE PR3 ROUTING FLIP
--       (not hand-threaded through message content); PR1 lands the
--       column only (no writers yet).
--     * report_injections.commissioner_instance_id — the report's
--       true destination under the same flip. PR1: column only.
--     * message_metadata.mission_id             — the delivery-time
--       mission stamp written by the MessageTapSlot sites (the
--       founding / re-founding-amendment carrier). PR1: written by the
--       user_message_entry tap via the threaded ``mission_id``
--       carrier; nullable — pre-PR1 rows and non-mission messages stay
--       NULL. The composite PK (thread_id, message_id) is UNCHANGED.
--
--   dependency_watchers.watcher_metadata gains a ``mission_id`` KEY in
--   the free-form JSONB — deliberately NO schema change (the mint site
--   already carries kind/child_id/parent_id/message_id keys).
--
--   All columns are nullable and default NULL — additive-only,
--   rollback-safe, old binaries ignore them (extra columns unread).
--   On PostgreSQL these three statements are mirrored idempotently by
--   ``daemon/manager.py::_ensure_postgres_columns()`` (the migration
--   runner is a NO-OP there — the dual-driver convention).

-- UP

ALTER TABLE report_injections ADD COLUMN mission_id TEXT;
ALTER TABLE report_injections ADD COLUMN commissioner_instance_id TEXT;
ALTER TABLE message_metadata ADD COLUMN mission_id TEXT;

-- DOWN
-- SQLite pre-3.35 has no DROP COLUMN; the columns are additive and
-- unread by legacy binaries, so the rollback is a no-op by design.
-- This migration is NOT REVERSIBLE on SQLite (no data loss — the
-- columns are nullable provenance stamps).

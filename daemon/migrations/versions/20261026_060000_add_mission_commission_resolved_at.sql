-- Migration: add the mission-commission RESOLUTION stamp (Proposal B PR3)
-- Created: 2026-10-26
-- Author: coder
-- Description:
--   Proposal B PR3 (OQ-3(b)) — the reconciliation sweep (RDRS lane 7)
--   needs a RESOLUTION stamp on the commission row: when the row left
--   ``outstanding`` (abandoned OR satisfied), WHEN did it leave and
--   WHY.
--
--   * mission_commissions.resolved_at — TEXT ISO UTC. Written by the
--     ONE guarded UPDATE that owns every resolution —
--     ``MissionRecordRepository.transition_commission_state`` — for
--     EVERY resolution (the sweep's abandon / satisfy-on-evidence, the
--     delivery terminator's satisfy, the mint lanes' supersede). NULL
--     while the row is outstanding ("unresolved" is the honest default).
--
--   DISTINCT FROM ``satisfied_at`` ON PURPOSE: dual-purposing
--   ``satisfied_at`` for ABANDONED rows would make the column name
--   lie — an abandoned commission was never satisfied. ``abandon_reason``
--   carries the WHY (the frozen vocabulary: worker_terminal_no_report
--   / ttl_expired_not_live / worker_row_missing).
--
--   Additive-only and rollback-safe: nullable, default NULL, old
--   binaries ignore it. On PostgreSQL the column is mirrored
--   idempotently by ``daemon/manager.py::_ensure_postgres_columns()``
--   (the migration runner is a NO-OP there — the dual-driver
--   convention; fresh PG databases get the column from
--   ``SQLModel.metadata.create_all()`` via the model field).

-- UP

ALTER TABLE mission_commissions ADD COLUMN resolved_at TEXT;

-- DOWN
-- SQLite pre-3.35 has no DROP COLUMN; the column is additive and
-- unread by legacy binaries, so the rollback is a no-op by design.
-- This migration is NOT REVERSIBLE on SQLite (no data loss — the
-- column is a nullable resolution stamp).

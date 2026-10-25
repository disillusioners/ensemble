-- Migration: open-epoch partial UNIQUE index on mission_records
-- Created: 2026-10-25
-- Author: coder
-- Description:
--   Proposal B — Mission-Record Anchor, PR2 of 3 (enforce reads).
--   F1 CARRY (from PR1's report): "at most ONE OPEN mission_records row
--   per anchor".
--
--   Why: the resolution ladder's rung-1 lookup ("does the actor have an
--   OPEN epoch as anchor?") returns a SINGLE row by construction. Before
--   this index, two OPEN epochs for one anchor were merely unlikely
--   (the ladder reuses the open epoch; the job_continue lane supersedes
--   the prior one) — not IMPOSSIBLE. A crash between the job_continue
--   lane's INSERT and its supersede left two open epochs, and the
--   "newest wins" read order silently picked one of them. The index
--   makes the invariant a CONSTRAINT: the second open epoch cannot be
--   written at all.
--
--   PARTIAL on ``status = 'open'``: closed epochs (settled / closed /
--   superseded) accumulate per anchor forever and MUST NOT be
--   constrained — only the LIVE state is unique.
--
--   The normalization UPDATE below is the IDEMPOTENT pre-step for
--   databases that already carry duplicate open epochs (the crash
--   window described above). It keeps the NEWEST open epoch per anchor
--   and marks the older ones ``superseded`` — the exact state the
--   job_continue lane intended. Re-running it is a no-op (the predicate
--   only matches rows that have a NEWER open sibling).
--
--   3-SITE PIN (byte-identical index name + predicate):
--     * this migration (SQLite),
--     * ``daemon/repositories/mission_record/models.py``
--       (``__table_args__`` — fresh databases via ``create_all``),
--     * ``daemon/manager.py::_ensure_postgres_columns`` (the PostgreSQL
--       mirror — the .sql runner is a NO-OP on PG).
--
--   Index name: ``ux_mission_records_anchor_open``.
--   Predicate:  ``status = 'open'``.
--
--   Additive-only in the schema sense (one index, no column, no table,
--   no destructive DDL). The normalization UPDATE only moves rows from
--   ``open`` to ``superseded`` — a status the job_continue lane already
--   writes — so it never destroys an epoch.

-- UP

-- Idempotent normalization: at most one open epoch per anchor. Keeps
-- the newest (created_at DESC, mission_id DESC as the tie-break).
UPDATE mission_records
   SET status = 'superseded'
 WHERE status = 'open'
   AND EXISTS (
       SELECT 1
         FROM mission_records newer
        WHERE newer.anchor_instance_id = mission_records.anchor_instance_id
          AND newer.status = 'open'
          AND (
              newer.created_at > mission_records.created_at
              OR (
                  newer.created_at = mission_records.created_at
                  AND newer.mission_id > mission_records.mission_id
              )
          )
   );

-- F1: the open-epoch partial unique index.
CREATE UNIQUE INDEX IF NOT EXISTS ux_mission_records_anchor_open
    ON mission_records(anchor_instance_id)
    WHERE status = 'open';

-- DOWN

DROP INDEX IF EXISTS ux_mission_records_anchor_open;

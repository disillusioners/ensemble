-- Migration: add terminal_reason column to job_queue_items
-- Created: 2026-06-29
-- Description: Phase 7c of Job-as-Queue-Proxy. Adds a terminal_reason
--   discriminator that records HOW a job terminated when
--   admission_state='done' (one of "completed" / "failed" / "cancelled"
--   / "aborted"). The Phase 5 column drop collapsed the 7-state legacy
--   ``status`` vocabulary onto a 4-value ``admission_state``, which made
--   cancelled / failed / completed indistinguishable from the queue side.
--   ``terminal_reason`` restores that discrimination for the resolver
--   read path (``work_resolver._job_to_record``).
--
--   Nullable with no default — pre-7c rows backfill as NULL and the
--   resolver falls back to the ``admission_state`` map for backward
--   compatibility.
--
--   The PostgreSQL counterpart lives in
--   ``daemon/manager.py::_ensure_postgres_columns`` (the .sql runner is
--   a NO-OP on PG, so the equivalent ADD COLUMN + CREATE INDEX are
--   inlined there for parity with fresh databases).

-- UP

ALTER TABLE job_queue_items ADD COLUMN terminal_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_job_queue_terminal_reason ON job_queue_items(terminal_reason);

-- DOWN

DROP INDEX IF EXISTS idx_job_queue_terminal_reason;
-- SQLite cannot DROP COLUMN easily; leave the column in place.
-- Migration: create instance_execution_leases table
-- Created: 2026-06-14
-- Author: system
-- Description:
--   Per-instance execution lease used by the Execution Gate
--   (daemon/services/execution_gate.py) to ensure that only one
--   dispatcher drives graph.astream for a given thread_id (== instance_id)
--   at a time. Closes the dual-dispatcher checkpoint race documented in
--   docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md.
--
--   Two dispatchers (JobQueue's MessageJobHandler and WorkerPool's
--   ProcessMessageProcessor) used to be able to call
--   _process_message_with_tracking concurrently for the same instance,
--   each writing langgraph checkpoints at the same step and clobbering
--   each other's messages (the giter-report-lost bug). The lease is
--   the single source of truth for "who is driving graph.astream right
--   now?"; the loser must back off and re-queue.
--
--   Acquisition: INSERT OR IGNORE (SQLite) / ON CONFLICT DO NOTHING
--   (Postgres). rowcount == 1 means acquired.
--   Release: DELETE WHERE instance_id=? AND holder_id=?. The
--   holder_id check prevents a stale loser from accidentally deleting
--   a fresh winner's lease.

-- UP

CREATE TABLE IF NOT EXISTS instance_execution_leases (
    instance_id   TEXT PRIMARY KEY,
    holder_id     TEXT NOT NULL,
    holder_kind   TEXT NOT NULL
                  CHECK(holder_kind IN ('message_job', 'task', 'resume')),
    acquired_at   TIMESTAMP NOT NULL,
    heartbeat_at  TIMESTAMP NOT NULL,
    process_id    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_lease_holder_id ON instance_execution_leases(holder_id);
CREATE INDEX IF NOT EXISTS idx_lease_holder_kind ON instance_execution_leases(holder_kind);

-- DOWN

DROP INDEX IF EXISTS idx_lease_holder_kind;
DROP INDEX IF EXISTS idx_lease_holder_id;
DROP TABLE IF EXISTS instance_execution_leases;

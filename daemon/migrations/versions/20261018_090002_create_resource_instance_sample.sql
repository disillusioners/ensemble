-- Migration: create resource_instance_sample
--           (resource-cost-control Phase 1 — per-instance attribution)
-- Created: 2026-10-18
-- Phase: resource-cost-control P1 (plan:
--   .agents/shared/planning/resource-cost-control/phase1-plan.md).
--   Backing store for T2 ``resource_instance_sample``: <= 1 row /
--   (instance, tick). Cardinality cap <= 64/tick (architecture §2.4);
--   the overflow sentinel ``__overflow__`` row is the caller's choice.
--
-- What lives here:
--   * ``instance_id`` joinable to daemon.instances (no FK — the
--     instances table lives in a separate module and the dialect-
--     neutral text id is the universal join key per architecture §2.6).
--   * ``agent_id`` is denormalized for read-path locality (the
--     self-join from instance to agent is one hop saved per
--     /api/resources/live fetch).
--   * ``estimated`` is NOT NULL: per-instance in-process CPU/RAM is
--     PARTIAL by physics (the daemon shares one process); the
--     column labels the row honestly as an estimate.
--   * Attributable counters (``charged_bytes``, ``deep_bytes``,
--     ``child_cpu_user_usec``, ``cpu_share_usec_est``) are written
--     NULL/0 in P1 (D13 — P2 fills).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new table — created by
--     ``SQLModel.metadata.create_all`` from the
--     :class:`ResourceInstanceSample` model
--     (``daemon/models/resource_monitor.py``) via the late-import
--     block in ``daemon/manager.py:738-748``.
--   - Column names/types stay byte-identical to the
--     ResourceInstanceSample model (canonical source for PG).
--   - 3-site index lockstep (D7): the two named indexes below are
--     byte-identical to ``__table_args__`` in
--     ``daemon/models/resource_monitor.py:ResourceInstanceSample``
--     and to the storage test pack (AC3).
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner; inline
--   comments that contain a ``;`` character are NOT stripped. This
--   file uses no inline semicolons.

-- UP

CREATE TABLE IF NOT EXISTS resource_instance_sample (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_ts TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    agent_id TEXT,
    status TEXT,
    scan_kind TEXT,
    -- BIGINT in the SQLModel (PG int8); INTEGER on SQLite. The
    -- maintenance_runs BigInteger precedent — incident 2026-09-28.
    charged_bytes INTEGER,
    deep_bytes INTEGER,
    graph_cache_present INTEGER,
    in_flight_tasks INTEGER,
    all_state_messages_len INTEGER,
    child_rss_kb INTEGER,
    child_cpu_user_usec INTEGER,
    cpu_share_usec_est INTEGER,
    -- 0|1 in SQLite, false|true on PG. NOT NULL because every row
    -- is labelled: in-process per-instance attribution is partial by
    -- physics (the daemon shares one process; the council / spec
    -- independently flagged this — architecture §2.2).
    estimated INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_resource_instance_sample_instance_bucket
    ON resource_instance_sample(instance_id, bucket_ts);

CREATE INDEX IF NOT EXISTS idx_resource_instance_sample_bucket
    ON resource_instance_sample(bucket_ts);

-- DOWN

DROP INDEX IF EXISTS idx_resource_instance_sample_bucket;
DROP INDEX IF EXISTS idx_resource_instance_sample_instance_bucket;
DROP TABLE IF EXISTS resource_instance_sample;
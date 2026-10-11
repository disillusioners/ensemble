-- Migration: create stored_watches (fix e8a61da3 — usage-limit
--           retry-supersede double-silence, Option A depth 3)
-- Created: 2026-10-11
-- Phase: watch-loss fix, user-picked Option A+E per the architect
-- options review (.agents/shared/planning/watch-loss-fix-options/
-- options-matrix.md). One row per recovery-armed watch executed by
-- the in-transaction F6 hook in RetryTurn.apply (turn_transitions.py)
-- when a usage-limit deferral re-keys job_watchers from the cancelled
-- parent work_id onto the retry child's fresh work_id.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new table → created by
--     ``SQLModel.metadata.create_all`` from the StoredWatch model
--     (daemon/repositories/job_queue/stored_watch_models.py) — the
--     snapshot-tables precedent: brand-new tables need no
--     ``_ensure_postgres_columns`` mirror. The explicit
--     ``StoredWatch.metadata.create_all(manager.engine)`` call in the
--     api lifespan (next to the JobWatcher precedent at api.py) also
--     covers databases whose manager-level create_all ran before the
--     model module was imported.
--   - Index / unique-constraint names MUST stay byte-identical to the
--     StoredWatch model's ``__table_args__`` declaration (the
--     canonical source). The uq_stored_watches_original_work_watcher
--     constraint is the first-store-wins fence the in-txn hook's
--     INSERT ... ON CONFLICT DO NOTHING targets.
--   - ``original_watch_events`` is JSON (list of strings) via
--     JSONBType on the model — same dual-dialect JSON treatment as
--     ``job_watchers.watch_events`` and ``drift_events.files``.

-- UP

CREATE TABLE IF NOT EXISTS stored_watches (
    stored_watch_id TEXT PRIMARY KEY,
    original_work_id TEXT NOT NULL,
    retry_work_id TEXT NOT NULL,
    watcher_instance_id TEXT NOT NULL REFERENCES instances(instance_id),
    original_watch_events JSON NOT NULL DEFAULT '[]',
    mission_instance_id TEXT NOT NULL,
    work_instance_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'armed',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_stored_watches_original_work_watcher
    ON stored_watches(original_work_id, watcher_instance_id);
CREATE INDEX IF NOT EXISTS idx_stored_watches_original_work_id
    ON stored_watches(original_work_id);
CREATE INDEX IF NOT EXISTS idx_stored_watches_retry_work_id
    ON stored_watches(retry_work_id);
CREATE INDEX IF NOT EXISTS idx_stored_watches_watcher_instance_id
    ON stored_watches(watcher_instance_id);
CREATE INDEX IF NOT EXISTS idx_stored_watches_status_created
    ON stored_watches(status, created_at);

-- DOWN

DROP INDEX IF EXISTS idx_stored_watches_status_created;
DROP INDEX IF EXISTS idx_stored_watches_watcher_instance_id;
DROP INDEX IF EXISTS idx_stored_watches_retry_work_id;
DROP INDEX IF EXISTS idx_stored_watches_original_work_id;
DROP INDEX IF EXISTS uq_stored_watches_original_work_watcher;
DROP TABLE IF EXISTS stored_watches;

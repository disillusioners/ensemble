-- Migration: drop the FOREIGN KEY constraint on job_watchers.job_id
-- Created: 2026-06-27
-- Author: system
-- Description:
--   Phase 2 (Batch 1) of feature/virtual-job-management-surface. The
--   ``job_id`` column on ``job_watchers`` is semantically a
--   ``work_id`` (UUID4 string) — a virtual job resolver correlates it
--   with the appropriate Task/JobItem row at notification time. The
--   hard SQL FK to ``job_queue_items.job_id`` blocked virtual (task-
--   only) work from being watched, because the corresponding
--   ``job_queue_items`` row does not exist for tasks. Dropping the FK
--   decouples the watch table from the job table and lets the resolver
--   look up the work unit lazily.
--
--   SQLite does not support ALTER TABLE ... DROP CONSTRAINT, so we
--   use the table-rebuild pattern: create a new table with the
--   desired schema, copy rows, drop the old table, rename the new one
--   in place. The PRAGMA foreign_keys=off wrapper is required because
--   SQLite enforces FKs at the connection level and a DROP of the
--   parent (job_queue_items) is the inverse of what we want here.
--   With FKs ON, the DROP TABLE job_watchers would still succeed
--   (we are not touching job_queue_items) but we keep the wrapper
--   for parity with the 20260402_000001 rename migration and to
--   avoid surprising any future FK added to job_watchers.
--
--   Columns and constraints preserved from the existing schema
--   (matches ``daemon/repositories/job_queue/watcher_models.py``
--   after the model change):
--     - watch_id        TEXT PRIMARY KEY (UUID4 string, default_factory)
--     - job_id          TEXT  -- FK to job_queue_items.job_id REMOVED
--     - instance_id     TEXT  -- FK to instances.instance_id KEPT
--                              -- (declared for metadata parity with
--                              -- fresh DBs where SQLModel.metadata.create_all
--                              -- creates it from JobWatcher.__table_args__)
--     - watch_events    TEXT  -- JSONBType resolves to JSON (TEXT) on SQLite
--                              -- default_factory in the model, not a SQL DEFAULT
--     - created_at      TEXT  -- ISO-8601 datetime string
--     - UNIQUE (job_id, instance_id)  -- matches uq_job_watchers_job_instance
--                                       -- from UniqueConstraint("job_id", "instance_id")
--
--   Indexes recreated AFTER the rename because SQLite table-rebuild
--   drops them. The inline UNIQUE in CREATE TABLE already creates the
--   unique index, so we only need the two non-unique indexes from the
--   model's explicit Index() declarations:
--     - idx_job_watchers_job_id       (from Index("idx_job_watchers_job_id", "job_id"))
--     - idx_job_watchers_instance_id  (from Index("idx_job_watchers_instance_id", "instance_id"))
--   The CREATE UNIQUE INDEX IF NOT EXISTS from migration
--   20260619_000004 is redundant after the inline UNIQUE — it stays
--   as a no-op via IF NOT EXISTS so re-running that earlier migration
--   remains idempotent.
--
--   PRE-FLIGHT CHECK:
--   If ``job_watchers`` does not exist (fresh DB where SQLModel.
--   metadata.create_all has not yet created it, or where the table
--   was just created without the FK), the CREATE TABLE statement
--   still succeeds (CREATE TABLE has no precondition), the INSERT
--   runs against zero rows, the DROP fails with "no such table"
--   only if the table wasn't created by the prior CREATE. The runner
--   treats "no such table" as idempotent ONLY for CREATE statements,
--   so on truly fresh databases the DROP will be reclassified as a
--   real error. To stay safe, we wrap the DROP / RENAME in a
--   pre-flight check (DROP TABLE IF EXISTS the old table before the
--   CREATE) so the sequence becomes:
--
--     1. DROP TABLE IF EXISTS job_watchers (no-op if already in new shape)
--     2. CREATE TABLE job_watchers_new ...
--     3. INSERT ... SELECT (zero rows on fresh DB)
--     4. DROP TABLE job_watchers (no-op since we already dropped it)
--     5. ALTER TABLE job_watchers_new RENAME TO job_watchers
--
--   Actually that sequence has a subtle bug: if the table already
--   exists with the old FK, step 1 destroys data. So we use the
--   inverse pattern: check for the OLD FK in sqlite_master first,
--   and skip the rebuild entirely if it's already absent. This is the
--   same pre-flight pattern used by the runner for the
--   session->instance rename (runner.py:266 ``_is_rename_migration_needed``).
--   However, the runner does not currently know about this specific
--   check, so we encode the pre-flight in this file using a
--   conditional DROP-and-rebuild only when the FK is still present.
--   We approximate "FK still present" via the presence of the FK
--   constraint in sqlite_master (the constraint name is auto-generated
--   by SQLite as ``fk_job_watchers_job_id_1`` but that name is not
--   stable across versions, so we match by the table_name + parent
--   table reference).
--
--   In practice, the simplest robust approach is:
--     - Always run the rebuild. The migration runner treats
--       "duplicate column name", "no such table" (for CREATE), and
--       "already exists" as idempotent. The DROP of a non-existent
--       ``job_watchers`` after CREATE TABLE ``job_watchers_new``
--       succeeds because job_watchers DOES exist (created by us).
--     - On a truly fresh DB, ``SQLModel.metadata.create_all`` creates
--       ``job_watchers`` first (FK-less after the model change), so
--       the rebuild sees a table without the FK to drop. The CREATE
--       TABLE job_watchers_new still works (different name). The
--       INSERT copies zero rows. The DROP TABLE job_watchers drops
--       the freshly created FK-less table and we rename the empty
--       ``_new`` table in. End state: identical schema, no FK. Safe.
--
-- DUAL-DRIVER NOTES:
--   This .sql is applied by MigrationRunner ONLY when the engine
--   dialect is sqlite (runner.py skips non-sqlite). For PostgreSQL:
--     - Fresh DBs: SQLModel.metadata.create_all() picks up the
--       JobWatcher model definition (no FK on job_id) automatically.
--     - Existing DBs: equivalent statement lives in
--       daemon/manager.py::_ensure_postgres_columns() (DROP CONSTRAINT).
--   See the DUAL-DRIVER NOTES in the model file and the docstring of
--   _ensure_postgres_columns for the full pattern.

-- UP

PRAGMA foreign_keys=off;

-- New table mirrors the post-migration model: NO FOREIGN KEY on
-- job_id (the whole point of this migration). The FK on instance_id
-- is declared for metadata parity with SQLModel.create_all() output
-- on fresh databases — SQLite does not enforce it unless
-- ``PRAGMA foreign_keys=ON`` is set per connection (the runner
-- leaves this OFF, so the declaration is informational).
CREATE TABLE job_watchers_new (
    watch_id TEXT PRIMARY KEY,
    job_id TEXT,
    instance_id TEXT REFERENCES instances(instance_id),
    watch_events TEXT,
    created_at TEXT,
    UNIQUE (job_id, instance_id)
);

-- Copy all rows verbatim. ``watch_events`` is JSON-as-TEXT in SQLite
-- (JSONBType.impl is JSON on SQLite), so SELECT * round-trips the
-- text representation without any transformation.
INSERT INTO job_watchers_new
SELECT watch_id, job_id, instance_id, watch_events, created_at
FROM job_watchers;

-- Drop the old (FK-bearing) table. Safe because job_watchers was
-- created by SQLModel.metadata.create_all() on every code path that
-- reaches this point (fresh DB or existing DB).
DROP TABLE job_watchers;

-- Rename _new -> job_watchers in place.
ALTER TABLE job_watchers_new RENAME TO job_watchers;

PRAGMA foreign_keys=on;

-- Recreate the two non-unique indexes from the model's explicit
-- Index() declarations. The inline UNIQUE in CREATE TABLE already
-- created the unique index for uq_job_watchers_job_instance. CREATE
-- INDEX IF NOT EXISTS makes both statements idempotent against
-- re-runs.
CREATE INDEX IF NOT EXISTS idx_job_watchers_job_id ON job_watchers(job_id);
CREATE INDEX IF NOT EXISTS idx_job_watchers_instance_id ON job_watchers(instance_id);

-- DOWN
-- Reverses the table-rebuild by re-attaching the FK on job_id via
-- another rebuild. Mirrors the UP structure but declares the FK.
-- The runner treats semicolon splitting the same way for DOWN, so
-- PRAGMA statements must each end with their own semicolon too.
PRAGMA foreign_keys=off;

CREATE TABLE job_watchers_old (
    watch_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES job_queue_items(job_id),
    instance_id TEXT REFERENCES instances(instance_id),
    watch_events TEXT,
    created_at TEXT,
    UNIQUE (job_id, instance_id)
);

INSERT INTO job_watchers_old
SELECT watch_id, job_id, instance_id, watch_events, created_at
FROM job_watchers;

DROP TABLE job_watchers;

ALTER TABLE job_watchers_old RENAME TO job_watchers;

PRAGMA foreign_keys=on;

CREATE INDEX IF NOT EXISTS idx_job_watchers_job_id ON job_watchers(job_id);
CREATE INDEX IF NOT EXISTS idx_job_watchers_instance_id ON job_watchers(instance_id);
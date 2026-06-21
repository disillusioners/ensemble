-- Migration: create dependency_watchers table
-- Created: 2026-06-21
-- Author: system
-- Description:
--   Phase D (Dependency Bus) of the decouple architecture. Replaces the
--   CorrelationManager in-memory pending-children map with a DB-backed
--   authoritative "parent waits for children" mechanism. When the
--   ``use_dependency_bus`` flag is ON, ``send_message`` writes a row here
--   on every FollowUp-bearing call and ``MessageTaskProcessor.process``
--   calls ``bus.emit_terminal(task_id, outcome)`` which atomically
--   transitions matching PENDING rows to FIRED/CANCELLED.
--
--   The watcher state lives in a single table that:
--
--     1. Backed by SQLite on dev and PostgreSQL in production. JSONB
--        columns (follow_up_payload, metadata) use the JSONBType
--        TypeDecorator defined in
--        ``daemon/repositories/infra/types.py`` — that decorator maps
--        to ``JSONB`` on PostgreSQL and ``JSON`` on SQLite, so the
--        same schema works on both drivers. The raw-SQL column type
--        here is ``JSON`` for the same reason (SQLite has no JSONB
--        type; PostgreSQL will read JSONB values fine from a column
--        that the SQLAlchemy decorator re-types to JSONB at the ORM
--        layer — but for raw SQL the table definition uses JSON).
--
--     2. Hot-path lookup is the ``(source_task_id, state)`` composite
--        index — every terminal-event emit fires a single targeted
--        SELECT against this index to find the parents that are still
--        PENDING for a given child task.
--
--     3. Cancellation scans use the ``(target_instance_id, state)``
--        composite index — when a parent instance is stopped, the
--        cancellation service scans its PENDING watchers and either
--        marks them CANCELLED (no children to wait for) or preserves
--        the PENDING rows so already-emitted terminal events still
--        fire the FollowUp.
--
--   This migration is a CREATE TABLE (no existing rows to migrate). It
--   applies on both SQLite and PostgreSQL via the MigrationRunner.
--   On PostgreSQL, the table is also created by
--   ``SQLModel.metadata.create_all()`` for fresh DBs — the migration
--   handles the case where the DB was created before the model was
--   registered with metadata, and the ``IF NOT EXISTS`` clauses make
--   both paths idempotent.
--
--   Schema mirrors daemon/repositories/dependency_bus/models.py.

-- UP

CREATE TABLE IF NOT EXISTS dependency_watchers (
    watch_id TEXT PRIMARY KEY,
    source_task_id TEXT NOT NULL,
    target_instance_id TEXT NOT NULL,
    follow_up_payload JSON NOT NULL,
    metadata JSON NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    fired_at TEXT,
    state TEXT NOT NULL DEFAULT 'PENDING'
);

-- Hot-path lookup: "which parent instances are still waiting on this
-- child task?" Used by the bus on every terminal event emit. A small
-- (state) suffix is critical because the vast majority of rows in a
-- long-lived system are FIRED/CANCELLED — without it, every emit would
-- full-scan the source_task_id bucket.
CREATE INDEX IF NOT EXISTS ix_dependency_watchers_source_state
    ON dependency_watchers(source_task_id, state);

-- Cancellation scan: "which child tasks are still pending for this
-- parent instance?" Used by the cancellation service when a parent is
-- stopped — the same state-suffix trade-off applies.
CREATE INDEX IF NOT EXISTS ix_dependency_watchers_target_state
    ON dependency_watchers(target_instance_id, state);

-- DOWN

DROP INDEX IF EXISTS ix_dependency_watchers_target_state;
DROP INDEX IF EXISTS ix_dependency_watchers_source_state;
DROP TABLE IF EXISTS dependency_watchers;

-- Migration: create source-GC archive tables (schedule_executions_archive,
-- source_configs_archive) — source-gc lane (board wi-ed230eb4).
-- Created: 2026-11-07
-- Phase: source-gc Phase 4 (plan:
--   .agents/shared/planning/source-gc/phase4-plan.md §2 — spec verbatim).
--
-- What lives here:
--   * ``schedule_executions_archive`` — the execution history of a reaped
--     scheduler source, copied BEFORE the child-first delete (the delete
--     is same-transaction: copy-then-delete, decisions.md D1-final).
--   * ``source_configs_archive`` — the full config snapshot of the reaped
--     source. ``config`` holds the JSON-serialized snapshot and is the
--     SOLE reconstruction input for a restored schedule (advisory C —
--     no restore API exists in v1 — recovery after a reap is MANUAL).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the models in
--     ``daemon/repositories/source/models.py`` as the canonical source.
--     The models register into the metadata via the
--     ``daemon/repositories/__init__.py`` import chain (module execution
--     registers every SQLModel class — the ``ScheduleExecution``
--     precedent) BEFORE the create_all call. The PG-side runner is a
--     NO-OP for this file.
--   - EVERY STRING COLUMN IS TEXT — never VARCHAR, in this file.
--   - NO FK to any existing table (archive ids are plain TEXT — memoir
--     precedent: house archive tables keep no FK to the table they
--     record).
--   - 3-site index lockstep: the index names AND column lists below are
--     byte-identical to the ``__table_args__`` of the models (and to the
--     P20 unit pin's assertion).
--   - NO ``credentials`` column (AM-4): scheduler rows carry
--     ``credentials=None`` always (zero credential references in the
--     scheduler adapter) — chat credentials that exist are Fernet
--     ciphertext at rest and can never enter this GC's reachable set.
--     Always-NULL column = pointless second secret surface.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path ``SQLModel.metadata.create_all(self._engine)`` runs
--   FIRST and the migration runner applies this file IMMEDIATELY AFTER
--   it. By the time this file is applied, create_all has already
--   created the two tables AND the model-declared indexes. A bare
--   CREATE would therefore raise "already exists" and the runner would
--   SWALLOW that error on its idempotent-skip branch — the failure
--   would be silent and this file would degrade into a no-op on every
--   boot. IF NOT EXISTS removes both failure modes and makes the file
--   applicable in the real boot order.
--
-- ROLLBACK POSTURE:
--   Additive only. The DOWN section drops the two tables (children
--   first). Rollback of the RELEASE is therefore FIX-LOSS, never an
--   incompatibility: the pre-lane tree references none of these tables,
--   so the old binary simply never touches them.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. This file uses no semicolon in any comment.

-- UP
CREATE TABLE IF NOT EXISTS schedule_executions_archive (
    execution_id  TEXT PRIMARY KEY,
    schedule_id   TEXT NOT NULL,
    triggered_at  TEXT,
    instance_id   TEXT,          -- survives the unmapped instance_mappings delete (architect §3 disclosure)
    status        TEXT,
    error_message TEXT,
    completed_at  TEXT,
    archived_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_executions_archive_schedule_id
    ON schedule_executions_archive (schedule_id);
CREATE INDEX IF NOT EXISTS idx_schedule_executions_archive_archived_at
    ON schedule_executions_archive (archived_at);

CREATE TABLE IF NOT EXISTS source_configs_archive (
    source_id     TEXT PRIMARY KEY,
    source_type   TEXT,
    name          TEXT,
    config        TEXT,          -- JSON-serialized snapshot (the only reconstruction input — SP-4)
    enabled       INTEGER,       -- boolean per driver
    autostart     INTEGER,
    status        TEXT,
    error_message TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    archived_at   TEXT NOT NULL
);                               -- AM-4: NO credentials column (scheduler rows are always NULL, chat rows unreachable)
CREATE INDEX IF NOT EXISTS idx_source_configs_archive_archived_at
    ON source_configs_archive (archived_at);
-- DOWN (never executes on rollback): children first
-- DROP INDEX IF EXISTS idx_source_configs_archive_archived_at;
-- DROP TABLE IF EXISTS source_configs_archive;
-- DROP INDEX IF EXISTS idx_schedule_executions_archive_archived_at;
-- DROP INDEX IF EXISTS idx_schedule_executions_archive_schedule_id;
-- DROP TABLE IF EXISTS schedule_executions_archive;

-- Migration: widen ck_job_queues_queue_type to include 'defer' and 'background' queue types
-- Created: 2026-07-14
-- Author: system
-- Description: Widen the CHECK constraint on job_queues.queue_type so the column
--              accepts the four queue types the JobQueue model declares:
--              'fifo', 'parallel', 'defer', 'background'. Without this, INSERTs
--              for system_defer_queue / system_background_queue provisioning fail
--              with a CHECK constraint violation, blocking E2E tests that exercise
--              the defer / background queue lanes.
--
--              The original 2026-04-09 migration declared:
--                  CHECK(queue_type IN ('fifo', 'parallel'))
--              which predates the defer + background lanes (Phase 3, 2026-06-27).
--              The JobQueue SQLModel was updated at that time, but the .sql
--              migration was never written, so any SQLite database created from
--              the original schema still rejects 'defer' / 'background' values.
--
--              PostgreSQL counterpart: ``daemon/manager.py::_ensure_postgres_columns``
--              (the .sql runner is a NO-OP on non-SQLite, so the PG version
--              drops + re-adds the constraint inline for parity with fresh
--              databases where ``SQLModel.metadata.create_all()`` picks up
--              the wider constraint from the model).
--
-- DUAL-DRIVER NOTES
--   - SQLite supports ``ALTER TABLE ... DROP CONSTRAINT`` since 3.35.0
--     (via table-rebuild under the hood); for portability we use the
--     explicit DROP/ADD pair documented by the SQLite docs.
--   - The constraint name ``ck_job_queues_queue_type`` matches the
--     ``CheckConstraint(name=...)`` declared on the JobQueue SQLModel
--     (``daemon/repositories/job_queue/models.py``), so both dialects
--     converge on the same constraint name.

-- UP

ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type;
ALTER TABLE job_queues ADD CONSTRAINT ck_job_queues_queue_type
  CHECK (queue_type IN ('fifo', 'parallel', 'defer', 'background'));

-- DOWN
-- Re-narrow the constraint to the original 2026-04-09 value set.
-- WARNING: this will fail if any row currently has queue_type IN
-- ('defer', 'background'); delete / migrate those rows first.
-- ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type;
-- ALTER TABLE job_queues ADD CONSTRAINT ck_job_queues_queue_type
--   CHECK (queue_type IN ('fifo', 'parallel'));
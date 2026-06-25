-- Migration: rename agent_id 'coder' → 'developer' across all tables
-- Created: 2026-06-26
-- Author: system
-- Description:
--   Phase 4 of the agent rename (coder → developer) — data migration
--   for the SQLite path. Updates ``agent_id`` (and the related
--   ``agent_dir`` path component) wherever the old ``coder`` agent
--   identifier was persisted, so existing rows continue to resolve to
--   the renamed ``agents/developer/`` directory and the registry's
--   ``resolve_pure_id('coder')`` backward-compat shim is no longer
--   the sole point of translation.
--
--   Mirrors the equivalent UPDATEs that lived in
--   ``daemon/repositories/factory.py:run_migrations()`` (the legacy
--   Python migration path, now disabled). The factory.py block is
--   unreachable for fresh DBs created after the disablement, and the
--   MigrationRunner (which is the production path for SQLite)
--   previously had no .sql equivalent — meaning SQLite databases
--   could start with ``agent_id='coder'`` rows still in place after
--   the rename. This file closes that gap.
--
-- DUAL-DRIVER NOTES:
--   MigrationRunner consumes .sql files ONLY for sqlite dialects
--   (runner.py: ``if "sqlite" not in str(self.engine.url): return []``).
--   For PostgreSQL the schema/data evolution is handled by
--   ``EnsembleManager._ensure_postgres_columns`` in ``daemon/manager.py``,
--   which must be extended in lockstep with any new .sql migration
--   that adds schema elements requiring pre-existence on existing PG
--   databases. The rename is a data-only migration; for PG it is
--   handled in ``_ensure_postgres_columns`` directly (parallel
--   UPDATEs to the ones below), and this .sql file is intentionally
--   not applied there.
--
-- IDEMPOTENCY:
--   The WHERE clause ``agent_id = 'coder'`` (or
--   ``creator_agent_id = 'coder'``) makes every UPDATE a no-op once
--   the rename is complete. Re-running this file on a DB where the
--   rename already happened updates zero rows and exits cleanly.
--   The runner records the version in ``schema_migrations`` after
--   the first successful run, so subsequent startups skip the file
--   entirely.
--
-- LEGACY ``jobqueue`` TABLE:
--   The original factory.py block also updated a legacy ``jobqueue``
--   table that pre-dated the rename to ``job_queue_items``. That
--   table does not exist on fresh DBs and the runner only treats
--   "no such table" as idempotent for CREATE statements — for
--   UPDATE statements it raises MigrationError, which would mark
--   this file as failed for any DB that never had the legacy table.
--   The standalone ``scripts/migrate_coder_to_developer.py`` script
--   already handles ``jobqueue`` defensively (try/except with a
--   warning) for the rare DBs that still carry it. Omitting it here
--   keeps the auto-apply path safe on every supported schema state.

-- UP

UPDATE instances
   SET agent_id = 'developer',
       agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer')
 WHERE agent_id = 'coder';

UPDATE instance_mappings
   SET agent_id = 'developer',
       agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer')
 WHERE agent_id = 'coder';

UPDATE job_queue_items
   SET agent_id = 'developer',
       agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer')
 WHERE agent_id = 'coder';

UPDATE dead_letter_items
   SET agent_id = 'developer',
       agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer')
 WHERE agent_id = 'coder';

UPDATE projects
   SET creator_agent_id = 'developer'
 WHERE creator_agent_id = 'coder';

-- DOWN
-- Best-effort reverse of the UP block. Only restores rows whose
-- agent_dir matches the new layout -- rows whose agent_dir was
-- independently edited between UP and DOWN will not be touched.
-- Idempotent in the same way the UP block is.
--
-- Note: avoid semicolons inside SQL comments in this section.
-- The migration runner splits statements on ';' and does not
-- respect SQL comment syntax, so a ';' embedded in a -- comment
-- would split the chunk mid-statement and produce a syntax error.

UPDATE instances
   SET agent_id = 'coder',
       agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder')
 WHERE agent_id = 'developer'
   AND agent_dir LIKE '%/agents/developer%';

UPDATE instance_mappings
   SET agent_id = 'coder',
       agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder')
 WHERE agent_id = 'developer'
   AND agent_dir LIKE '%/agents/developer%';

UPDATE job_queue_items
   SET agent_id = 'coder',
       agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder')
 WHERE agent_id = 'developer'
   AND agent_dir LIKE '%/agents/developer%';

UPDATE dead_letter_items
   SET agent_id = 'coder',
       agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder')
 WHERE agent_id = 'developer'
   AND agent_dir LIKE '%/agents/developer%';

UPDATE projects
   SET creator_agent_id = 'coder'
 WHERE creator_agent_id = 'developer';
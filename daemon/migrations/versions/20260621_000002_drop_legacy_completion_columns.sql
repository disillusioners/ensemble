-- Migration: drop legacy completion columns (D10)
-- Created: 2026-06-21
-- Author: system
-- Description:
--   Phase D (Dependency Bus) of the decouple architecture. Drops the
--   legacy completion-state columns and junction table that were
--   used by the CorrelationManager (pre-Bus) implementation:
--
--     * ``instances.waiting_for`` (INTEGER, count of pending children)
--     * ``instances.children``     (TEXT/JSON, denormalized cache of child IDs)
--     * ``instance_hierarchy`` junction table (parent_id, child_id)
--
--   After the Dependency Bus is the single completion authority,
--   these are dead artifacts:
--
--     - ``waiting_for`` was a REBUILD-ONLY cache, never read for
--       runtime control flow (CorrelationManager.is_complete() /
--       get_pending_count() are the source of truth). See C10.
--     - ``children`` was the denormalized cache that was doubly
--       broken (RMW races at 4 sites AND overridden on every read
--       via _enrich_instance()). See C10.
--     - ``instance_hierarchy`` was the parent->child junction
--       table, replaced functionally by ``dependency_watchers``
--       (Phase D Phase 1, migration 20260621_000001) plus the
--       existing ``instances.parent_id`` column for tree
--       enumeration. See unified-dispatcher.md §step 7.
--
--   ============================================================================
--   WARNING: THIS MIGRATION IS IRREVERSIBLE AND DATA-DESTRUCTIVE.
--   ============================================================================
--   All data in the dropped columns and table is PERMANENTLY LOST
--   after this migration runs. The DOWN section below recreates the
--   schema (empty columns, empty table) but the data is gone --
--   there is no recovery path.
--
--   This migration is NOT auto-applied. Per the reviewer's §7.2
--   recommendation, it exists as a file so operators can apply it
--   manually AFTER 2+ weeks of clean Dependency Bus operation in
--   production. Do NOT apply until:
--
--     1. The Dependency Bus flag has been ON in production for
--        2+ weeks with no rollbacks to ``USE_LEGACY_WAITING_FOR_CASCADE``.
--     2. A pre-migration snapshot/backup of the database has been
--        taken and verified.
--     3. Operators are on-call and prepared to roll forward (the
--        DOWN section below does NOT restore data).
--
--   ============================================================================
--   WARNING: RUNNING THIS DESTROYS THE CORRELATION-MANAGER ROLLBACK PATH.
--   ============================================================================
--   The ``waiting_for`` column holds the rebuild cache that the
--   legacy CorrelationManager kill-switch
--   (``USE_LEGACY_WAITING_FOR_CASCADE=ON``) depends on. After this
--   migration runs, setting ``USE_DEPENDENCY_BUS=false`` will have
--   NO ``waiting_for`` data to fall back to. Verify the Dependency
--   Bus is fully clean before applying.
--
--   ============================================================================
--   Dialect notes:
--     - PostgreSQL: supports ``ALTER TABLE ... DROP COLUMN IF EXISTS``
--       natively. This is the recommended target environment. The
--       migration runner NO-OPs all .sql files on PostgreSQL
--       (runner.py lines 446-448), so this file is INERT on
--       PostgreSQL until an operator applies it manually (e.g.
--       via psql) -- which is the intended behavior.
--     - SQLite 3.35.0+ supports ``ALTER TABLE ... DROP COLUMN`` but
--       does NOT support the ``IF EXISTS`` variant. The migration
--       runner DOES pick up .sql files on SQLite. If you intend to
--       run this on SQLite, either (a) skip the file via the
--       runner's filter mechanism, (b) hand-edit the UP section
--       to drop the IF EXISTS clauses, or (c) upgrade SQLite and
--       accept the first-run syntax error on a one-time manual
--       retry. The post-drop ``DROP TABLE IF EXISTS`` clause is
--       supported on both dialects.
--
--   Schema reference: daemon/repositories/instance/models.py
--     - ``Instance.waiting_for``  line 79 (INTEGER, default 0)
--     - ``Instance.children``      line 73 (TEXT, default '[]')
--     - ``InstanceHierarchy``      line 38 (__tablename__ =
--       "instance_hierarchy", PK = (parent_id, child_id))

-- UP

-- Drop waiting_for (count of pending children; rebuild-only cache,
-- never read for runtime control flow).
ALTER TABLE instances DROP COLUMN IF EXISTS waiting_for;

-- Drop children (denormalized JSON cache of child IDs; replaced by
-- enrichment from instance_hierarchy on every read; doubly broken
-- under contention per C10).
ALTER TABLE instances DROP COLUMN IF EXISTS children;

-- Drop instance_hierarchy junction table (parent_id, child_id).
-- Parent->child relationships are recoverable from the
-- ``parent_id`` column on ``instances`` (see
-- unified-dispatcher.md §step 7) and from ``dependency_watchers``
-- rows (Phase D Phase 1).
DROP TABLE IF EXISTS instance_hierarchy;

-- DOWN
-- ============================================================================
-- DATA LOSS WARNING
-- ============================================================================
-- This DOWN section recreates the schema (columns, table) as empty
-- containers. The data that was in those columns and rows at the
-- time the UP migration ran is PERMANENTLY LOST. Rolling back does
-- NOT restore the data -- it only restores the ability for new
-- instances to write to those columns. Schema recreation is NOT
-- data recovery.

-- Recreate waiting_for as INTEGER (rebuild-only cache; default 0).
-- Matches the SQLModel definition at
-- daemon/repositories/instance/models.py:79.
ALTER TABLE instances ADD COLUMN waiting_for INTEGER NOT NULL DEFAULT 0;

-- Recreate children as TEXT (denormalized cache; default '[]').
-- Matches the SQLModel definition at
-- daemon/repositories/instance/models.py:73.
ALTER TABLE instances ADD COLUMN children TEXT NOT NULL DEFAULT '[]';

-- Recreate instance_hierarchy junction table (empty).
-- Matches the SQLModel definition at
-- daemon/repositories/instance/models.py:38-44.
CREATE TABLE IF NOT EXISTS instance_hierarchy (
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);

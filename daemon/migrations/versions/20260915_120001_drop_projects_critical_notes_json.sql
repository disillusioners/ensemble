-- Migration: drop the dead projects.critical_notes JSON column
-- Created: 2026-09-15
-- Author: coder (critical-notes-retrieval Phase 1)
-- Description:
--   Drop the legacy ``projects.critical_notes`` JSON column. The column
--   has been dead since the critical_notes table extraction (the SQLModel
--   Project has no such field — fresh create_all() lineages never get
--   it); this migration retires it on lineages that predate the table.
--
--   PRECONDITION MECHANISM (N4, 2026-09-15 — LEADER-CHOSEN SHAPE:
--   discoverable column, not an inline check): the file declares a
--   PRECONDITION header marker (see below). The runner parses the
--   marker (same header-parsing family as the manual-only marker),
--   evaluates it BEFORE executing any SQL (``SELECT sqlite_version()``
--   semver compare), and on failure records a SKIP-LEDGER row: the row
--   lands in ``schema_migrations`` with ``skip_reason`` populated (and
--   the declared ``precondition`` stored on the row for
--   discoverability), a WARNING is logged, boot proceeds, and the
--   migration NEVER RE-FIRES (``get_applied_versions`` counts the skip
--   row as applied). The ledger columns ``precondition`` /
--   ``skip_reason`` were added to ``SchemaMigration`` in the same
--   commit — see ``daemon/migrations/models.py`` +
--   ``runner.py::_sync_migrations_table_schema``. The mechanism is
--   pinned in ``daemon/migrations/README.md`` (Migration File Format →
--   Preconditions).
--
--   DUAL-DRIVER NOTES:
--     * SQLite >= 3.35.0 supports DROP COLUMN; older versions execute
--       nothing (skip-with-ledger-marker + WARN — the harmless dead
--       column is retained, never failing boot).
--     * PostgreSQL executes this drop via the MIRROR, not this file:
--       the ordered .sql runner is a NO-OP on PG (runner.py), so
--       ``_ensure_postgres_columns`` (daemon/manager.py) carries
--       ``ALTER TABLE projects DROP COLUMN IF EXISTS critical_notes``
--       for existing PG databases. Fresh PG databases never had the
--       column (create_all() emits the model, which has no such field).
--     * DOWN re-adds the column, gated on the SAME precondition
--       (runner-side: rollback evaluates the declared precondition and
--       skips SQL on failure while still clearing the ledger row). On
--       PG the DOWN is a manual operation (see the mirror comment) —
--       the runner never runs .sql DOWN on PG.

-- PRECONDITION: sqlite>=3.35.0

-- UP

ALTER TABLE projects DROP COLUMN critical_notes;

-- DOWN

ALTER TABLE projects ADD COLUMN critical_notes JSON DEFAULT '[]';

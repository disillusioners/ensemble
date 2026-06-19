-- Migration: Add UNIQUE constraint on projects(name) for H14
-- Created: 2026-06-19
-- Author: system
-- Description:
--   C8 + H11 + H12 + H14 project repository concurrency fix rollup:
--
--   H14: ``SQLModelProjectRepository.create`` used to perform a
--   check-then-insert (``SELECT WHERE name = ?`` then ``INSERT``) which
--   had a TOCTOU race: two concurrent callers with the same name both
--   passed the pre-flight check and both reached the INSERT, producing
--   duplicate ``projects`` rows. The repository now relies on the
--   ``uq_projects_name`` UNIQUE constraint declared in
--   ``Project.__table_args__`` and translates the resulting
--   ``IntegrityError`` into a clean ``ValueError``.
--
--   For PostgreSQL (the v0.5.2+ default dialect),
--   ``SQLModel.metadata.create_all()`` picks up the new
--   ``UniqueConstraint`` automatically on fresh DBs -- same precedent
--   as the instance_mappings, job_watchers, lock_slot, version, and
--   infra_assets migrations. Existing PostgreSQL DBs need to add the
--   constraint manually:
--       ALTER TABLE projects ADD CONSTRAINT uq_projects_name UNIQUE (name);
--   This .sql migration is therefore skipped by the runner on
--   PostgreSQL and applied only on SQLite.
--
--   SQLite does not retroactively apply new ``__table_args__`` to
--   tables created by ``SQLModel.metadata.create_all()``, so we add a
--   ``CREATE UNIQUE INDEX IF NOT EXISTS`` here. Functionally equivalent
--   for ``INSERT ... ON CONFLICT`` purposes on SQLite, and the
--   migration runner (daemon/migrations/runner.py) treats "already
--   exists" errors as idempotent, so re-running this file is safe.
--
--   PRE-FLIGHT DEDUP:
--   Before creating the UNIQUE INDEX, any pre-existing duplicate
--   ``projects.name`` rows must be removed, otherwise the
--   ``CREATE UNIQUE INDEX`` would fail with
--   "UNIQUE constraint failed: projects.name" and the runner would
--   treat that as a real error rather than the idempotent
--   "already exists" case. We keep the row with the largest ``rowid``
--   per ``name`` group, which is the most recently inserted project
--   under normal operation. FKs from ``project_metadata_records``,
--   ``instances``, etc. cascade on delete per the
--   ``ondelete="CASCADE"`` declarations in those models.
--
--   H12: The ``project_tags`` and ``project_shortnames`` junction
--   tables already enforce ``(project_id, tag)`` / ``(project_id,
--   shortname)`` uniqueness via their composite primary keys, so
--   ``add_tag`` / ``remove_tag`` / ``add_shortname`` / ``remove_shortname``
--   already have the column set required for ``INSERT ... ON CONFLICT
--   DO NOTHING`` via the dialect-aware ``on_conflict_do_nothing``
--   helper. No separate UNIQUE INDEX is needed for those tables, and
--   attempting to add one would error on the duplicate index name
--   with the existing composite primary key.
--
--   C8 + H11: ``relationships`` and ``related_directories`` are JSON
--   columns on the ``projects`` table. ``add_relationship`` /
--   ``remove_relationship`` and ``add_related_directory`` /
--   ``remove_related_directory`` now issue dialect-aware single-
--   statement UPDATEs (``jsonb_set`` / ``json_set`` /
--   ``json_array_elements_text`` / ``json_each``) instead of
--   in-Python RMW. No schema change is required for these fixes.
--
--   NOTE: this migration file deliberately avoids semicolons inside
--   SQL comments because runner.py executes the UP section via
--   ``migration.up_sql.split`` with ``;`` as separator and naively
--   treats every semicolon as a statement boundary regardless of
--   whether it sits inside a ``--`` comment line.

-- UP

-- STEP 1: Dedupe pre-existing duplicates. The DELETE must run before
-- the index is created, otherwise the CREATE UNIQUE INDEX would fail.
DELETE FROM projects
WHERE rowid NOT IN (
    SELECT MAX(rowid) FROM projects GROUP BY name
);

-- STEP 2: Enforce at most one project per name. Together with the
-- IntegrityError-to-ValueError translation in
-- SQLModelProjectRepository.create and .update, this makes name
-- uniqueness atomic across processes. Two concurrent callers creating
-- projects with the same name both reach the INSERT, the loser gets a
-- UNIQUE constraint violation, and the dialect-specific on_conflict
-- path is irrelevant because we use bare INSERT (not INSERT ... ON
-- CONFLICT) for the projects row itself.
CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_name
    ON projects (name);

-- DOWN

DROP INDEX IF EXISTS uq_projects_name;

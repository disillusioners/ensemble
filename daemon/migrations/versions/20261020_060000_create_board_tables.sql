-- Migration: create board tables
--           (ari-tools Phase 1 — the Workbook)
-- Created: 2026-10-20
-- Phase: ari-tools P1 (plan:
--   .agents/shared/planning/ari-tools/phase1-plan.md).
--   The global, DB-backed work board: three tables that carry every
--   durable work item, its append-only note timeline, and its tag
--   junction.
--
-- What lives here:
--   * ``board_items``      — one row per work item (title, markdown
--     body, 4-value status, optional project FK, optional deadline,
--     priority, linked job ids, provenance).
--   * ``board_notes``      — the APPEND-ONLY per-item timeline. The
--     child FK to ``board_items.id`` is NO ACTION, so a hard delete of
--     the parent item is refused by the database. That refusal is the
--     design, not a side effect.
--   * ``board_item_tags``  — the item/tag junction. Composite PRIMARY
--     KEY ``(item_id, tag)`` dedupes the stored rows. The junction rows
--     are written ONLY at create, so tags are IMMUTABLE post-create.
--
-- GLOBAL NAMESPACE (D1):
--   There is deliberately no context_key, no tree_root_id, no source
--   and no project-scoped table here. Every Ari instance and the
--   leader read and write the SAME rows. Project and channel are
--   attributes of an item (a nullable project FK, the channel as a
--   plain tag string).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the :class:`BoardItem`,
--     :class:`BoardNote` and :class:`BoardItemTag` models
--     (``daemon/models/board.py``) as the canonical source. The models
--     register into the metadata via the one-line late-import block in
--     ``daemon/manager.py`` BEFORE the create_all call (the
--     app_settings / snapshot / maintenance_runs / drift_events
--     precedent). Brand-new tables need NO ``_ensure_postgres_columns``
--     mirror. The PG-side runner is a NO-OP for this file.
--   - Column names and types stay byte-identical to the models
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR.
--   - 3-site index lockstep (D7): the index names AND column lists
--     below are byte-identical to the ``__table_args__`` of the models
--     in ``daemon/models/board.py`` (and to the N7 parity test).
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path ``SQLModel.metadata.create_all(self._engine)``
--   runs FIRST and the migration runner applies this file IMMEDIATELY
--   AFTER it. By the time this file is applied, create_all has
--   already created the three tables AND the six model-declared
--   indexes. A bare CREATE would therefore raise "already exists" and
--   the runner would SWALLOW that error on its idempotent-skip branch
--   — the failure would be silent and this file would degrade into a
--   no-op on every boot. IF NOT EXISTS removes both failure modes and
--   makes the file applicable in the real boot order.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. This file uses no semicolon in any comment.

-- UP

CREATE TABLE IF NOT EXISTS board_items (
    id TEXT NOT NULL PRIMARY KEY,
    title TEXT NOT NULL,
    body_md TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL,
    due_at TEXT,
    priority INTEGER,
    linked_job_ids JSON NOT NULL DEFAULT '[]',
    created_by_instance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_board_items_status_updated
    ON board_items (status, updated_at);

CREATE INDEX IF NOT EXISTS idx_board_items_updated_at
    ON board_items (updated_at);

CREATE INDEX IF NOT EXISTS idx_board_items_project_id
    ON board_items (project_id);

CREATE INDEX IF NOT EXISTS idx_board_items_due_at
    ON board_items (due_at);

CREATE TABLE IF NOT EXISTS board_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES board_items(id),
    ts TEXT NOT NULL,
    author_instance TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_board_notes_item_id_ts
    ON board_notes (item_id, ts);

CREATE TABLE IF NOT EXISTS board_item_tags (
    item_id TEXT NOT NULL REFERENCES board_items(id),
    tag TEXT NOT NULL,
    PRIMARY KEY (item_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_board_item_tags_tag_item
    ON board_item_tags (tag, item_id);

-- DOWN
--
-- The child tables are dropped BEFORE the parent so the rollback path
-- is order-safe with the NO ACTION foreign keys in place.

DROP INDEX IF EXISTS idx_board_item_tags_tag_item;
DROP TABLE IF EXISTS board_item_tags;
DROP INDEX IF EXISTS idx_board_notes_item_id_ts;
DROP TABLE IF EXISTS board_notes;
DROP INDEX IF EXISTS idx_board_items_due_at;
DROP INDEX IF EXISTS idx_board_items_project_id;
DROP INDEX IF EXISTS idx_board_items_updated_at;
DROP INDEX IF EXISTS idx_board_items_status_updated;
DROP TABLE IF EXISTS board_items;

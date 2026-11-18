-- Migration: create agent group chat tables (Phase 2 / P1 substrate)
--           (user-ratified design: .agents/shared/planning/group-chat/
--           architecture-recommendation.md §1.3 — the spec of record)
-- Created: 2026-11-15
-- Phase: group-chat P1 — substrate / persistence core + boot
--   (branch feature/group-chat-p1, base 30e7cf69c).
--
-- What lives here (exactly 3 CREATE TABLE — ratified item #1: the
-- generic own-.sql-per-table doctrine is explicitly superseded for
-- this lane by the multi-table-single-file precedent: board tables
-- 20261020_060000, memoir-v2 20261109_021154):
--   * ``chatrooms``         — room identity + scope + lifecycle.
--     ``state`` carries exactly active | closed; PURGE is a terminal
--     TRANSITION (whole-row deletion), not a persisted state (R2 —
--     purged rooms are nonexistent; no tombstone, no archive tables).
--   * ``chatroom_members``  — roster truth + the per-participant
--     cursor substrate. EVERY participant has a row, INCLUDING the
--     owner (seeded at create, role=owner, cursors 0 — D2). The
--     re-derivation predicate is
--     seq > max(last_read_seq, last_delivered_seq).
--   * ``room_messages``     — the durable append-only log
--     (BoardNote-adapted). UNIQUE(room_id, seq) is the per-room
--     monotonic-seq backstop behind the per-room write lock.
--     addressed_members is the R1 structured routing field.
--
-- NO 4th table — the watermark lives on chatroom_members (folded
-- cursors; architecture §1.3 last row).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql for
--     the 3-table substrate — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the :class:`ChatRoom`,
--     :class:`ChatRoomMember` and :class:`RoomMessage` models
--     (``daemon/repositories/room/models.py``) as the canonical
--     source. The models register into the metadata via the
--     pre-create_all late-import block in ``daemon/manager.py``
--     BEFORE the create_all call (the app_settings / board / POK
--     precedent). Brand-new tables need NO ``_ensure_postgres_columns``
--     mirror. The PG-side runner is a NO-OP for this file.
--   - Column names and types stay byte-identical to the models
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR. ``seq`` and the cursors are BIGINT on
--     PG / INTEGER-affinity on SQLite (SQLAlchemy BigInteger on both).
--   - Index lockstep: the index names AND column lists below are
--     byte-identical to the ``__table_args__`` of the models in
--     ``daemon/repositories/room/models.py`` (and to the parity pin
--     test tests/unit/repositories/test_room_models_and_migration.py).
--
-- FK SHAPE (NO ACTION — the BoardNote shape):
--   Both child tables reference ``chatrooms(room_id)`` with NO ACTION
--   (no ``ON DELETE`` clause at all): a hard delete of the parent room
--   is refused by the database UNLESS the children are deleted first.
--   That refusal is the design, not a side effect — the purge
--   transaction deletes messages → members → room in ONE transaction
--   (R2: one content-free ``room_purged`` event row + the three
--   deletes commit or roll back atomically).
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path ``SQLModel.metadata.create_all(self._engine)``
--   runs FIRST and the migration runner applies this file IMMEDIATELY
--   AFTER it. By the time this file is applied, create_all has
--   already created the three tables AND the model-declared indexes.
--   A bare CREATE would therefore raise "already exists" and the
--   runner would SWALLOW that error on its idempotent-skip branch —
--   the failure would be silent and this file would degrade into a
--   no-op on every boot. IF NOT EXISTS removes both failure modes.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. This file uses no semicolon in any comment.
--
-- ROLLBACK (additive-only; rollback_safe=true): DOWN drops the child
-- tables BEFORE the parent (order-safe with the NO ACTION FKs), then
-- the operator deletes this file's schema_migrations row. Accepted
-- loss = the rooms' content (R2 posture — a purge is the same
-- deletion, deliberately).

-- UP

CREATE TABLE IF NOT EXISTS chatrooms (
    room_id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    tree_root_id TEXT NOT NULL,
    owner_instance_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    closed_at TEXT,
    last_post_at TEXT,
    message_count BIGINT NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chatrooms_tree_root_id
    ON chatrooms (tree_root_id);

CREATE INDEX IF NOT EXISTS idx_chatrooms_state_closed_at
    ON chatrooms (state, closed_at);

CREATE INDEX IF NOT EXISTS idx_chatrooms_state_last_post_at
    ON chatrooms (state, last_post_at);

CREATE TABLE IF NOT EXISTS chatroom_members (
    room_id TEXT NOT NULL REFERENCES chatrooms(room_id),
    instance_id TEXT NOT NULL,
    member_name TEXT NOT NULL,
    agent_id TEXT,
    role TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    last_read_seq BIGINT NOT NULL DEFAULT 0,
    last_delivered_seq BIGINT NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (room_id, instance_id),
    UNIQUE (room_id, member_name)
);

CREATE INDEX IF NOT EXISTS idx_chatroom_members_instance_id
    ON chatroom_members (instance_id);

CREATE TABLE IF NOT EXISTS room_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL REFERENCES chatrooms(room_id),
    seq BIGINT NOT NULL,
    author_instance_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    text TEXT NOT NULL,
    tagged_members JSON,
    addressed_members JSON,
    kind TEXT NOT NULL DEFAULT 'message',
    created_at TEXT NOT NULL,
    UNIQUE (room_id, seq)
);

-- DOWN
--
-- The indexes drop BEFORE the tables (a table drop would take its
-- indexes with it, turning the DROP INDEX lines into silent no-ops);
-- then the child tables drop BEFORE the parent so the rollback path
-- is order-safe with the NO ACTION foreign keys in place.

DROP INDEX IF EXISTS idx_chatroom_members_instance_id;
DROP INDEX IF EXISTS idx_chatrooms_state_last_post_at;
DROP INDEX IF EXISTS idx_chatrooms_state_closed_at;
DROP INDEX IF EXISTS idx_chatrooms_tree_root_id;
DROP TABLE IF EXISTS room_messages;
DROP TABLE IF EXISTS chatroom_members;
DROP TABLE IF EXISTS chatrooms;

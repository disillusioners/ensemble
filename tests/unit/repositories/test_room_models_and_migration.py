"""Migration-structure + model-lockstep pins for the group-chat P1 substrate.

Marker-grep pattern of ``test_project_future_migration.py`` (AC20
lineage). The migration runner NEVER executes .sql on PG — the file is
the SQLite-side canonical DDL + the permanent record (create_all
handles the brand-new tables on PG via the manager's pre-create_all
registration).

O-5 RE-BASING (group-chat P3 carry-over): the pins that verified
EXECUTION-CHECKABLE facts (columns, FK shape, DOWN order, index
lockstep) no longer string-match the .sql text — they apply_migration
the real file through the REAL runner (via
``tests.helpers.migration_execution``) and assert on the LIVE schema.
The runner-mechanics pins (F1 pre-mark, pending list, ledger rows,
driver fingerprints) live in
``test_room_migration_runner_execution.py``.

Pins (design of record: architecture-recommendation.md §1.3 + the
ratified item #1 — ONE multi-table .sql, 3 CREATE TABLE; D2 owner
seed; the shared non-terminal/terminal status sets with the
10-state enum-totality pin):
  * exactly ONE chatroom migration file, stamp convention
    (``YYYYMMDD_HHMMSS`` prefix, globally unique across the
    directory — a duplicate prefix raises
    ``DuplicateMigrationVersionError`` at boot);
  * 3 CREATE TABLE IF NOT EXISTS statements (chatrooms /
    chatroom_members / room_messages), ``-- UP`` / ``-- DOWN``;
  * EXECUTED: pinned columns per table, NO ACTION FK shape (and its
    real enforcement), DOWN drops exactly what UP created (child-first,
    proven by the rollback succeeding under FK pragma), index lockstep
    model ↔ executed schema;
  * UNIQUE backstops named in the MODELS (uq_chatroom_members_room_member_name
    / uq_room_messages_room_seq);
  * models registered with ``SQLModel.metadata`` (the PG
    create_all source of truth);
  * the lifecycle columns exist AT BIRTH (state / closed_at /
    last_post_at / cursors — no later migration needed);
  * the shared room status sets partition the 10-member
    ``InstanceStatus`` enum EXACTLY (disjoint union == all members —
    a future enum addition reddens until classified; derivation is
    from the ENUM, never from ``ALIVE_INSTANCE_STATUSES`` which
    omits ``waiting``);
  * EventKind carries the content-free ``room_*`` family with
    ``room_purged`` present (registry in P1, emission of
    ``room_purged`` in P1 — the rest are later-phase emitters).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
from daemon.repositories.room.models import (  # noqa: F401 — register room
    ChatRoom,
    ChatRoomMember,
    RoomMessage,
)
from tests.helpers.migration_execution import (
    CHATROOM_MIGRATION_FILENAME,
    apply_migration_file,
    sqlite_columns,
    sqlite_foreign_keys,
    sqlite_indexes,
    table_exists,
)

MIGRATIONS_DIR = Path("daemon/migrations/versions")


def _executed_engine(tmp_path) -> Engine:
    """Fresh file-backed SQLite whose schema is built ONLY by the
    executed migrations (BOTH room .sql files — the full set boot
    applies; FK pragma ON) — the O-5 re-basing substrate. Callers own
    disposal."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/room_migration_rebased.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    apply_migration_file(eng, CHATROOM_MIGRATION_FILENAME)
    from tests.helpers.migration_execution import (
        PROPOSAL_DEDUP_MIGRATION_FILENAME,
    )

    apply_migration_file(eng, PROPOSAL_DEDUP_MIGRATION_FILENAME)
    return eng


def _find_migration() -> Path:
    matches = sorted(MIGRATIONS_DIR.glob("*_create_chatroom_tables.sql"))
    assert len(matches) == 1, (
        f"Expected exactly one create_chatroom_tables migration, found: {matches}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# File identity + duplicate-prefix guard
# ---------------------------------------------------------------------------


def test_migration_filename_matches_stamp_convention():
    path = _find_migration()
    assert re.fullmatch(
        r"\d{8}_\d{6}_create_chatroom_tables\.sql", path.name
    ), path.name


def test_migration_prefix_globally_unique():
    """Two parseable files sharing a version prefix raise
    DuplicateMigrationVersionError at boot (runner guard) — the
    directory census must stay collision-free."""
    path = _find_migration()
    prefix = path.name[:15]
    assert re.fullmatch(r"\d{8}_\d{6}", prefix), prefix
    collisions = [
        p.name
        for p in MIGRATIONS_DIR.glob("*.sql")
        if p.name[:15] == prefix
    ]
    assert collisions == [path.name], (
        f"Duplicate migration prefix {prefix}: {collisions}"
    )


# ---------------------------------------------------------------------------
# 3 CREATE TABLE + UP/DOWN structure
# ---------------------------------------------------------------------------


def test_migration_has_exactly_three_create_tables():
    content = _find_migration().read_text()
    creates = re.findall(
        r"CREATE TABLE IF NOT EXISTS (\w+)", content
    )
    assert creates == [
        "chatrooms",
        "chatroom_members",
        "room_messages",
    ], creates


def test_migration_has_up_and_down_markers():
    content = _find_migration().read_text()
    assert "-- UP" in content
    assert "-- DOWN" in content


def test_migration_tables_carry_the_pinned_columns(tmp_path):
    """O-5 re-based (was raw-DDL regex): the EXECUTED schema carries the
    pinned columns per table, with the lifecycle semantics verified on
    the live columns (NOT NULL / defaults), not on DDL prose."""
    eng = _executed_engine(tmp_path)
    try:
        expected = {
            "chatrooms": [
                "room_id", "name", "tree_root_id", "owner_instance_id",
                "state", "closed_at", "last_post_at", "message_count",
                "created_at", "updated_at",
            ],
            "chatroom_members": [
                "room_id", "instance_id", "member_name", "agent_id",
                "role", "joined_at", "last_read_seq", "last_delivered_seq",
                "state",
            ],
            "room_messages": [
                "id", "room_id", "seq", "author_instance_id",
                "author_name", "text", "tagged_members",
                "addressed_members", "kind", "created_at",
                # P3 proposal-dedup columns (migration 20261115_120000).
                "source_message_id", "trigger", "member",
            ],
        }
        for table, cols in expected.items():
            info = sqlite_columns(eng, table)
            assert [c["name"] for c in info] == cols, (table, info)
        # Lifecycle semantics, live: room state defaults to 'active'
        # and is NOT NULL; the cursors + message_count default 0.
        rooms = {c["name"]: c for c in sqlite_columns(eng, "chatrooms")}
        assert rooms["state"]["notnull"] == 1
        assert rooms["state"]["dflt_value"] == "'active'"
        assert rooms["message_count"]["dflt_value"] == "0"
        members = {c["name"]: c for c in sqlite_columns(eng, "chatroom_members")}
        assert members["last_read_seq"]["dflt_value"] == "0"
        assert members["last_delivered_seq"]["dflt_value"] == "0"
        assert members["state"]["dflt_value"] == "'active'"
    finally:
        eng.dispose()


def test_migration_fk_is_no_action_shape(tmp_path):
    """O-5 re-based (was raw-text absence-of-clause): the EXECUTED FK
    metadata shows both child tables referencing chatrooms(room_id)
    with on_delete == NO ACTION (the BoardNote shape: purge deletes
    children FIRST; a stray parent delete is refused)."""
    eng = _executed_engine(tmp_path)
    try:
        for table in ("chatroom_members", "room_messages"):
            fks = sqlite_foreign_keys(eng, table)
            room_fks = [f for f in fks if f["table"] == "chatrooms"]
            assert room_fks, (table, fks)
            assert any(f["from"] == "room_id" for f in room_fks)
            for f in room_fks:
                assert (f["on_delete"] or "NO ACTION") == "NO ACTION", (
                    table, f
                )
    finally:
        eng.dispose()


def test_migration_down_drops_exactly_what_up_created(tmp_path):
    """O-5 re-based (was UP/DOWN regex pairing): rollback_migration
    EXECUTES the DOWN — with children seeded and the FK pragma ON, the
    rollback succeeds (proving the child-first order: a parent-first
    drop would be FK-refused) and drops exactly the three tables +
    their indexes, leaving schema_migrations alive."""
    eng = _executed_engine(tmp_path)
    try:
        from sqlalchemy import text

        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO chatrooms (room_id, name, tree_root_id, "
                "owner_instance_id, state, message_count, created_at, "
                "updated_at) VALUES ('r1', 'a', 'root', 'own', 'active', "
                "0, 't0', 't0')"
            ))
            conn.execute(text(
                "INSERT INTO chatroom_members (room_id, instance_id, "
                "member_name, role, joined_at, last_read_seq, "
                "last_delivered_seq, state) VALUES ('r1', 'c1', 'w', "
                "'child', 't0', 0, 0, 'active')"
            ))
            conn.execute(text(
                "INSERT INTO room_messages (room_id, seq, "
                "author_instance_id, author_name, text, tagged_members, "
                "addressed_members, kind, created_at) VALUES "
                "('r1', 1, 'c1', 'w', 'body', '[]', '[]', 'message', 't0')"
            ))
            conn.commit()

        from daemon.migrations.runner import MigrationRunner
        from tests.helpers.migration_execution import MIGRATIONS_DIR

        runner = MigrationRunner(eng, migrations_dir=MIGRATIONS_DIR)
        runner.rollback_migration("20261115_080000")
        for table in ("chatrooms", "chatroom_members", "room_messages"):
            assert not table_exists(eng, table), table
        with eng.connect() as conn:
            remaining = set(
                conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).scalars()
            )
        assert "schema_migrations" in remaining
    finally:
        eng.dispose()


# ---------------------------------------------------------------------------
# 3-site index lockstep: model __table_args__ ↔ EXECUTED schema ↔ this pin
# ---------------------------------------------------------------------------


def test_index_lockstep_models_vs_migration(tmp_path):
    """O-5 re-based (was model-attr ↔ CREATE INDEX regex): the executed
    migration schema and the model-created schema carry the SAME index
    column-sets (+ uniqueness) per table. Constraint NAMES differ by
    origin (models declare named UniqueConstraints; the migration's
    inline UNIQUEs are SQLite-auto-named), so the lockstep compares
    (column-set → unique) pairs — the driver-honest invariant."""
    model_engine = create_engine(
        f"sqlite:///{tmp_path}/model_schema.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    eng = _executed_engine(tmp_path)
    try:
        SQLModel.metadata.create_all(model_engine)
        for table in ("chatrooms", "chatroom_members", "room_messages"):
            model_sets = {
                frozenset(idx["columns"]): idx["unique"]
                for idx in sqlite_indexes(model_engine, table).values()
            }
            executed_sets = {
                frozenset(idx["columns"]): idx["unique"]
                for idx in sqlite_indexes(eng, table).values()
            }
            assert model_sets == executed_sets, (
                table,
                sorted(map(sorted, model_sets)),
                sorted(map(sorted, executed_sets)),
            )
    finally:
        model_engine.dispose()
        eng.dispose()


def test_unique_constraints_pinned_in_models():
    """UNIQUE(room_id, member_name) + UNIQUE(room_id, seq) — the two
    structural backstops (naming registry / per-room seq)."""
    from daemon.repositories.room.models import ChatRoomMember, RoomMessage

    member_uniques = [
        c.name
        for c in ChatRoomMember.__table__.constraints
        if hasattr(c, "name") and c.name
    ]
    assert "uq_chatroom_members_room_member_name" in member_uniques

    message_uniques = [
        c.name
        for c in RoomMessage.__table__.constraints
        if hasattr(c, "name") and c.name
    ]
    assert "uq_room_messages_room_seq" in message_uniques


# ---------------------------------------------------------------------------
# Model registration (PG create_all source of truth)
# ---------------------------------------------------------------------------


def test_models_registered_with_sqlmodel_metadata():
    import sqlmodel

    from daemon.repositories.room import models as room_models  # noqa: F401

    tables = sqlmodel.SQLModel.metadata.tables
    for name in ("chatrooms", "chatroom_members", "room_messages"):
        assert name in tables, f"{name} not registered with SQLModel.metadata"


# ---------------------------------------------------------------------------
# 10-state enum-totality pin (the shared status sets)
# ---------------------------------------------------------------------------


def test_status_sets_partition_the_instance_enum_exactly():
    """THE TOTALLY PIN: terminal ∪ non-terminal == ALL 10 enum members,
    disjoint. Derived from the ENUM (never from
    ``ALIVE_INSTANCE_STATUSES`` which omits ``waiting``) — a future
    InstanceStatus addition reddens until it is classified here."""
    from daemon.repositories.instance.models import InstanceStatus
    from daemon.repositories.room.models import (
        NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
        TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    )

    enum_values = {s.value for s in InstanceStatus}
    assert len(enum_values) == 10, (
        f"InstanceStatus grew/shrank: {sorted(enum_values)} — classify "
        f"new members in the room status sets (enum-totality pin)"
    )

    terminal = set(TERMINAL_INSTANCE_STATUSES_FOR_ROOMS)
    non_terminal = set(NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS)
    assert terminal | non_terminal == enum_values, (
        terminal | non_terminal,
        enum_values,
    )
    assert terminal.isdisjoint(non_terminal)
    # The design's exact classification (architecture §4.1):
    assert terminal == {"completed", "error", "terminated", "failed"}
    assert non_terminal == {
        "idle",
        "running",
        "waiting",
        "paused",
        "queued",
        "waiting_children",
    }
    # `waiting` is ALIVE for room liveness — never misclassified.
    assert "waiting" in non_terminal


def test_room_state_and_member_state_enums_are_pinned():
    from daemon.repositories.room.models import (
        RoomMemberState,
        RoomMessageKind,
        RoomState,
    )

    assert {s.value for s in RoomState} == {"active", "closed"}
    assert {s.value for s in RoomMemberState} == {"active", "left"}
    assert {k.value for k in RoomMessageKind} == {
        "message",
        "system",
        "task_proposal",
        "task_decision",
    }


# ---------------------------------------------------------------------------
# EventKind registry (P1: registry lands; room_purged emitted in P1)
# ---------------------------------------------------------------------------


def test_eventkind_room_family_present_additively():
    from daemon.repositories.event.models import EventKind

    room_values = {k.value for k in EventKind if k.value.startswith("room_")}
    assert {
        "room_post",
        "room_join",
        "room_leave",
        "room_tag_delivery",
        "room_proposal",
        "room_decision",
        "room_purged",
    } <= room_values
    # registry-before-emission contract: room_purged exists at P1 —
    # the purge transaction inserts it (the repo test proves the
    # emission); no other room kind is emitted anywhere at P1.
    assert EventKind.ROOM_PURGED.value == "room_purged"

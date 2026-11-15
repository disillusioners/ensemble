"""Migration-structure + model-lockstep pins for the group-chat P1 substrate.

Marker-grep pattern of ``test_project_future_migration.py`` (AC20
lineage). The migration runner NEVER executes .sql on PG — the file is
the SQLite-side canonical DDL + the permanent record (create_all
handles the brand-new tables on PG via the manager's pre-create_all
registration).

Pins (design of record: architecture-recommendation.md §1.3 + the
ratified item #1 — ONE multi-table .sql, 3 CREATE TABLE; D2 owner
seed; the shared non-terminal/terminal status sets with the
10-state enum-totality pin):
  * exactly ONE chatroom migration file, stamp convention
    (``YYYYMMDD_HHMMSS`` prefix, globally unique across the
    directory — a duplicate prefix raises
    ``DuplicateMigrationVersionError`` at boot);
  * 3 CREATE TABLE IF NOT EXISTS statements (chatrooms /
    chatroom_members / room_messages), ``-- UP`` / ``-- DOWN``,
    DOWN drops exactly what UP created, child-first;
  * 3-site index lockstep (model ``__table_args__`` ↔ migration
    ``CREATE INDEX`` ↔ this pin);
  * UNIQUE backstops present: (room_id, member_name) and
    (room_id, seq);
  * NO ACTION FK shape (REFERENCES chatrooms(room_id) with NO
    ``ON DELETE`` clause — the BoardNote shape);
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

MIGRATIONS_DIR = Path("daemon/migrations/versions")


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


def test_migration_tables_carry_the_pinned_columns():
    content = _find_migration().read_text()

    def _table_ddl(table: str) -> str:
        m = re.search(
            rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);",
            content,
            re.DOTALL,
        )
        assert m, f"table {table} not found"
        return m.group(1)

    rooms = _table_ddl("chatrooms")
    for col in (
        "room_id TEXT NOT NULL PRIMARY KEY",
        "name TEXT NOT NULL",
        "tree_root_id TEXT NOT NULL",
        "owner_instance_id TEXT NOT NULL",
        "state TEXT NOT NULL DEFAULT 'active'",
        "closed_at TEXT",
        "last_post_at TEXT",
        "message_count BIGINT NOT NULL DEFAULT 0",
        "created_at TEXT NOT NULL",
        "updated_at TEXT NOT NULL",
    ):
        assert col in rooms, f"chatrooms missing {col!r}"

    members = _table_ddl("chatroom_members")
    for col in (
        "room_id TEXT NOT NULL REFERENCES chatrooms(room_id)",
        "instance_id TEXT NOT NULL",
        "member_name TEXT NOT NULL",
        "agent_id TEXT",
        "role TEXT NOT NULL",
        "joined_at TEXT NOT NULL",
        "last_read_seq BIGINT NOT NULL DEFAULT 0",
        "last_delivered_seq BIGINT NOT NULL DEFAULT 0",
        "state TEXT NOT NULL DEFAULT 'active'",
        "PRIMARY KEY (room_id, instance_id)",
        "UNIQUE (room_id, member_name)",
    ):
        assert col in members, f"chatroom_members missing {col!r}"

    messages = _table_ddl("room_messages")
    for col in (
        "room_id TEXT NOT NULL REFERENCES chatrooms(room_id)",
        "seq BIGINT NOT NULL",
        "author_instance_id TEXT NOT NULL",
        "author_name TEXT NOT NULL",
        "text TEXT NOT NULL",
        "tagged_members JSON",
        "addressed_members JSON",
        "kind TEXT NOT NULL DEFAULT 'message'",
        "created_at TEXT NOT NULL",
        "UNIQUE (room_id, seq)",
    ):
        assert col in messages, f"room_messages missing {col!r}"


def _executable_sql(content: str) -> str:
    """Strip full-line ``--`` comments — the runner does the same
    before the statement split; pins must judge the EXECUTABLE text,
    never comment prose."""
    return "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("--")
    )


def test_migration_fk_is_no_action_shape():
    """NO ACTION = REFERENCES with NO ``ON DELETE`` clause (BoardNote
    shape: purge deletes children FIRST; a stray parent delete is
    refused)."""
    content = _executable_sql(_find_migration().read_text())
    assert content.count("REFERENCES chatrooms(room_id)") == 2
    assert "ON DELETE" not in content


def test_migration_down_drops_exactly_what_up_created():
    content = _find_migration().read_text()
    down = _executable_sql(content.split("-- DOWN", 1)[1])
    up = _executable_sql(content.split("-- DOWN", 1)[0])

    up_indexes = set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", up))
    down_indexes = set(re.findall(r"DROP INDEX IF EXISTS (\w+)", down))
    assert up_indexes == down_indexes, (up_indexes, down_indexes)

    up_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", up))
    down_tables = re.findall(r"DROP TABLE IF EXISTS (\w+)", down)
    assert down_tables == [
        "room_messages",
        "chatroom_members",
        "chatrooms",
    ], down_tables  # child-first order
    assert set(down_tables) == up_tables


# ---------------------------------------------------------------------------
# 3-site index lockstep: model __table_args__ ↔ migration ↔ this pin
# ---------------------------------------------------------------------------


def test_index_lockstep_models_vs_migration():
    from daemon.repositories.room.models import ChatRoom, ChatRoomMember

    model_indexes = set()
    for model in (ChatRoom, ChatRoomMember):
        for arg in model.__table_args__:
            name = getattr(arg, "name", None)
            if isinstance(name, str) and name.startswith("idx_"):
                model_indexes.add(name)

    content = _find_migration().read_text()
    sql_indexes = set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", content))

    assert model_indexes == sql_indexes, (model_indexes, sql_indexes)


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

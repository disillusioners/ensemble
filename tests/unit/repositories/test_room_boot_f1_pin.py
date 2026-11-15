"""Boot pins for the group-chat P1 substrate.

Three pin families (commission P1 item 4; architecture §10 "By
construction" + the D4 pinned pair):

F-1 BLAST-RADIUS PIN (RK-4):
  The F-1 boot wipe's doomed predicate spans ``message_queue`` ×
  ``task`` ONLY (predicate SQL ``message_queue/repository.py`` — the
  taskless-READY delete). Room rows are a different table/PK space;
  the room substrate must NOT depend on message_queue lifecycle.
  This test seeds BOTH populations, runs the REAL wipe path
  (``MessageQueueRepository.clear_all(preserve_in_flight=True)`` —
  the same code ``capture_doomed_ids_and_audit`` feeds the deferred
  DELETE from), and asserts:
    * the doomed message_queue rows ARE deleted (the wipe worked —
      non-vacuous), and
    * every room row (rooms / members / messages) survives
      byte-identical.

BOOT NO-ROUTE PIN (pinned pair #2):
  ``run_room_boot_regen`` is pure re-derivation: after a run, no
  message_queue / task rows exist, no instance row changed status,
  and the only room writes are NONE (read-only pass).

PER-ROOM LOCK CONCURRENCY (RK-12):
  Concurrent appends through :class:`RoomChatService` (the ONLY
  sanctioned async path) serialize under the LRU per-room lock —
  unique, gap-free seq assignment; the LRU registry evicts at
  ``MAX_ROOM_LOCKS`` and move-to-end refreshes on reuse (verbatim
  dispatcher-port semantics).
"""

from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
import daemon.repositories.message_queue.models  # noqa: F401 — register queue
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.room.boot import run_room_boot_regen
from daemon.repositories.room.models import ChatRoom, ChatRoomMember, RoomMessage
from daemon.repositories.room.repository import RoomRepository
from daemon.repositories.room.service import MAX_ROOM_LOCKS, RoomChatService


# ---------------------------------------------------------------------------
# F9-parity harness — file-backed SQLite (per-connection PRAGMAs, NullPool)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = create_engine(
        f"sqlite:///{tmp_path}/room_boot_pins.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_instance(eng: Engine, instance_id: str, parent_id: str | None = None) -> None:
    with Session(eng) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                parent_id=parent_id,
                status="idle",
            )
        )
        s.commit()


def _seed_doomed_queue_row(eng: Engine, instance_id: str) -> str:
    """A TASKLESS READY message_queue row — the F-1 doomed class."""
    with Session(eng) as s:
        row = MessageQueue(
            instance_id=instance_id,
            content="backlog message doomed by F-1",
            status=MessageStatus.READY.value,
        )
        s.add(row)
        s.commit()
        return row.message_id


def _seed_room_with_content(repo: RoomRepository, tree_root: str, owner: str) -> str:
    rid = repo.create_room(
        name="pin-room",
        tree_root_id=tree_root,
        owner_instance_id=owner,
        owner_member_name="lead",
    )["room"]["room_id"]
    repo.append_message(
        room_id=rid,
        author_instance_id=owner,
        author_name="lead",
        text_body="room content that MUST survive the queue wipe",
        tagged_members=["lead"],
    )
    return rid


# ---------------------------------------------------------------------------
# F-1 blast-radius pin
# ---------------------------------------------------------------------------


def test_f1_wipe_cannot_reach_room_rows(engine, tmp_path):
    """THE F-1 PIN: the real wipe deletes the doomed queue rows and
    leaves EVERY room row untouched (architecture §10 'By
    construction' — different table/PK space)."""
    repo = RoomRepository(engine)

    _seed_instance(engine, "owner-1", None)
    doomed_id = _seed_doomed_queue_row(engine, "owner-1")
    room_id = _seed_room_with_content(repo, "owner-1", "owner-1")

    # Pre-wipe truth
    with Session(engine) as s:
        pre_rooms = s.exec(select(ChatRoom)).all()
        pre_members = s.exec(select(ChatRoomMember)).all()
        pre_messages = s.exec(select(RoomMessage)).all()
    assert len(pre_rooms) == 1 and len(pre_members) == 1
    assert len(pre_messages) == 1
    assert pre_messages[0].text == "room content that MUST survive the queue wipe"

    # THE REAL WIPE — the F-1 path InstanceManager.__init__ feeds
    # (capture_doomed_ids_and_audit is the capture; clear_all is the
    # in-band delete the deferred DELETE_PHASE replays).
    queue_repo = SQLModelMessageQueueRepository(engine=engine)
    doomed = queue_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    assert doomed == [doomed_id], "wipe capture must find the taskless READY row"
    queue_repo.clear_all(preserve_in_flight=True)

    # Non-vacuous: the queue row is really gone.
    with Session(engine) as s:
        assert s.exec(select(MessageQueue)).all() == []

    # THE PIN: room rows survive, byte-identical.
    with Session(engine) as s:
        post_rooms = s.exec(select(ChatRoom)).all()
        post_members = s.exec(select(ChatRoomMember)).all()
        post_messages = s.exec(select(RoomMessage)).all()
    assert len(post_rooms) == 1 and post_rooms[0].room_id == room_id
    assert len(post_members) == 1
    assert len(post_messages) == 1
    assert post_messages[0].text == pre_messages[0].text
    assert post_messages[0].seq == pre_messages[0].seq
    assert post_messages[0].created_at == pre_messages[0].created_at

    # Room reads still work after the wipe (persistence does not
    # depend on message_queue lifecycle).
    assert repo.get_room(room_id) is not None
    assert len(repo.list_messages(room_id, caller_instance_id="owner-1")) == 1


def test_room_tables_have_no_taskless_ready_shape(engine):
    """A FORBIDDEN-SHAPE pin: no room table carries the columns the F-1
    doomed predicate keys on (status='ready' taskless rows) — room
    persistence is structurally outside the queue lifecycle."""
    for model in (ChatRoom, ChatRoomMember, RoomMessage):
        columns = {c.name for c in model.__table__.columns}
        assert "status" not in columns, (
            f"{model.__tablename__}: room rows are not queue rows — the "
            f"F-1 doomed predicate cannot select them"
        )
        assert "message_id" not in columns
        assert "processing_task_id" not in columns


# ---------------------------------------------------------------------------
# Boot no-route pin (pinned pair #2)
# ---------------------------------------------------------------------------


def test_boot_regen_does_not_route_wake_or_revive(engine):
    """run_room_boot_regen is PURE derivation: no queue/task rows
    minted, no instance status flipped, no events written."""
    repo = RoomRepository(engine)
    _seed_instance(engine, "owner-1", None)
    _seed_instance(engine, "child-1", "owner-1")
    rid = _seed_room_with_content(repo, "owner-1", "owner-1")

    # give child-1 a membership row so the derivation has 2 participants
    with Session(engine) as s:
        s.add(
            ChatRoomMember(
                room_id=rid,
                instance_id="child-1",
                member_name="worker",
                agent_id="ari",
                role="child",
                joined_at="2026-11-15T08:00:00+00:00",
            )
        )
        s.commit()

    counts = run_room_boot_regen(engine)
    assert counts["pending_rows"] == 2  # owner + child above 0/0
    assert counts["rooms_with_pending"] == 1
    assert counts["members_with_pending"] == 2

    # NO-ROUTE pin: the pass wrote NOTHING.
    with Session(engine) as s:
        assert s.exec(select(MessageQueue)).all() == []
        assert s.exec(select(Event)).all() == []
        statuses = {
            i.instance_id: i.status
            for i in s.exec(select(Instance)).all()
        }
    assert statuses == {"owner-1": "idle", "child-1": "idle"}  # no flips

    # A second run is deterministic and side-effect free (idempotent
    # derivation — the at-least-once re-derivation base).
    counts2 = run_room_boot_regen(engine)
    assert counts2 == counts
    with Session(engine) as s:
        assert s.exec(select(MessageQueue)).all() == []


def test_boot_regen_sees_closed_rooms_not_purged(engine):
    """Pinned pair #1, state dimension: active + closed covered; purged
    rooms (no rows) structurally absent."""
    # IntegrityError classification is pinned in test_room_repository.py
    # (name-collision fingerprints + FK-vs-seq); this file never needs it.
    repo = RoomRepository(engine)
    _seed_instance(engine, "owner-1", None)

    rid_active = _seed_room_with_content(repo, "owner-1", "owner-1")
    rid_closed = _seed_room_with_content(repo, "owner-1", "owner-1")
    with Session(engine) as s:
        row = s.get(ChatRoom, rid_closed)
        row.state = "closed"
        row.closed_at = "2026-11-15T09:00:00+00:00"
        s.add(row)
        s.commit()

    counts = run_room_boot_regen(engine)
    assert counts["rooms_with_pending"] == 2

    repo.purge_room(rid_closed, reason="test")
    counts = run_room_boot_regen(engine)
    assert counts["rooms_with_pending"] == 1  # only the active room


def test_boot_regen_on_empty_db_is_a_clean_zero(engine):
    counts = run_room_boot_regen(engine)
    assert counts == {
        "pending_rows": 0,
        "rooms_with_pending": 0,
        "members_with_pending": 0,
    }


# ---------------------------------------------------------------------------
# Per-room lock concurrency (the RK-12 response)
# ---------------------------------------------------------------------------


def test_concurrent_appends_are_unique_and_gap_free(engine):
    """Two writers × N appends each through the service — seqs are
    exactly 1..2N with no duplicates (per-room lock + single-statement
    INSERT + UNIQUE backstop)."""
    repo = RoomRepository(engine)
    service = RoomChatService(engine)
    _seed_instance(engine, "owner-1", None)
    _seed_instance(engine, "child-1", "owner-1")
    rid = _seed_room_with_content(repo, "owner-1", "owner-1")

    async def writer(instance_id: str, name: str, n: int) -> list[int]:
        return [
            (
                await service.append_message(
                    room_id=rid,
                    author_instance_id=instance_id,
                    author_name=name,
                    text_body=f"w-{instance_id}-{i}",
                )
            )["seq"]
            for i in range(n)
        ]

    async def run() -> tuple[list[int], list[int]]:
        a, b = await asyncio.gather(
            writer("owner-1", "lead", 15),
            writer("child-1", "worker", 15),
        )
        return a, b

    seqs_a, seqs_b = asyncio.run(run())
    all_seqs = sorted(seqs_a + seqs_b)
    # seq=1 is the seed message from _seed_room_with_content; the two
    # concurrent writers own exactly 2..31 — unique, gap-free.
    assert all_seqs == list(range(2, 32)), (
        f"seq collision or gap under concurrency: {all_seqs}"
    )

    with Session(engine) as s:
        rows = s.exec(select(RoomMessage)).all()
    assert len(rows) == 31
    assert sorted(r.seq for r in rows) == list(range(1, 32))
    room = repo.get_room(rid)
    assert room["message_count"] == 31


def test_lru_lock_registry_evicts_and_refreshes():
    """Dispatcher-port semantics: move-to-end on reuse, oldest-eviction
    at capacity."""
    service = RoomChatService(engine=None)  # lock registry needs no engine

    async def run() -> None:
        for i in range(MAX_ROOM_LOCKS):
            await service._get_room_lock(f"room-{i}")
        assert len(service._room_locks) == MAX_ROOM_LOCKS
        assert "room-0" in service._room_locks  # oldest present pre-eviction

        # Touch room-0 → moves to end (most recently used).
        await service._get_room_lock("room-0")
        ids = list(service._room_locks)
        assert ids[-1] == "room-0"

        # One more → evicts room-1 (the NEW oldest), keeps room-0.
        await service._get_room_lock("room-new")
        assert len(service._room_locks) == MAX_ROOM_LOCKS
        assert "room-0" in service._room_locks
        assert "room-1" not in service._room_locks
        assert "room-new" in service._room_locks

    asyncio.run(run())


def test_concurrent_append_and_cursor_writers_do_not_interleave(engine):
    """The lock serializes the whole append (gate + seq + counters) —
    message_count must equal the appended count, never drift."""
    repo = RoomRepository(engine)
    service = RoomChatService(engine)
    _seed_instance(engine, "owner-1", None)
    rid = _seed_room_with_content(repo, "owner-1", "owner-1")

    async def churn(n: int) -> None:
        for i in range(n):
            await service.append_message(
                room_id=rid,
                author_instance_id="owner-1",
                author_name="lead",
                text_body=f"c{i}",
            )
            await service.advance_read_cursor(rid, "owner-1", i + 1)

    async def run_both() -> None:
        await asyncio.gather(churn(10), churn(10))

    asyncio.run(run_both())
    room = repo.get_room(rid)
    assert room["message_count"] == 21  # seed + 20 — no lost update

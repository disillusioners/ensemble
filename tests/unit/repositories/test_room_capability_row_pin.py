"""Agent group chat P6 — the §2.1 CAPABILITY-ROW conformance pin for
the GC/purge lifecycle.

Architecture §2.1 is "THE PUBLISHED CAPABILITY ROW (the ONE definition
of ``closed`` and ``purged``)". The GC sweeper's purge is the only
thing that moves a room into the third column, so the sweeper's
OUTCOME must match that column EXACTLY:

| Capability                     | active | closed                | purged                        |
|--------------------------------|--------|-----------------------|-------------------------------|
| read tool / log read           | yes    | yes (read-only)       | **no — room nonexistent**     |
| posts                          | yes    | no (typed room-closed)| **no — not-found**             |
| digest block / unread computed | yes    | yes                   | **no — nonexistent**          |
| nudges                         | yes    | no                    | **no**                        |
| tagged re-derivation           | yes    | **yes — until purge** | **no**                        |

What this file pins:

* **CLOSED** — the log still reads, the unread still computes, the
  TAGGED row still re-derives (the at-least-once window survives the
  close), and a post is refused with the typed room-closed error.
* **PURGED** — the room is NONEXISTENT to EVERY predicate: the room
  row, the log read, the roster read, the unread count, the tagged
  re-derivation, the nudge snapshot and the tree room list. There is
  no tombstone and no archive table (R2).
* **THE GC LINK** — the sweeper's purge produces exactly that state
  (the lifecycle and the capability row agree).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.room.models import ChatRoom
from daemon.repositories.room.repository import RoomClosedError, RoomNotFoundError
from daemon.repositories.room.service import RoomChatService
from daemon.services.room_gc import RoomGcSweeper
from tests.helpers.room_harness import (
    make_room,
    make_room_engine,
    seed_instance,
)


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_capability_row.db")
    yield eng
    eng.dispose()


@pytest.fixture
def svc(engine) -> RoomChatService:
    return RoomChatService(engine)


def _closed_room(engine: Engine, svc: RoomChatService) -> str:
    seed_instance(engine, "owner-1", None, "idle")
    room_id = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="cap-room"
    )
    svc._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="tagged milestone",
        tagged_members=["lead"],
    )
    with Session(engine) as s:
        room = s.get(ChatRoom, room_id)
        room.state = "closed"
        room.closed_at = datetime.now(timezone.utc).isoformat()
        s.commit()
    return room_id


def _purged_room(engine: Engine, svc: RoomChatService) -> str:
    seed_instance(engine, "owner-1", None, "idle")
    room_id = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="gone-room"
    )
    svc._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="tagged milestone",
        tagged_members=["lead"],
    )
    svc._repo.purge_room(room_id, reason="capability-row-pin")
    return room_id


# ── the CLOSED column ──────────────────────────────────────────────────


def test_a_closed_room_still_reads_and_still_re_derives(engine, svc):
    room_id = _closed_room(engine, svc)
    # read: yes (read-only)
    messages = svc._repo.list_messages(
        room_id, caller_instance_id="owner-1", after_seq=0, limit=50
    )
    assert len(messages) == 1
    # unread computed: yes
    assert svc._repo.unread_count(room_id, caller_instance_id="owner-1") == 1
    # tagged re-derivation: yes — UNTIL the purge
    pending = svc._repo.derive_undelivered_tagged()
    assert any(row["room_id"] == room_id for row in pending)


def test_a_closed_room_refuses_posts_with_the_typed_error(engine, svc):
    room_id = _closed_room(engine, svc)
    with pytest.raises(RoomClosedError):
        svc._repo.append_message(
            room_id=room_id,
            author_instance_id="owner-1",
            author_name="lead",
            text_body="nope",
        )


# ── the PURGED column ──────────────────────────────────────────────────


def test_a_purged_room_is_nonexistent_to_every_predicate(engine, svc):
    room_id = _purged_room(engine, svc)
    repo = svc._repo

    # the room row
    assert repo.get_room(room_id) is None
    # the log read
    with pytest.raises(RoomNotFoundError):
        repo.list_messages(room_id, caller_instance_id="owner-1", limit=50)
    # the roster read
    with pytest.raises(RoomNotFoundError):
        repo.list_members(room_id, caller_instance_id="owner-1")
    # the unread count
    with pytest.raises(RoomNotFoundError):
        repo.unread_count(room_id, caller_instance_id="owner-1")
    # the tagged re-derivation
    assert not any(
        row["room_id"] == room_id
        for row in repo.derive_undelivered_tagged()
    )
    # the nudge snapshot
    assert not any(
        row["room_id"] == room_id
        for row in repo.nudge_snapshot_for_instance("owner-1")
    )
    # the tree room list (the P6 read surface)
    assert room_id not in {
        room["room_id"] for room in repo.list_rooms_for_tree("owner-1")
    }
    # the GC scan input sets
    assert room_id not in {
        room["room_id"] for room in repo.list_rooms_by_state("active")
    }
    assert room_id not in {
        room["room_id"] for room in repo.list_rooms_by_state("closed")
    }


def test_a_purged_room_refuses_posts_with_the_typed_not_found(engine, svc):
    room_id = _purged_room(engine, svc)
    with pytest.raises(RoomNotFoundError):
        svc._repo.append_message(
            room_id=room_id,
            author_instance_id="owner-1",
            author_name="lead",
            text_body="nope",
        )


# ── the GC link ────────────────────────────────────────────────────────


async def test_the_gc_purge_produces_the_purged_column_exactly(
    engine, svc
):
    """The sweeper's outcome IS the third column of the capability
    row — the lifecycle and the published row agree."""
    seed_instance(engine, "owner-1", None, "completed")
    room_id = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="gc-room"
    )
    with Session(engine) as s:
        room = s.get(ChatRoom, room_id)
        room.last_post_at = (
            datetime.now(timezone.utc) - timedelta(hours=100)
        ).isoformat()
        s.commit()

    manager = SimpleNamespace(_room_chat_service=svc)
    summary = await RoomGcSweeper(manager=manager).sweep(dry_run=False)
    assert summary["purged"] == [room_id]

    # The capability row's purged column — every predicate says
    # "nonexistent".
    assert svc._repo.get_room(room_id) is None
    with pytest.raises(RoomNotFoundError):
        svc._repo.list_messages(room_id, caller_instance_id="owner-1", limit=50)
    assert not any(
        row["room_id"] == room_id
        for row in svc._repo.derive_undelivered_tagged()
    )

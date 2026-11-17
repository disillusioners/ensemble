"""Agent group chat P6 — the room repository's P6 query surface.

The GC sweeper's inputs and the P6 read surface's inputs are read
through FOUR new repository methods. This file pins them at the SQL
level (real file-backed SQLite, the shared room harness):

* ``list_rooms_for_tree`` — the TREE scope (a room of another tree is
  not listed), the ACTIVE member count, ``last_message_seq`` (``None``
  for a room with no posts), and the stable ordering.
* ``list_rooms_by_state`` — the GC scan's input set (state-filtered).
* ``list_active_member_instance_ids`` — the ACTIVE roster only (a LEFT
  row is retired).
* ``get_instance_records`` — the liveness inputs (status + parent_id);
  a MISSING row is absent from the map (not alive).
* ``rooms_with_undelivered_tagged`` — the cursor guard: a TAGGED row
  above a participant's DELIVERED cursor guards the room; a delivered
  row does not; an untagged row does not.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.room.models import ChatRoom
from daemon.repositories.room.service import RoomChatService
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
)


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_gc_repo.db")
    yield eng
    eng.dispose()


@pytest.fixture
def svc(engine) -> RoomChatService:
    return RoomChatService(engine)


def test_list_rooms_for_tree_is_tree_scoped(engine, svc):
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "other-1", None, "idle")
    mine = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="mine"
    )
    theirs = make_room(
        svc._repo, tree_root="other-1", owner="other-1", name="theirs"
    )
    rooms = svc._repo.list_rooms_for_tree("owner-1")
    assert [r["room_id"] for r in rooms] == [mine]
    assert theirs not in {r["room_id"] for r in rooms}
    # An empty scope is empty (never a full scan).
    assert svc._repo.list_rooms_for_tree("") == []


def test_list_rooms_for_tree_counts_the_active_roster_and_the_last_seq(
    engine, svc
):
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    room_id = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="counted"
    )
    join_member(engine, room_id, "child-1", "worker")
    rooms = svc._repo.list_rooms_for_tree("owner-1")
    assert rooms[0]["member_count"] == 2
    # No posts yet ⇒ no last message.
    assert rooms[0]["last_message_seq"] is None

    svc._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="one",
    )
    svc._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="two",
    )
    rooms = svc._repo.list_rooms_for_tree("owner-1")
    assert rooms[0]["last_message_seq"] == 2
    assert rooms[0]["last_post_at"] is not None


def test_list_rooms_by_state_filters_on_the_state(engine, svc):
    seed_instance(engine, "owner-1", None, "idle")
    active = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="active"
    )
    closed = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="closed"
    )
    with Session(engine) as s:
        room = s.get(ChatRoom, closed)
        room.state = "closed"
        s.commit()

    active_ids = {r["room_id"] for r in svc._repo.list_rooms_by_state("active")}
    closed_ids = {r["room_id"] for r in svc._repo.list_rooms_by_state("closed")}
    assert active in active_ids and closed not in active_ids
    assert closed in closed_ids and active not in closed_ids


def test_list_active_member_instance_ids_skips_left_rows(engine, svc):
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    seed_instance(engine, "child-2", "owner-1", "idle")
    room_id = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="roster"
    )
    join_member(engine, room_id, "child-1", "worker")
    join_member(engine, room_id, "child-2", "worker-2")
    with Session(engine) as s:
        from daemon.repositories.room.models import ChatRoomMember

        member = s.get(ChatRoomMember, (room_id, "child-2"))
        member.state = "left"
        s.commit()

    roster = svc._repo.list_active_member_instance_ids([room_id])
    assert set(roster[room_id]) == {"owner-1", "child-1"}
    # An empty input is empty (no query).
    assert svc._repo.list_active_member_instance_ids([]) == {}


def test_get_instance_records_carries_status_and_parent(engine, svc):
    seed_instance(engine, "root-1", None, "idle")
    seed_instance(engine, "owner-1", "root-1", "completed")
    records = svc._repo.get_instance_records(["root-1", "owner-1", "ghost"])
    assert records["root-1"]["status"] == "idle"
    assert records["root-1"]["parent_id"] is None
    assert records["owner-1"]["parent_id"] == "root-1"
    # A MISSING row is absent (the caller treats it as not-alive).
    assert "ghost" not in records
    assert svc._repo.get_instance_records([]) == {}


def test_rooms_with_undelivered_tagged_is_the_cursor_guard(engine, svc):
    seed_instance(engine, "owner-1", None, "idle")
    tagged_room = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="tagged"
    )
    plain_room = make_room(
        svc._repo, tree_root="owner-1", owner="owner-1", name="plain"
    )
    svc._repo.append_message(
        room_id=tagged_room,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="ping",
        tagged_members=["lead"],
    )
    svc._repo.append_message(
        room_id=plain_room,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="no tag here",
    )

    guarded = svc._repo.rooms_with_undelivered_tagged([tagged_room, plain_room])
    assert guarded == {tagged_room}

    # Deliver the tagged row ⇒ the guard releases the room.
    svc._repo.advance_delivered_cursor(tagged_room, "owner-1", 1)
    guarded = svc._repo.rooms_with_undelivered_tagged([tagged_room, plain_room])
    assert guarded == set()
    # An empty input is empty.
    assert svc._repo.rooms_with_undelivered_tagged([]) == set()

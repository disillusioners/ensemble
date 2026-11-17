"""Agent group chat P6 — the CLOSED-STATE PRODUCER (the C item).

Architecture §2.1 publishes the capability row with a ``closed``
column, and §10 ratifies the P-close GC arm (``state=closed AND
closed_at < now − 14d`` → purge). UNTIL the C item, NOTHING in
production ever set ``chatrooms.state='closed'``: the predicate was
correct and the PRODUCER was missing, so the whole closed column was
unreachable and the P-close arm could never fire.

This file pins the producer:

* the repository transition ``active → closed`` stamps ``closed_at``;
* the OWNER predicate is enforced AT THE DB LAYER (fail-closed): a
  non-owner cannot close the room, and the room stays active;
* the transition is FORWARD-ONLY: an already-closed room is refused
  (there is no reopen);
* an absent/purged room is refused (R2: purged = nonexistent);
* the service fires the CONTENT-FREE ``room_closed`` lifecycle event
  (the F-3 notifier → the SSE room channel) — ids and the state only,
  NO room name, NO member name;
* the closed state FEEDS the ratified P-close arm: a room closed
  through the producer is a P-close candidate once its ``closed_at``
  ages past 14 days (and is NOT one inside the window);
* a closed room is read-only for posts (the capability row's closed
  column, reached through the producer rather than a hand-seeded row).

The harness is the shared room harness (file-backed SQLite, the real
repository and the real service facade).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from daemon.repositories.room.models import ChatRoom
from daemon.repositories.room.repository import (
    RoomClosedError,
    RoomNotFoundError,
    RoomNotOwnerError,
)
from daemon.repositories.room.service import RoomChatService
from daemon.services.room_gc import ROOM_GC_CLOSE_AGE_DAYS, RoomGcSweeper
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
)


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_close_producer.db")
    yield eng
    eng.dispose()


@pytest.fixture
def service(engine) -> RoomChatService:
    return RoomChatService(engine)


@pytest.fixture
def room(engine, service) -> str:
    """A room with an owner ("owner-1") and one child member."""
    seed_instance(engine, "owner-1", None, "idle")
    room_id = make_room(
        service._repo,
        tree_root="owner-1",
        owner="owner-1",
        name="close-room",
        owner_member_name="lead",
    )
    seed_instance(engine, "child-1", "owner-1", "idle")
    join_member(engine, room_id, "child-1", "worker")
    return room_id


# ---------------------------------------------------------------------------
# the transition
# ---------------------------------------------------------------------------


def test_the_owner_closes_the_room_and_closed_at_is_stamped(service, room):
    closed = service._repo.close_room(room, owner_instance_id="owner-1")
    assert closed["state"] == "closed", closed
    assert closed["closed_at"], closed
    # The durable row carries the same state.
    stored = service._repo.get_room(room)
    assert stored["state"] == "closed", stored
    assert stored["closed_at"] == closed["closed_at"], stored
    # The LOG is untouched — a closed room still reads.
    assert stored["message_count"] == 0, stored


def test_a_non_owner_cannot_close_the_room(service, room):
    """The OWNER predicate is enforced AT THE DB LAYER (fail-closed)."""
    with pytest.raises(RoomNotOwnerError):
        service._repo.close_room(room, owner_instance_id="child-1")
    # The room is UNCHANGED — the refused close left no trace.
    stored = service._repo.get_room(room)
    assert stored["state"] == "active", stored
    assert stored["closed_at"] is None, stored


def test_an_already_closed_room_is_refused(service, room):
    """The transition is FORWARD-ONLY (``active → closed``)."""
    service._repo.close_room(room, owner_instance_id="owner-1")
    with pytest.raises(RoomClosedError):
        service._repo.close_room(room, owner_instance_id="owner-1")
    assert service._repo.get_room(room)["state"] == "closed"


def test_an_absent_room_is_refused(service):
    with pytest.raises(RoomNotFoundError):
        service._repo.close_room("no-such-room", owner_instance_id="owner-1")


def test_a_purged_room_is_refused(service, room):
    """R2: a purged room is NONEXISTENT — the close sees not-found."""
    service._repo.purge_room(room, reason="test")
    with pytest.raises(RoomNotFoundError):
        service._repo.close_room(room, owner_instance_id="owner-1")


# ---------------------------------------------------------------------------
# the content-free lifecycle event (the F-3 notifier)
# ---------------------------------------------------------------------------


async def test_the_close_emits_a_content_free_room_closed_event(service, room):
    """The F-3 machinery: the service's lifecycle notifier fires.

    The payload carries ids + the state — NO room name, NO member
    name, NO message content (the SSE room channel's fail-closed
    content-free contract).
    """
    seen: list[dict[str, Any]] = []

    async def _notifier(*, room_id: str, kind: str, data: dict) -> None:
        seen.append({"room_id": room_id, "kind": kind, "data": dict(data)})

    service.set_lifecycle_notifier(_notifier)
    await service.close_room(room_id=room, owner_instance_id="owner-1")

    assert len(seen) == 1, seen
    event = seen[0]
    assert event["kind"] == "room_closed", event
    assert event["room_id"] == room, event
    assert event["data"]["room_id"] == room, event
    assert event["data"]["state"] == "closed", event
    # CONTENT-FREE: no name, no message text, no body.
    forbidden = {"name", "room_name", "member_name", "text", "body", "content"}
    assert not forbidden.intersection(event["data"]), event
    # The guard's own content-bearing key list — none of them present.
    from daemon.services.live_event_hub import LiveEventHub

    assert not LiveEventHub._ROOM_CHANNEL_CONTENT_KEYS.intersection(
        event["data"]
    ), event


async def test_a_failed_close_emits_no_event(service, room):
    """A refused close NEVER broadcasts a lifecycle event."""
    seen: list[dict[str, Any]] = []

    async def _notifier(*, room_id: str, kind: str, data: dict) -> None:
        seen.append({"kind": kind})

    service.set_lifecycle_notifier(_notifier)
    with pytest.raises(RoomNotOwnerError):
        await service.close_room(room_id=room, owner_instance_id="child-1")
    assert seen == [], seen


# ---------------------------------------------------------------------------
# the closed state feeds the ratified P-close arm
# ---------------------------------------------------------------------------


def _age_closed_at(engine: Engine, room_id: str, days: float) -> None:
    """Backdate ``closed_at`` (the producer stamps 'now')."""
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with Session(engine) as s:
        row = s.get(ChatRoom, room_id)
        assert row is not None
        row.closed_at = stamp
        s.commit()


def _sweeper(engine: Engine, service: RoomChatService) -> RoomGcSweeper:
    manager = SimpleNamespace(_room_chat_service=service, _engine=engine)
    return RoomGcSweeper(
        manager=manager, kv_repo=None, kv_context_key="room_close_test"
    )


async def test_the_producer_makes_the_p_close_arm_reachable(
    engine, service, room
):
    """The ratified P-close arm fires on a room closed THROUGH the
    producer (the arm was unreachable before the C item)."""
    await service.close_room(room_id=room, owner_instance_id="owner-1")
    # Inside the window: NOT a candidate yet.
    summary = await _sweeper(engine, service).sweep(dry_run=False)
    assert summary["candidates"] == [], summary
    assert summary["purged"] == [], summary
    # Past the 14d window: the P-close arm fires.
    _age_closed_at(engine, room, ROOM_GC_CLOSE_AGE_DAYS + 1)
    summary = await _sweeper(engine, service).sweep(dry_run=False)
    assert [c["room_id"] for c in summary["candidates"]] == [room], summary
    assert summary["candidates"][0]["trigger"] == "p_close", summary
    assert summary["purged"] == [room], summary
    # R2: the purged room is nonexistent.
    assert service._repo.get_room(room) is None


# ---------------------------------------------------------------------------
# the capability row, reached through the producer
# ---------------------------------------------------------------------------


def test_a_closed_room_refuses_posts(service, room):
    """The capability row's closed column: posts are refused."""
    service._repo.close_room(room, owner_instance_id="owner-1")
    with pytest.raises(RoomClosedError):
        service._repo.append_message(
            room_id=room,
            author_instance_id="owner-1",
            author_name="lead",
            text_body="one more thing",
        )

"""Agent group chat P6 — the §9 post budget AT THE ``room_post``
CHOKEPOINT (A-P6-1, BINDING).

The agent-facing half of the cap:

* FOUR posts succeed; the FIFTH is refused with the house TYPED
  refusal envelope (``reason=post-cap``) — the same envelope shape
  every other room refusal uses.
* The refusal happens at the PRE-APPEND region — the refused post
  NEVER reaches the room log (the append is the commit point of the
  log: a budget refusal that still wrote a row would be a lie).
* The budget RESETS across a turn boundary (the next dispatch's root
  graph task is a new turn key) — the posts land again.
* The cap is SERVER-SIDE only: the tool answer is the refusal
  envelope, never a silent drop.

The harness is the shared room harness (file-backed SQLite) with a
REAL ``RoomChatService``/``RoomDeliveryService`` and a manager stub
carrying a REAL ``RoomPostTurnCap``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.room.models import RoomMessage
from daemon.repositories.room.service import RoomChatService
from daemon.services.room_delivery import RoomDeliveryService
from daemon.services.room_post_cap import ROOM_POSTS_PER_TURN, RoomPostTurnCap
from daemon.tools.room import create_room_tools
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
)


class _FakeTask:
    """A stand-in for the root graph task (its id() is the turn key)."""


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_post_cap.db")
    yield eng
    eng.dispose()


def _manager(engine: Engine, svc: RoomChatService):
    """A manager stub carrying the room seams + the REAL cap."""
    manager = MagicMock()
    manager.has_live_graph_task = MagicMock(return_value=False)
    manager.set_injection = MagicMock()
    manager.enqueue_message = AsyncMock(return_value=MagicMock(queued=True))
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    manager._room_chat_service = svc
    manager._room_delivery_service = RoomDeliveryService(manager, svc)
    manager._instance_repository = MagicMock()
    manager._instance_repository.get_tree_root_id = MagicMock(return_value="tree-1")
    # The P6 cap: a REAL service over a manager-level registry.
    manager._room_post_turn_counter = {}
    manager._graph_tasks = {}
    manager._room_post_turn_cap = RoomPostTurnCap(manager=manager)
    return manager


@pytest.fixture
def stack(engine):
    """A room with an owner ("lead")."""
    svc = RoomChatService(engine)
    owner = "owner-1"
    seed_instance(engine, owner, None, "idle")
    room_id = make_room(
        svc._repo, tree_root=owner, owner=owner, owner_member_name="lead"
    )
    return engine, svc, room_id, owner


def _tools(manager, instance_id: str):
    return {
        t.name: t
        for t in create_room_tools(
            manager, current_instance_id=instance_id, agent_id="ari"
        )
    }


def _post(tools, room_id: str, text: str = "milestone"):
    return asyncio.run(
        tools["room_post"].ainvoke({"room_id": room_id, "text": text})
    )


def _row_count(engine: Engine, room_id: str) -> int:
    with Session(engine) as s:
        return len(
            s.exec(
                select(RoomMessage).where(RoomMessage.room_id == room_id)
            ).all()
        )


def test_four_posts_land_and_the_fifth_is_refused(stack):
    engine, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)

    for index in range(ROOM_POSTS_PER_TURN):
        raw = _post(tools, room_id, f"milestone {index}")
        assert raw.startswith("{"), raw
        assert json.loads(raw)["seq"] == index + 1

    raw = _post(tools, room_id, "one too many")
    assert raw.startswith("Error:"), raw
    assert "room_post REFUSED" in raw, raw
    assert "reason=post-cap" in raw, raw


def test_the_refused_post_never_reaches_the_room_log(stack):
    engine, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)

    for index in range(ROOM_POSTS_PER_TURN + 2):
        _post(tools, room_id, f"milestone {index}")
    # The append is the COMMIT POINT of the room log — the refused
    # posts left no rows behind.
    assert _row_count(engine, room_id) == ROOM_POSTS_PER_TURN


def test_the_budget_resets_across_a_turn_boundary(stack):
    engine, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)

    for index in range(ROOM_POSTS_PER_TURN):
        _post(tools, room_id, f"milestone {index}")
    assert _post(tools, room_id).startswith("Error:")

    # The next dispatch ⇒ a new root graph task ⇒ a new turn key ⇒ a
    # fresh budget; the post lands again.
    manager._graph_tasks[owner] = _FakeTask()
    raw = _post(tools, room_id, "next turn")
    assert raw.startswith("{"), raw
    assert json.loads(raw)["seq"] == ROOM_POSTS_PER_TURN + 1


def test_a_manager_without_a_cap_has_no_budget(stack):
    """The ``*_if_available`` posture: a manager without the cap
    service has NO budget (the cap is a cost control, not an
    admission gate)."""
    engine, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    manager._room_post_turn_cap = None
    tools = _tools(manager, owner)
    for index in range(ROOM_POSTS_PER_TURN + 2):
        raw = _post(tools, room_id, f"milestone {index}")
        assert raw.startswith("{"), raw

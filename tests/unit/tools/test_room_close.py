"""Agent group chat P6 — the ``room_close`` verb (the C item).

The CLOSED-STATE PRODUCER's agent-facing half: the parent closes the
room, the room becomes read-only, and the ratified P-close arm
(``closed_at < now − 14d`` → purge) becomes reachable.

What it pins:

* the happy path — the OWNER closes the room; the answer carries the
  new state and the ``closed_at`` stamp;
* the OWNER-ONLY gate — a member that does not own the room is
  refused with the typed ``not-the-owner`` envelope, and the room
  stays ACTIVE;
* the membership gate beneath the owner check (the house posture);
* the typed refusals: not-found (absent / purged), already-closed
  (the transition is forward-only), no-room-service;
* the LAYER-2 GATE is the FIRST logic — an unresolved agent touches
  no service seam at all;
* the closed room is READ-ONLY — a post after the close is refused
  with the typed ``room-closed`` envelope (the capability row).

The harness is the shared room harness (file-backed SQLite) with a
REAL ``RoomChatService``/``RoomDeliveryService`` over a MagicMock
manager.
"""

from __future__ import annotations

import asyncio
import json
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.engine import Engine

from daemon.repositories.room.service import RoomChatService
from daemon.services.room_delivery import RoomDeliveryService
from daemon.tools.room import create_room_tools
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
)


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_close_tool.db")
    yield eng
    eng.dispose()


def _manager(engine: Engine, svc: RoomChatService):
    """A manager stub carrying the room seams the tools touch."""
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
    return manager


@pytest.fixture
def stack(engine):
    """A room with an owner ("lead") and a child member ("worker")."""
    svc = RoomChatService(engine)
    owner = "owner-1"
    seed_instance(engine, owner, None, "idle")
    room_id = make_room(
        svc._repo, tree_root=owner, owner=owner, owner_member_name="lead"
    )
    seed_instance(engine, "child-1", owner, "idle")
    join_member(engine, room_id, "child-1", "worker")
    return engine, svc, room_id, owner


def _tools(manager, instance_id: str, agent_id: str = "ari"):
    return {
        t.name: t
        for t in create_room_tools(
            manager, current_instance_id=instance_id, agent_id=agent_id
        )
    }


def _call(coro):
    return asyncio.run(coro)


# ── the happy path ───────────────────────────────────────────────────


def test_the_owner_closes_the_room(stack):
    engine, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(tools["room_close"].ainvoke({"room_id": room_id}))
    assert raw.startswith("{"), raw
    payload = json.loads(raw)
    assert payload["room_id"] == room_id, payload
    assert payload["state"] == "closed", payload
    assert payload["closed_at"], payload
    # The durable row carries the transition.
    stored = svc._repo.get_room(room_id)
    assert stored["state"] == "closed", stored
    assert stored["closed_at"], stored


def test_a_closed_room_is_read_only(stack):
    """The capability row's closed column, reached through the verb."""
    engine, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    _call(tools["room_close"].ainvoke({"room_id": room_id}))
    raw = _call(
        tools["room_post"].ainvoke({"room_id": room_id, "text": "one more"})
    )
    assert raw.startswith("Error:"), raw
    assert "room-closed" in raw, raw


# ── the owner gate ───────────────────────────────────────────────────


def test_a_member_that_does_not_own_the_room_is_refused(stack):
    """Close is PARENT-ONLY (architecture §3/§5).

    The refusal is TYPED (``reason=not-the-owner``) and the room stays
    ACTIVE — a refused close writes nothing.
    """
    engine, svc, room_id, _owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, "child-1")
    raw = _call(tools["room_close"].ainvoke({"room_id": room_id}))
    assert raw.startswith("Error:"), raw
    assert "room_close REFUSED" in raw, raw
    assert "reason=not-the-owner" in raw, raw
    # The room is UNCHANGED.
    assert svc._repo.get_room(room_id)["state"] == "active"


def test_a_non_member_is_refused(stack):
    """The membership gate beneath the owner check (house posture)."""
    engine, svc, room_id, _owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, "outsider-1")
    raw = _call(tools["room_close"].ainvoke({"room_id": room_id}))
    assert raw.startswith("Error:"), raw
    assert "reason=not-the-owner" in raw, raw
    assert svc._repo.get_room(room_id)["state"] == "active"


# ── the typed refusals ───────────────────────────────────────────────


def test_an_absent_room_is_typed(stack):
    engine, svc, _room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(tools["room_close"].ainvoke({"room_id": "no-such-room"}))
    assert raw.startswith("Error:"), raw
    assert "reason=room-not-found" in raw, raw


def test_a_purged_room_is_typed(stack):
    """R2: a purged room is NONEXISTENT — not-found, never "closed"."""
    engine, svc, room_id, owner = stack
    svc._repo.purge_room(room_id, reason="test")
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(tools["room_close"].ainvoke({"room_id": room_id}))
    assert raw.startswith("Error:"), raw
    assert "reason=room-not-found" in raw, raw


def test_an_already_closed_room_is_typed(stack):
    """The transition is FORWARD-ONLY (``active → closed``)."""
    engine, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    _call(tools["room_close"].ainvoke({"room_id": room_id}))
    raw = _call(tools["room_close"].ainvoke({"room_id": room_id}))
    assert raw.startswith("Error:"), raw
    assert "reason=room-closed" in raw, raw


def test_without_a_room_service_the_room_service_is_named(stack):
    engine, _svc, room_id, owner = stack
    manager = _manager(engine, MagicMock())
    manager._room_chat_service = None
    tools = _tools(manager, owner)
    raw = _call(tools["room_close"].ainvoke({"room_id": room_id}))
    assert raw.startswith("Error:"), raw
    assert "reason=room-service-unavailable" in raw, raw


# ── the layer-2 gate is FIRST ────────────────────────────────────────


def test_a_refused_caller_touches_no_room_service():
    """The gate is FIRST — a refused caller reaches NO service seam.

    The room service is a MOCK here: the assertion is that NONE of its
    seams was called (the refusal happens before the handle is even
    resolved).
    """
    manager = MagicMock()
    room_service = MagicMock()
    for name in (
        "get_room",
        "get_member",
        "list_members",
        "close_room",
        "create_room",
        "append_message",
        "read_messages",
    ):
        setattr(room_service, name, AsyncMock())
    manager._room_chat_service = room_service
    manager._room_delivery_service = MagicMock()
    tools = _tools(manager, "owner-1", agent_id="")
    raw = _call(tools["room_close"].ainvoke({"room_id": "r-1"}))
    assert raw.startswith("Error:"), raw
    assert "reason=unknown-agent" in raw, raw
    for name in ("get_room", "get_member", "close_room"):
        getattr(room_service, name).assert_not_called()

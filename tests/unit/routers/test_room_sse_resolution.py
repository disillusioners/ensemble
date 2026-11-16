"""Agent group chat P6 — the SSE ROOM-RESOLUTION PRE-BRANCH.

Architecture §8 pins the shape: *"SSE observability only (+room-resolution
pre-branch, ``routers/messages.py:960-963``; hub
``services/live_event_hub.py``)"*, and the §13 P6 row lists the
"SSE pre-branch" as P6 content. A-P6-10 (lane 1) DISCLOSED that the
branch did not exist; this file is the pin set that closes it.

WHAT IS PINNED

* the INSTANCE branch is unchanged — a live instance id resolves to
  itself and the ``room_id`` is empty;
* the ROOM branch — a room id resolves to the room's OWNER instance
  (the owner is inside the owner-ancestry chain, and the room's
  content-free event rows ride their instance's stream);
* the resolution is OBSERVABLE on the stream — the ``connected`` event
  carries the resolved instance, the room id and the branch that fired;
* FAIL-CLOSED — an id that is neither a live instance nor an existing
  room answers the typed 404; a PURGED room has no rows ⇒ it is
  NONEXISTENT (R2) ⇒ 404; a room whose owner instance is gone ⇒ 404;
* OBSERVABILITY-ONLY — the pre-branch resolves a STREAM TARGET and
  nothing else: it never routes, wakes, revives or delivers;
* the hub's ALIAS registration — the room-resolved connection receives
  the events streamed under EITHER key (the room id OR the owner
  instance), and the removal is SYMMETRIC (no alias bucket outlives
  its connection).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from sqlalchemy.engine import Engine

from daemon.repositories.room.service import RoomChatService
from daemon.routers.messages import _resolve_sse_target, stream_events
from daemon.services.live_event_hub import LiveEventHub
from tests.helpers.room_harness import make_room, make_room_engine, seed_instance


OWNER = "inst-owner-0001"
OTHER = "inst-other-0002"


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_sse_resolution.db")
    yield eng
    eng.dispose()


@pytest.fixture
def svc(engine) -> RoomChatService:
    return RoomChatService(engine)


class _DeliveryWitness:
    """The observability-only witness — a delivery surface that RECORDS
    every call. The pre-branch must never touch it."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def route_post(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {}


class _Manager:
    """A manager stub carrying the seams the SSE route touches."""

    def __init__(self, svc: RoomChatService) -> None:
        self._room_chat_service = svc
        self._known: set[str] = set()
        self.is_shutting_down = False
        # The observability-only witness: the pre-branch must never
        # touch the delivery/routing surface.
        self._room_delivery_service = _DeliveryWitness()

    async def get_instance(self, instance_id: str) -> dict[str, Any]:
        if instance_id not in self._known:
            raise KeyError(instance_id)
        return {"instance_id": instance_id}


def _request(manager: _Manager, hub: LiveEventHub) -> Any:
    """A minimal Request-shaped object (the route reads app.state)."""
    app = SimpleNamespace(
        state=SimpleNamespace(manager=manager, live_hub=hub)
    )
    return SimpleNamespace(app=app)


async def _first_event(
    manager: _Manager, hub: LiveEventHub, raw_id: str
) -> dict[str, Any]:
    """Pull the FIRST SSE event the route yields (the ``connected``)."""
    response = await stream_events(raw_id, _request(manager, hub))
    iterator = response.body_iterator
    try:
        return await iterator.__anext__()
    finally:
        await iterator.aclose()


# ── the resolution helper ───────────────────────────────────────────────


class TestTheRoomResolutionPreBranch:
    async def test_an_instance_id_resolves_to_itself(
        self, engine, svc
    ) -> None:
        """The INSTANCE branch is the historical path — unchanged."""
        seed_instance(engine, OWNER)
        manager = _Manager(svc)
        manager._known.add(OWNER)
        resolved, room_id = await _resolve_sse_target(manager, OWNER)
        assert resolved == OWNER
        assert room_id == ""

    async def test_a_room_id_resolves_to_its_owner_instance(
        self, engine, svc
    ) -> None:
        """The ROOM branch — the room id resolves to the OWNER's stream."""
        seed_instance(engine, OWNER)
        manager = _Manager(svc)
        manager._known.add(OWNER)
        room_id = make_room(
            svc._repo,
            tree_root=OWNER,
            owner=OWNER,
            name="scene-3 build",
            owner_member_name="producer",
        )
        resolved, resolved_room = await _resolve_sse_target(manager, room_id)
        assert resolved == OWNER, resolved
        assert resolved_room == room_id

    async def test_an_unknown_id_is_a_typed_404(self, engine, svc) -> None:
        """Fail-closed: neither an instance nor a room ⇒ 404."""
        from fastapi import HTTPException

        manager = _Manager(svc)
        with pytest.raises(HTTPException) as excinfo:
            await _resolve_sse_target(manager, "no-such-id")
        assert excinfo.value.status_code == 404

    async def test_a_purged_room_is_a_typed_404(self, engine, svc) -> None:
        """A PURGED room has no rows ⇒ NONEXISTENT (R2) ⇒ 404."""
        from fastapi import HTTPException

        seed_instance(engine, OWNER)
        manager = _Manager(svc)
        manager._known.add(OWNER)
        room_id = make_room(
            svc._repo,
            tree_root=OWNER,
            owner=OWNER,
            name="doomed",
            owner_member_name="producer",
        )
        svc._repo.purge_room(room_id, reason="p6-sse-test")
        with pytest.raises(HTTPException) as excinfo:
            await _resolve_sse_target(manager, room_id)
        assert excinfo.value.status_code == 404

    async def test_a_room_whose_owner_is_gone_is_a_typed_404(
        self, engine, svc
    ) -> None:
        """The stream needs a LIVE target — a missing owner ⇒ 404."""
        from fastapi import HTTPException

        seed_instance(engine, OWNER)
        manager = _Manager(svc)
        # The owner row exists in the DB but is NOT a known live instance
        # for this manager — the room resolves, the owner does not.
        room_id = make_room(
            svc._repo,
            tree_root=OWNER,
            owner=OWNER,
            name="orphan",
            owner_member_name="producer",
        )
        with pytest.raises(HTTPException) as excinfo:
            await _resolve_sse_target(manager, room_id)
        assert excinfo.value.status_code == 404

    async def test_a_manager_without_the_room_service_answers_404(
        self, engine, svc
    ) -> None:
        """A daemon without the room substrate keeps the old 404."""
        from fastapi import HTTPException

        manager = _Manager(svc)
        manager._room_chat_service = None
        with pytest.raises(HTTPException) as excinfo:
            await _resolve_sse_target(manager, "room-1")
        assert excinfo.value.status_code == 404

    async def test_the_resolution_is_observability_only(self, engine, svc):
        """The pre-branch resolves a STREAM TARGET and nothing else — it
        never routes, wakes, revives or delivers."""
        seed_instance(engine, OWNER)
        manager = _Manager(svc)
        manager._known.add(OWNER)
        room_id = make_room(
            svc._repo,
            tree_root=OWNER,
            owner=OWNER,
            name="scene-3 build",
            owner_member_name="producer",
        )
        await _resolve_sse_target(manager, room_id)
        assert manager._room_delivery_service.calls == []


# ── the observable resolution on the stream ─────────────────────────────


class TestTheConnectedEventCarriesTheResolution:
    async def test_the_instance_branch_stamps_the_instance(self, engine, svc):
        seed_instance(engine, OWNER)
        manager = _Manager(svc)
        manager._known.add(OWNER)
        hub = LiveEventHub()
        event = await _first_event(manager, hub, OWNER)
        assert event["event"] == "connected"
        payload = json.loads(event["data"])
        assert payload == {
            "instance_id": OWNER,
            "room_id": "",
            "resolved_from": "instance",
        }, payload

    async def test_the_room_branch_stamps_the_resolution(self, engine, svc):
        seed_instance(engine, OWNER)
        manager = _Manager(svc)
        manager._known.add(OWNER)
        room_id = make_room(
            svc._repo,
            tree_root=OWNER,
            owner=OWNER,
            name="scene-3 build",
            owner_member_name="producer",
        )
        hub = LiveEventHub()
        event = await _first_event(manager, hub, room_id)
        assert event["event"] == "connected"
        payload = json.loads(event["data"])
        assert payload == {
            "instance_id": OWNER,
            "room_id": room_id,
            "resolved_from": "room",
        }, payload


# ── the hub's alias registration (the room-as-instance-id reuse) ────────


class TestTheHubAliasRegistration:
    async def test_a_room_resolved_connection_receives_both_keys(self):
        """The reuse: an event streamed under EITHER key reaches it."""
        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-owner", queue, alias="room-1")
        assert await hub.get_connection_count("inst-owner") == 1
        assert await hub.get_connection_count("room-1") == 1

        await hub.stream_message("inst-owner", {"message_id": "m-1"})
        await hub.stream_message("room-1", {"message_id": "m-2"})
        first = queue.get_nowait()
        second = queue.get_nowait()
        assert {
            first["message"]["message_id"],
            second["message"]["message_id"],
        } == {"m-1", "m-2"}

    async def test_the_removal_is_symmetric(self):
        """No alias bucket outlives its connection."""
        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-owner", queue, alias="room-1")
        await hub.remove_connection("inst-owner", queue, alias="room-1")
        assert await hub.get_connection_count("inst-owner") == 0
        assert await hub.get_connection_count("room-1") == 0

    async def test_the_single_key_shape_is_unchanged(self):
        """The default (no alias) keeps the historical registration."""
        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-1", queue)
        assert await hub.get_connection_count("inst-1") == 1
        await hub.remove_connection("inst-1", queue)
        assert await hub.get_connection_count("inst-1") == 0

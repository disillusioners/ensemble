"""Agent group chat P6 — the HTTP room-read surface (the observability
modal's backend).

The wire contract is BINDING (the frontend codes against it in
parallel), so this file pins the EXACT field sets:

* ``GET /api/instances/{instance_id}/rooms`` →
  ``{"rooms": [{"room_id", "name", "created_at", "closed",
  "member_count", "last_message_at", "last_message_seq"}]}``
* ``GET /api/rooms/{room_id}/messages?after_seq=&limit=`` →
  ``{"messages": [{"seq", "instance_id", "member_name", "content",
  "created_at", "kind"}], "next_after_seq": int|null}``

What the pins cover:

* the FIELD SETS are exact (no extra key, none missing);
* the room list is TREE-scoped (a room of another tree is not listed);
* a CLOSED room is listed (``closed: true`` — the §2.1 capability row
  keeps closed rooms readable); a PURGED room is NOT listed (R2);
* the message window is ASCENDING by ``seq`` and the cursor is
  CLAMPED to the last returned seq (never an unclamped echo);
  ``next_after_seq`` is ``null`` when the window is exhausted;
* the limit is bounded (max 200);
* the fail-closed posture: an unknown room, a purged room and an
  out-of-scope instance all answer the typed 404;
* the ROLE posture: the route declares the EXISTING ``instances:read``
  scope (no new auth model).
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.room.models import ChatRoom, ChatRoomMember
from daemon.repositories.room.service import RoomChatService
from daemon.routers.rooms import router as rooms_router
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    resolve_tree_root_id,
    seed_instance,
)

#: The EXACT field set of one room entry (the FE contract).
ROOM_FIELDS = {
    "room_id",
    "name",
    "created_at",
    "closed",
    "member_count",
    "last_message_at",
    "last_message_seq",
}

#: The EXACT field set of one message entry (the FE contract).
MESSAGE_FIELDS = {
    "seq",
    "instance_id",
    "member_name",
    "content",
    "created_at",
    "kind",
}


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_http_api.db")
    yield eng
    eng.dispose()


@pytest.fixture
def svc(engine) -> RoomChatService:
    return RoomChatService(engine)


class _Manager:
    """A manager stub carrying the seams the routes touch."""

    def __init__(self, engine: Engine, svc: RoomChatService) -> None:
        self._engine = engine
        self._room_chat_service = svc
        self._instance_repository = _InstanceRepo(engine)
        self._known: set[str] = set()

    async def get_instance(self, instance_id: str) -> dict[str, Any]:
        if instance_id not in self._known:
            raise KeyError(instance_id)
        return {"instance_id": instance_id}


class _InstanceRepo:
    """A tree-root resolver over the real instance rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_tree_root_id(self, instance_id: str) -> str | None:
        return resolve_tree_root_id(self._engine, instance_id)


@pytest.fixture
def client(engine, svc) -> Iterator[TestClient]:
    manager = _Manager(engine, svc)
    app = FastAPI()
    app.include_router(rooms_router, prefix="/api")
    app.state.manager = manager
    with TestClient(app) as test_client:
        test_client.manager = manager  # type: ignore[attr-defined]
        yield test_client


def _room(
    engine: Engine,
    svc: RoomChatService,
    *,
    tree: str,
    owner: str,
    name: str = "alpha",
) -> str:
    return make_room(
        svc._repo, tree_root=tree, owner=owner, name=name, owner_member_name="lead"
    )


# ── the room list ──────────────────────────────────────────────────────


def test_the_room_list_carries_the_exact_contract_fields(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")

    response = client.get("/api/instances/owner-1/rooms")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"rooms"}
    assert len(body["rooms"]) == 1
    entry = body["rooms"][0]
    assert set(entry) == ROOM_FIELDS
    assert entry["room_id"] == room_id
    assert entry["name"] == "alpha"
    assert entry["closed"] is False
    assert entry["member_count"] == 1
    assert entry["last_message_at"] is None
    assert entry["last_message_seq"] is None
    assert isinstance(entry["created_at"], str)


def test_the_room_list_is_tree_scoped(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "other-1", None, "idle")
    manager._known.update({"owner-1", "other-1"})
    mine = _room(engine, svc, tree="owner-1", owner="owner-1")
    theirs = _room(engine, svc, tree="other-1", owner="other-1", name="beta")

    body = client.get("/api/instances/owner-1/rooms").json()
    room_ids = [entry["room_id"] for entry in body["rooms"]]
    assert room_ids == [mine]
    assert theirs not in room_ids


def test_a_closed_room_is_listed_and_a_purged_room_is_not(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    closed = _room(engine, svc, tree="owner-1", owner="owner-1", name="closed-room")
    purged = _room(engine, svc, tree="owner-1", owner="owner-1", name="purged-room")
    with Session(engine) as s:
        room = s.get(ChatRoom, closed)
        room.state = "closed"
        s.commit()
    svc._repo.purge_room(purged, reason="test")

    body = client.get("/api/instances/owner-1/rooms").json()
    entries = {entry["room_id"]: entry for entry in body["rooms"]}
    assert closed in entries
    assert entries[closed]["closed"] is True
    assert purged not in entries


def test_the_member_count_counts_the_active_roster_only(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    seed_instance(engine, "child-2", "owner-1", "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")
    join_member(engine, room_id, "child-1", "worker")
    join_member(engine, room_id, "child-2", "worker-2")
    # A LEFT row is retired — it is not a member of the room.
    with Session(engine) as s:
        member = s.get(ChatRoomMember, (room_id, "child-2"))
        member.state = "left"
        s.commit()

    body = client.get("/api/instances/owner-1/rooms").json()
    assert body["rooms"][0]["member_count"] == 2


def test_an_unknown_instance_answers_404(client):
    response = client.get("/api/instances/nope/rooms")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "instance_not_found"


# ── the message window ─────────────────────────────────────────────────


def test_the_message_window_carries_the_exact_contract_fields(
    client, engine, svc
):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")
    svc._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="milestone one",
    )

    response = client.get(f"/api/rooms/{room_id}/messages")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"messages", "next_after_seq"}
    assert len(body["messages"]) == 1
    message = body["messages"][0]
    assert set(message) == MESSAGE_FIELDS
    assert message["seq"] == 1
    assert message["instance_id"] == "owner-1"
    assert message["member_name"] == "lead"
    assert message["content"] == "milestone one"
    assert message["kind"] == "message"
    assert isinstance(message["created_at"], str)
    # Exhausted ⇒ the cursor is null.
    assert body["next_after_seq"] is None


def test_the_cursor_is_clamped_to_the_last_returned_seq(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")
    for index in range(5):
        svc._repo.append_message(
            room_id=room_id,
            author_instance_id="owner-1",
            author_name="lead",
            text_body=f"m{index}",
        )

    body = client.get(f"/api/rooms/{room_id}/messages?limit=2").json()
    assert [m["seq"] for m in body["messages"]] == [1, 2]
    # CLAMPED to the last RETURNED seq — never the requested cursor
    # echoed back, never a seq beyond the window.
    assert body["next_after_seq"] == 2

    body = client.get(
        f"/api/rooms/{room_id}/messages?after_seq=2&limit=2"
    ).json()
    assert [m["seq"] for m in body["messages"]] == [3, 4]
    assert body["next_after_seq"] == 4

    body = client.get(
        f"/api/rooms/{room_id}/messages?after_seq=4&limit=2"
    ).json()
    assert [m["seq"] for m in body["messages"]] == [5]
    assert body["next_after_seq"] is None


def test_the_limit_is_bounded(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")
    for index in range(3):
        svc._repo.append_message(
            room_id=room_id,
            author_instance_id="owner-1",
            author_name="lead",
            text_body=f"m{index}",
        )
    body = client.get(f"/api/rooms/{room_id}/messages?limit=9999").json()
    assert len(body["messages"]) == 3
    # A non-positive limit falls back to the default window.
    body = client.get(f"/api/rooms/{room_id}/messages?limit=0").json()
    assert len(body["messages"]) == 3


def test_an_unknown_room_answers_404(client):
    response = client.get("/api/rooms/nope/messages")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "room_not_found"


def test_a_purged_room_answers_404(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")
    svc._repo.purge_room(room_id, reason="test")
    response = client.get(f"/api/rooms/{room_id}/messages")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "room_not_found"


def test_an_out_of_scope_instance_answers_404(client, engine, svc):
    """The optional ``?instance_id=`` scope rides the SAME fail-closed
    predicate: an instance that is neither an active member nor in the
    owner-ancestry chain gets the typed not-found."""
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "outsider-1", None, "idle")
    manager._known.update({"owner-1", "outsider-1"})
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")

    response = client.get(
        f"/api/rooms/{room_id}/messages?instance_id=outsider-1"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "room_not_found"


# ── the infra-fault envelope (M3, A-P6-25) ────────────────────────────
#
# A faulting room-service call must NEVER escape to the global 500
# handler (its ``{code: INTERNAL_ERROR, message: str(exc)}`` body is
# the wrong shape for this surface AND echoes raw fault text to the
# client). Each pin INJECTS a fault and asserts the TYPED envelope
# fields — the shape, the vocabulary token, and that the fault text
# stays server-side.


def test_a_faulted_room_list_answers_the_typed_envelope(
    client, engine, svc, monkeypatch
):
    """A faulted room-LIST read answers 503 ``room_service_unavailable``
    in the ``{error, message}`` envelope — never a raw 500 body."""
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")

    async def _boom(tree_root):
        raise RuntimeError("room list boom")

    monkeypatch.setattr(svc, "list_rooms_for_tree", _boom)
    response = client.get("/api/instances/owner-1/rooms")
    body = response.json()
    assert response.status_code == 503
    assert body["detail"]["error"] == "room_service_unavailable"
    assert body["detail"]["message"] == (
        "the room list could not be read right now"
    )
    # The security note: the fault text never reaches the wire.
    assert "boom" not in response.text


def test_a_faulted_room_resolution_answers_the_typed_404(
    client, engine, svc, monkeypatch
):
    """A faulted room RESOLUTION answers the SAME fail-closed 404 the
    SSE lane's pre-branch answers on a broken read (A-P6-11 family):
    an unresolvable room is never distinguished from an absent one on
    the wire."""
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")

    async def _boom(target):
        raise RuntimeError("room resolve boom")

    monkeypatch.setattr(svc, "get_room", _boom)
    response = client.get(f"/api/rooms/{room_id}/messages")
    body = response.json()
    assert response.status_code == 404
    assert body["detail"]["error"] == "room_not_found"
    assert "boom" not in response.text


def test_a_faulted_log_read_answers_the_typed_503(
    client, engine, svc, monkeypatch
):
    """A faulted LOG read (the room itself resolved fine) answers 503
    ``room_service_unavailable`` — present-but-faulted, retryable,
    generic message."""
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")

    real_read = svc.read_messages

    async def _boom(*args, **kwargs):
        raise RuntimeError("log read boom")

    monkeypatch.setattr(svc, "read_messages", _boom)
    response = client.get(f"/api/rooms/{room_id}/messages")
    body = response.json()
    assert response.status_code == 503
    assert body["detail"]["error"] == "room_service_unavailable"
    assert body["detail"]["message"] == (
        "the room log could not be read right now"
    )
    assert "boom" not in response.text

    # The typed not-found is STILL the answer for a genuinely absent
    # room — the wrap never shadows the vocabulary's 404 member.
    monkeypatch.setattr(svc, "read_messages", real_read)
    response = client.get("/api/rooms/nope/messages")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "room_not_found"


def test_an_active_member_scope_reads_the_room(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    manager._known.update({"owner-1", "child-1"})
    room_id = _room(engine, svc, tree="owner-1", owner="owner-1")
    join_member(engine, room_id, "child-1", "worker")
    svc._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="milestone",
    )
    response = client.get(
        f"/api/rooms/{room_id}/messages?instance_id=child-1"
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["messages"]) == 1


def test_the_routes_declare_the_instances_read_scope(client):
    """The role posture: the EXISTING ``instances:read`` scope — no new
    auth model (the scope cage pins the family entry too)."""
    scopes = set()
    for route in client.app.routes:
        for dep in getattr(getattr(route, "dependant", None), "dependencies", []) or []:
            declared = getattr(dep.call, "required_scopes", None)
            if declared:
                scopes.update(declared)
    assert "instances:read" in scopes

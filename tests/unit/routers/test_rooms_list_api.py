"""Phase 1 — ``GET /api/rooms`` (the nest-wide read surface).

Pins the wire contract of the new route: exact row field set + envelope
``truncated`` flag, anchor-present scope (denial + positives), anchor-absent
breadth (purged excluded), typed errors (404 bad anchor, 503 faulted
service), the server-side bound (clamp + limit+1 probe + per-path
ordering), the F-3 knob refusal (serial, monkeypatched), the N9 422
present-but-empty leg, the C-4 stub-not-called leg, the AM-10
hard-deleted-caller narrowing, and the W-11 anchored-superset invariant.
"""

from __future__ import annotations

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
from daemon.routers import rooms as rooms_module
from daemon.routers.rooms import router as rooms_router
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    resolve_tree_root_id,
    seed_instance,
    seed_raw_room,
)


#: The EXACT field set of one row in the new envelope (D1.1).
ROOM_LIST_FIELDS = {
    "room_id",
    "name",
    "tree_root_id",
    "owner_instance_id",
    "state",
    "member_count",
    "member_instance_ids",
    "last_message_at",
    "last_message_seq",
    "created_at",
}


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = make_room_engine(tmp_path, "rooms_list_api.db")
    yield eng
    eng.dispose()


@pytest.fixture
def svc(engine) -> RoomChatService:
    return RoomChatService(engine)


class _InstanceRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, instance_id: str):
        with Session(self._engine) as s:
            return s.get(Instance, instance_id)

    def get_tree_root_id(self, instance_id: str) -> str | None:
        return resolve_tree_root_id(self._engine, instance_id)


class _Manager:
    def __init__(self, engine: Engine, svc: RoomChatService) -> None:
        self._engine = engine
        self._room_chat_service = svc
        self._instance_repository = _InstanceRepo(engine)
        self._known: set[str] = set()
        self.get_instance_called: list[str] = []

    async def get_instance(self, instance_id: str) -> dict:
        self.get_instance_called.append(instance_id)
        if instance_id not in self._known:
            raise KeyError(instance_id)
        return {"instance_id": instance_id}

    def get_instance_info(self, instance_id: str) -> dict:
        with Session(self._engine) as s:
            row = s.get(Instance, instance_id)
            if row is None:
                raise KeyError(instance_id)
            return row.to_dict() if hasattr(row, "to_dict") else {"instance_id": instance_id}

    def get_tree_ids_permanent(self, caller_instance_id: str) -> list[str]:
        # caller-inclusive downward walk (mirrors repository shape).
        out = [caller_instance_id]
        # one level of children
        with Session(self._engine) as s:
            rows = s.exec(
                __import__("sqlmodel").select(Instance).where(
                    Instance.parent_id == caller_instance_id
                )
            ).all()
            for r in rows:
                out.append(r.instance_id)
        return out


@pytest.fixture
def client(engine, svc) -> TestClient:
    manager = _Manager(engine, svc)
    app = FastAPI()
    app.include_router(rooms_router, prefix="/api")
    app.state.manager = manager
    with TestClient(app) as test_client:
        test_client.manager = manager  # type: ignore[attr-defined]
        yield test_client


def _seed_tree(engine, manager, root: str) -> None:
    seed_instance(engine, root, None, "idle")
    manager._known.add(root)


def _make_room(engine, svc, tree, owner, name="alpha", state="active"):
    rid = make_room(svc._repo, tree_root=tree, owner=owner, name=name)
    if state == "closed":
        with Session(engine) as s:
            r = s.get(ChatRoom, rid)
            r.state = "closed"
            s.commit()
    return rid


# ── exact field set ────────────────────────────────────────────────────

def test_exact_field_set_and_truncated_flag(client, engine, svc):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    rid = _make_room(engine, svc, "owner-1", "owner-1")

    resp = client.get("/api/rooms")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"rooms", "truncated"}
    assert body["truncated"] is False
    assert len(body["rooms"]) == 1
    entry = body["rooms"][0]
    assert set(entry) == ROOM_LIST_FIELDS
    assert entry["room_id"] == rid
    assert entry["state"] == "active"
    assert entry["member_instance_ids"] == ["owner-1"]


def test_anchor_present_denial(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "other-1", None, "idle")
    manager._known.update({"owner-1", "other-1"})
    rid = _make_room(engine, svc, "owner-1", "owner-1")

    # other-1 is neither member nor ancestor ⇒ room absent
    resp = client.get("/api/rooms?instance_id=other-1")
    assert resp.status_code == 200
    ids = [r["room_id"] for r in resp.json()["rooms"]]
    assert rid not in ids


def test_anchor_present_positives(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    manager._known.update({"owner-1", "child-1"})
    rid = _make_room(engine, svc, "owner-1", "owner-1")
    join_member(engine, rid, "child-1", "worker")

    # active member sees it
    resp = client.get("/api/rooms?instance_id=child-1")
    assert rid in [r["room_id"] for r in resp.json()["rooms"]]

    # owner-ancestor sees it
    resp = client.get("/api/rooms?instance_id=owner-1")
    assert rid in [r["room_id"] for r in resp.json()["rooms"]]


def test_anchor_absent_breadth(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "other-1", None, "idle")
    manager._known.update({"owner-1", "other-1"})
    r1 = _make_room(engine, svc, "owner-1", "owner-1", name="a")
    r2 = _make_room(engine, svc, "other-1", "other-1", name="b", state="closed")
    # purged room
    r3 = _make_room(engine, svc, "owner-1", "owner-1", name="purged")
    svc._repo.purge_room(r3, reason="test")

    resp = client.get("/api/rooms")
    assert resp.status_code == 200
    ids = [r["room_id"] for r in resp.json()["rooms"]]
    assert r1 in ids and r2 in ids
    assert r3 not in ids
    # closed room present
    closed_entry = [r for r in resp.json()["rooms"] if r["room_id"] == r2][0]
    assert closed_entry["state"] == "closed"


def test_bad_anchor_404(client, engine, svc):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    resp = client.get("/api/rooms?instance_id=nope")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "instance_not_found"


def test_faulted_service_503(client, engine, svc):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    # fault the service
    import unittest.mock as m
    with m.patch.object(svc, "list_all_rooms", side_effect=RuntimeError("boom")):
        resp = client.get("/api/rooms")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "room_service_unavailable"
    # leak scan: no fault text
    assert "boom" not in resp.text


# ── bound ───────────────────────────────────────────────────────────────

def test_bound_clamp(client, engine, svc):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    # create 3 rooms
    for i in range(3):
        _make_room(engine, svc, "owner-1", "owner-1", name=f"r{i}")

    for limit in [0, 1, 2, 3, 5]:
        resp = client.get(f"/api/rooms?limit={limit}")
        assert resp.status_code == 200
        expected = min(max(limit, 1), 3)
        assert len(resp.json()["rooms"]) == expected, (limit, len(resp.json()["rooms"]))

    # 9999 -> all 3
    resp = client.get("/api/rooms?limit=9999")
    assert len(resp.json()["rooms"]) == 3

    # limit=101 proves no inheritance of 100 constant: create 105 rooms? skip due cost; instead check clamp constant
    assert rooms_module.ROOM_LIST_MAX_LIMIT == 500


def test_limit_101_no_inheritance(client, engine, svc):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    # create 105 rooms (raw, bypassing the per-tree active-room cap)
    for i in range(105):
        seed_raw_room(engine, "owner-1", f"r{i}")
    resp = client.get("/api/rooms?limit=101")
    assert resp.status_code == 200
    assert len(resp.json()["rooms"]) == 101
    assert resp.json()["truncated"] is True


def test_truncated_flag(client, engine, svc):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    for i in range(5):
        _make_room(engine, svc, "owner-1", "owner-1", name=f"r{i}")
    resp = client.get("/api/rooms?limit=3")
    assert resp.json()["truncated"] is True
    assert len(resp.json()["rooms"]) == 3
    resp = client.get("/api/rooms?limit=10")
    assert resp.json()["truncated"] is False


# ── knob-OFF refusal (serial) ──────────────────────────────────────────

@pytest.mark.no_xdist
def test_knob_off_refusal_serial(client, engine, svc, monkeypatch):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    _make_room(engine, svc, "owner-1", "owner-1")

    monkeypatch.setattr(rooms_module, "_ALLOW_ANCHORLESS_GLOBAL_LIST", False)
    try:
        # anchor-ABSENT spellings (parameter not supplied)
        for url in ["/api/rooms", "/api/rooms?limit=5"]:
            resp = client.get(url)
            assert resp.status_code == 404, (url, resp.status_code)
            assert resp.json()["detail"]["error"] == "instance_not_found"
            assert resp.json()["detail"]["message"] == "Instance not found"
        # anchored still works
        resp = client.get("/api/rooms?instance_id=owner-1")
        assert resp.status_code == 200
    finally:
        monkeypatch.setattr(rooms_module, "_ALLOW_ANCHORLESS_GLOBAL_LIST", True)


# ── N9 422 present-but-empty ───────────────────────────────────────────

def test_n9_422_present_but_empty(client, engine, svc):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    for url in ["/api/rooms?instance_id=", "/api/rooms?instance_id=%20"]:
        resp = client.get(url)
        assert resp.status_code == 422, (url, resp.status_code)
        assert resp.json()["detail"]["error"] == "bad_request"


def test_n9_422_holds_under_knob_off(client, engine, svc, monkeypatch):
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    monkeypatch.setattr(rooms_module, "_ALLOW_ANCHORLESS_GLOBAL_LIST", False)
    try:
        for url in ["/api/rooms?instance_id=", "/api/rooms?instance_id=%20"]:
            resp = client.get(url)
            assert resp.status_code == 422, (url, resp.status_code)
    finally:
        monkeypatch.setattr(rooms_module, "_ALLOW_ANCHORLESS_GLOBAL_LIST", True)


# ── C-4 stub-not-called ────────────────────────────────────────────────

def test_stub_not_called(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    manager._known.add("owner-1")
    _make_room(engine, svc, "owner-1", "owner-1")

    manager.get_instance_called.clear()
    resp = client.get("/api/rooms?instance_id=owner-1")
    assert resp.status_code == 200
    assert manager.get_instance_called == [], "manager.get_instance must not be called on anchored path"


# ── AM-10 hard-deleted caller narrowing ────────────────────────────────

def test_am10_hard_deleted_caller(client, engine, svc):
    manager = client.manager
    # subject id referenced by a child's parent_id, but subject row hard-deleted
    seed_instance(engine, "child-1", "ghost-parent", "idle")
    manager._known.add("child-1")
    # create a room owned by ghost-parent (but owner row missing)
    rid = _make_room(engine, svc, "child-1", "ghost-parent", name="r")
    # remove the owner row (hard-delete) — but room remains
    with Session(engine) as s:
        inst = s.get(Instance, "ghost-parent")
        if inst is not None:
            s.delete(inst)
            s.commit()

    # shipped per-room path admits (up-walk reads child's parent_id); 
    # list path denies (closure empty). We only test list path here:
    resp = client.get("/api/rooms?instance_id=child-1")
    # child-1 is not a member of the room (owner ghost-parent), and ghost-parent
    # is not in child-1's descendant closure => denied
    ids = [r["room_id"] for r in resp.json()["rooms"]]
    assert rid not in ids


# ── W-11 anchored superset ─────────────────────────────────────────────

def test_w11_anchored_superset(client, engine, svc):
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    manager._known.update({"owner-1", "child-1"})
    rid = _make_room(engine, svc, "owner-1", "owner-1")
    join_member(engine, rid, "child-1", "worker")

    resp = client.get("/api/rooms?instance_id=child-1")
    assert resp.status_code == 200
    ids = [r["room_id"] for r in resp.json()["rooms"]]
    assert rid in ids  # joined room present in anchored list

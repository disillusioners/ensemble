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

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

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

ROOMS_ROUTER_SRC = (
    Path(__file__).resolve().parents[3] / "daemon" / "routers" / "rooms.py"
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
        # caller-inclusive downward walk (mirrors the real facade shape —
        # including its root-existence guard: a missing root ⇒ []).
        with Session(self._engine) as s:
            if s.get(Instance, caller_instance_id) is None:
                return []
            out = [caller_instance_id]
            children = s.exec(
                select(Instance).where(
                    Instance.parent_id == caller_instance_id
                )
            ).all()
            for r in children:
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


def _stamp_created_at(engine, rid: str, seq: int) -> None:
    """Pin a room's ``created_at`` — the ordering key is deterministic."""
    with Session(engine) as s:
        row = s.get(ChatRoom, rid)
        stamp = f"2026-11-16T00:00:00.{seq:06d}+00:00"
        row.created_at = stamp
        row.updated_at = stamp
        s.commit()


def _seed_raw_rooms_bulk(
    engine, count: int, *, tree_root: str, owner: str
) -> list[str]:
    """Insert ``count`` rooms in ONE session with strictly increasing
    ``created_at`` — the bound legs need scale without N commits."""
    ids: list[str] = []
    with Session(engine) as s:
        for i in range(count):
            rid = f"bulk-{i:05d}"
            stamp = f"2026-11-16T00:00:00.{i:06d}+00:00"
            s.add(
                ChatRoom(
                    room_id=rid,
                    name=f"r{i}",
                    tree_root_id=tree_root,
                    owner_instance_id=owner,
                    state="active",
                    closed_at=None,
                    last_post_at=None,
                    message_count=0,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            ids.append(rid)
        s.commit()
    return ids


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
    # A-3 / D7 — the message is FIXED and INPUT-FREE: no echo of the
    # caller's input, and identical to the knob-OFF refusal below.
    assert resp.json()["detail"]["message"] == "Instance not found"
    assert "nope" not in resp.json()["detail"]["message"]


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
    # The limit+1 PROBE pin: at the exact boundary the probe's extra row
    # is what decides the flag — 5 rows with limit=5 is NOT truncated,
    # with limit=4 it is (no "≥" off-by-one, no len-based shortcut).
    resp = client.get("/api/rooms?limit=5")
    assert len(resp.json()["rooms"]) == 5
    assert resp.json()["truncated"] is False
    resp = client.get("/api/rooms?limit=4")
    assert len(resp.json()["rooms"]) == 4
    assert resp.json()["truncated"] is True


def test_bound_clamp_at_scale_and_default(client, engine, svc):
    """Criterion 20 — the clamp is honoured at EVERY pinned boundary.

    505 rows: enough to exercise 500 (the hard max) and 9999 (clamped).
    """
    manager = client.manager
    _seed_tree(engine, manager, "owner-1")
    total = 505
    _seed_raw_rooms_bulk(engine, total, tree_root="owner-1", owner="owner-1")

    # The DEFAULT limit leg: no ``limit`` param ⇒ 200 (D1.1-a).
    resp = client.get("/api/rooms")
    assert resp.status_code == 200
    assert len(resp.json()["rooms"]) == 200
    assert resp.json()["truncated"] is True

    for limit, expected in [
        (0, 1),  # clamped up to 1
        (1, 1),
        (100, 100),
        (101, 101),  # NO inheritance of the MAX_PAGE_LIMIT=100 constant
        (200, 200),
        (201, 201),
        (500, 500),  # the hard max is INCLUSIVE
        (9999, 500),  # clamped down to the hard max
    ]:
        resp = client.get(f"/api/rooms?limit={limit}")
        assert resp.status_code == 200, limit
        body = resp.json()
        assert len(body["rooms"]) == expected, (limit, len(body["rooms"]))
        assert body["truncated"] is True, limit


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


# ── AM-10 hard-deleted caller narrowing (W-3 re-aim) ───────────────────

def test_am10_hard_deleted_caller_list_denies_shipped_admits(
    client, engine, svc
):
    """AM-10 / W-3 — the ONE narrowing edge, both halves pinned.

    Subject = ``ghost-parent``: its instance ROW is hard-deleted, but its
    id is still referenced by ``child-1.parent_id``. The room is owned by
    ``child-1``.

    * LIST path (descendant closure): the closure is computed through
      ``get_tree_ids_permanent``, which returns ``[]`` when the anchor's
      own row is gone ⇒ closure empty ⇒ membership-only ⇒ DENIED.
    * SHIPPED per-room path (``_require_room_access``): the up-walk adds
      each ``parent_id`` it reads from the CURRENT row WITHOUT verifying
      that the parent's row still exists ⇒ ``ghost-parent`` lands in the
      chain ⇒ ADMITTED.

    Direction is fail-closed (never wider) and unreachable over HTTP (the
    router 404s a missing anchor), hence the repository-level form.
    """
    manager = client.manager
    seed_instance(engine, "child-1", "ghost-parent", "idle")
    rid = _make_room(engine, svc, "child-1", "child-1", name="r")
    # No ghost-parent row is ever created — but make the deletion explicit
    # if a helper seeded one.
    with Session(engine) as s:
        inst = s.get(Instance, "ghost-parent")
        if inst is not None:
            s.delete(inst)
            s.commit()

    repo = svc._repo

    # The closure the router would pass in: the REAL facade shape — a
    # missing root row yields [].
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )

    descendant_ids = SQLModelInstanceRepository(
        engine
    ).get_tree_ids_permanent("ghost-parent")
    assert descendant_ids == []

    # 1) LIST path DENIES (membership-only; ghost-parent has no row).
    readable, truncated = repo.list_readable_rooms_for_caller(
        "ghost-parent", descendant_ids=set(descendant_ids), limit=200
    )
    assert [r["room_id"] for r in readable] == []
    assert truncated is False

    # 2) SHIPPED per-room path ADMITS (the up-walk trusts child-1's
    #    parent_id without verifying the parent row exists).
    payload = repo._require_room_access(rid, "ghost-parent")
    assert payload["room_id"] == rid


# ── criterion 21 — anchored filter-then-clamp (anti-corruption) ────────

def test_criterion21_anchored_filter_then_clamp(client, engine, svc):
    """A joined room PAST the page boundary survives the clamp.

    The scan is ordered ``(created_at, room_id)``. Non-readable rooms fill
    the HEAD of the scan; the anchor's joined rooms are created LAST. A
    clamp-then-filter implementation would clamp to the (non-readable)
    head and return an empty page — filter-then-clamp returns the joined
    room and reports the readable omission.
    """
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    seed_instance(engine, "stranger-1", None, "idle")
    manager._known.update({"owner-1", "child-1", "stranger-1"})

    # N+1 NON-readable rooms (owned by a stranger, outside child-1's
    # membership and its owner-ancestry) at the HEAD of the scan.
    for i in range(3):
        head = _make_room(engine, svc, "stranger-1", "stranger-1", name=f"head-{i}")
        _stamp_created_at(engine, head, i)

    # The anchor's joined rooms — created LAST (past a limit=1 boundary).
    j1 = _make_room(engine, svc, "owner-1", "owner-1", name="joined-1")
    join_member(engine, j1, "child-1", "worker")
    _stamp_created_at(engine, j1, 10)
    j2 = _make_room(engine, svc, "owner-1", "owner-1", name="joined-2")
    join_member(engine, j2, "child-1", "worker")
    _stamp_created_at(engine, j2, 11)

    resp = client.get("/api/rooms?instance_id=child-1&limit=1")
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["room_id"] for r in body["rooms"]]
    # clamp-then-filter would have returned [] here (the head rows are
    # not readable); filter-then-clamp returns the first joined room.
    assert ids == [j1]
    assert body["truncated"] is True  # one readable room was omitted


# ── AM-12 single-branch grep pin ───────────────────────────────────────

def test_am12_single_branch_knob_grep_pin():
    """The knob is consulted in EXACTLY ONE branch (criterion 6)."""
    src = ROOMS_ROUTER_SRC.read_text(encoding="utf-8")
    # ONE negated gate — no second anchor-less path can fail open.
    assert src.count("if not _ALLOW_ANCHORLESS_GLOBAL_LIST") == 1
    gate_lines = [
        line.strip()
        for line in src.splitlines()
        if "_ALLOW_ANCHORLESS_GLOBAL_LIST" in line and line.strip().startswith("if ")
    ]
    assert gate_lines == ["if not _ALLOW_ANCHORLESS_GLOBAL_LIST:"]
    # The knob is DEFINED once, module-level.
    assert src.count("_ALLOW_ANCHORLESS_GLOBAL_LIST: bool = True") == 1
    # The ONLY unanchored fetch sits AFTER the gate (fail-open guard).
    assert src.count("room_service.list_all_rooms(") == 1
    assert src.index("if not _ALLOW_ANCHORLESS_GLOBAL_LIST:") < src.index(
        "room_service.list_all_rooms("
    )


# ── W-11 anchored superset (SET equality) ──────────────────────────────

def test_w11_anchored_superset(client, engine, svc):
    """An instance with N active memberships sees EVERY joined room — and
    nothing else (exact set, not a subset match)."""
    manager = client.manager
    seed_instance(engine, "owner-1", None, "idle")
    seed_instance(engine, "child-1", "owner-1", "idle")
    seed_instance(engine, "stranger-1", None, "idle")
    manager._known.update({"owner-1", "child-1", "stranger-1"})

    joined: list[str] = []
    for i in range(3):
        rid = _make_room(engine, svc, "owner-1", "owner-1", name=f"joined-{i}")
        join_member(engine, rid, "child-1", f"worker-{i}")
        _stamp_created_at(engine, rid, i)
        joined.append(rid)

    # A room child-1 neither joined nor owns (owner is a stranger ⇒ not in
    # child-1's descendant closure) — must NOT appear.
    outsider = _make_room(engine, svc, "stranger-1", "stranger-1", name="outside")
    _stamp_created_at(engine, outsider, 9)

    resp = client.get("/api/rooms?instance_id=child-1")
    assert resp.status_code == 200
    ids = {r["room_id"] for r in resp.json()["rooms"]}
    assert ids == set(joined)

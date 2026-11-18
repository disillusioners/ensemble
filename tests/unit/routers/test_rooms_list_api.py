"""Phase 1 — ``GET /api/rooms`` (the nest-wide read surface).

Pins the wire contract of the new route: exact row field set + envelope
``truncated`` flag, anchor-present scope (denial + positives), anchor-absent
breadth (purged excluded), typed errors (404 bad anchor, 503 faulted
service), the server-side bound (clamp + limit+1 probe + per-path
ordering), the F-3 knob refusal (serial, monkeypatched), the N9 422
present-but-empty leg, the C-4 stub-not-called leg, the AM-10
hard-deleted-caller narrowing, and the W-11 anchored-superset invariant.

Code-council close-out (BE lane, 2026-11-18):

* **W2** — the manager double DELEGATES ``get_tree_ids_permanent`` to the
  real ``SQLModelInstanceRepository`` (full permanent subtree, missing
  root ⇒ ``[]``), and ``test_w2_grandparent_anchor_sees_grandchilds_room``
  puts the anchor strictly ABOVE the owner — the only configuration in
  which the ancestor-of-owner predicate branch is observable.
* **W3** — the anchored path is pinned as a LEAK assertion (no
  unreadable room's title / roster / row id / tree / owner appears in
  the body at all), and the anchor-less path is pinned as the EXACT
  nest-wide set (S-2's unfiltered breadth).
* **A3** — the knob's default is env-resolved
  (``ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST``) with the module constant as
  the fallback; parse / fallback / single-source are pinned here.
* **A5** — the knob-flipping legs carry ``no_xdist`` (process-global
  mutation).
* **A6** — the AM-12 single-branch pin is an AST check (not a text grep)
  with its own in-memory counter-mutation fixture.
"""

from __future__ import annotations

import ast
from pathlib import Path

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
        """DELEGATE to the REAL facade (council W2(a)).

        The production callable is
        ``SQLModelInstanceRepository.get_tree_ids_permanent`` — a
        caller-inclusive walk of ``instances.parent_id`` over the FULL
        permanent subtree (anchor + ALL descendants), with the real
        "missing root row ⇒ []" guard.

        A local re-implementation HAD DRIFTED: it returned the caller
        plus DIRECT CHILDREN only, so ``descendant_ids`` handed to the
        repository never contained a grandchild — which made the
        ancestor-of-owner predicate branch (``owner in descendant_set``)
        unobservable, i.e. deletable with the whole suite green (council
        W2's counter-mutation). Delegating to the real walk keeps the
        double honest by construction instead of by re-derivation.
        """
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        return SQLModelInstanceRepository(
            self._engine
        ).get_tree_ids_permanent(caller_instance_id)


@pytest.fixture
def client(engine, svc) -> TestClient:
    manager = _Manager(engine, svc)
    app = FastAPI()
    app.include_router(rooms_router, prefix="/api")
    app.state.manager = manager
    with TestClient(app) as test_client:
        test_client.manager = manager  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture(autouse=True)
def _pin_knob_default():
    """Pin the F-3 knob to its SHIPPED wide default for every leg.

    A3 made the knob's default env-resolved at IMPORT time
    (``ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST``), so an operator who
    tightens the knob would otherwise flip this whole suite's ambient
    posture — the anchor-less breadth / clamp / truncation legs would
    start 404ing for a reason that has nothing to do with what they
    test. The suite pins the knob to the shipped default; the knob's
    OFF behaviour is pinned by the two flip legs, and the env
    resolution itself is pinned by the A3 legs (including the restart
    leg, which reloads the module under a temporary env override).
    """
    original = rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST
    rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST = (
        rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT
    )
    try:
        yield
    finally:
        rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST = original


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


@pytest.mark.no_xdist
def test_n9_422_holds_under_knob_off(client, engine, svc, monkeypatch):
    """422 taxonomy survives the knob being OFF.

    A5: this leg flips the SAME process-global module attribute as its
    marked twin ``test_knob_off_refusal_serial``, so it must carry the
    same ``no_xdist`` marker — under xdist a process-global mutation in
    one worker is either lost or leaks into sibling tests.
    """
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


# ── AM-12 / A6 single-branch knob pin (AST, not prose) ─────────────────

_KNOB_NAME = "_ALLOW_ANCHORLESS_GLOBAL_LIST"


def _knob_refs(node: ast.AST) -> bool:
    """True iff ``node``'s subtree mentions the knob NAME."""
    return any(
        isinstance(sub, ast.Name) and sub.id == _KNOB_NAME
        for sub in ast.walk(node)
    )


def _knob_gate_violations(src: str) -> list[str]:
    """Structural check: the F-3 knob is consulted in EXACTLY ONE branch.

    Returns human-readable violations (``[]`` ⇒ clean). An AST walk —
    NOT a text grep — so a ternary, a boolean expression, a comparison,
    or a second gate all REDDEN, which a substring count cannot see
    (council A6: the raw-text pin was prose-satisfiable).

    Pins, in order:
    1. exactly ONE store of the knob (its single module-level definition);
    2. exactly ONE load of the knob (the gate's test operand);
    3. exactly ONE ``If`` node whose test references it, shaped
       ``if not <knob>:`` (the house negation — no ``== False`` escape);
    4. NO ternary / boolean-op / comparison expression mentions it
       anywhere (a second decision route, even branchless);
    5. exactly ONE unanchored fetch (``room_service.list_all_rooms``) and
       it sits AFTER the gate's line — the fetch can never run ungated.
    """
    tree = ast.parse(src)
    issues: list[str] = []

    stores = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == _KNOB_NAME
        and isinstance(n.ctx, ast.Store)
    ]
    loads = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == _KNOB_NAME
        and isinstance(n.ctx, ast.Load)
    ]
    if len(stores) != 1:
        issues.append(
            f"knob STORED {len(stores)}x (want exactly 1 module-level definition)"
        )
    else:
        # The single store must be the MODULE-LEVEL definition — a store
        # inside a function would leave the module attribute unset and
        # break the gate at import (the old text pin asserted this too).
        module_level = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == _KNOB_NAME
        ]
        if len(module_level) != 1:
            issues.append(
                "knob is NOT defined once at module level "
                f"({len(module_level)} module-level AnnAssign)"
            )
    if len(loads) != 1:
        issues.append(
            f"knob LOADED {len(loads)}x (want exactly 1 — the single gate test)"
        )

    gates = [
        n for n in ast.walk(tree) if isinstance(n, ast.If) and _knob_refs(n.test)
    ]
    if len(gates) != 1:
        issues.append(
            f"{len(gates)} If-node(s) test the knob (want exactly 1)"
        )
    for gate in gates:
        test = gate.test
        shaped = (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id == _KNOB_NAME
        )
        if not shaped:
            issues.append(
                f"gate test at line {gate.lineno} is not shaped "
                "'if not _ALLOW_ANCHORLESS_GLOBAL_LIST:'"
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.IfExp) and _knob_refs(node):
            issues.append(
                f"TERNARY at line {node.lineno} references the knob — a "
                "branchless second decision route"
            )
        if isinstance(node, ast.BoolOp) and _knob_refs(node):
            issues.append(
                f"BOOL expression at line {node.lineno} references the knob"
            )
        if isinstance(node, ast.Compare) and _knob_refs(node):
            issues.append(
                f"COMPARISON at line {node.lineno} references the knob"
            )

    fetches = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "list_all_rooms"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "room_service"
    ]
    if len(fetches) != 1:
        issues.append(
            f"{len(fetches)} unanchored fetch(es) of room_service.list_all_rooms "
            "(want exactly 1)"
        )
    elif gates:
        gate_line = min(g.lineno for g in gates)
        for fetch in fetches:
            if fetch.lineno <= gate_line:
                issues.append(
                    f"unanchored fetch at line {fetch.lineno} does NOT sit "
                    f"after the gate (line {gate_line}) — it could run ungated"
                )
    return issues


def test_am12_single_branch_knob_grep_pin():
    """The knob is consulted in EXACTLY ONE branch (criterion 6).

    A6: replaced the raw-text pin with the AST check above — the text
    form accepted a ternary (``x = a if _ALLOW_ANCHORLESS_GLOBAL_LIST
    else b``) and any prose mention of the gate line.
    """
    src = ROOMS_ROUTER_SRC.read_text(encoding="utf-8")
    assert _knob_gate_violations(src) == []


def test_a6_knob_gate_ast_counter_mutation():
    """A6 counter-mutation: the AST check REDDENS on mutated sources.

    Operates on IN-MEMORY strings — the real router is never mutated.
    Four shapes: (i) a ternary second route (the exact shape the old
    text pin accepted), (ii) a second gate, (iii) the gate neutralised
    so the unanchored fetch runs ungated, (iv) a ``== False`` comparison
    gate.
    """
    src = ROOMS_ROUTER_SRC.read_text(encoding="utf-8")

    gate = "        if not _ALLOW_ANCHORLESS_GLOBAL_LIST:\n"

    # (i) a ternary routes the decision a second time (old text pin: GREEN).
    ternary = src.replace(
        gate,
        gate
        + "            _knob_mode = 1 if _ALLOW_ANCHORLESS_GLOBAL_LIST else 0\n",
        1,
    )
    assert ternary != src, "counter-mutation fixture did not apply"
    ternary_issues = _knob_gate_violations(ternary)
    assert any("TERNARY" in i or "LOADED" in i for i in ternary_issues), (
        ternary_issues
    )

    # (ii) a SECOND gate — two branches consult the knob (appended helper
    #      keeps the mutated source syntactically valid).
    two_gates = src + (
        "\n\ndef _w2_second_gate() -> bool:\n"
        "    if not _ALLOW_ANCHORLESS_GLOBAL_LIST:\n"
        "        return False\n"
        "    return True\n"
    )
    two_gate_issues = _knob_gate_violations(two_gates)
    assert any("If-node" in i for i in two_gate_issues), two_gate_issues

    # (iii) the gate neutralised (``if False:``) — the unanchored fetch is
    #       no longer knob-gated at all. Still parseable, so the check has
    #       real source to inspect.
    no_gate = src.replace(gate, "        if False:\n", 1)
    assert no_gate != src
    no_gate_issues = _knob_gate_violations(no_gate)
    assert any("If-node" in i or "LOADED" in i for i in no_gate_issues), (
        no_gate_issues
    )

    # (iv) a COMPARISON gate (``if _ALLOW... == False:``) — same branch
    #      count but a non-house negation shape must redden too.
    cmp_gate = src.replace(
        gate, "        if _ALLOW_ANCHORLESS_GLOBAL_LIST == False:\n", 1
    )
    assert cmp_gate != src
    cmp_issues = _knob_gate_violations(cmp_gate)
    assert any("COMPARISON" in i or "not shaped" in i for i in cmp_issues), (
        cmp_issues
    )

    # Sanity: the real source is clean under the same checker (so the
    # counter-mutations above are the only difference).
    assert _knob_gate_violations(src) == []


# ── A3 — env-var knob default (no release needed to tighten) ───────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("  yes  ", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("False", False),
        ("no", False),
        ("off", False),
    ],
)
def test_a3_env_knob_parses_strict_booleans(monkeypatch, raw, expected):
    """A3 — the env var re-defaults the knob without a code release."""
    monkeypatch.setenv(
        rooms_module._ANCHORLESS_GLOBAL_LIST_ENV, raw
    )
    assert rooms_module._resolve_allow_anchorless_global_list() is expected


@pytest.mark.parametrize("raw", [None, "", "   ", "maybe", "2", "tru"])
def test_a3_env_knob_falls_back_to_wide_default(monkeypatch, raw):
    """A3 — unset / blank / unparseable ⇒ the historical wide default.

    A typo must never tighten the lane (and must never crash the
    import): the fallback is the existing constant's value.
    """
    if raw is None:
        monkeypatch.delenv(
            rooms_module._ANCHORLESS_GLOBAL_LIST_ENV, raising=False
        )
    else:
        monkeypatch.setenv(rooms_module._ANCHORLESS_GLOBAL_LIST_ENV, raw)
    assert (
        rooms_module._resolve_allow_anchorless_global_list()
        is rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT
    )
    assert rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT is True


def test_a3_env_knob_default_is_wide_and_single_sourced():
    """A3 — the shipped DEFAULT stays True; the env read is ONE function.

    The knob stays a module attribute (so ``monkeypatch.setattr`` flips
    still work — see the two knob legs above) and the env read happens
    in exactly one place, at import, never inside the handler.
    """
    assert rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT is True
    assert rooms_module._ANCHORLESS_GLOBAL_LIST_ENV == (
        "ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST"
    )
    src = ROOMS_ROUTER_SRC.read_text(encoding="utf-8")
    # The env var is read in exactly ONE function — the resolver.
    assert src.count("os.environ.get(_ANCHORLESS_GLOBAL_LIST_ENV)") == 1
    # ...and the handler body never reads the environment.
    handler = src[
        src.index("async def list_rooms(") : src.index("def _shape_rows(")
    ]
    assert "os.environ" not in handler
    assert "_ANCHORLESS_GLOBAL_LIST_ENV" not in handler


def test_a3_env_knob_redefaults_on_restart(monkeypatch):
    """A3 — a RESTART (not a release) re-defaults the knob.

    ``importlib.reload`` re-executes the module body — exactly what a
    daemon restart does, because the env var is read ONCE, at import.
    The reload happens INSIDE the test (the real router FILE is never
    touched) and the module is reloaded back to the ambient state in a
    ``finally`` — the suite's knob default is restored either way.

    This is the leg that answers the operator's question: tighten the
    knob ⇒ set the env var ⇒ restart the daemon ⇒ the anchor-less lane
    is refused. No code release, no image rebuild.
    """
    import importlib

    env_name = rooms_module._ANCHORLESS_GLOBAL_LIST_ENV
    monkeypatch.setenv(env_name, "false")
    try:
        importlib.reload(rooms_module)
        assert rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST is False
    finally:
        monkeypatch.delenv(env_name, raising=False)
        importlib.reload(rooms_module)
    assert rooms_module._ALLOW_ANCHORLESS_GLOBAL_LIST is True


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


# ── W2(b) — the ancestor-of-owner branch, made OBSERVABLE ──────────────

def test_w2_grandparent_anchor_sees_grandchilds_room(client, engine, svc):
    """W2(b) — a STRICT ancestor of a room's owner reads that room.

    Three-level chain ``grandparent-1 → parent-1 → child-1``; the room
    is owned by ``child-1``; the anchor is ``grandparent-1``, which is
    NOT a member. The identity disjunct cannot cover this (the owner is
    not the caller) and membership cannot either (the anchor holds no
    member row) — ``owner in descendant_set`` is the ONLY route.

    Council W2's counter-mutation (deleting that disjunct) makes THIS
    leg fail; every leg that predates it stayed green because the local
    manager double returned caller + DIRECT children only and no case
    put the anchor strictly above the owner.
    """
    manager = client.manager
    seed_instance(engine, "grandparent-1", None, "idle")
    seed_instance(engine, "parent-1", "grandparent-1", "idle")
    seed_instance(engine, "child-1", "parent-1", "idle")
    seed_instance(engine, "stranger-1", None, "idle")
    manager._known.update(
        {"grandparent-1", "parent-1", "child-1", "stranger-1"}
    )

    rid = _make_room(
        engine, svc, "grandparent-1", "child-1", name="w2-grandchild-room"
    )
    _stamp_created_at(engine, rid, 1)
    outside = _make_room(
        engine, svc, "stranger-1", "stranger-1", name="w2-outside-room"
    )
    _stamp_created_at(engine, outside, 2)

    # Preconditions that make the branch observable (asserted, not assumed):
    # (1) the owner is a GRANDCHILD of the anchor — depth 2, so the anchor
    #     is not the owner and not a direct parent;
    with Session(engine) as s:
        child = s.get(Instance, "child-1")
        parent = s.get(Instance, "parent-1")
        assert child is not None and child.parent_id == "parent-1"
        assert parent is not None and parent.parent_id == "grandparent-1"

    resp = client.get("/api/rooms?instance_id=grandparent-1")
    assert resp.status_code == 200
    rooms = resp.json()["rooms"]

    # (2) EXACT SET (W-11 precedent — equality, not a subset check).
    assert {r["room_id"] for r in rooms} == {rid}

    # (3) the anchor holds NO membership row — only the owner does, so the
    #     identity disjunct and the membership disjunct are both provably
    #     out of play and `owner in descendant_set` carries the leg.
    assert rooms[0]["owner_instance_id"] == "child-1"
    assert rooms[0]["member_instance_ids"] == ["child-1"]
    assert "grandparent-1" not in rooms[0]["member_instance_ids"]


# ── W3 — anchored fail-closed leak pins + the S-2 global breadth ───────

def test_w3_anchored_path_leaks_no_unreadable_room(client, engine, svc):
    """W3 — every row on the ANCHORED path is predicate-checked.

    A LEAK assertion, not a set comparison: the unreadable rooms carry
    unforgeable tokens in their title, their ``tree_root_id`` and their
    roster, and the anchored response BODY is searched for each one. A
    row that leaked in any field reddens this leg.
    """
    manager = client.manager
    seed_instance(engine, "anchor-1", None, "idle")
    seed_instance(engine, "member-child-1", "anchor-1", "idle")
    seed_instance(engine, "stranger-a", None, "idle")
    seed_instance(engine, "stranger-b", None, "idle")
    manager._known.update(
        {"anchor-1", "member-child-1", "stranger-a", "stranger-b"}
    )

    readable = _make_room(
        engine, svc, "anchor-1", "anchor-1", name="w3-readable-room"
    )
    join_member(engine, readable, "member-child-1", "worker")
    _stamp_created_at(engine, readable, 0)

    # Two unreadable rooms in TWO different foreign trees, each with a
    # distinctive title / owner / roster token.
    leak_tokens: list[str] = []
    for i, (tree, owner) in enumerate(
        (("stranger-a", "stranger-a"), ("stranger-b", "stranger-b"))
    ):
        title = f"w3-unreadable-title-{i}"
        roster_id = f"w3-unreadable-member-{i}"
        room_id = _make_room(engine, svc, tree, owner, name=title)
        join_member(engine, room_id, roster_id, f"w3member{i}")
        _stamp_created_at(engine, room_id, i + 1)
        leak_tokens.extend([title, roster_id, room_id])

    resp = client.get("/api/rooms?instance_id=member-child-1")
    assert resp.status_code == 200
    body = resp.json()
    text = resp.text

    # 1) exact set — only the readable room
    assert {r["room_id"] for r in body["rooms"]} == {readable}
    # 2) no unreadable room's TITLE appears anywhere in the body
    # 3) no unreadable room's ROSTER id appears anywhere in the body
    # 4) no unreadable room's ROW id appears anywhere in the body
    for token in leak_tokens:
        assert token not in text, f"anchored response leaked {token!r}"
    # 5) no unreadable room's TREE / OWNER anchor appears either
    for token in ("stranger-a", "stranger-b"):
        assert token not in text, f"anchored response leaked anchor {token!r}"

    # The readable row is genuinely present (the leg is not vacuous).
    assert readable in text
    assert "w3-readable-room" in text


def test_w3_anchorless_exposure_is_exactly_nest_wide(client, engine, svc):
    """W3 — the anchor-LESS path is the nest-wide set, UNFILTERED (S-2).

    Pins the architect's S-2 adjudication instead of assuming it: the
    anchor-less response carries EVERY non-purged room — ACTIVE and
    CLOSED, across every tree, member or not — while the SAME caller
    ANCHORED sees only its readable subset. The two postures are
    therefore provably different, and the difference is exactly the
    fail-closed predicate.
    """
    manager = client.manager
    seed_instance(engine, "plain-1", None, "idle")
    seed_instance(engine, "tree-a-root", None, "idle")
    seed_instance(engine, "tree-b-root", None, "idle")
    manager._known.update({"plain-1", "tree-a-root", "tree-b-root"})

    # The caller's OWN room — the only row the anchored read may return.
    own = _make_room(engine, svc, "plain-1", "plain-1", name="w3-own")
    # A foreign-tree ACTIVE room — unreadable for plain-1, nest-wide visible.
    foreign_active = _make_room(
        engine, svc, "tree-a-root", "tree-a-root", name="w3-a1"
    )
    # A foreign-tree CLOSED room — still listed (no state filter at all).
    foreign_closed = _make_room(
        engine, svc, "tree-b-root", "tree-b-root", name="w3-b1", state="closed"
    )
    # A PURGED room — nonexistent, must never appear.
    purged = _make_room(
        engine, svc, "tree-a-root", "tree-a-root", name="w3-purged"
    )
    svc._repo.purge_room(purged, reason="w3")

    expected = {own, foreign_active, foreign_closed}

    resp = client.get("/api/rooms?limit=500")
    assert resp.status_code == 200
    body = resp.json()
    anchorless_ids = {r["room_id"] for r in body["rooms"]}
    # EXACT nest-wide set: every non-purged row, no filtering, no extra.
    assert anchorless_ids == expected, anchorless_ids ^ expected
    assert body["truncated"] is False
    assert purged not in anchorless_ids
    assert foreign_closed in anchorless_ids
    closed_row = [r for r in body["rooms"] if r["room_id"] == foreign_closed][0]
    assert closed_row["state"] == "closed"

    # The SAME caller ANCHORED: only the room it owns ⇒ the predicate bites.
    anchored = client.get("/api/rooms?instance_id=plain-1")
    assert anchored.status_code == 200
    anchored_ids = {r["room_id"] for r in anchored.json()["rooms"]}
    assert anchored_ids == {own}
    assert foreign_active not in anchored_ids
    assert foreign_closed not in anchored_ids
    # The S-2 pin in one assertion: the anchor-less breadth is STRICTLY
    # wider than the anchored read — unfiltered vs predicate-checked.
    assert len(anchorless_ids) > len(anchored_ids)

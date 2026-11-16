"""The ``room`` tool verb matrix — happy paths + typed refusals.

Agent group chat Phase 2 / P5 (2026-11-16).

Real file-backed SQLite (the F9-parity room harness), a REAL
``RoomChatService`` and a REAL ``RoomDeliveryService`` over a
MagicMock manager whose lifecycle seams (set_injection /
enqueue_message / get_queue_stats / has_live_graph_task) are
controlled per test.

Coverage map:

* ``room_create`` — happy path (room + OWNER membership row) + the
  service-absent refusal.
* ``room_post`` — happy path (the append carries the RESOLVED names +
  the durable resolution warnings), the membership gate (non-member /
  LEFT), the closed-room refusal, the purged-room not-found, the
  body-cap refusal.
* ``room_read`` — happy path (the messages + the roster + the read
  cursor advance), the non-member not-found, the purged-room
  not-found.
* ``room_task_decision`` — happy path (the decision row lands on the
  room), the non-owner refusal (tasking is parent-decided), the
  unknown-proposal refusal.
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
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.repositories.room.models import ChatRoom, RoomMessage
from daemon.services.room_delivery import RoomDeliveryService
from daemon.tools.room import create_room_tools
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
)


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
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_tools.db")
    yield eng
    eng.dispose()


@pytest.fixture
def stack(engine):
    """A room with an owner ("lead") and a child member ("worker")."""
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)
    owner = "owner-1"
    seed_instance(engine, owner, None, "idle")
    room_id = make_room(repo, tree_root=owner, owner=owner, owner_member_name="lead")
    seed_instance(engine, "child-1", owner, "idle")
    join_member(engine, room_id, "child-1", "worker")
    return engine, repo, svc, room_id, owner


def _tools(manager, instance_id: str, agent_id: str = "ari"):
    return {
        t.name: t
        for t in create_room_tools(
            manager, current_instance_id=instance_id, agent_id=agent_id
        )
    }


def _call(coro):
    return asyncio.run(coro)


# ── room_create ────────────────────────────────────────────────────────


def test_room_create_opens_a_room_and_seeds_the_owner_row(stack):
    engine, repo, svc, _room_id, _owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, "owner-1")
    raw = _call(tools["room_create"].ainvoke({"name": "scene-3", "member_name": "producer"}))
    assert raw.startswith("{"), raw
    payload = json.loads(raw)
    assert payload["room_id"]
    assert payload["your_member_name"] == "producer", payload
    # The OWNER membership row exists (D2) — one transaction.
    created = repo.get_room(payload["room_id"])
    assert created is not None
    owner_row = repo.get_member(payload["room_id"], "owner-1")
    assert owner_row is not None
    assert owner_row["role"] == "owner"
    assert owner_row["member_name"] == "producer"
    assert owner_row["last_read_seq"] == 0
    assert owner_row["last_delivered_seq"] == 0


def test_room_create_without_a_room_service_is_typed(stack):
    engine, _repo, svc, _room_id, _owner = stack
    manager = _manager(engine, svc)
    manager._room_chat_service = None
    tools = _tools(manager, "owner-1")
    raw = _call(tools["room_create"].ainvoke({"name": "x"}))
    assert raw.startswith("Error:"), raw
    assert "room-service-unavailable" in raw, raw


# ── room_post ──────────────────────────────────────────────────────────


def test_room_post_happy_path_persists_resolved_names_and_warnings(stack):
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_post"].ainvoke(
            {
                "room_id": room_id,
                "text": "scene 3 build done",
                "tagged": ["worker", "not-a-member"],
                "addressed_to": ["worker"],
            }
        )
    )
    payload = json.loads(raw)
    assert payload["seq"] == 1, payload
    assert payload["author"] == "lead"
    # The RESOLVED names persist (the machine-shaped routing columns).
    assert payload["tagged"] == ["worker"], payload
    assert payload["addressed_to"] == ["worker"], payload
    # The UNRESOLVED name persists as the durable warning set (A-P3-16).
    assert payload["warnings"] == [
        {"kind": "tag", "name": "not-a-member", "reason": "unresolvable"}
    ], payload
    with Session(engine) as s:
        rows = list(s.exec(select(RoomMessage)).all())
        assert len(rows) == 1
        row = rows[0]
        assert list(row.tagged_members or []) == ["worker"]
        assert list(row.addressed_members or []) == ["worker"]
        assert list(row.resolution_warnings or []) == [
            {"kind": "tag", "name": "not-a-member", "reason": "unresolvable"}
        ]


def test_room_post_refuses_a_non_member(stack):
    engine, repo, svc, room_id, _owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, "outsider-1")
    raw = _call(tools["room_post"].ainvoke({"room_id": room_id, "text": "hi"}))
    assert raw.startswith("Error:"), raw
    assert "not-a-member" in raw, raw
    with Session(engine) as s:
        assert len(list(s.exec(select(RoomMessage)).all())) == 0, (
            "no row may be written"
        )


def test_room_post_refuses_a_closed_room(stack):
    engine, repo, svc, room_id, owner = stack
    # Close the room through the room row's own state column.
    from daemon.repositories.room.models import ChatRoom

    with Session(engine) as s:
        row = s.get(ChatRoom, room_id)
        row.state = "closed"
        s.commit()
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(tools["room_post"].ainvoke({"room_id": room_id, "text": "hi"}))
    assert raw.startswith("Error:"), raw
    assert "room-closed" in raw, raw


def test_room_post_refuses_a_purged_room_as_not_found(stack):
    engine, repo, svc, room_id, owner = stack
    repo.purge_room(room_id, reason="test")
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(tools["room_post"].ainvoke({"room_id": room_id, "text": "hi"}))
    assert raw.startswith("Error:"), raw
    assert "room-not-found" in raw, raw


def test_room_post_refuses_an_oversized_body(stack):
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_post"].ainvoke({"room_id": room_id, "text": "x" * 4001})
    )
    assert raw.startswith("Error:"), raw
    assert "invalid-input" in raw, raw


def test_room_post_routes_through_the_delivery_service(stack):
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_post"].ainvoke(
            {"room_id": room_id, "text": "ping", "tagged": ["worker"]}
        )
    )
    payload = json.loads(raw)
    assert payload["tag_outcomes"], payload
    assert payload["tag_outcomes"][0]["member_name"] == "worker"


def test_room_post_without_a_delivery_service_persists_and_flags_the_gap(stack):
    """M2: an ABSENT delivery router is a TRUTHFUL signal.

    The append is the durable truth — the row is committed BEFORE the
    routing call, so the tool can never refuse the post here (that
    would negate an already-persisted row). The honest signal is
    ``routing_error: true`` PLUS the reason token naming the absent
    router, so the agent can tell "the routing never ran" from
    "there was nobody to notify" (the old shape reported
    ``routing_error: false`` for both).
    """
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    manager._room_delivery_service = None
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_post"].ainvoke(
            {"room_id": room_id, "text": "ping", "tagged": ["worker"]}
        )
    )
    payload = json.loads(raw)
    assert payload["routing_error"] is True, payload
    assert payload["routing_reason"] == "delivery-service-unavailable", payload
    # The post LANDED — the append is the durable truth (the boot
    # re-derivation recovers the undelivered tagged rows).
    with Session(engine) as s:
        rows = list(s.exec(select(RoomMessage)).all())
        assert len(rows) == 1, rows
        assert rows[0].text == "ping"


def test_room_post_with_a_delivery_service_reports_no_routing_error(stack):
    """The router-present path is UNCHANGED: no error, no reason."""
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_post"].ainvoke(
            {"room_id": room_id, "text": "ping", "tagged": ["worker"]}
        )
    )
    payload = json.loads(raw)
    assert payload["routing_error"] is False, payload
    assert payload["routing_reason"] == "", payload
    assert payload["tag_outcomes"], payload


# ── room_read ──────────────────────────────────────────────────────────


def test_room_read_returns_the_log_and_advances_the_read_cursor(stack):
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    _call(tools["room_post"].ainvoke({"room_id": room_id, "text": "one"}))
    _call(tools["room_post"].ainvoke({"room_id": room_id, "text": "two"}))
    raw = _call(tools["room_read"].ainvoke({"room_id": room_id}))
    payload = json.loads(raw)
    assert [m["text"] for m in payload["messages"]] == ["one", "two"]
    assert payload["next_after_seq"] == 2
    names = {m["member_name"] for m in payload["roster"]}
    assert names == {"lead", "worker"}, names
    # The read ACK — the read cursor advanced to the last seq returned.
    member = repo.get_member(room_id, owner)
    assert member["last_read_seq"] == 2, member


def test_room_read_refuses_a_non_member(stack):
    engine, repo, svc, room_id, _owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, "outsider-1")
    raw = _call(tools["room_read"].ainvoke({"room_id": room_id}))
    assert raw.startswith("Error:"), raw
    assert "room-not-found" in raw, raw


def test_room_read_refuses_a_purged_room(stack):
    engine, repo, svc, room_id, owner = stack
    repo.purge_room(room_id, reason="test")
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(tools["room_read"].ainvoke({"room_id": room_id}))
    assert raw.startswith("Error:"), raw
    assert "room-not-found" in raw, raw


def test_room_read_honours_the_after_seq_cursor(stack):
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    _call(tools["room_post"].ainvoke({"room_id": room_id, "text": "one"}))
    _call(tools["room_post"].ainvoke({"room_id": room_id, "text": "two"}))
    raw = _call(tools["room_read"].ainvoke({"room_id": room_id, "after_seq": 1}))
    payload = json.loads(raw)
    assert [m["text"] for m in payload["messages"]] == ["two"], payload


# ── room_task_decision ─────────────────────────────────────────────────


def test_room_task_decision_records_the_decision_on_the_room(stack):
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    # A proposal row: the parent posts a message addressed to a terminal
    # member — the delivery router mints the durable proposal.
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()
    _call(
        tools["room_post"].ainvoke(
            {"room_id": room_id, "text": "who can finish scene 4?", "tagged": ["worker"]}
        )
    )
    proposals = [
        m for m in repo.list_messages(
            room_id, caller_instance_id=owner, after_seq=0, limit=50
        )
        if m["kind"] == "task_proposal"
    ]
    assert len(proposals) == 1, proposals
    proposal = proposals[0]

    raw = _call(
        tools["room_task_decision"].ainvoke(
            {
                "room_id": room_id,
                "proposal_seq": proposal["seq"],
                "decision": "revive",
                "note": "worker knows the scene",
            }
        )
    )
    payload = json.loads(raw)
    assert payload["recorded"] is True, payload
    assert payload["decision"] == "revive"
    assert payload["member_name"] == "worker", payload

    # The decision is ON THE ROOM (the Ruling-A loop-back).
    kinds = [
        m["kind"]
        for m in repo.list_messages(
            room_id, caller_instance_id=owner, after_seq=0, limit=50
        )
    ]
    assert "task_decision" in kinds, kinds
    decisions = [
        m
        for m in repo.list_messages(
            room_id, caller_instance_id=owner, after_seq=0, limit=50
        )
        if m["kind"] == "task_decision"
    ]
    assert decisions[0]["member"] == "worker"
    assert "revive" in decisions[0]["text"]


def test_room_task_decision_refuses_a_non_owner(stack):
    engine, repo, svc, room_id, _owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, "child-1")
    raw = _call(
        tools["room_task_decision"].ainvoke(
            {"room_id": room_id, "proposal_seq": 1, "decision": "revive"}
        )
    )
    assert raw.startswith("Error:"), raw
    assert "not-the-owner" in raw, raw


def test_room_task_decision_refuses_an_unknown_proposal(stack):
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_task_decision"].ainvoke(
            {"room_id": room_id, "proposal_seq": 99, "decision": "revive"}
        )
    )
    assert raw.startswith("Error:"), raw
    assert "no-such-proposal" in raw, raw


def test_room_task_decision_without_the_room_service_names_the_room_service(stack):
    """M5: each missing service names ITSELF.

    The two services are independent (a daemon can carry the room
    service and NOT the delivery/routing service), so the refusal
    token must match the component that is actually missing — and
    must NEVER name the other one.
    """
    engine, _repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    manager._room_chat_service = None
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_task_decision"].ainvoke(
            {"room_id": room_id, "proposal_seq": 1, "decision": "revive"}
        )
    )
    assert raw.startswith("Error:"), raw
    assert "room-service-unavailable" in raw, raw
    assert "delivery-service-unavailable" not in raw, raw


def test_room_task_decision_without_the_delivery_service_names_the_delivery_service(
    stack,
):
    """M5: the delivery/routing service has its OWN token + message.

    The pre-fix shape refused with the ROOM service's token and text
    even when only the delivery service was missing — the agent was
    told the wrong component was gone.
    """
    engine, _repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    manager._room_delivery_service = None
    tools = _tools(manager, owner)
    raw = _call(
        tools["room_task_decision"].ainvoke(
            {"room_id": room_id, "proposal_seq": 1, "decision": "revive"}
        )
    )
    assert raw.startswith("Error:"), raw
    assert "delivery-service-unavailable" in raw, raw
    assert "room-service-unavailable" not in raw, raw


# ── P5 review re-gate pins (2026-11-16) ───────────────────────────────


def test_room_task_decision_duplicate_is_typed_and_never_leaks(stack):
    """A second decision for the SAME proposal rides the dedup-UNIQUE
    backstop: the repository raises RoomError with the IntegrityError
    EMBEDDED, and the tool must answer with a static typed envelope —
    the constraint name / DETAIL / driver text never reach the payload
    (the PB-F1 leak class, room.py's own house rule)."""
    engine, repo, svc, room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    # Mint the proposal (a tagged post to a terminal child).
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()
    _call(
        tools["room_post"].ainvoke(
            {"room_id": room_id, "text": "who can finish scene 4?", "tagged": ["worker"]}
        )
    )
    proposals = [
        m for m in repo.list_messages(
            room_id, caller_instance_id=owner, after_seq=0, limit=50
        )
        if m["kind"] == "task_proposal"
    ]
    proposal = proposals[0]
    first = _call(
        tools["room_task_decision"].ainvoke(
            {
                "room_id": room_id,
                "proposal_seq": proposal["seq"],
                "decision": "revive",
            }
        )
    )
    assert first.startswith("{"), first
    # The SECOND decision for the same proposal — the dedup refusal.
    raw = _call(
        tools["room_task_decision"].ainvoke(
            {
                "room_id": room_id,
                "proposal_seq": proposal["seq"],
                "decision": "revive",
            }
        )
    )
    assert raw.startswith("Error:"), raw
    assert "duplicate-decision" in raw, raw
    assert "already recorded" in raw, raw
    # THE NO-LEAK ASSERTION: neither the constraint identity, nor the
    # driver text, nor the repository wrapper text may appear.
    for fragment in (
        "UNIQUE constraint failed",   # SQLite driver message
        "duplicate key",              # PG driver message
        "DETAIL:",                    # PG detail line
        "source_message_id",          # the constraint's columns
        "uq_room_messages",           # the constraint name
        "IntegrityError",             # the wrapped driver exception
        "append integrity failure",   # the repository wrapper text
    ):
        assert fragment not in raw, (fragment, raw)
    # The refusal persisted NOTHING — exactly one decision on the log.
    decisions = [
        m for m in repo.list_messages(
            room_id, caller_instance_id=owner, after_seq=0, limit=50
        )
        if m["kind"] == "task_decision"
    ]
    assert len(decisions) == 1, decisions


def test_room_create_refuses_when_the_tree_room_cap_is_reached(stack):
    """The 9th ACTIVE room in one tree is refused — typed at the tool
    (``room-cap``), repository-enforced (A-P5-14). The tool-created
    rooms all land in the mocked tree ``tree-1``."""
    engine, repo, svc, _room_id, owner = stack
    manager = _manager(engine, svc)
    tools = _tools(manager, owner)
    for i in range(8):
        raw = _call(
            tools["room_create"].ainvoke({"name": f"scene-{i}", "member_name": "producer"})
        )
        assert raw.startswith("{"), (i, raw)
    # The 9th — the cap refusal.
    raw = _call(
        tools["room_create"].ainvoke({"name": "scene-8", "member_name": "producer"})
    )
    assert raw.startswith("Error:"), raw
    assert "room-cap" in raw, raw
    assert "ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8" in raw, raw
    assert "close a room" in raw, raw
    # Nothing was inserted by the refusal — still 8 rooms in the tree.
    with Session(engine) as s:
        held = len(
            s.exec(
                select(ChatRoom).where(ChatRoom.tree_root_id == "tree-1")
            ).all()
        )
    assert held == 8, held

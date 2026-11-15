"""P3 delivery router behavior pins.

Real file-backed SQLite (F9-parity harness), real RoomChatService +
RoomDeliveryService wired over a MagicMock manager whose lifecycle
seams (get_instance_info/get_queue_stats/set_injection/enqueue_message/
has_live_graph_task) are controlled per test.
"""

from __future__ import annotations

import asyncio
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.repositories.room.models import RoomMessage
from daemon.services.room_delivery import RoomDeliveryService, LANE_B_CAS_ALLOWED
from tests.helpers.room_harness import make_room, make_room_engine


def _seed_instance(eng: Engine, iid: str, parent: str | None = None, status: str = "idle") -> None:
    with Session(eng) as s:
        s.add(Instance(instance_id=iid, agent_id="ari", agent_dir="/agents/ari",
                       parent_id=parent, status=status))
        s.commit()


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_delivery.db")
    yield eng
    eng.dispose()


@pytest.fixture
def stack(engine):
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)
    caller = "owner-1"
    _seed_instance(engine, caller, None, "idle")
    room_id = make_room(repo, tree_root=caller, owner=caller, owner_member_name="lead")
    # add a child member
    _seed_instance(engine, "child-1", caller, "idle")
    with Session(engine) as s:
        from daemon.repositories.room.models import ChatRoomMember
        s.add(ChatRoomMember(room_id=room_id, instance_id="child-1", member_name="worker",
                             agent_id="ari", role="child",
                             joined_at="2026-11-15T08:00:00+00:00",
                             last_read_seq=0, last_delivered_seq=0, state="active"))
        s.commit()
    return engine, repo, svc, room_id, caller


def _make_manager(engine, svc, *, statuses=None, queue_stats=None,
                  live_graph=True, inject_ok=True, enqueue_ok=True):
    statuses = statuses or {}
    queue_stats = queue_stats or {}
    manager = MagicMock()
    manager._engine = engine
    manager._room_chat_service = svc

    def get_status(iid):
        return statuses.get(iid, "idle")
    manager.get_instance_info = MagicMock(side_effect=lambda iid: {"status": get_status(iid)})
    # room delivery uses room service, not manager, for status reads; but
    # manager.has_live_graph_task is used.
    manager.has_live_graph_task = MagicMock(return_value=live_graph)

    async def fake_enqueue(iid, msg, **kw):
        if not enqueue_ok:
            raise RuntimeError("enqueue boom")
        return MagicMock(queued=True, message_id="m-1")
    manager.enqueue_message = AsyncMock(side_effect=fake_enqueue)
    manager.set_injection = MagicMock()
    if not inject_ok:
        manager.set_injection.side_effect = RuntimeError("inject boom")
    manager.get_queue_stats = AsyncMock(
        side_effect=lambda iid: queue_stats.get(iid, {"pending_count": 0, "processing_count": 0})
    )
    return manager


def test_lane_map_totality_pin():
    # The router uses the shared sets; re-assert the partition here.
    from daemon.repositories.room.models import (
        TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
        NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    )
    from daemon.repositories.instance.models import InstanceStatus
    enum_values = {s.value for s in InstanceStatus}
    assert TERMINAL_INSTANCE_STATUSES_FOR_ROOMS | NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS == enum_values
    assert TERMINAL_INSTANCE_STATUSES_FOR_ROOMS.isdisjoint(NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS)


def test_addressed_to_terminal_does_not_revive_member(stack):
    engine, repo, svc, room_id, caller = stack
    # child terminal
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()

    manager = _make_manager(engine, svc, statuses={"child-1": "completed", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    manager._room_delivery_service = delivery

    # post a tagged message from owner to worker
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hello", tagged_members=["worker"], addressed_members=[],
        )
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )
    res = asyncio.run(run())
    # member must NOT be revived
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        assert inst.status == "completed", "member must never be auto-revived"
    # a proposal outcome should be present
    outcomes = [o["outcome"] for o in res["tag_outcomes"]]
    assert "proposal" in outcomes


def test_addressed_to_running_no_injection(stack):
    engine, repo, svc, room_id, caller = stack
    manager = _make_manager(engine, svc, statuses={"child-1": "running", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hi", tagged_members=[], addressed_members=["worker"],
        )
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )
    res = asyncio.run(run())
    # addressed to running → digest only, NO set_injection call for the member
    member_calls = [c for c in manager.set_injection.call_args_list
                    if c.args and c.args[0] == "child-1"]
    assert member_calls == [], "addressing alone must never inject"
    addr_outcomes = [o["outcome"] for o in res["addressed_outcomes"]]
    assert "digest_only" in addr_outcomes


def test_co_presence_tag_dominates(stack):
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()
    manager = _make_manager(engine, svc, statuses={"child-1": "completed", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hi", tagged_members=["worker"], addressed_members=["worker"],
        )
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )
    res = asyncio.run(run())
    addr_outcomes = [o["outcome"] for o in res["addressed_outcomes"]]
    assert "tag_dominates" in addr_outcomes


def test_cross_room_addressed_fails_closed(stack):
    engine, repo, svc, room_id, caller = stack
    # a name not in this room's roster
    manager = _make_manager(engine, svc, statuses={"child-1": "running", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hi", tagged_members=[], addressed_members=["nope-not-a-member"],
        )
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )
    res = asyncio.run(run())
    assert "addressed unresolved: 'nope-not-a-member'" in res["warnings"]
    assert res["addressed_outcomes"] == []


def test_unresolvable_left_post_succeeds(stack):
    engine, repo, svc, room_id, caller = stack
    # leave the child
    async def run():
        await svc.leave_room(room_id=room_id, instance_id="child-1")
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hi", tagged_members=[], addressed_members=["worker"],
        )
        return post
    post = asyncio.run(run())
    # post succeeded; routing should warn (worker now LEFT → not in active roster)
    manager = _make_manager(engine, svc, statuses={"child-1": "completed", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run2():
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )
    res = asyncio.run(run2())
    assert "addressed unresolved: 'worker'" in res["warnings"]


def test_dedup_no_duplicate_proposal(stack):
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()
    manager = _make_manager(engine, svc, statuses={"child-1": "completed", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hi", tagged_members=["worker"], addressed_members=[],
        )
        r1 = await delivery.route_post(room_id=room_id, post=post,
                                       sender_instance_id=caller, sender_member_name="lead")
        # re-route same post → should not mint a second proposal
        r2 = await delivery.route_post(room_id=room_id, post=post,
                                       sender_instance_id=caller, sender_member_name="lead")
        return r1, r2, post
    r1, r2, post = asyncio.run(run())
    # count proposal rows
    with Session(engine) as s:
        proposals = s.exec(__import__("sqlmodel").select(RoomMessage).where(
            RoomMessage.room_id == room_id, RoomMessage.kind == "task_proposal")).all()
    assert len(proposals) == 1, f"expected 1 proposal, got {len(proposals)}"


def test_parent_terminal_one_hop_no_recursion(stack):
    engine, repo, svc, room_id, caller = stack
    # owner (parent) terminal; child terminal
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        owner = s.get(Instance, caller)
        owner.status = "completed"
        s.commit()
    manager = _make_manager(engine, svc, statuses={"child-1": "completed", caller: "completed"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hi", tagged_members=["worker"], addressed_members=[],
        )
        return await delivery.route_post(room_id=room_id, post=post,
                                         sender_instance_id=caller, sender_member_name="lead")
    res = asyncio.run(run())
    # owner must NOT be revived
    with Session(engine) as s:
        owner = s.get(Instance, caller)
        assert owner.status == "completed", "parent must not be auto-revived (one-hop)"
    # no enqueue to parent (would revive) — set_injection not called for parent
    parent_injects = [c for c in manager.set_injection.call_args_list if c.args and c.args[0] == caller]
    # parent injection may happen only if running; here parent terminal → no injection
    assert parent_injects == []


def test_lane_map_branch_for_paused_member(stack):
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "paused"
        s.commit()
    manager = _make_manager(engine, svc, statuses={"child-1": "paused", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="hi", tagged_members=["worker"], addressed_members=[],
        )
        return await delivery.route_post(room_id=room_id, post=post,
                                         sender_instance_id=caller, sender_member_name="lead")
    res = asyncio.run(run())
    outcomes = [o["outcome"] for o in res["tag_outcomes"]]
    assert "parked_paused" in outcomes


def test_addressed_terminal_parent_notice_shape(stack):
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()
    manager = _make_manager(engine, svc, statuses={"child-1": "completed", caller: "running"},
                            live_graph=True)
    delivery = RoomDeliveryService(manager, svc)
    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="please revive", tagged_members=[], addressed_members=["worker"],
        )
        return await delivery.route_post(room_id=room_id, post=post,
                                         sender_instance_id=caller, sender_member_name="lead")
    res = asyncio.run(run())
    addr_outcomes = [o["outcome"] for o in res["addressed_outcomes"]]
    assert "parent_notice" in addr_outcomes
    # a proposal row minted with trigger addressed_terminal
    with Session(engine) as s:
        proposals = s.exec(__import__("sqlmodel").select(RoomMessage).where(
            RoomMessage.room_id == room_id, RoomMessage.kind == "task_proposal")).all()
    assert any(p.trigger == "addressed_terminal" for p in proposals)

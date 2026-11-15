"""P3 delivery router behavior pins.

Real file-backed SQLite (F9-parity harness), real RoomChatService +
RoomDeliveryService wired over a MagicMock manager whose lifecycle
seams (get_queue_stats/set_injection/enqueue_message/
has_live_graph_task) are controlled per test.

Coverage map: tagged lane-map outcomes (injected / enqueued /
parked_paused / proposal / deferred_cursor) × addressing outcomes
(tag_dominates / parent_notice / digest_only / deferred_cursor) × boot
pins (mint-only at boot, counts dict, cursor untouched on mint fail).
"""

from __future__ import annotations

import asyncio
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlmodel
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.repositories.room.models import RoomMessage
from daemon.services.room_delivery import RoomDeliveryService
from tests.helpers.room_harness import join_member, make_room, make_room_engine


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
    join_member(engine, room_id, "child-1", "worker")
    return engine, repo, svc, room_id, caller


def _make_manager(engine, svc, *, queue_stats=None,
                  live_graph=True, inject_ok=True, enqueue_ok=True):
    queue_stats = queue_stats or {}
    manager = MagicMock()
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
    assert TERMINAL_INSTANCE_STATUSES_FOR_ROOMS | NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS == (
        enum_values
    )
    assert TERMINAL_INSTANCE_STATUSES_FOR_ROOMS.isdisjoint(NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS)


def test_addressed_to_terminal_does_not_revive_member(stack):
    engine, repo, svc, room_id, caller = stack
    # child terminal
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()

    manager = _make_manager(engine, svc, live_graph=True)
    delivery = RoomDeliveryService(manager, svc)

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
    manager = _make_manager(engine, svc, live_graph=True)
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
    manager = _make_manager(engine, svc, live_graph=True)
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
    manager = _make_manager(engine, svc, live_graph=True)
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
    manager = _make_manager(engine, svc, live_graph=True)
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
    manager = _make_manager(engine, svc, live_graph=True)
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
    proposals = _proposal_rows(engine, room_id)
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
    manager = _make_manager(engine, svc, live_graph=True)
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
    parent_injects = [
        c for c in manager.set_injection.call_args_list if c.args and c.args[0] == caller
    ]
    # parent injection may happen only if running; here parent terminal → no injection
    assert parent_injects == []


def test_lane_map_branch_for_paused_member(stack):
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "paused"
        s.commit()
    manager = _make_manager(engine, svc, live_graph=True)
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
    manager = _make_manager(engine, svc, live_graph=True)
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
    proposals = _proposal_rows(engine, room_id)
    assert any(p.trigger == "addressed_terminal" for p in proposals)


# ---------------------------------------------------------------------------
# P3 remediation pins — CAS-miss / fresh==running branch (A-P3-17), the
# graphless-running parent downgrade (A-P3-19), boot counts (A-P3-20).
# Mutation-grade: reverting room_delivery.py to the pre-fix 3b092ea85
# shape reddens every test in this section (each asserts behavior the
# pre-fix code did not perform — see the remediation report).
# ---------------------------------------------------------------------------


def _race_flip_to_running(engine, svc):
    """Wrap svc.cas_instance_status_to_running so a concurrent actor's
    running-flip lands between the lane read and the CAS — the exact
    TOCTOU that makes the CAS miss (rowcount 0) with a fresh running
    status. Deterministic race simulation; restores the real method."""
    real_cas = svc.cas_instance_status_to_running

    async def racing_cas(iid, allowed):
        with Session(engine) as s:
            inst = s.get(Instance, iid)
            inst.status = "running"
            s.commit()
        return await real_cas(iid, allowed)

    svc.cas_instance_status_to_running = racing_cas  # type: ignore[method-assign]
    return real_cas


def _member_row(engine, room_id):
    from daemon.repositories.room.models import ChatRoomMember
    with Session(engine) as s:
        return s.get(ChatRoomMember, (room_id, "child-1"))


def test_cas_miss_running_injects_and_advances_cursor(stack):
    """Finding 1 / A-P3-17: a tagged lane-B row whose CAS misses against
    a concurrent running flip must take lane (a) — INJECT (with the
    A-P3-1 composite source stamp) and advance the cursor. Pre-fix shape
    advanced the cursor and returned "enqueued" without delivering."""
    engine, repo, svc, room_id, caller = stack
    real_cas = _race_flip_to_running(engine, svc)
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        try:
            post = await svc.append_message(
                room_id=room_id, author_instance_id=caller, author_name="lead",
                text_body="ping", tagged_members=["worker"], addressed_members=[],
            )
            return post, await delivery.route_post(
                room_id=room_id, post=post,
                sender_instance_id=caller, sender_member_name="lead",
            )
        finally:
            svc.cas_instance_status_to_running = real_cas  # type: ignore[method-assign]

    post, res = asyncio.run(run())
    outcomes = [o["outcome"] for o in res["tag_outcomes"]]
    assert outcomes == ["injected"], f"expected injected, got {outcomes}"
    # the injection carried the A-P3-1 composite source stamp
    inj = [c for c in manager.set_injection.call_args_list
           if c.args and c.args[0] == "child-1"]
    assert len(inj) == 1, "lane (a) injection must fire exactly once"
    expected_source = f"room:{room_id}:lead:{post['seq']}:author={caller}"
    assert inj[0].kwargs.get("source") == expected_source
    # cursor advanced on successful delivery
    assert _member_row(engine, room_id).last_delivered_seq == post["seq"]


def test_cas_miss_running_inject_failure_leaves_cursor_untouched(stack):
    """Finding 1 / A-P3-17: when the post-CAS-miss injection cannot be
    completed, the outcome is deferred_cursor and the delivered cursor
    is NOT advanced — boot re-derivation must still see (and retry) the
    row. Pre-fix shape returned "enqueued" with the cursor advanced."""
    engine, repo, svc, room_id, caller = stack
    real_cas = _race_flip_to_running(engine, svc)
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=False)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        try:
            post = await svc.append_message(
                room_id=room_id, author_instance_id=caller, author_name="lead",
                text_body="ping", tagged_members=["worker"], addressed_members=[],
            )
            return post, await delivery.route_post(
                room_id=room_id, post=post,
                sender_instance_id=caller, sender_member_name="lead",
            )
        finally:
            svc.cas_instance_status_to_running = real_cas  # type: ignore[method-assign]

    post, res = asyncio.run(run())
    outcomes = [o["outcome"] for o in res["tag_outcomes"]]
    assert outcomes == ["deferred_cursor"], f"expected deferred_cursor, got {outcomes}"
    assert manager.enqueue_message.await_count == 0, "no enqueue on the inject lane"
    assert _member_row(engine, room_id).last_delivered_seq == 0, (
        "failed delivery must NOT advance the delivered cursor"
    )


def test_cas_miss_running_graphless_downgrades_to_enqueue(stack):
    """Finding 1 / A-P3-17: the DEFECT-A downgrade — fresh running with
    NO live graph task cannot see an injection, so the branch enqueues
    (lane (b) shape) and advances the cursor. Pre-fix shape neither
    injected nor enqueued (silent drop)."""
    engine, repo, svc, room_id, caller = stack
    real_cas = _race_flip_to_running(engine, svc)
    manager = _make_manager(engine, svc, live_graph=False, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        try:
            post = await svc.append_message(
                room_id=room_id, author_instance_id=caller, author_name="lead",
                text_body="ping", tagged_members=["worker"], addressed_members=[],
            )
            return post, await delivery.route_post(
                room_id=room_id, post=post,
                sender_instance_id=caller, sender_member_name="lead",
            )
        finally:
            svc.cas_instance_status_to_running = real_cas  # type: ignore[method-assign]

    post, res = asyncio.run(run())
    outcomes = [o["outcome"] for o in res["tag_outcomes"]]
    assert outcomes == ["enqueued"], f"expected enqueued (downgrade), got {outcomes}"
    assert manager.set_injection.call_count == 0, "graphless running cannot inject"
    assert manager.enqueue_message.await_count == 1
    enqueue_args = manager.enqueue_message.await_args
    assert enqueue_args.args[0] == "child-1"
    assert enqueue_args.kwargs.get("metadata") == {
        "room_id": room_id, "seq": post["seq"],
    }
    assert _member_row(engine, room_id).last_delivered_seq == post["seq"]


def test_tagged_terminal_graphless_running_parent_gets_enqueue(stack):
    """Finding 4 / A-P3-19: a graphless-running PARENT (running, no live
    graph task) receives the terminal-tag proposal notice via the
    DEFECT-A downgrade to enqueue instead of silently durable-pending."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        owner = s.get(Instance, caller)
        owner.status = "running"  # graphless-running parent, not idle
        s.commit()
    manager = _make_manager(engine, svc, live_graph=False, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="status?", tagged_members=["worker"], addressed_members=[],
        )
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )

    res = asyncio.run(run())
    outcomes = [o["outcome"] for o in res["tag_outcomes"]]
    assert "proposal" in outcomes
    # parent is running ∧ graphless → the proposal notice is ENQUEUED
    parent_enqueues = [c for c in manager.enqueue_message.await_args_list
                       if c.args and c.args[0] == caller]
    assert len(parent_enqueues) == 1, (
        "graphless-running parent must get the lane-(b) enqueue downgrade"
    )
    assert parent_enqueues[0].kwargs.get("metadata", {}).get("kind") == "task_proposal"
    assert manager.set_injection.call_count == 0


def test_addressed_terminal_graphless_running_parent_gets_enqueue(stack):
    """Finding 4 / A-P3-19: same downgrade for the ADDRESSED lane's
    parent-target matrix — graphless-running parent enqueued, not
    silently durable-pending."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        owner = s.get(Instance, caller)
        owner.status = "running"  # graphless-running parent, not idle
        s.commit()
    manager = _make_manager(engine, svc, live_graph=False, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="please revive", tagged_members=[], addressed_members=["worker"],
        )
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )

    res = asyncio.run(run())
    addr_outcomes = [o["outcome"] for o in res["addressed_outcomes"]]
    assert "parent_notice" in addr_outcomes
    parent_enqueues = [c for c in manager.enqueue_message.await_args_list
                       if c.args and c.args[0] == caller]
    assert len(parent_enqueues) == 1, (
        "graphless-running parent must get the lane-(b) enqueue downgrade"
    )
    assert parent_enqueues[0].kwargs.get("metadata", {}).get("kind") == "task_proposal"


def test_boot_deliver_counts_reflect_outcomes(stack):
    """Finding 5 / A-P3-20: boot_deliver counts outcomes, not attempts —
    a parked_paused row counts deferred (cursor untouched), the same row
    delivers on a later boot once the member is routable."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "paused"
        s.commit()
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    # seed the tagged row (unrouted — the boot pass is the router);
    # advance the OWNER's delivered cursor so the pinned pair #1's
    # owner-row over-inclusion doesn't ride along — this pin isolates
    # the parked MEMBER row.
    post = asyncio.run(svc.append_message(
        room_id=room_id, author_instance_id=caller, author_name="lead",
        text_body="boot ping", tagged_members=["worker"], addressed_members=[],
    ))
    asyncio.run(svc.advance_delivered_cursor(room_id, caller, post["seq"]))

    counts1 = asyncio.run(delivery.boot_deliver(engine))
    assert counts1["delivered"] == 0, "parked row is not delivered"
    assert counts1["deferred"] == 1, "parked row counts deferred"

    # member becomes routable → the SAME row delivers on the next boot
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "idle"
        s.commit()
    counts2 = asyncio.run(delivery.boot_deliver(engine))
    assert counts2["delivered"] == 1
    assert counts2["deferred"] == 0


# ---------------------------------------------------------------------------
# P3 remediation iteration-2 pins — F1 mint-admission gating (A-P3-22),
# C1 boot pair-#2 dedicated pin, C2 boot terminal race (A-P3-21), F3
# alive-addressee no-wake. F1 + C2 pins are mutation-grade (evidence in
# the remediation report); C1/F3 pin EXISTING guarantees that lacked a
# dedicated pin (pair #2 and §4.4's promised pin #2 respectively).
# ---------------------------------------------------------------------------


def _raise_append(svc):
    """Swap svc.append_message for a raiser; returns the real method for
    restoration. Used to inject a proposal-mint failure deterministically."""
    real_append = svc.append_message

    async def boom(**kw):
        raise RuntimeError("mint boom")

    svc.append_message = boom  # type: ignore[method-assign]
    return real_append


def _proposal_rows(engine, room_id):
    with Session(engine) as s:
        return s.exec(sqlmodel.select(RoomMessage).where(
            RoomMessage.room_id == room_id, RoomMessage.kind == "task_proposal")).all()


def test_mint_failure_route_time_defers_and_keeps_cursor(stack):
    """F1 / A-P3-22 (lane (c), route-time): when the proposal mint fails,
    the terminal lane must return deferred_cursor, leave the delivered
    cursor UNTOUCHED, and NOT parent-route — the advanced cursor would
    permanently hide the undelivered notification. Mutation-grade:
    reddens against the pre-fix 939cf171a shape (swallowed mint +
    unconditional advance → 'proposal')."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        owner = s.get(Instance, caller)
        owner.status = "running"  # routing (if it ran) would inject — detectable
        s.commit()
    post = asyncio.run(svc.append_message(
        room_id=room_id, author_instance_id=caller, author_name="lead",
        text_body="hello", tagged_members=["worker"], addressed_members=[],
    ))
    real_append = _raise_append(svc)
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        try:
            return await delivery.route_post(
                room_id=room_id, post=post,
                sender_instance_id=caller, sender_member_name="lead",
            )
        finally:
            svc.append_message = real_append  # type: ignore[method-assign]

    res = asyncio.run(run())
    outcomes = [o["outcome"] for o in res["tag_outcomes"]]
    assert outcomes == ["deferred_cursor"], f"expected deferred_cursor, got {outcomes}"
    assert _proposal_rows(engine, room_id) == [], "no proposal row may exist"
    assert _member_row(engine, room_id).last_delivered_seq == 0, (
        "mint failure must NOT advance the delivered cursor"
    )
    assert manager.set_injection.call_count == 0, "parent routing must be gated"
    assert manager.enqueue_message.await_count == 0


def test_mint_failure_boot_time_defers_and_keeps_cursor(stack):
    """F1 / A-P3-22 (lane (c′)/boot-time): a mint failure at boot counts
    deferred, leaves the cursor untouched, and mints no row — the row
    re-derives (and retries) on the next boot. Mutation-grade: reddens
    against the pre-fix 939cf171a shape (delivered += 1 + cursor advance
    on a swallowed mint)."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        s.commit()
    post = asyncio.run(svc.append_message(
        room_id=room_id, author_instance_id=caller, author_name="lead",
        text_body="boot ping", tagged_members=["worker"], addressed_members=[],
    ))
    asyncio.run(svc.advance_delivered_cursor(room_id, caller, post["seq"]))
    real_append = _raise_append(svc)
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        try:
            return await delivery.boot_deliver(engine)
        finally:
            svc.append_message = real_append  # type: ignore[method-assign]

    counts = asyncio.run(run())
    assert counts["delivered"] == 0, "failed mint is not delivered"
    assert counts["deferred"] == 1, "failed mint counts deferred (retry next boot)"
    assert _proposal_rows(engine, room_id) == [], "no proposal row may exist"
    assert _member_row(engine, room_id).last_delivered_seq == 0, (
        "mint failure must NOT advance the delivered cursor"
    )


def test_boot_terminal_mints_exactly_once_and_never_routes_parent(stack):
    """C1 — the pinned pair #2 DEDICATED pin: terminal member + tagged
    row at boot → EXACTLY ONE task_proposal row minted, Y14 cursor
    advance, ZERO parent set_injection/enqueue_message calls, parent
    status unchanged."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "completed"
        owner = s.get(Instance, caller)
        owner.status = "running"  # if routing happened, injection would fire
        s.commit()
    post = asyncio.run(svc.append_message(
        room_id=room_id, author_instance_id=caller, author_name="lead",
        text_body="boot ping", tagged_members=["worker"], addressed_members=[],
    ))
    asyncio.run(svc.advance_delivered_cursor(room_id, caller, post["seq"]))
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    counts = asyncio.run(delivery.boot_deliver(engine))
    proposals = _proposal_rows(engine, room_id)
    assert len(proposals) == 1, f"exactly one proposal, got {len(proposals)}"
    assert counts["delivered"] == 1, "terminal mint advances the cursor (Y14)"
    assert _member_row(engine, room_id).last_delivered_seq == post["seq"]
    assert manager.set_injection.call_count == 0, "never parent-inject at boot"
    assert manager.enqueue_message.await_count == 0, "never parent-enqueue at boot"
    with Session(engine) as s:
        assert s.get(Instance, caller).status == "running", "parent untouched"


def test_boot_terminal_race_never_routes_parent(stack):
    """C2 / A-P3-21: a member flipping terminal between boot_deliver's
    status read and _tagged_lane's re-read takes the MINT-ONLY boot
    branch (at_boot=True) — the parent-routing branch never fires at
    boot, restoring pair #2's absolute wording. Mutation-grade: reddens
    when the at_boot guard is removed (parent gets injected/enqueued)."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        owner = s.get(Instance, caller)
        owner.status = "running"  # routing (if it ran) would inject — detectable
        s.commit()
    post = asyncio.run(svc.append_message(
        room_id=room_id, author_instance_id=caller, author_name="lead",
        text_body="race ping", tagged_members=["worker"], addressed_members=[],
    ))
    asyncio.run(svc.advance_delivered_cursor(room_id, caller, post["seq"]))
    # Deterministic race: the FIRST status read (boot_deliver's lane
    # assignment) sees the member alive; the member flips terminal before
    # _tagged_lane's re-read.
    real_status = svc.get_instance_status
    flipped = False

    async def racing_status(iid):
        nonlocal flipped
        if iid == "child-1":
            if not flipped:
                flipped = True
                return "idle"  # boot_deliver's lane-assignment read sees alive
            with Session(engine) as s:
                inst = s.get(Instance, iid)
                inst.status = "completed"
                s.commit()
        return await real_status(iid)  # every other read (parent!) passes through

    svc.get_instance_status = racing_status  # type: ignore[method-assign]
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        try:
            return await delivery.boot_deliver(engine)
        finally:
            svc.get_instance_status = real_status  # type: ignore[method-assign]

    counts = asyncio.run(run())
    assert counts["delivered"] == 1, "terminal mint at boot advances the cursor"
    assert len(_proposal_rows(engine, room_id)) == 1, "exactly one proposal"
    assert manager.set_injection.call_count == 0, (
        "pair #2 absolute: the race path must NEVER parent-inject at boot"
    )
    assert manager.enqueue_message.await_count == 0, (
        "pair #2 absolute: the race path must NEVER parent-enqueue at boot"
    )


def test_addressed_alive_not_running_is_digest_only_no_wake(stack):
    """F3 — §4.4's doc-promised pin #2: an addressed-to ALIVE-NOT-RUNNING
    addressee gets digest-only with NO wake — no MessageQueue write, no
    injection call (addressing alone never interrupts)."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "idle"  # alive, not running
        s.commit()
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=caller, author_name="lead",
            text_body="fyi", tagged_members=[], addressed_members=["worker"],
        )
        return await delivery.route_post(
            room_id=room_id, post=post,
            sender_instance_id=caller, sender_member_name="lead",
        )

    res = asyncio.run(run())
    addr_outcomes = [o["outcome"] for o in res["addressed_outcomes"]]
    assert addr_outcomes == ["digest_only"]
    assert manager.enqueue_message.await_count == 0, "alive addressee: no wake"
    assert manager.set_injection.call_count == 0, "alive addressee: no injection"


def test_boot_two_hop_race_never_routes_parent(stack):
    """C2 residual / A-P3-24 — the TWO-HOP race: the member reads ALIVE
    at boot_deliver's lane read AND at _tagged_lane's re-read, then
    flips TERMINAL before _lane_b_enqueue's fresh re-read (hooked on the
    CAS: alive at descent, terminal at the fresh read). At boot the
    fresh-terminal branch must stay MINT-ONLY: proposal minted, ZERO
    parent routing, member never revived.

    Trap avoidance (the C2 one-hop pin's lesson): the PARENT is seeded
    routable-looking (running in the DB + live-graph mock) and the flip
    touches ONLY the member — under the targeted mutation the reddening
    assertion is the real _route_proposal_to_parent call firing
    set_injection on the untouched running parent, not a parent-status
    side-effect. Mutation-grade: reddens when the fresh-terminal
    at_boot guard is removed (see the remediation report's red/green)."""
    engine, repo, svc, room_id, caller = stack
    with Session(engine) as s:
        inst = s.get(Instance, "child-1")
        inst.status = "idle"  # alive at BOTH pre-descent reads
        owner = s.get(Instance, caller)
        owner.status = "running"  # routable-looking parent, never mutated
        s.commit()
    post = asyncio.run(svc.append_message(
        room_id=room_id, author_instance_id=caller, author_name="lead",
        text_body="two-hop ping", tagged_members=["worker"], addressed_members=[],
    ))
    asyncio.run(svc.advance_delivered_cursor(room_id, caller, post["seq"]))
    # The flip rides the CAS: by the time _lane_b_enqueue's fresh
    # re-read runs, the member is terminal (CAS misses — terminal is
    # outside the allowed set).
    real_cas = svc.cas_instance_status_to_running

    async def flipping_cas(iid, allowed):
        if iid == "child-1":
            with Session(engine) as s:
                inst = s.get(Instance, iid)
                inst.status = "completed"
                s.commit()
        return await real_cas(iid, allowed)

    svc.cas_instance_status_to_running = flipping_cas  # type: ignore[method-assign]
    manager = _make_manager(engine, svc, live_graph=True, inject_ok=True)
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        try:
            return await delivery.boot_deliver(engine)
        finally:
            svc.cas_instance_status_to_running = real_cas  # type: ignore[method-assign]

    counts = asyncio.run(run())
    proposals = _proposal_rows(engine, room_id)
    assert len(proposals) == 1, f"exactly one proposal, got {len(proposals)}"
    assert counts["delivered"] == 1, "boot counting classifies proposal as delivered"
    assert _member_row(engine, room_id).last_delivered_seq == post["seq"], "Y14 advance"
    assert manager.set_injection.call_count == 0, (
        "two-hop window: NEVER parent-inject at boot"
    )
    assert manager.enqueue_message.await_count == 0, (
        "two-hop window: NEVER parent-enqueue at boot"
    )
    with Session(engine) as s:
        assert s.get(Instance, "child-1").status == "completed", "member not revived"
        assert s.get(Instance, caller).status == "running", "parent untouched"


# ---------------------------------------------------------------------------
# P3 carry-over — registry-clear-on-stop symmetry
# ---------------------------------------------------------------------------


class TestRoomLockRegistryClear:
    async def test_clear_lock_registry_drops_all_locks(self, engine):
        """The shutdown step's mechanic: clear_lock_registry drops
        every per-room lock and returns the count; a subsequent
        get_room_lock mints a FRESH lock (identity differs)."""
        svc = RoomChatService(engine)
        l1 = await svc.get_room_lock("room-a")
        l2 = await svc.get_room_lock("room-b")
        dropped = await svc.clear_lock_registry()
        assert dropped == 2
        # The registry is empty: a fresh get mints a NEW lock.
        l3 = await svc.get_room_lock("room-a")
        assert l3 is not l1

    async def test_clear_on_empty_registry_is_zero(self, engine):
        svc = RoomChatService(engine)
        assert await svc.clear_lock_registry() == 0

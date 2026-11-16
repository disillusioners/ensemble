"""P4 nudge hooks behavior pins (agent group chat, arm (a) + (a2)).

The Emperor's rhythm-aware delivery mechanized. Real file-backed
SQLite (F9-parity harness), real RoomChatService + (where the strong
form needs it) RoomDeliveryService over a plain fake manager whose
lifecycle seams are controlled per test.

Pin map (deliverables 1-7):
  (a)  todo-boundary note: fires with unread>0, silent at unread==0
  (b)  carrier non-narrowing: fires on a non-done update (filter
       decides, never the carrier)
  (c)  (a2) holds at completion with unread>0, releases after read
  (d)  loop-guard: busy room + repeated boundaries = bounded nudges;
       completion always reachable
  (e)  shared latch: no double nudge same turn across BOTH arms
  (f)  watermark re-arm: fires again only when max(seq) > last_nudged
  (g)  strong form: tag named + lane-(a) re-attempt + latch cap +
       spent-latch ⇒ next turn
  (h)  non-member instances unaffected
  (i)  compaction non-interference (injected-message protection)
  plus the graph-side shape pins: gate node return contract, out-edge
  router map, terminal-intercept wrapper, drain-wrapper peek
  priority, and the redeliver entry's lane-(a) delegation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.graph import (
    ATTESTATION_GATE_NODE_NAME,
    END,
    INJECTION_CONTINUATION_MAX,
    create_injection_aware_router,
    create_room_completion_gate_node,
    create_room_completion_should_continue,
    create_should_end_room_completion,
)
from daemon._content_hardening import is_injected_message
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.services.room_delivery import RoomDeliveryService
from daemon.services.room_nudge import (
    ROOM_COMPLETION_GATE_NODE_NAME,
    ROOM_COMPLETION_ROUTE_KEY,
    ROOM_NUDGE_MARKER,
    RoomNudgeCoordinator,
)
from daemon.services.todo_manager import TodoGraphManager
from tests.helpers.room_harness import join_member, make_room, make_room_engine


# =============================================================================
# Harness
# =============================================================================


def _seed_instance(eng: Engine, iid: str, parent: str | None = None,
                   status: str = "idle") -> None:
    with Session(eng) as s:
        s.add(Instance(instance_id=iid, agent_id="ari",
                       agent_dir="/agents/ari", parent_id=parent,
                       status=status))
        s.commit()


class _FakeManager:
    """Plain manager double carrying the P4 seams (no MagicMock: the
    latch dict + task registry must be REAL objects — object identity
    is load-bearing)."""

    def __init__(self, engine: Engine, svc: RoomChatService):
        self._room_chat_service = svc
        self._room_nudge_latch: dict = {}
        self._graph_tasks: dict = {}
        self._room_delivery_service: RoomDeliveryService | None = None
        self._todo_manager = TodoGraphManager()
        self._live_hub = None
        self.set_injection = MagicMock()
        self.has_live_graph_task = MagicMock(return_value=True)
        self.enqueue_message = MagicMock()


def _new_turn(manager: _FakeManager, iid: str) -> object:
    """Mint a fresh turn: a new root-graph-task sentinel object."""
    task = object()
    manager._graph_tasks[iid] = task
    return task


@pytest.fixture
def stack(tmp_path):
    eng = make_room_engine(tmp_path, "room_nudge.db")
    repo = RoomRepository(eng)
    svc = RoomChatService(eng)
    # owner + one child member; owner owns the room.
    _seed_instance(eng, "owner-1", None, "idle")
    _seed_instance(eng, "mem-1", "owner-1", "idle")
    room_id = make_room(repo, tree_root="owner-1", owner="owner-1",
                        name="alpha", owner_member_name="lead")
    join_member(eng, room_id, "mem-1", "worker")
    manager = _FakeManager(eng, svc)
    coordinator = RoomNudgeCoordinator(manager)
    manager._room_nudge_coordinator = coordinator
    delivery = RoomDeliveryService(manager, svc)
    manager._room_delivery_service = delivery
    yield {
        "eng": eng, "repo": repo, "svc": svc, "room_id": room_id,
        "manager": manager, "coordinator": coordinator,
        "delivery": delivery,
    }
    eng.dispose()


def _post(svc, room_id, author="owner-1", author_name="lead",
          text="hello", tagged=None, addressed=None):
    return asyncio.run(svc.append_message(
        room_id=room_id, author_instance_id=author, author_name=author_name,
        text_body=text, tagged_members=tagged or [], addressed_members=addressed or [],
    ))


def _read_all(eng, room_id, iid):
    """The member reads the room to the top (advance last_read_seq)."""
    repo = RoomRepository(eng)
    msgs = repo.list_messages(room_id, after_seq=0, limit=1000,
                              caller_instance_id=iid)
    top = max((m["seq"] for m in msgs), default=0)
    repo.advance_read_cursor(room_id, iid, top)


def _snapshot_rows(stack, iid):
    return asyncio.run(stack["svc"].nudge_snapshot_for_instance(iid))


# =============================================================================
# (a) — todo-step boundary
# =============================================================================


class TestTodoBoundaryArm:
    def test_a_fires_with_unread_and_silent_at_zero(self, stack):
        """Pin (a): unread>0 ⇒ the note; unread==0 ⇒ byte-identical ""."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.todo_boundary_note("mem-1")) == ""
        _post(stack["svc"], stack["room_id"])  # unread=1 for mem-1
        _post(stack["svc"], stack["room_id"])  # unread=2
        note = asyncio.run(coord.todo_boundary_note("mem-1"))
        assert "2 unread" in note
        assert "team chatroom" in note
        # After the member READS the room: silent again (fresh turn).
        _new_turn(stack["manager"], "mem-1")
        _read_all(stack["eng"], stack["room_id"], "mem-1")
        assert asyncio.run(coord.todo_boundary_note("mem-1")) == ""

    def test_b_carrier_non_narrowing_fires_on_non_done_update(self, stack):
        """Pin (b): the carrier fires on EVERY successful update — the
        note rides an ``in_progress`` update exactly as it would a
        done-update. Asserted through the REAL todo tool, not a
        source-grep."""
        from daemon.tools.todo_tools import create_todo_tools

        mgr = stack["manager"]
        iid = "mem-1"
        _new_turn(mgr, iid)
        tools = create_todo_tools(mgr, iid, None)
        by_name = {t.name: t for t in tools}
        created = asyncio.run(by_name["todo_list_create"].coroutine(
            items=["step one", "step two"]))
        assert "ERROR" not in created
        _post(stack["svc"], stack["room_id"])  # unread=1
        node_id = mgr._todo_manager.get_graph(iid)["nodes"][0]["id"]
        result = asyncio.run(by_name["todo_graph_update"].coroutine(
            node_id=node_id,
            status="in_progress",
        ))
        assert "ERROR" not in result
        assert "unread" in result and "team chat" in result, (
            "the note must ride a NON-done update — the carrier is "
            "non-narrowing; the filter decides"
        )

    def test_h_non_member_unaffected(self, stack):
        """Pin (h): an instance with no room memberships gets no note
        and no gate fire; its evaluation leaves zero latch rows."""
        _seed_instance(stack["eng"], "loner-1", None, "idle")
        _new_turn(stack["manager"], "loner-1")
        _post(stack["svc"], stack["room_id"])  # unread for mem-1, not loner
        assert asyncio.run(
            stack["coordinator"].todo_boundary_note("loner-1")) == ""
        decision = asyncio.run(
            stack["coordinator"].evaluate_completion("loner-1"))
        assert decision["fire"] is False
        assert stack["manager"]._room_nudge_latch == {}

    def test_snapshot_member_scope(self, stack):
        """The substrate scan counts unread for the RIGHT member and
        resolves tag targets by member_name."""
        _post(stack["svc"], stack["room_id"])
        rows = _snapshot_rows(stack, "mem-1")
        assert len(rows) == 1
        assert rows[0]["unread"] == 1
        assert rows[0]["max_seq"] == 1
        owner_rows = _snapshot_rows(stack, "owner-1")
        assert owner_rows[0]["unread"] == 1  # owner has a seeded row too


# =============================================================================
# (a2) — completion gate
# =============================================================================


class TestCompletionGateArm:
    def test_c_gate_holds_then_releases_after_read(self, stack):
        """Pin (c): unread>0 at completion ⇒ fire + reminder; after the
        member reads the room ⇒ pass."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is False
        _post(stack["svc"], stack["room_id"], text="check this")
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is True
        assert "unread" in decision["reminder"]
        assert decision["reminder"].startswith("[SYSTEM CONTEXT:")
        # The member reads the room; a fresh turn passes clean.
        _read_all(stack["eng"], stack["room_id"], "mem-1")
        _new_turn(stack["manager"], "mem-1")
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is False

    def test_gate_node_return_contract(self, stack):
        """The gate node: fire ⇒ messages + route hint 'agent'; pass ⇒
        {} (zero side effects)."""
        node = create_room_completion_gate_node(
            stack["manager"], None)
        config = {"configurable": {"thread_id": "mem-1"}}
        _post(stack["svc"], stack["room_id"])
        _new_turn(stack["manager"], "mem-1")
        result = asyncio.run(node({}, config))
        assert result[ROOM_COMPLETION_ROUTE_KEY] == "agent"
        msgs = result["messages"]
        assert len(msgs) == 1 and isinstance(msgs[0], HumanMessage)
        assert msgs[0].additional_kwargs[ROOM_NUDGE_MARKER] is True
        assert msgs[0].additional_kwargs["injected_message"] is True
        # Spent turn: pass ⇒ {}.
        result2 = asyncio.run(node({}, config))
        assert result2 == {}

    def test_gate_node_fail_open_on_coordinator_fault(self, stack):
        """A coordinator fault never errors the turn (C3 fail-open)."""
        mgr = stack["manager"]

        class _Boom:
            def evaluate_completion(self, iid):
                raise RuntimeError("scan boom")

        mgr._room_nudge_coordinator = _Boom()
        node = create_room_completion_gate_node(mgr, "mem-1")
        assert asyncio.run(node({}, {"configurable": {"thread_id": "mem-1"}})) == {}
        # Unwired manager: fail open too.
        assert asyncio.run(create_room_completion_gate_node(
            object(), "mem-1")({}, {"configurable": {"thread_id": "mem-1"}})) == {}

    def test_out_edge_router_map(self, stack):
        """Router: hint ⇒ 'agent'; no hint ⇒ attestation when wired,
        END otherwise. The map always carries the explicit 'agent' key."""
        r_active = create_should_end_room_completion(True)
        r_off = create_should_end_room_completion(False)
        assert r_active({ROOM_COMPLETION_ROUTE_KEY: "agent"}) == "agent"
        assert r_active({}) == ATTESTATION_GATE_NODE_NAME
        assert r_off({}) == END
        assert r_off({ROOM_COMPLETION_ROUTE_KEY: "agent"}) == "agent"

    def test_terminal_intercept_wrapper(self, stack):
        """Wrapper: END and the attestation verdict map to the room
        gate; non-terminal verdicts pass; disabled ⇒ base unchanged."""
        base = lambda s: s.get("v")  # noqa: E731
        wrapped = create_room_completion_should_continue(
            base, room_gate_enabled=True)
        assert wrapped({"v": END}) == ROOM_COMPLETION_GATE_NODE_NAME
        assert wrapped({"v": ATTESTATION_GATE_NODE_NAME}) == (
            ROOM_COMPLETION_GATE_NODE_NAME)
        assert wrapped({"v": "tools"}) == "tools"
        assert create_room_completion_should_continue(
            base, room_gate_enabled=False) is base


# =============================================================================
# Loop-guard: latch + watermark
# =============================================================================


class TestLoopGuard:
    def test_d_busy_room_bounded_completion_reachable(self, stack):
        """Pin (d): a busy room + repeated completion boundaries ⇒ the
        gate fires AT MOST once per turn; completion is ALWAYS
        reachable (a later evaluation in the same turn passes even
        though unread stays >0)."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        _post(stack["svc"], stack["room_id"])  # busy room: 1 unread
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        # The room keeps being busy (no read, no new message yet):
        for _ in range(5):
            decision = asyncio.run(coord.evaluate_completion("mem-1"))
            assert decision["fire"] is False, (
                "the latch MUST cap the family at one nudge per turn — "
                "completion always reachable"
            )
        # Next turn, no new messages: the watermark keeps it silent.
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is False

    def test_e_no_double_nudge_same_turn_across_arms(self, stack):
        """Pin (e): the (a) todo note spends the room's reminder turn;
        the (a2) gate in the SAME turn does not re-fire (and vice
        versa)."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        _post(stack["svc"], stack["room_id"])
        note = asyncio.run(coord.todo_boundary_note("mem-1"))
        assert note != ""
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is False, (
            "one room nudge per turn across the WHOLE (a)/(a2) family"
        )
        # Reverse order: gate first, then the todo note is silent.
        stack["manager"]._room_nudge_latch.clear()
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        assert asyncio.run(coord.todo_boundary_note("mem-1")) == ""

    def test_f_watermark_rearm_on_new_messages(self, stack):
        """Pin (f): re-fire ONLY when max(seq) > last_nudged — a new
        message re-arms; the same batch never re-nags."""
        coord = stack["coordinator"]
        _post(stack["svc"], stack["room_id"], text="m1")     # seq 1
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        # Turn 2, same batch: silent.
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is False
        # New message arrives: re-armed.
        _post(stack["svc"], stack["room_id"], text="m2")     # seq 2
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        # Same turn again: spent.
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is False

    def test_d2_new_message_mid_turn_still_one_nudge_per_turn(self, stack):
        """Pin (d2) — the ≤1-per-turn bound, observable: a message
        landing MID-TURN re-arms the watermark but the SAME turn's
        next boundary must stay silent; only the NEXT turn fires."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        _post(stack["svc"], stack["room_id"], text="m1")     # seq 1
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        # Busy room: another message lands mid-turn (watermark armed).
        _post(stack["svc"], stack["room_id"], text="m2")     # seq 2
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is False, (
            "≤1 room nudge per turn: a mid-turn message re-arms the "
            "watermark but the turn spend holds"
        )
        # The todo arm in the same turn is spent too.
        assert asyncio.run(coord.todo_boundary_note("mem-1")) == ""
        # Next turn: fires on the new batch.
        _new_turn(stack["manager"], "mem-1")
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is True
        assert "2 unread" in decision["reminder"]

    def test_e2_todo_arm_turn_spend_holds_on_midturn_message(self, stack):
        """Pin (e2): the (a) arm's spend holds within the turn even
        when a second message lands between two todo updates."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        _post(stack["svc"], stack["room_id"], text="m1")
        assert asyncio.run(coord.todo_boundary_note("mem-1")) != ""
        _post(stack["svc"], stack["room_id"], text="m2")
        assert asyncio.run(coord.todo_boundary_note("mem-1")) == "", (
            "≤1 room nudge per turn across the family: the second todo "
            "update in the SAME turn must not re-note"
        )
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.todo_boundary_note("mem-1")) != ""

    def test_restarted_process_bounded_duplicate_accepted(self, stack):
        """N8: the RAM latch lost on restart ⇒ one bounded duplicate —
        the very next boundary fires once, then the cap re-arms."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        _post(stack["svc"], stack["room_id"])
        assert asyncio.run(coord.todo_boundary_note("mem-1")) != ""
        # "Restart": RAM latch wiped.
        stack["manager"]._room_nudge_latch.clear()
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is False


# =============================================================================
# Strong form — tagged pending at completion
# =============================================================================


class TestStrongForm:
    def test_g_tag_named_and_lane_a_reattempt(self, stack):
        """Pin (g1): a pending TAGGED row ⇒ the reminder NAMES the tag
        AND the re-attempt rides the real lane-(a) machinery (member
        running + live graph ⇒ set_injection + delivered cursor
        advance — Y14 admission-gated)."""
        eng, svc, mgr, coord = (
            stack["eng"], stack["svc"], stack["manager"],
            stack["coordinator"],
        )
        with Session(eng) as s:
            inst = s.get(Instance, "mem-1")
            inst.status = "running"
            s.commit()
        _post(svc, stack["room_id"], text="urgent",
              tagged=["worker"])
        _new_turn(mgr, "mem-1")
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is True
        assert "@worker" in decision["reminder"]
        assert len(decision["pending_tags"]) == 1
        mgr.set_injection.reset_mock()
        outcomes = asyncio.run(
            coord.reattempt_pending_tags("mem-1", decision["pending_tags"]))
        assert outcomes[0]["outcome"] == "injected"
        assert mgr.set_injection.call_count == 1
        source = mgr.set_injection.call_args.kwargs.get("source", "")
        assert source.startswith(f"room:{stack['room_id']}:lead:1:")
        # Y14: the delivered cursor advanced at injection.
        member = stack["repo"].get_member(stack["room_id"], "mem-1")
        assert member["last_delivered_seq"] == 1

    def test_g_latch_caps_second_reattempt_same_turn(self, stack):
        """Pin (g2): ≤1 tag re-attempt per turn — a second evaluation
        in the same turn does not re-fire even with rows still
        pending (injection failing keeps them durable)."""
        eng, svc, mgr, coord = (
            stack["eng"], stack["svc"], stack["manager"],
            stack["coordinator"],
        )
        mgr.has_live_graph_task = MagicMock(return_value=True)
        mgr.set_injection.side_effect = RuntimeError("inject boom")
        with Session(eng) as s:
            inst = s.get(Instance, "mem-1")
            inst.status = "running"
            s.commit()
        _post(svc, stack["room_id"], tagged=["worker"])
        _new_turn(mgr, "mem-1")
        d1 = asyncio.run(coord.evaluate_completion("mem-1"))
        assert d1["fire"] is True and d1["pending_tags"]
        outcomes = asyncio.run(
            coord.reattempt_pending_tags("mem-1", d1["pending_tags"]))
        assert outcomes[0]["outcome"] == "deferred_cursor"
        member = stack["repo"].get_member(stack["room_id"], "mem-1")
        assert member["last_delivered_seq"] == 0, (
            "A-P3-22: a failed admission leaves the cursor untouched"
        )
        # Same turn: spent — no re-fire.
        d2 = asyncio.run(coord.evaluate_completion("mem-1"))
        assert d2["fire"] is False

    def test_g_spent_latch_tag_resurfaces_next_turn(self, stack):
        """Pin (g3): the watermark spent ⇒ tag STILL re-surfaces next
        turn (the escalation's re-arm is the turn, not the message
        batch)."""
        eng, svc, mgr, coord = (
            stack["eng"], stack["svc"], stack["manager"],
            stack["coordinator"],
        )
        mgr.set_injection.side_effect = RuntimeError("inject boom")
        with Session(eng) as s:
            inst = s.get(Instance, "mem-1")
            inst.status = "running"
            s.commit()
        _post(svc, stack["room_id"], tagged=["worker"])
        _new_turn(mgr, "mem-1")
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        # Same turn: spent.
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is False
        # NEXT turn, no new messages (watermark spent): the tag
        # re-surfaces.
        _new_turn(mgr, "mem-1")
        d3 = asyncio.run(coord.evaluate_completion("mem-1"))
        assert d3["fire"] is True
        assert d3["pending_tags"], "pending tag must re-surface next turn"
        assert "@worker" in d3["reminder"]

    def test_redeliver_single_member_scope(self, stack):
        """The re-attempt delivers to exactly ONE member (calling
        route_post wholesale would re-deliver to other tagged members
        whose cursors already advanced)."""
        eng, svc, mgr = stack["eng"], stack["svc"], stack["manager"]
        _seed_instance(eng, "mem-2", "owner-1", "running")
        join_member(eng, stack["room_id"], "mem-2", "plumber")
        with Session(eng) as s:
            for iid in ("mem-1", "mem-2"):
                inst = s.get(Instance, iid)
                inst.status = "running"
                s.commit()
        _post(svc, stack["room_id"], tagged=["worker", "plumber"])
        # mem-2 already had theirs delivered (cursor advanced out of
        # band); only mem-1's row is pending FOR mem-1.
        stack["repo"].advance_delivered_cursor(
            stack["room_id"], "mem-2", 1)
        mgr.set_injection.reset_mock()
        rows = _snapshot_rows(stack, "mem-1")
        assert rows[0]["pending_tags"], "mem-1's row still pending"
        outcomes = asyncio.run(stack["delivery"].redeliver_tag_for_member(
            room_id=stack["room_id"],
            post=asyncio.run(svc.read_messages(
                stack["room_id"], caller_instance_id="mem-1",
                after_seq=0, limit=10))[0],
            member={"room_id": stack["room_id"], "instance_id": "mem-1",
                    "member_name": "worker"},
        ))
        assert outcomes == "injected"
        # Exactly ONE injection — for mem-1 only.
        assert mgr.set_injection.call_count == 1
        target = mgr.set_injection.call_args.args[0]
        assert target == "mem-1"


# =============================================================================
# (i) — compaction non-interference + drain-wrapper backstop
# =============================================================================


class TestCompactionAndBackstop:
    def test_i_nudge_message_is_injected_protected(self, stack):
        """Pin (i): the gate's nudge HumanMessage is classified as an
        injected message — compaction preserves it verbatim and the
        attestation ladder excludes it from real-user scans."""
        node = create_room_completion_gate_node(stack["manager"], None)
        config = {"configurable": {"thread_id": "mem-1"}}
        _post(stack["svc"], stack["room_id"])
        _new_turn(stack["manager"], "mem-1")
        result = asyncio.run(node({}, config))
        nudge = result["messages"][0]
        assert is_injected_message(nudge) is True
        assert isinstance(nudge, HumanMessage)
        assert nudge.additional_kwargs.get(ROOM_NUDGE_MARKER) is True

    def test_backstop_peek_detours_on_terminal_with_interest(self, stack):
        """Drain-wrapper peek: terminal verdict + empty queue + RAM
        interest ⇒ detour to the gate node; no interest ⇒ verdict;
        PEEK consumes nothing (latch untouched)."""
        coord = stack["coordinator"]
        mgr = stack["manager"]
        coord._ram_interest["mem-1"] = True
        slot = MagicMock()
        slot.get = MagicMock(return_value=None)  # empty queue
        base = MagicMock(return_value="end_candidate")
        router = create_injection_aware_router(
            base, slot,
            room_completion_probe=coord.ram_interest,
            room_gate_node_name=ROOM_COMPLETION_GATE_NODE_NAME,
        )
        verdict = router({}, {"configurable": {"thread_id": "mem-1"}})
        assert verdict == ROOM_COMPLETION_GATE_NODE_NAME
        assert mgr._room_nudge_latch == {}, "peek must consume nothing"

        coord._ram_interest["mem-1"] = False
        assert router({}, {"configurable": {"thread_id": "mem-1"}}) == (
            "end_candidate")

    def test_backstop_no_probe_no_detour_and_no_db_in_router(self, stack):
        """Without the probe (legacy wiring) the wrapper is verdict-
        identical: the peek never fires."""
        slot = MagicMock()
        slot.get = MagicMock(return_value=None)
        base = MagicMock(return_value=END)
        router = create_injection_aware_router(base, slot)
        assert router({}, {"configurable": {"thread_id": "mem-1"}}) == END

    def test_backstop_injection_queue_drains_first(self, stack):
        """Priority: a pending user injection outranks the room detour —
        the wrapper routes 'agent' (the existing drain), never the
        gate, while the queue holds entries."""
        coord = stack["coordinator"]
        coord._ram_interest["mem-1"] = True
        slot = MagicMock()
        slot.get = MagicMock(return_value=[object()])  # non-empty queue
        base = MagicMock(return_value="end_candidate")
        router = create_injection_aware_router(
            base, slot,
            room_completion_probe=coord.ram_interest,
            room_gate_node_name=ROOM_COMPLETION_GATE_NODE_NAME,
        )
        assert router({}, {"configurable": {"thread_id": "mem-1"}}) == "agent"

    def test_backstop_non_terminal_verdict_never_peeks(self, stack):
        """Non-terminal verdicts ('tools') pass through untouched — the
        peek is TERMINAL-verdict-only (the (a) arm owns mid-turn)."""
        coord = stack["coordinator"]
        coord._ram_interest["mem-1"] = True
        slot = MagicMock()
        slot.get = MagicMock(return_value=None)
        base = MagicMock(return_value="tools")
        router = create_injection_aware_router(
            base, slot,
            room_completion_probe=coord.ram_interest,
            room_gate_node_name=ROOM_COMPLETION_GATE_NODE_NAME,
        )
        assert router({}, {"configurable": {"thread_id": "mem-1"}}) == "tools"
        slot.get.assert_not_called()

    def test_injection_cap_budget_unchanged(self, stack):
        """The injection continuation budget is untouched by the room
        peek (same INJECTION_CONTINUATION_MAX contract)."""
        assert INJECTION_CONTINUATION_MAX == 8
        slot = MagicMock()
        slot.get = MagicMock(return_value=None)
        base = MagicMock(return_value=END)
        router = create_injection_aware_router(
            base, slot,
            room_completion_probe=lambda iid: False,
            room_gate_node_name=ROOM_COMPLETION_GATE_NODE_NAME,
        )
        for _ in range(INJECTION_CONTINUATION_MAX + 1):
            pass  # no continuations: queue empty ⇒ plain verdicts
        assert router({}, {"configurable": {"thread_id": "mem-1"}}) == END


# =============================================================================
# Wiring-order verification (plan P4 row: the real build_instance_graph)
# =============================================================================


class _RecordingGraph:
    """Captures add_node / add_conditional_edges / add_edge calls."""

    def __init__(self):
        self.nodes: dict = {}
        self.cond_edges: list = []
        self.plain_edges: list = []

    def add_node(self, name, fn, *a, **k):
        self.nodes[name] = fn

    def add_conditional_edges(self, start, router, path_map, *a, **k):
        self.cond_edges.append((start, router, dict(path_map)))

    def add_edge(self, start, end, *args, **kwargs):
        self.plain_edges.append((start, end))

    def set_entry_point(self, *a, **k):
        pass

    def compile(self, *a, **k):
        return MagicMock(name="compiled")


class TestWiringOrder:
    """The plan P4 row's wiring-order verification: the chain lands in
    the REAL build_instance_graph under BOTH language configurations,
    and legacy (coordinator-less) wiring stays byte-identical."""

    def _build(self, monkeypatch, *, manager, language_enabled):
        from unittest.mock import patch

        from daemon.services.attestation_gate import (
            GateSettings,
        )

        monkeypatch.setattr(
            "daemon.services.attestation_gate.resolve_gate_settings",
            lambda: GateSettings(mode="enforce", window=3, deny_bound=3),
        )
        monkeypatch.setattr(
            "daemon.services.attestation_gate._CACHED_GATE_SETTINGS",
            None,
        )
        recording = _RecordingGraph()
        tools = [MagicMock(name="t1"), MagicMock(name="t2")]
        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class, \
                patch("daemon.graph.StateGraph", return_value=recording), \
                patch("daemon.graph.ToolNode"):
            mock_llm_instance = MagicMock()
            mock_llm_instance.bind_tools.return_value = MagicMock()
            mock_llm_class.return_value = mock_llm_instance
            from daemon.graph import build_instance_graph

            build_instance_graph(
                tools=tools,
                checkpointer=MagicMock(),
                llm_config={"model": "gpt-4o", "api_key": "t"},
                system_prompt="p",
                user_language="English",
                language_check_enabled=language_enabled,
                injection_slot=MagicMock(),
                manager=manager,
                attestation_enabled=True,
                graph_config={"configurable": {"thread_id": "mem-1"}},
            )
        return recording

    def test_chain_enabled_language(self, monkeypatch, stack):
        """language_check → room_completion_gate → attestation_gate →
        END: the language_check out-edge carries ONLY {retry,
        room gate} (no direct terminal keys); the room gate's own map
        carries the explicit 'agent' key + attestation + END; the
        agent-side map carries the room gate as the backstop detour
        target."""
        mgr = stack["manager"]
        recording = self._build(monkeypatch, manager=mgr,
                                language_enabled=True)
        assert ROOM_COMPLETION_GATE_NODE_NAME in recording.nodes
        maps = {start: pm for start, _r, pm in recording.cond_edges}
        lc_paths = maps["language_check"]
        assert set(lc_paths) == {"retry", ROOM_COMPLETION_GATE_NODE_NAME}, (
            "every terminal verdict that survived language check must "
            "reach the room gate first"
        )
        gate_paths = maps[ROOM_COMPLETION_GATE_NODE_NAME]
        assert gate_paths["agent"] == "agent"
        assert gate_paths[ATTESTATION_GATE_NODE_NAME] == (
            ATTESTATION_GATE_NODE_NAME)
        assert gate_paths[END] == END
        agent_paths = maps["agent"]
        assert agent_paths.get(ROOM_COMPLETION_GATE_NODE_NAME) == (
            ROOM_COMPLETION_GATE_NODE_NAME)
        # The language_check router is room-gate-wrapped: a completed
        # state routes to the room gate, not to attestation/END.
        lc_router = dict(
            (start, r) for start, r, _pm in recording.cond_edges
        )["language_check"]
        assert lc_router({"language_check_retry": False}) == (
            ROOM_COMPLETION_GATE_NODE_NAME)
        assert lc_router({"language_check_retry": True}) == "retry"

    def test_chain_disabled_language_seam(self, monkeypatch, stack):
        """N4: disabled-language seam — the room gate sits between the
        base terminal router and the attestation wrapper; the agent
        map carries the room gate (no direct END key)."""
        mgr = stack["manager"]
        recording = self._build(monkeypatch, manager=mgr,
                                language_enabled=False)
        assert ROOM_COMPLETION_GATE_NODE_NAME in recording.nodes
        maps = {start: pm for start, _r, pm in recording.cond_edges}
        agent_paths = maps["agent"]
        assert agent_paths.get(ROOM_COMPLETION_GATE_NODE_NAME) == (
            ROOM_COMPLETION_GATE_NODE_NAME)
        assert END not in agent_paths, (
            "the room gate owns the pass-through: no direct END key "
            "on the agent router map"
        )
        gate_paths = maps[ROOM_COMPLETION_GATE_NODE_NAME]
        assert gate_paths["agent"] == "agent"
        assert gate_paths[ATTESTATION_GATE_NODE_NAME] == (
            ATTESTATION_GATE_NODE_NAME)
        # The agent router is room-gate-wrapped: END verdict → room gate.
        agent_router = dict(
            (start, r) for start, r, _pm in recording.cond_edges
        )["agent"]
        final_ai_state = {
            "messages": [AIMessage(content="final report", tool_calls=[])]
        }
        assert agent_router(final_ai_state) == ROOM_COMPLETION_GATE_NODE_NAME

    def test_legacy_manager_without_coordinator_is_unwired(self, monkeypatch):
        """Fail-open absence: a manager without the coordinator wires
        NOTHING — no room gate node, no path changes."""
        legacy = MagicMock()
        del legacy._room_nudge_coordinator  # absent attribute
        recording = self._build(monkeypatch, manager=legacy,
                                language_enabled=True)
        assert ROOM_COMPLETION_GATE_NODE_NAME not in recording.nodes
        maps = {start: pm for start, _r, pm in recording.cond_edges}
        assert ROOM_COMPLETION_GATE_NODE_NAME not in maps.get(
            "language_check", {})
        assert ROOM_COMPLETION_GATE_NODE_NAME not in maps.get("agent", {})

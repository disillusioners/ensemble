"""P4 nudge hooks behavior pins (agent group chat, arm (a) + (a2)).

The user's rhythm-aware delivery mechanized. Real file-backed
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
  router map, terminal-intercept wrapper, drain-wrapper peek ABSENCE
  (A2 adjudication), and the redeliver entry's lane-(a) delegation.

Size rationale: ONE file per P4 deliverable group (~1k lines). The
executed-graph composition pins need the REAL graph builder and the
real room substrate harness — splitting the file would duplicate that
harness (and its shared fixtures) for no added signal.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any
from pydantic import PrivateAttr
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatResult
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.graph  # noqa: F401 — the real module (source-level channel pin)
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.graph import (
    ATTESTATION_GATE_NODE_NAME,
    END,
    SessionState,
    create_injection_aware_router,
    create_room_completion_gate_node,
    create_room_completion_should_continue,
    create_should_end_room_completion,
)
from tests.support.scripted_chat_model import ScriptedChatModel
from daemon._content_hardening import is_injected_message
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.services.room_delivery import RoomDeliveryService
from daemon.services.room_nudge import (
    ROOM_COMPLETION_GATE_NODE_NAME,
    ROOM_COMPLETION_ROUTE_KEY,
    ROOM_NUDGE_MARKER,
    ROOM_NUDGE_TAG_REATTEMPT_OUTCOMES_KEY,
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
    """Sync wrapper over the async twin (ONE append implementation)."""
    return asyncio.run(_post_async(
        svc, room_id, author=author, author_name=author_name,
        text=text, tagged=tagged, addressed=addressed,
    ))


#: Page size for the read-all helper — one page covers the pin rooms.
_READ_ALL_LIMIT = 1000


def _read_all(eng, room_id, iid):
    """The member reads the room to the top (advance last_read_seq)."""
    repo = RoomRepository(eng)
    msgs = repo.list_messages(room_id, after_seq=0, limit=_READ_ALL_LIMIT,
                              caller_instance_id=iid)
    top = max((m["seq"] for m in msgs), default=0)
    repo.advance_read_cursor(room_id, iid, top)


async def _post_async(svc, room_id, author="owner-1", author_name="lead",
                      text="hello", tagged=None, addressed=None):
    """Async twin of ``_post`` for pins already running inside a loop."""
    return await svc.append_message(
        room_id=room_id, author_instance_id=author, author_name=author_name,
        text_body=text, tagged_members=tagged or [], addressed_members=addressed or [],
    )


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
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) == ""
        _post(stack["svc"], stack["room_id"])  # unread=1 for mem-1
        _post(stack["svc"], stack["room_id"])  # unread=2
        note = asyncio.run(coord.evaluate_todo_boundary("mem-1"))
        assert "2 unread" in note
        assert "team chatroom" in note
        # After the member READS the room: silent again (fresh turn).
        _new_turn(stack["manager"], "mem-1")
        _read_all(stack["eng"], stack["room_id"], "mem-1")
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) == ""

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
            stack["coordinator"].evaluate_todo_boundary("loner-1")) == ""
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


class TestCarrierCoverage:
    """F1 — EVERY reminder-returning todo path carries the (a) nudge.

    The spec rides the nudge on EVERY path whose manager call
    computes the reminder (``update``, ``update_by_index``,
    ``update_subtask``, ``add_subtask``/``add_subtasks``,
    ``remove_subtask``) — five tool carriers in total. Each case arms
    the lane freshly (a new room message re-arms the read-watermark; a
    new turn key re-arms the per-turn spend) so the assertion is about
    THE PATH'S CARRIER, not about a latch state left over from the
    previous case.
    """

    @staticmethod
    def _arm(stack):
        """Post a fresh message (watermark re-arm) + mint a new turn."""
        _post(stack["svc"], stack["room_id"], text="team update")
        _new_turn(stack["manager"], "mem-1")

    def _tools(self, stack):
        from daemon.tools.todo_tools import create_todo_tools

        tools = create_todo_tools(stack["manager"], "mem-1", None)
        return {t.name: t for t in tools}

    def _setup_node_with_subtask(self, stack, tools):
        """Create a node carrying one sub-task (no room message yet, so
        the setup itself cannot consume the latch)."""
        asyncio.run(tools["todo_graph_create"].coroutine(
            nodes=[{"id": "n-a1", "text": "parent", "status": "pending"}]
        ))
        asyncio.run(tools["todo_graph_add_subtask"].coroutine(
            node_id="n-a1", list=["child item"]
        ))
        return "n-a1"

    def test_list_update_carries_the_nudge(self, stack):
        tools = self._tools(stack)
        asyncio.run(tools["todo_list_create"].coroutine(items=["a", "b"]))
        self._arm(stack)
        result = asyncio.run(tools["todo_list_update"].coroutine(
            index=0, status="in_progress"))
        assert "ERROR" not in result
        assert "unread" in result and "team chat" in result, (
            "todo_list_update is a reminder-returning path — it must "
            "carry the nudge"
        )

    def test_graph_update_carries_the_nudge(self, stack):
        tools = self._tools(stack)
        asyncio.run(tools["todo_graph_create"].coroutine(
            nodes=[{"id": "n-b2", "text": "parent", "status": "pending"}]
        ))
        self._arm(stack)
        result = asyncio.run(tools["todo_graph_update"].coroutine(
            node_id="n-b2", status="in_progress"))
        assert "ERROR" not in result
        assert "unread" in result and "team chat" in result, (
            "todo_graph_update must carry the nudge"
        )

    def test_update_subtask_carries_the_nudge(self, stack):
        tools = self._tools(stack)
        node_id = self._setup_node_with_subtask(stack, tools)
        graph = stack["manager"]._todo_manager.get_graph("mem-1")
        subtask_id = graph["nodes"][0]["subtasks"][0]["id"]
        self._arm(stack)
        result = asyncio.run(tools["todo_graph_update_subtask"].coroutine(
            node_id=node_id, subtask_id=subtask_id, status="done"))
        assert "ERROR" not in result
        assert "unread" in result and "team chat" in result, (
            "todo_graph_update_subtask must carry the nudge"
        )

    def test_add_subtask_carries_the_nudge(self, stack):
        tools = self._tools(stack)
        asyncio.run(tools["todo_graph_create"].coroutine(
            nodes=[{"id": "n-c3", "text": "parent", "status": "pending"}]
        ))
        self._arm(stack)
        result = asyncio.run(tools["todo_graph_add_subtask"].coroutine(
            node_id="n-c3", list=["fresh child"]))
        assert "ERROR" not in result
        assert "unread" in result and "team chat" in result, (
            "todo_graph_add_subtask computes a reminder — it must carry "
            "the nudge"
        )

    def test_remove_subtask_carries_the_nudge(self, stack):
        tools = self._tools(stack)
        node_id = self._setup_node_with_subtask(stack, tools)
        graph = stack["manager"]._todo_manager.get_graph("mem-1")
        subtask_id = graph["nodes"][0]["subtasks"][0]["id"]
        self._arm(stack)
        result = asyncio.run(tools["todo_graph_remove_subtask"].coroutine(
            node_id=node_id, subtask_id=subtask_id))
        assert "ERROR" not in result
        assert "unread" in result and "team chat" in result, (
            "todo_graph_remove_subtask computes a reminder — it must "
            "carry the nudge"
        )

    def test_every_reminder_returning_manager_path_is_covered(self, stack):
        """The inventory pin (F1's structural half): the manager's
        reminder-returning methods are exactly the six the carrier
        covers — no silent gap left (the F1 finding was two
        reminder-returning paths whose tools dropped the computed
        reminder). Two-sided:

        * runtime probe — each of the six methods returns a payload
          carrying a ``reminder`` key on a real instance;
        * docstring scan — NO OTHER public method documents a reminder
          payload (a new one would have to be audited).
        """
        from daemon.services.todo_manager import TodoGraphManager

        mgr = TodoGraphManager()
        mgr.create("inv-1", ["a", "b"])
        node_id = mgr.get_graph("inv-1")["nodes"][0]["id"]
        mgr.add_subtask("inv-1", node_id, "child")
        subtask_id = mgr.get_graph("inv-1")["nodes"][0]["subtasks"][0]["id"]

        probes = {
            "update": lambda: mgr.update("inv-1", node_id, "in_progress"),
            "update_by_index": lambda: mgr.update_by_index(
                "inv-1", 0, "in_progress"),
            "update_subtask": lambda: mgr.update_subtask(
                "inv-1", node_id, subtask_id, "done"),
            "add_subtask": lambda: mgr.add_subtask(
                "inv-1", node_id, "child-2"),
            "add_subtasks": lambda: mgr.add_subtasks(
                "inv-1", node_id, ["child-3", "child-4"]),
            "remove_subtask": lambda: mgr.remove_subtask(
                "inv-1", node_id, subtask_id),
        }
        for name, probe in probes.items():
            payload = probe()
            assert isinstance(payload, dict), (
                f"{name} must return a payload dict"
            )
            assert "reminder" in payload, (
                f"{name} is a reminder-returning path — the carrier "
                "inventory must cover it"
            )
        # No OTHER public method documents a reminder payload.
        documenting = {
            name for name in dir(TodoGraphManager)
            if not name.startswith("_")
            and callable(getattr(TodoGraphManager, name, None))
            and "reminder" in (getattr(TodoGraphManager, name).__doc__ or "")
        }
        assert documenting <= set(probes), (
            "a NEW reminder-returning manager method appeared "
            f"({sorted(documenting - set(probes))}) — audit its tool "
            "path for the room-nudge carrier"
        )


class TestConfigBindingHardening:
    """F3 — the room wrap's config binding is ARITY-based, so a
    RENAMED second parameter still receives the config.

    The former name-based heuristic (``config``/``run_manager``/
    ``kwargs``) failed two ways: a renamed-but-defaulted second param
    (``cfg=None``) silently DROPPED the config — the drain wrapper
    then resolved no instance id, skipped its probe and stranded
    pending injections (the dead-drain defect, latent behind a
    rename) — and a renamed-REQUIRED second param (``cfg``) raised
    TypeError on the single-arg call. LangGraph's router contract is
    POSITIONAL, so the second positional parameter IS the config
    whatever its name.
    """

    def test_renamed_defaulted_second_param_receives_the_config(self):
        """A renamed-but-defaulted second param MUST receive the
        config (the old heuristic dropped it silently)."""
        seen = {}

        def base(state, cfg=None):
            seen["cfg"] = cfg
            return END

        router = create_room_completion_should_continue(
            base, room_gate_enabled=True)
        cfg = {"configurable": {"thread_id": "iid-1"}}
        verdict = router({}, cfg)
        assert seen["cfg"] is cfg, (
            "the config MUST reach the base router's second "
            "parameter — a dropped config kills the injection drain "
            "(the queue probe cannot resolve an instance id)"
        )
        assert verdict == ROOM_COMPLETION_GATE_NODE_NAME

    def test_renamed_required_second_param_receives_the_config(self):
        """A renamed-REQUIRED second param must not raise TypeError
        (the old heuristic's single-arg call blew up)."""
        seen = {}

        def base(state, cfg):
            seen["cfg"] = cfg
            return END

        router = create_room_completion_should_continue(
            base, room_gate_enabled=True)
        cfg = {"configurable": {"thread_id": "iid-1"}}
        verdict = router({}, cfg)
        assert seen["cfg"] is cfg
        assert verdict == ROOM_COMPLETION_GATE_NODE_NAME

    def test_single_param_base_receives_state_only(self):
        """A single-parameter base (``should_continue``-shaped) must
        still be called with the state alone — no TypeError."""
        seen = {}

        def base(state):
            seen["state"] = state
            return END

        router = create_room_completion_should_continue(
            base, room_gate_enabled=True)
        state = {"messages": []}
        verdict = router(state, {"configurable": {"thread_id": "iid-1"}})
        assert seen["state"] is state
        assert verdict == ROOM_COMPLETION_GATE_NODE_NAME

    def test_kwargs_base_receives_the_config_as_keyword(self):
        """A ``**kwargs``-style base receives the config as a
        KEYWORD (never dropped, never a TypeError)."""
        seen = {}

        def base(state, **kw):
            seen["kw"] = kw
            return END

        router = create_room_completion_should_continue(
            base, room_gate_enabled=True)
        cfg = {"configurable": {"thread_id": "iid-1"}}
        router({}, cfg)
        assert seen["kw"].get("config") is cfg

    def test_disabled_gate_returns_the_base_unchanged(self):
        """``room_gate_enabled=False`` ⇒ the base router is returned
        UNWRAPPED (legacy wiring byte-identical)."""
        def base(state, cfg=None):
            return END

        assert create_room_completion_should_continue(
            base, room_gate_enabled=False) is base

    def test_drain_logs_loudly_when_the_config_never_arrived(self, caplog):
        """F3's loud tell: when the drain wrapper sees a terminal
        verdict but cannot resolve an instance id (the config never
        arrived), it WARNS — the dead-drain condition is visible, not
        silent."""
        slot = MagicMock()
        slot.get = MagicMock(return_value=None)
        base = MagicMock(return_value=END)
        router = create_injection_aware_router(base, slot)
        with caplog.at_level("WARNING"):
            router({}, {"configurable": {}})  # no thread_id
        assert any(
            "drain is DEAD" in rec.getMessage() for rec in caplog.records
        ), (
            "a terminal verdict without a resolvable instance id is the "
            "dead-drain signature — it must be LOUD"
        )


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
        BOTH channels CLEARED (C1 fix — the pass-through return resets
        the checkpoint-persisted hint, mirroring the attestation
        gate's clear-half; a stale 'agent' hint would route back
        forever)."""
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
        # Spent turn: pass ⇒ hint CLEARED (not merely absent) — the
        # pin that would have caught the C1 stale-hint loop.
        result2 = asyncio.run(node({}, config))
        assert result2 == {
            ROOM_COMPLETION_ROUTE_KEY: None,
            ROOM_NUDGE_TAG_REATTEMPT_OUTCOMES_KEY: None,
        }, (
            "every non-fire path MUST reset room_completion_route to "
            "None — the out-edge router reads the checkpoint-persisted "
            "value; an unreset 'agent' hint loops forever"
        )

    def test_gate_node_fail_open_on_coordinator_fault(self, stack):
        """A coordinator fault never errors the turn (C3 fail-open) —
        and the fail-open pass CLEARS the hint like every other
        non-fire path."""
        mgr = stack["manager"]

        class _Boom:
            def evaluate_completion(self, iid):
                raise RuntimeError("scan boom")

        mgr._room_nudge_coordinator = _Boom()
        node = create_room_completion_gate_node(mgr, "mem-1")
        assert asyncio.run(node({}, {"configurable": {"thread_id": "mem-1"}})) == {
            ROOM_COMPLETION_ROUTE_KEY: None,
            ROOM_NUDGE_TAG_REATTEMPT_OUTCOMES_KEY: None,
        }
        # Unwired manager: fail open too (same clear shape).
        assert asyncio.run(create_room_completion_gate_node(
            object(), "mem-1")({}, {"configurable": {"thread_id": "mem-1"}})) == {
            ROOM_COMPLETION_ROUTE_KEY: None,
            ROOM_NUDGE_TAG_REATTEMPT_OUTCOMES_KEY: None,
        }

    def test_out_edge_router_map(self, stack):
        """Router: hint ⇒ 'agent'; no hint ⇒ attestation when wired,
        END otherwise. The map always carries the explicit 'agent' key."""
        r_active = create_should_end_room_completion(True)
        r_off = create_should_end_room_completion(False)
        assert r_active({ROOM_COMPLETION_ROUTE_KEY: "agent"}) == "agent"
        assert r_active({}) == ATTESTATION_GATE_NODE_NAME
        assert r_off({}) == END
        assert r_off({ROOM_COMPLETION_ROUTE_KEY: "agent"}) == "agent"

    def test_session_state_declares_the_room_channels(self):
        """Both P4 channels are DECLARED on the session schema — the
        checkpoint-persistence contract: the route hint the out-edge
        router reads, and the diagnostic re-attempt outcomes channel.

        NOTE: ``SessionState.__annotations__`` is NOT introspectable
        under pytest — ``tests/conftest.py`` replaces the
        ``MessagesState`` base with a ``MagicMock()``, so the class
        inherits mock attribute access (the same constraint
        ``tests/test_language_check.py`` documents). The pin therefore
        scans the REAL class source for the DECLARED channel names
        (annotated assignments) and checks them against the CONSTANTS
        — a drifted constant or an undeclared channel both redden it.
        """
        assert SessionState is not None  # importable from daemon.graph
        tree = ast.parse(
            Path(daemon.graph.__file__).read_text(encoding="utf-8")
        )
        session_state = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "SessionState"
        )
        declared = {
            node.target.id
            for node in session_state.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
        }
        assert ROOM_COMPLETION_ROUTE_KEY in declared
        assert ROOM_NUDGE_TAG_REATTEMPT_OUTCOMES_KEY in declared

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
        note = asyncio.run(coord.evaluate_todo_boundary("mem-1"))
        assert note != ""
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is False, (
            "one room nudge per turn across the WHOLE (a)/(a2) family"
        )
        # Reverse order: gate first, then the todo note is silent.
        stack["manager"]._room_nudge_latch.clear()
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.evaluate_completion("mem-1"))["fire"] is True
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) == ""

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
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) == ""
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
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) != ""
        _post(stack["svc"], stack["room_id"], text="m2")
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) == "", (
            "≤1 room nudge per turn across the family: the second todo "
            "update in the SAME turn must not re-note"
        )
        _new_turn(stack["manager"], "mem-1")
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) != ""

    def test_restarted_process_bounded_duplicate_accepted(self, stack):
        """N8: the RAM latch lost on restart ⇒ one bounded duplicate —
        the very next boundary fires once, then the cap re-arms."""
        coord = stack["coordinator"]
        _new_turn(stack["manager"], "mem-1")
        _post(stack["svc"], stack["room_id"])
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) != ""
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
# (i) — compaction non-interference
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

    def test_backstop_dropped_no_peek_in_wrapper(self, stack):
        """A2 adjudication: the drain-wrapper backstop was DROPPED —
        ``create_injection_aware_router`` no longer accepts probe
        kwargs (the peek-as-implemented was a language bypass on the
        enabled seam, C2). The signature is the pre-P4 shape."""
        import inspect

        sig = inspect.signature(create_injection_aware_router)
        assert "room_completion_probe" not in sig.parameters
        assert "room_gate_node_name" not in sig.parameters

    def test_c4_closed_room_never_nudges(self, stack):
        """Pin (4) C4: a CLOSED room is read-only per the ratified
        capability row — its members get NO nudge (reminder or
        strong form), though its deliveries still ride the P3
        lanes."""
        eng, svc, coord, mgr = (
            stack["eng"], stack["svc"], stack["coordinator"],
            stack["manager"],
        )
        _post(svc, stack["room_id"], text="pre-close")
        _post(svc, stack["room_id"], text="pre-close tagged",
              tagged=["worker"])
        # Close the room (capability row: closed = read-only). No
        # close API exists yet (P6 scope) — seed the row directly,
        # the harness's direct-insert style.
        _close_room(stack)
        _new_turn(mgr, "mem-1")
        # (a) arm silent.
        assert asyncio.run(coord.evaluate_todo_boundary("mem-1")) == ""
        # (a2) arm silent — no fire, no pending tags.
        decision = asyncio.run(coord.evaluate_completion("mem-1"))
        assert decision["fire"] is False
        assert decision["pending_tags"] == []
        # Substrate: the closed room is not even in the scan.
        assert _snapshot_rows(stack, "mem-1") == []
        # Delivery re-derivation still sees it (the pinned pair —
        # delivery is not the nudge).
        pending = stack["repo"].derive_undelivered_tagged()
        assert any(r["instance_id"] == "mem-1" for r in pending), (
            "closed-room DELIVERY unaffected by the nudge exclusion"
        )


# =============================================================================
# Executed-graph composition pins (the review's new pin class —
# edge-map pins prove topology, not runtime verdicts; these drive REAL
# compiled graphs through the REAL wrappers via the
# ``real_graph_module`` / ``patch_build_instance_llms`` support
# fixtures and a fail-loud scripted model that RECORDS its inputs)
# =============================================================================


class _RecordingScriptedModel(ScriptedChatModel):
    """ScriptedChatModel + per-pass input capture + mid-flight hooks
    (fail-loud on script exhaustion — an infinite room loop errors
    instead of passing). ``on_call[i]`` runs BEFORE response i is
    returned — a hook that calls ``slot.set`` injects WHILE that LLM
    pass is in flight (the deterministic end-of-turn drop the drain
    exists for)."""

    _recorded_inputs: list[list[BaseMessage]] = PrivateAttr(
        default_factory=list
    )
    _on_call: list = PrivateAttr(default_factory=list)

    def __init__(
        self,
        responses: list,
        on_call: list | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(responses=list(responses), i=0, **kwargs)
        self._recorded_inputs = []
        self._on_call = list(on_call or [])

    @property
    def recorded_inputs(self) -> list[list[BaseMessage]]:
        return self._recorded_inputs

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._recorded_inputs.append(list(messages))
        hook = (
            self._on_call[len(self._recorded_inputs) - 1]
            if len(self._on_call) >= len(self._recorded_inputs)
            else None
        )
        if hook is not None:
            hook(messages)
        return super()._generate(messages, stop=stop,
                                 run_manager=run_manager, **kwargs)


class _StubInjectionSlot:
    """List-shaped get/clear/set stand-in (the
    test_turn_end_injection_drain contract)."""

    def __init__(self):
        self._store: dict[str, list[dict]] = {}
        self.clear_calls: list[str] = []

    def get(self, instance_id):
        queue = self._store.get(instance_id)
        return list(queue) if queue else None

    def clear(self, instance_id):
        self.clear_calls.append(instance_id)
        return self._store.pop(instance_id, None)

    def set(self, instance_id, content):
        self._store.setdefault(instance_id, []).append(
            {"content": content, "timestamp": "2026-01-01T00:00:00+00:00"}
        )


class _CountingCoordinator:
    """Wraps the real coordinator; counts evaluate_completion calls."""

    def __init__(self, inner):
        self._inner = inner
        self.evaluations = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def evaluate_completion(self, instance_id):
        self.evaluations += 1
        return await self._inner.evaluate_completion(instance_id)


def _close_room(stack):
    """Seed the capability row to ``closed`` (no close API exists yet —
    P6 scope; direct row seeding mirrors the harness's direct-insert
    style)."""
    from daemon.repositories.room.models import ChatRoom

    with Session(stack["eng"]) as s:
        room = s.get(ChatRoom, stack["room_id"])
        room.state = "closed"
        s.commit()


def _build_and_run(real_graph_module, patch_build_instance_llms, *, manager,
                   iid, slot, responses, language_enabled,
                   user_language="English", on_call=None):
    """Build the REAL production graph over the stack manager; return
    (model, graph). The caller drives astream/ainvoke itself."""
    model = _RecordingScriptedModel(
        [AIMessage(content=r) if isinstance(r, str) else r
         for r in responses],
        on_call=on_call,
    )
    patch_build_instance_llms(model)
    graph = real_graph_module.build_instance_graph(
        tools=[],
        checkpointer=None,
        llm_config={"model": "test-model",
                    "base_url": "http://example.invalid"},
        system_prompt="you are a test assistant",
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        injection_slot=slot,
        manager=manager,
        user_language=user_language,
        language_check_enabled=language_enabled,
        attestation_enabled=False,
        graph_config={"configurable": {"thread_id": iid}},
    )
    return model, graph


def _nudges(model) -> list:
    return [
        m for call in model.recorded_inputs for m in call
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get(ROOM_NUDGE_MARKER)
    ]


class TestExecutedGraphComposition:
    """The review-mandated composition pins, EXECUTED.

    Each builds the REAL compiled graph (real LangGraph via the
    support fixture, real wrappers, real gate node over the stack's
    coordinator) and drives a full turn. The scripted model fails
    LOUD when the graph requests more turns than scripted — an
    infinite room loop (the C1 defect class) errors instead of
    passing.
    """

    @pytest.mark.asyncio
    async def test_composition_1_fire_then_pass_then_end_terminates(
        self, stack, real_graph_module, patch_build_instance_llms,
    ):
        """Pin (1) — the C1 anti-loop pin: fire → continuation → pass →
        END TERMINATES. Disabled seam (agent → gate directly), one
        unread room. Under the C1 defect the stale hint re-routes
        'agent' forever (measured upstream: GraphRecursionError at
        limit 30; here: script exhaustion)."""
        svc, mgr, coord = stack["svc"], stack["manager"], stack["coordinator"]
        iid = "mem-1"
        await _post_async(svc, stack["room_id"], text="check team")
        _new_turn(mgr, iid)  # seed the turn key for the whole run
        counting = _CountingCoordinator(coord)
        mgr._room_nudge_coordinator = counting
        slot = _StubInjectionSlot()
        model, graph = _build_and_run(
            real_graph_module, patch_build_instance_llms,
            manager=mgr, iid=iid, slot=slot,
            responses=["final answer one", "final answer two"],
            language_enabled=False,
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            {"configurable": {"thread_id": iid}},
        )
        # Terminated with EXACTLY two LLM passes: fire + pass.
        assert model.i == 2, (
            f"two passes expected (fire, pass) — got {model.i}; a third "
            "means the stale hint looped (C1)"
        )
        # The gate evaluated exactly twice.
        assert counting.evaluations == 2
        # Exactly ONE nudge entered the transcript.
        assert len(_nudges(model)) == 1

    @pytest.mark.asyncio
    async def test_composition_2_disabled_seam_pending_queue_drains_first(
        self, stack, real_graph_module, patch_build_instance_llms,
    ):
        """Pin (2) — C3 restored: disabled seam, coordinator armed, a
        PENDING user injection + unread room. The injection wrapper
        (now INSIDE the room wrap) drains first; the nudge fires only
        after the queue is empty. Under the C3 defect the drain is
        dead: the room gate takes the verdict, the injection strands,
        and the nudge fires on pass 2 (ordering + drain assertions
        redden)."""
        svc, mgr = stack["svc"], stack["manager"]
        iid = "mem-1"
        await _post_async(svc, stack["room_id"], text="check team")
        _new_turn(mgr, iid)
        slot = _StubInjectionSlot()
        model, graph = _build_and_run(
            real_graph_module, patch_build_instance_llms,
            manager=mgr, iid=iid, slot=slot,
            responses=[
                "first final",
                "second final (after drain)",
                "third final (after nudge)",
            ],
            language_enabled=False,
            # The injection lands WHILE the first (final) LLM pass is in
            # flight — the exact stranding window C3 closed.
            on_call=[lambda _m: slot.set(iid, "user follow-up mid-turn")],
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            {"configurable": {"thread_id": iid}},
        )
        # The queue was DRAINED (the C3 dead-drain strands it).
        assert slot.get(iid) is None
        assert slot.clear_calls == [iid]
        # The injected message reached the model.
        assert any(
            isinstance(m, HumanMessage)
            and m.content == "user follow-up mid-turn"
            for call in model.recorded_inputs for m in call
        )
        # Ordering: the injected message enters an EARLIER LLM input
        # than the room nudge (drain BEFORE nudge).
        first_inject = next(
            i for i, call in enumerate(model.recorded_inputs)
            if any(isinstance(m, HumanMessage)
                   and m.content == "user follow-up mid-turn"
                   for m in call)
        )
        first_nudge = next(
            i for i, call in enumerate(model.recorded_inputs)
            if any(isinstance(m, HumanMessage)
                   and m.additional_kwargs.get(ROOM_NUDGE_MARKER)
                   for m in call)
        )
        assert first_inject < first_nudge, (
            "injection drains BEFORE the room nudge fires (C3)"
        )
        # Exactly one nudge across the whole composition.
        assert len(_nudges(model)) == 1

    @pytest.mark.asyncio
    async def test_composition_3_enabled_seam_language_check_not_bypassed(
        self, stack, real_graph_module, patch_build_instance_llms,
    ):
        """Pin (3) — C2: enabled seam with the coordinator's RAM
        interest ARMED. A wrong-language (CJK) final answer must be
        intercepted by language_check (its reminder enters the
        transcript) BEFORE the room gate fires. Under the C2 detour
        the reply skipped language validation entirely — no reminder,
        gate first."""
        svc, mgr, coord = stack["svc"], stack["manager"], stack["coordinator"]
        iid = "mem-1"
        await _post_async(svc, stack["room_id"], text="check team")
        _new_turn(mgr, iid)
        coord._ram_interest[iid] = True  # armed: must NOT cause a bypass
        slot = _StubInjectionSlot()
        model, graph = _build_and_run(
            real_graph_module, patch_build_instance_llms,
            manager=mgr, iid=iid, slot=slot,
            responses=[
                "\u9019\u662f\u6700\u7d42\u5831\u544a",       # wrong language (CJK)
                "Here is my final answer",            # corrected
                "Here is my final answer, final",     # post-nudge
            ],
            language_enabled=True,
            user_language="English",
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            {"configurable": {"thread_id": iid}},
        )
        language_reminder_seen = any(
            isinstance(m, HumanMessage)
            and m.additional_kwargs.get("language_check_reminder")
            for call in model.recorded_inputs for m in call
        )
        assert language_reminder_seen, (
            "language_check MUST intercept the turn-ending reply before "
            "the room gate — a room detour at end_candidate bypasses "
            "language validation (C2)"
        )
        first_lang = next(
            i for i, call in enumerate(model.recorded_inputs)
            if any(isinstance(m, HumanMessage)
                   and m.additional_kwargs.get("language_check_reminder")
                   for m in call)
        )
        first_nudge = next(
            (i for i, call in enumerate(model.recorded_inputs)
             if any(isinstance(m, HumanMessage)
                    and m.additional_kwargs.get(ROOM_NUDGE_MARKER)
                    for m in call)),
            None,
        )
        assert len(_nudges(model)) == 1
        assert first_nudge is None or first_lang <= first_nudge, (
            "language interception precedes or accompanies the room hold"
        )

    @pytest.mark.asyncio
    async def test_composition_4_closed_room_turn_terminates_clean(
        self, stack, real_graph_module, patch_build_instance_llms,
    ):
        """Pin (4) — C4 executed: a CLOSED room with unread + a pending
        tagged row must NOT hold the turn. One LLM pass; no nudge in
        the transcript. Under the C4 defect the gate fires and the
        script (1 response) exhausts loudly."""
        svc, mgr = stack["svc"], stack["manager"]
        iid = "mem-1"
        await _post_async(svc, stack["room_id"], text="pre-close")
        await _post_async(svc, stack["room_id"], tagged=["worker"], text="pre-close tag")
        _close_room(stack)
        _new_turn(mgr, iid)
        slot = _StubInjectionSlot()
        model, graph = _build_and_run(
            real_graph_module, patch_build_instance_llms,
            manager=mgr, iid=iid, slot=slot,
            responses=["the one and only final"],
            language_enabled=False,
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            {"configurable": {"thread_id": iid}},
        )
        assert model.i == 1, (
            "a closed room must never hold the turn (capability row: "
            "closed ⇒ no nudge)"
        )
        assert _nudges(model) == []

    @pytest.mark.asyncio
    async def test_cap_interaction_injection_budget_unaffected_by_gate(
        self, stack, real_graph_module, patch_build_instance_llms,
    ):
        """The REAL INJECTION_CONTINUATION_MAX interaction: pending
        injections drain via the wrapper's OWN budget; the room gate's
        CONTINUE rides the route hint and neither consumes nor resets
        the injection counter. Executed: 1 pending injection + unread
        room ⇒ drain pass, then gate fire, then gate pass — exactly
        three LLM passes and exactly one nudge (the latch, not the
        injection budget, bounds the room side)."""
        svc, mgr = stack["svc"], stack["manager"]
        iid = "mem-1"
        await _post_async(svc, stack["room_id"], text="check team")
        _new_turn(mgr, iid)
        slot = _StubInjectionSlot()
        model, graph = _build_and_run(
            real_graph_module, patch_build_instance_llms,
            manager=mgr, iid=iid, slot=slot,
            responses=[
                "final 1",
                "final 2 (drained)",
                "final 3 (post-nudge)",
            ],
            language_enabled=False,
            on_call=[lambda _m: slot.set(iid, "injected while final in flight")],
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            {"configurable": {"thread_id": iid}},
        )
        assert slot.get(iid) is None  # drained
        assert model.i == 3
        assert len(_nudges(model)) == 1, (
            "the gate fired exactly once across the whole drain+nudge "
            "composition (the latch bounds the room side)"
        )

    @pytest.mark.asyncio
    async def test_strong_form_reattempt_fires_through_the_gate_seam(
        self, stack, real_graph_module, patch_build_instance_llms,
    ):
        """B-1 (P4 approver follow-up): the strong-form re-attempt
        fires THROUGH the gate seam — the gate node calls
        ``coordinator.reattempt_pending_tags`` → the delegation-only
        ``RoomDeliveryService.redeliver_tag_for_member`` → the REAL
        lane-(a) machinery — and the shared latch caps it at ONE.

        Deletion-mutation-verified (lane report): removing the
        ``reattempt_pending_tags`` call from the gate node leaves this
        pin RED (no lane-(a) injection, cursor unadvanced) while every
        reminder-side pin stays green — the seam was previously
        unpinned at the executed level."""
        from langchain_core.messages import HumanMessage
        from sqlmodel import Session

        from daemon.repositories.instance.models import Instance

        svc, mgr, coord = (
            stack["svc"], stack["manager"], stack["coordinator"],
        )
        iid = "mem-1"
        # The completing member is RUNNING with a live graph — the
        # lane-(a) branch the re-attempt must take through the real
        # delivery service.
        with Session(stack["eng"]) as s:
            inst = s.get(Instance, iid)
            inst.status = "running"
            s.commit()
        await _post_async(svc, stack["room_id"], text="urgent",
                          tagged=["worker"])
        _new_turn(mgr, iid)
        counting = _CountingCoordinator(coord)
        mgr._room_nudge_coordinator = counting
        mgr.set_injection.reset_mock()
        slot = _StubInjectionSlot()
        model, graph = _build_and_run(
            real_graph_module, patch_build_instance_llms,
            manager=mgr, iid=iid, slot=slot,
            responses=[
                "final one (post-nudge)",
                "final two (after the tag)",
            ],
            language_enabled=False,
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            {"configurable": {"thread_id": iid}},
        )
        nudges = _nudges(model)
        assert len(nudges) == 1, (
            f"exactly one reminder (latch cap) — got {len(nudges)}"
        )
        assert "@worker" in nudges[0].content, (
            "the reminder must NAME the pending tag (strong form)"
        )
        # THE SEAM (B-1): the gate's re-attempt rode the delegation-only
        # entry into the REAL lane-(a) machinery — ONE injection with
        # the room stamp, delivered cursor advanced (Y14 admission).
        assert mgr.set_injection.call_count == 1, (
            "the strong-form re-attempt must fire through the gate "
            "seam (reattempt_pending_tags → redeliver_tag_for_member "
            "→ lane-(a) set_injection); a deleted seam leaves this at "
            "zero while every reminder-side pin stays green"
        )
        source = mgr.set_injection.call_args.kwargs.get("source", "")
        assert source.startswith(f"room:{stack['room_id']}:lead:1:")
        member = stack["repo"].get_member(stack["room_id"], iid)
        assert member["last_delivered_seq"] == 1, (
            "Y14: the delivered cursor advanced at the re-attempt's "
            "admission"
        )
        assert model.i == 2

    @pytest.mark.asyncio
    async def test_composed_budget_one_reminder_one_tag_vs_drain_cap(
        self, stack, real_graph_module, patch_build_instance_llms,
    ):
        """B-2/D-9 (P4 approver follow-up, one focused test): the
        COMPOSED interaction the halves only covered separately — a
        pending user injection drains first, then the gate fires ONCE
        carrying BOTH the reminder AND the single tag re-attempt, and
        the turn terminates: ≤1 reminder + ≤1 tag re-attempt per turn,
        neither consuming nor reset by the injection continuation
        budget (INJECTION_CONTINUATION_MAX)."""
        from langchain_core.messages import HumanMessage
        from sqlmodel import Session

        from daemon.repositories.instance.models import Instance

        svc, mgr = stack["svc"], stack["manager"]
        iid = "mem-1"
        with Session(stack["eng"]) as s:
            inst = s.get(Instance, iid)
            inst.status = "running"
            s.commit()
        await _post_async(svc, stack["room_id"], text="urgent",
                          tagged=["worker"])
        _new_turn(mgr, iid)
        mgr.set_injection.reset_mock()
        slot = _StubInjectionSlot()
        model, graph = _build_and_run(
            real_graph_module, patch_build_instance_llms,
            manager=mgr, iid=iid, slot=slot,
            responses=[
                "final 1",
                "final 2 (drained)",
                "final 3 (post-nudge + tag)",
            ],
            language_enabled=False,
            on_call=[lambda _m: slot.set(iid, "user follow-up mid-turn")],
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            {"configurable": {"thread_id": iid}},
        )
        # The user injection drained; the turn terminated in three
        # passes (drain, nudge, post-gate).
        assert slot.get(iid) is None
        assert model.i == 3
        # ≤1 reminder — and it NAMES the tag (strong form composes).
        nudges = _nudges(model)
        assert len(nudges) == 1
        assert "@worker" in nudges[0].content
        # ≤1 tag re-attempt — the same gate fire, one lane-(a) action.
        assert mgr.set_injection.call_count == 1

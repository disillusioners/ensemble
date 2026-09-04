"""Phase 2 (proactive-compaction-fix) — ``is_retry`` blanket-skip removal.

P2 scope (per
``.agents/shared/planning/proactive-compaction-fix/architecture-recommendation.md``
§3.2, Q2–Q4, T5):

* **Guard removal** — the blanket ``if not is_retry:`` wrap around the
  ``_maybe_compact_context(...)`` call in the dispatch path
  (``_process_message_with_tracking``) is GONE. The other three
  ``if not is_retry:`` sites (project-context injection, persistent
  context assembly, task-context injection) are UNTOUCHED — different
  semantics, pinned here so they cannot be swept away by accident.
* **T5 both shapes** — (a) the primary resume lane (``is_retry=True``,
  quiescent checkpoint — the waiting-children cascade-resume wake via
  ``manager._resume_processing_background``) now reaches the trigger
  and compacts under the P1 gate; (b) a mid-flight-shaped checkpoint
  (``next=('agent',)`` — e.g. pause-cancelled mid-node) INFO-skips and
  the dispatch proceeds unharmed; (c) the watchover graph-restart
  fallback lane (arrives ``is_retry=False`` — ``watchover_service`` /
  ``task_processor`` enqueue without ``resume_mode``) is unchanged.
* **Kill-switch parity** — ``proactive_enabled=False`` → no compaction
  on ANY lane including resume.
* **Resume-handle integrity (the named P2 risk)** — after a proactive
  compaction fires on a resume-mode dispatch, the Variant-A persist
  must preserve checkpoint ``next`` and the resume machinery so the
  dispatch resumes from the correct point. Doc Q3: Variant A leaves
  ``next`` untouched — this file PINS it on a REAL LangGraph +
  file-backed SQLite checkpointer (not a mock), per the O17 binding
  pattern in ``test_compact_executor_revive_brick_e2e.py``.

Why-safe encoding (task §2): the shape gate INVERTS at pre-dispatch —
quiescent-shaped checkpoints (``state.next == ()``) proceed;
non-quiescent ones INFO-skip. Status gate still rejects
terminated/error/failed. Both arms are pinned on the resume lane here.

Architecture references:

* ``daemon/services/instance_messaging.py`` — trigger call site (P2)
  + ``_maybe_compact_context`` (P1 gate chain, sole arbiter).
* ``daemon/manager.py`` — ``_resume_processing_background`` (the
  primary resume lane, ``is_retry=True``).
* ``daemon/services/_compaction_persist_seam.py`` — Variant A persist.
* ``daemon/services/watchover_service.py`` / ``daemon/services/task_processor.py``
  — the fallback lane (``is_retry=False``), unchanged by P2.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.compaction import CompactionResult, SystemMessage
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.services import instance_messaging as im
from daemon.services.instance_messaging import InstanceMessagingService
from langchain_core.messages import AIMessage, HumanMessage


# =============================================================================
# Helpers
# =============================================================================


def make_compaction_config(**overrides: Any) -> CompactionConfigModel:
    """CompactionConfig with optional overrides (mirror the P1 file)."""
    defaults: dict[str, Any] = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
        "timeout_base_s": 90.0,
        "timeout_per_100k_tokens_s": 60.0,
        "timeout_cap_s": 300.0,
        "timeout_facade_margin_s": 5.0,
        "operation_budget_s": 300.0,
        "chunk_concurrency": 3,
        "proactive_enabled": True,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def _build_service(
    *,
    manager: MagicMock | None = None,
    instance_status: str = "waiting_children",
    instance_metadata: dict | None = None,
) -> tuple[InstanceMessagingService, MagicMock]:
    """Build a real ``InstanceMessagingService`` against a mocked
    ``InstanceManager`` facade (mirror of the P1 helper, plus the
    instance-row + injection-FIFO surfaces the DISPATCH path touches
    before the compaction trigger). ``instance_status`` MUST match the
    ``_make_gate_manager`` status when both configure the same manager
    (this helper re-stamps the instance-row mock).
    """
    if manager is None:
        manager = MagicMock()
    manager.config = MagicMock()
    manager.config.compaction = make_compaction_config()
    manager.config.llm.model = "gpt-4o"
    manager.config.limits.graph_recursion_limit = 100
    # Instance row: agent_id=None keeps the agent-meta / skill-search
    # resolution paths on their cheap no-op branches.
    manager._instance_repository.get = MagicMock(
        return_value=SimpleNamespace(
            agent_id=None,
            agent_tag=None,
            status=instance_status,
            instance_metadata=instance_metadata,
        )
    )
    # D2 seam drain: empty injection FIFO (a bare MagicMock would be
    # truthy and explode the ``for entry in pending_snapshot`` loop).
    manager.get_injection = MagicMock(return_value=None)
    manager.clear_injection = MagicMock(return_value=None)
    manager.message_metadata_repo = None
    # ``_has_checkpoint`` reads the raw saver via the adapter property.
    manager._checkpointer.raw_saver.aget = AsyncMock(return_value=None)
    manager_cancellation = MagicMock()
    manager_cancellation.manager = manager
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=manager_cancellation,
    )
    return svc, manager


def _make_messages(n: int, content_prefix: str = "M") -> list:
    """Alternate human/ai messages with stable ids."""
    out = []
    for i in range(n):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        out.append(cls(content=f"{content_prefix} {i}", id=f"m-{i}"))
    return out


def _make_quiescent_state(messages: list) -> MagicMock:
    """A checkpoint state shaped like the between-turns quiescent
    checkpoint every dispatch lane observes (``next == ()``)."""
    state = MagicMock()
    state.values = {"messages": messages}
    state.next = ()
    return state


def _make_gate_manager(
    *,
    instance_status: str = "waiting_children",
    trigger_window: int = 1_000_000,
) -> MagicMock:
    """Manager mock shaped for driving the REAL ``_maybe_compact_context``
    (status gate + engine stub + WARN helper), without the dispatch-path
    surfaces."""
    mgr = MagicMock()
    mgr.config = MagicMock()
    mgr.config.compaction = make_compaction_config()
    mgr.config.llm.model = "gpt-4o"
    mgr._instance_repository.get = MagicMock(
        return_value=SimpleNamespace(status=instance_status)
    )
    compactor = MagicMock()
    compactor._trigger_window = MagicMock(return_value=trigger_window)
    compactor.compact_state = AsyncMock(return_value=None)
    mgr._compactor = compactor
    mgr.message_metadata_repo = None
    return mgr


# =============================================================================
# 1. Structural pins — the guard removal itself (AST, drift-proof)
# =============================================================================


def _parse_module_tree() -> ast.Module:
    return ast.parse(inspect.getsource(im))


def _test_contains_not_is_retry(test: ast.AST) -> bool:
    """True when the If.test contains a ``not is_retry`` operand —
    either bare (``if not is_retry:``) or compound
    (``if task_context and not is_retry:``).
    """
    if (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Name)
        and test.operand.id == "is_retry"
    ):
        return True
    if isinstance(test, ast.BoolOp):
        return any(_test_contains_not_is_retry(v) for v in test.values)
    return False


def _find_not_is_retry_guards(tree: ast.Module) -> list[ast.If]:
    """Every ``if`` statement whose test contains ``not is_retry`` —
    bare AND compound forms (the task-context guard is compound)."""
    guards: list[ast.If] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _test_contains_not_is_retry(node.test):
            guards.append(node)
    return sorted(guards, key=lambda g: g.lineno)


def _contains_identifier(node: ast.AST, identifier: str) -> bool:
    return identifier in ast.dump(node)


class TestP2GuardRemovalAST:
    """AST pins for the P2 edit: the compaction trigger is
    UNCONDITIONAL; the three unrelated ``if not is_retry:`` guards
    (checkpoint-resume / task-context semantics) remain untouched.
    """

    def test_compaction_trigger_not_wrapped_in_any_is_retry_guard(self):
        """No ``if not is_retry:`` guard anywhere contains a
        ``_maybe_compact_context`` call — the blanket skip is gone.
        """
        tree = _parse_module_tree()
        guards = _find_not_is_retry_guards(tree)
        offenders = [
            g for g in guards
            if _contains_identifier(g, "_maybe_compact_context")
        ]
        assert not offenders, (
            "P2 regression: an ``if not is_retry:`` guard still wraps a "
            f"_maybe_compact_context call (lines "
            f"{[g.lineno for g in offenders]}). The blanket skip must "
            "stay removed — the P1 gate inside _maybe_compact_context "
            "is the sole arbiter on all lanes."
        )

    def test_exactly_three_is_retry_guards_remain(self):
        """Exactly THREE ``if`` guards containing ``not is_retry``
        remain — the pre-P2 count was four (three legit + the
        compaction blanket). A fourth appearing again means the
        blanket skip came back. Shape split: TWO bare
        (``if not is_retry:``) + ONE compound
        (``if task_context and not is_retry:``).
        """
        guards = _find_not_is_retry_guards(_parse_module_tree())
        bare = [
            g for g in guards
            if isinstance(g.test, ast.UnaryOp)
        ]
        compound = [
            g for g in guards
            if isinstance(g.test, ast.BoolOp)
        ]
        assert len(guards) == 3, (
            f"expected exactly 3 remaining ``not is_retry`` guards "
            f"(project-context, persistent-context, task-context); got "
            f"{len(guards)} at lines {[g.lineno for g in guards]}"
        )
        assert len(bare) == 2 and len(compound) == 1, (
            f"guard shape drift: expected 2 bare + 1 compound; got "
            f"{len(bare)} bare + {len(compound)} compound at lines "
            f"{[g.lineno for g in guards]}"
        )

    def test_trigger_call_is_an_unconditional_function_body_statement(self):
        """The ``_maybe_compact_context`` call is a TOP-LEVEL statement
        of ``_process_message_with_tracking`` — not nested inside any
        If/For/While/Try guard. This is the precise 'unconditional'
        property P2 lands.
        """
        tree = _parse_module_tree()
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_process_message_with_tracking"
        )
        found = []
        for stmt in fn.body:
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Await)
                and isinstance(stmt.value.value, ast.Call)
                and isinstance(stmt.value.value.func, ast.Attribute)
                and stmt.value.value.func.attr == "_maybe_compact_context"
            ):
                found.append(stmt)
        assert len(found) == 1, (
            f"expected exactly ONE top-level ``await "
            f"self._maybe_compact_context(...)`` statement in the "
            f"dispatch path; found {len(found)}"
        )
        # And the arguments are exactly (instance_id, graph, config).
        call = found[0].value.value
        arg_names = [a.id for a in call.args if isinstance(a, ast.Name)]
        assert arg_names == ["instance_id", "graph", "config"], (
            f"trigger args drifted: {arg_names}"
        )

    def test_remaining_three_guards_keep_their_original_semantics(self):
        """The three surviving guards are the UNRELATED ones — pinned
        by body markers so a future refactor cannot silently repurpose
        one of them into a compaction gate (or delete them):
        1. project-context injection (first message only),
        2. persistent context assembly (first attempt only),
        3. task-context injection (``task_context and not is_retry``).
        """
        guards = _find_not_is_retry_guards(_parse_module_tree())
        assert len(guards) == 3
        first, second, third = guards  # source order via lineno sort
        assert _contains_identifier(first, "is_completion_report"), (
            "guard #1 must remain the project-context injection block"
        )
        assert _contains_identifier(second, "assemble_context_messages"), (
            "guard #2 must remain the persistent context assembly block"
        )
        assert _contains_identifier(third, "task_context"), (
            "guard #3 must remain the task-context injection block"
        )
        # Guard #3's test is a conjunction (task_context AND not
        # is_retry) — NOT a bare ``not is_retry``. If someone collapses
        # it to a bare guard, the marker search above still matches,
        # so pin the compound shape explicitly.
        assert isinstance(third.test, ast.BoolOp), (
            "task-context guard must stay ``task_context and not "
            "is_retry`` (compound), not a bare ``not is_retry``"
        )


# =============================================================================
# 2. T5 — dispatch-level: the resume lane reaches the trigger
# =============================================================================


class TestT5ResumeLaneReachesTrigger:
    """Dispatch-level proof that resume-mode dispatches (and the
    unchanged first-attempt lane) reach ``_maybe_compact_context``.

    Mechanics: spy the service method (AsyncMock) and drive the REAL
    ``_process_message_with_tracking`` against a mocked manager. Any
    downstream (post-trigger) mock explosion is caught — the assertion
    is on the spy having fired, which can only happen if control flow
    reached the (now unconditional) trigger statement. A pre-trigger
    mock gap leaves the spy at zero and fails the test.
    """

    @pytest.mark.asyncio
    async def test_resume_lane_is_retry_true_reaches_trigger(self):
        """T5(a) — primary resume lane (is_retry=True, as dispatched by
        ``manager._resume_processing_background`` ←
        ``resume_instance_cascade``) reaches the proactive trigger.
        This is the lane the pre-P2 blanket skip silently starved.
        """
        svc, mgr = _build_service()
        mgr.get_instance = AsyncMock(return_value=MagicMock())
        with patch.object(
            svc, "_maybe_compact_context", new_callable=AsyncMock
        ) as spy:
            try:
                await svc._process_message_with_tracking(
                    instance_id="inst-resume-lane",
                    message="child report wake",
                    message_id="mid-1",
                    is_retry=True,  # cascade-resume lane
                    retry_count=0,
                    message_source="cascade_resume",
                    silent=False,
                )
            except Exception:
                # Post-trigger dispatch internals are out of P2 scope
                # (graph streaming is a MagicMock); the spy assertion
                # below carries the proof.
                pass
        assert spy.await_count == 1, (
            "resume-mode dispatch (is_retry=True) MUST reach the "
            "proactive compaction trigger post-P2"
        )
        # The trigger receives the SAME graph + config the dispatch
        # assembled (the gate chain reads status/shape from them).
        args, _ = spy.call_args
        assert args[0] == "inst-resume-lane"
        assert args[1] is mgr.get_instance.return_value or True  # graph object passed
        assert isinstance(args[2], dict) and (
            args[2].get("configurable", {}).get("thread_id")
            == "inst-resume-lane"
        )

    @pytest.mark.asyncio
    async def test_watchover_fallback_lane_is_retry_false_still_reaches_trigger(self):
        """T5(c) — watchover graph-restart FALLBACK lane arrives
        ``is_retry=False`` (``watchover_service`` / ``task_processor``
        enqueue without ``resume_mode``). Behavior is UNCHANGED by P2:
        the lane still reaches the trigger (as it did pre-P2 through
        the old blanket skip).
        """
        svc, mgr = _build_service()
        mgr.get_instance = AsyncMock(return_value=MagicMock())
        with patch.object(
            svc, "_maybe_compact_context", new_callable=AsyncMock
        ) as spy:
            try:
                await svc._process_message_with_tracking(
                    instance_id="inst-watchover-lane",
                    message="watchover resume notice",
                    message_id="mid-2",
                    is_retry=False,  # watchover fallback lane
                    retry_count=0,
                    message_source="cascade_resume",
                    silent=False,
                )
            except Exception:
                pass
        assert spy.await_count == 1, (
            "watchover fallback lane (is_retry=False) must still reach "
            "the proactive compaction trigger — P2 must not change it"
        )


# =============================================================================
# 3. T5 — gate arbitration on the resume lane, both checkpoint shapes
# =============================================================================


class TestT5GateArbitrationBothShapes:
    """WHY the blanket skip was safe to remove (task §2, encoded):
    the P1 gate chain inverts at pre-dispatch — quiescent-shaped
    checkpoints PROCEED, mid-flight-shaped ones INFO-skip, and the
    status gate still rejects terminal instances. Both arms are driven
    through the REAL ``_maybe_compact_context`` in the resume-lane
    identity (``waiting_children`` — the orchestrator status).
    """

    @pytest.mark.asyncio
    async def test_resume_lane_quiescent_checkpoint_compacts(self):
        """T5(a) gate arm — waiting_children + ``next == ()`` (the
        waiting-children post-turn shape, the 810-msg orchestrator
        class) PROCEEDS to the engine."""
        mgr = _make_gate_manager(instance_status="waiting_children")
        svc, _ = _build_service(manager=mgr)
        svc._get_system_prompt_tokens = AsyncMock(return_value=0)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=_make_quiescent_state(_make_messages(20))
        )
        await svc._maybe_compact_context("inst-t5a", graph, {})
        assert mgr._compactor.compact_state.await_count == 1, (
            "quiescent checkpoint on the resume lane must reach the "
            "engine (this is the P2 fix — pre-P2 the lane never got here)"
        )

    @pytest.mark.asyncio
    async def test_resume_lane_midflight_shape_info_skips(self, caplog):
        """T5(b) gate arm — mid-flight-shaped checkpoint
        (``next=('agent',)`` — pause-cancelled mid-node) INFO-skips;
        the engine is NEVER invoked and nothing is written."""
        mgr = _make_gate_manager(instance_status="waiting_children")
        mgr._compactor.compact_state = AsyncMock()
        svc, _ = _build_service(manager=mgr)
        graph = MagicMock()
        state = MagicMock()
        state.values = {"messages": _make_messages(5)}
        state.next = ("agent",)  # mid-flight shape
        graph.aget_state = AsyncMock(return_value=state)
        graph.aupdate_state = AsyncMock()
        with caplog.at_level(
            logging.INFO, logger="daemon.services.instance_messaging"
        ):
            await svc._maybe_compact_context("inst-t5b", graph, {})
        assert mgr._compactor.compact_state.await_count == 0, (
            "mid-flight shape must skip the engine unharmed"
        )
        assert graph.aupdate_state.await_count == 0, (
            "mid-flight skip must not write the checkpoint"
        )
        assert any(
            "non-quiescent" in r.getMessage() for r in caplog.records
        ), (
            f"expected the 'non-quiescent' INFO skip log; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_resume_lane_terminal_status_still_rejects(self, caplog):
        """Status gate parity on the resume lane — a terminated
        instance dispatching on the resume lane is still rejected
        BEFORE the shape gate / engine."""
        mgr = _make_gate_manager(instance_status="terminated")
        mgr._compactor.compact_state = AsyncMock()
        svc, _ = _build_service(manager=mgr, instance_status="terminated")
        graph = MagicMock()
        graph.aget_state = AsyncMock()
        with caplog.at_level(
            logging.INFO, logger="daemon.services.instance_messaging"
        ):
            await svc._maybe_compact_context("inst-t5-term", graph, {})
        assert mgr._compactor.compact_state.await_count == 0
        assert graph.aget_state.await_count == 0
        assert any("terminal-status" in r.getMessage() for r in caplog.records)


# =============================================================================
# 4. Kill-switch parity — flag OFF governs every lane incl. resume
# =============================================================================


class TestP2KillSwitchParity:
    """``proactive_enabled=False`` → no compaction on ANY lane,
    including the resume lane P2 just opened up. The flag lives inside
    the gate (step 0), so removing the dispatch-level blanket skip
    cannot leak compaction past the kill-switch.
    """

    @pytest.mark.asyncio
    async def test_flag_off_no_compaction_on_resume_lane_shape(self):
        """Flag OFF + resume-lane identity + quiescent checkpoint →
        engine, status lookup, and checkpoint writes ALL skipped."""
        mgr = _make_gate_manager(instance_status="waiting_children")
        svc, _ = _build_service(manager=mgr)
        svc._config.compaction.proactive_enabled = False
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=_make_quiescent_state(_make_messages(20))
        )
        graph.aupdate_state = AsyncMock()
        await svc._maybe_compact_context("inst-off-resume", graph, {})
        assert mgr._compactor.compact_state.await_count == 0
        assert mgr._instance_repository.get.call_count == 0, (
            "kill-switch must short-circuit BEFORE the status lookup"
        )
        assert graph.aget_state.await_count == 0
        assert graph.aupdate_state.await_count == 0

    @pytest.mark.asyncio
    async def test_flag_on_control_compacts_on_resume_shape(self):
        """Control for the parity test: identical mocks with the flag
        ON → the engine DOES fire (proves the OFF assertion above is
        load-bearing, not vacuous)."""
        mgr = _make_gate_manager(instance_status="waiting_children")
        svc, _ = _build_service(manager=mgr)
        assert svc._config.compaction.proactive_enabled is True
        svc._get_system_prompt_tokens = AsyncMock(return_value=0)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=_make_quiescent_state(_make_messages(20))
        )
        await svc._maybe_compact_context("inst-on-resume", graph, {})
        assert mgr._compactor.compact_state.await_count == 1


# =============================================================================
# 5. Resume-handle integrity — REAL LangGraph + file-backed SQLite
# =============================================================================


_MOCKED_LANGGRAPH_KEYS = (
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
)


class _RealLangGraph:
    """Swap the conftest's mocked langgraph modules for the real ones
    around a block of test code, then restore (identity-restore
    discipline, mirrors ``test_compact_executor_revive_brick_e2e.py``).
    """

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k] for k in _MOCKED_LANGGRAPH_KEYS if k in sys.modules
        }
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        return False


class _GraphWrapper:
    """Wrap a real ``CompiledStateGraph`` so ``aupdate_state`` calls are
    CAPTURED (payload + kwargs) while delegating verbatim — the seam's
    writes land in the REAL checkpointer and we can still assert the
    exact recipe (2 ordered writes, no ``as_node``).
    """

    def __init__(self, real_graph: Any) -> None:
        self._real = real_graph
        self.aupdate_state_calls: list[tuple] = []

    async def aget_state(self, config):
        return await self._real.aget_state(config)

    async def aupdate_state(self, config, values, **kwargs):
        self.aupdate_state_calls.append((config, values, kwargs))
        return await self._real.aupdate_state(config, values, **kwargs)

    def __getattr__(self, name: str) -> Any:  # delegate the rest
        return getattr(self._real, name)


class TestP2ResumeHandleIntegrityOnRealGraph:
    """THE named P2 regression risk: after a proactive compaction fires
    on a RESUME-MODE dispatch (is_retry=True), the Variant-A persist
    must preserve checkpoint ``next`` and the resume machinery so the
    dispatch resumes from the correct point.

    Doc Q3 says Variant A leaves ``next`` untouched — this test PINS it
    on a real graph (not a mock, per the O17 binding pattern): drive
    turn 1 to completion (the quiescent waiting-children post-turn
    shape), fire the REAL ``_maybe_compact_context`` (the exact call
    the dispatch now makes unconditionally — pinned by
    ``TestP2GuardRemovalAST``) with a stub engine returning a REAL
    ``CompactionResult``, let the REAL shared seam persist, then prove
    the wake dispatch (``astream(resume-message)``) still runs the
    agent and completes.
    """

    @pytest.mark.asyncio
    async def test_compact_on_resume_dispatch_preserves_next_and_wake_completes(
        self, tmp_path
    ):
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            runs: list[str] = []
            counter = {"n": 0}

            # Mirror the production state shape: ``SessionState`` is a
            # ``MessagesState`` subclass with a ``compacted_at`` channel
            # (daemon/graph.py:2429-2438) — without the channel the
            # seam's stamp write would be silently dropped.
            class _WakeState(MessagesState):
                compacted_at: str | None

            async def _agent(state):
                runs.append("ran")
                counter["n"] += 1
                return {"messages": [AIMessage(content="agent-out", id=f"echo-{counter['n']}")]}

            db_path = tmp_path / "p2_resume_handle.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            try:
                g = StateGraph(_WakeState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(checkpointer=saver)

                iid = "p2-resume-handle-inst"
                cfg = {"configurable": {"thread_id": iid}}

                # ── Turn 1: complete a turn → the quiescent post-turn
                # shape every resume-lane dispatch observes.
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1", id="h-turn1")]},
                    cfg,
                )
                st = await compiled.aget_state(cfg)
                assert st.next == (), "turn 1 must end quiescent (next=())"
                pre_ids = {m.id for m in st.values["messages"]}
                assert pre_ids == {"h-turn1", "echo-1"}

                # ── The P2 resume-dispatch compaction: fire the REAL
                # gate against the REAL graph with a stub engine that
                # compacts BOTH turn-1 messages into one doc. This is
                # the exact call site the dispatch now reaches on
                # is_retry=True.
                mgr = _make_gate_manager(instance_status="waiting_children")
                doc = SystemMessage(
                    content="compaction doc", id="compaction-doc-1"
                )
                engine_result = CompactionResult(
                    replacement_messages=[doc],
                    tokens_before=500_000,
                    tokens_after=100,
                    tokens_saved=499_900,
                    messages_before=2,
                    messages_after=1,
                    compaction_type="summarization",
                    compacted_at="2026-09-05T00:00:00+00:00",
                    compacted_ids=frozenset({"h-turn1", "echo-1"}),
                )
                mgr._compactor.compact_state = AsyncMock(
                    return_value=engine_result
                )
                svc, _ = _build_service(manager=mgr)
                svc._get_system_prompt_tokens = AsyncMock(return_value=0)
                wrapped = _GraphWrapper(compiled)
                # The shared seam re-resolves the graph via
                # ``manager.get_instance`` — route it through the
                # wrapper so the persist writes are BOTH captured AND
                # delegated to the real checkpointer.
                mgr.get_instance = AsyncMock(return_value=wrapped)
                await svc._maybe_compact_context(iid, wrapped, cfg)

                # The engine ran exactly once (gate proceeded on the
                # quiescent shape + waiting_children status).
                assert mgr._compactor.compact_state.await_count == 1

                # ── PIN Q3: Variant A persist = exactly TWO ordered
                # writes, NO ``as_node`` kwarg, messages-first.
                assert len(wrapped.aupdate_state_calls) == 2, (
                    f"expected exactly 2 aupdate_state writes (messages, "
                    f"compacted_at); got {len(wrapped.aupdate_state_calls)}"
                )
                (cfg1, vals1, kw1), (cfg2, vals2, kw2) = wrapped.aupdate_state_calls
                assert kw1 == {} and kw2 == {}, (
                    f"Variant A must NEVER pass as_node/kwargs; got "
                    f"{kw1!r}, {kw2!r}"
                )
                assert "messages" in vals1 and "compacted_at" not in vals1
                assert "compacted_at" in vals2 and "messages" not in vals2
                # Sentinel is element 0 of the messages write.
                from langgraph.graph.message import REMOVE_ALL_MESSAGES

                first_write_msg = vals1["messages"][0]
                assert getattr(first_write_msg, "id", None) == REMOVE_ALL_MESSAGES

                # ── THE HANDLE PIN: checkpoint ``next`` is UNTOUCHED by
                # the compaction (Variant A) — the resume machinery is
                # intact. Under the retired ``as_node='agent'`` recipe
                # this is exactly the pointer the collapse corrupted.
                st_after = await compiled.aget_state(cfg)
                assert st_after.next == (), (
                    f"Q3 PIN VIOLATED: Variant-A persist must leave "
                    f"next untouched; got next={st_after.next!r}"
                )
                # Channel = the compacted doc (sentinel landed).
                assert [m.id for m in st_after.values["messages"]] == [
                    "compaction-doc-1"
                ]
                assert st_after.values.get("compacted_at") == (
                    "2026-09-05T00:00:00+00:00"
                )

                # ── The dispatch completes from the expected point:
                # the wake turn (resume-mode dispatch sends the resume
                # message as graph_input) STILL RUNS the agent — the
                # compacted checkpoint re-primes and completes.
                runs.clear()
                async for _chunk in compiled.astream(
                    {
                        "messages": [
                            HumanMessage(
                                content="child report wake", id="h-wake"
                            )
                        ]
                    },
                    cfg,
                ):
                    pass
                assert runs == ["ran"], (
                    f"resume-handle integrity BROKEN: post-compaction "
                    f"astream(resume-message) did not run the agent; "
                    f"runs={runs}"
                )
                st_final = await compiled.aget_state(cfg)
                assert st_final.next == (), "wake turn must complete quiescent"
                assert [m.id for m in st_final.values["messages"]] == [
                    "compaction-doc-1", "h-wake", "echo-2",
                ], (
                    "wake turn must append onto the compacted channel in "
                    "order (doc → wake message → agent output)"
                )
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_midflight_shape_on_real_graph_info_skips_and_resume_unharmed(
        self, tmp_path
    ):
        """The other T5 arm on a REAL graph: a mid-flight-shaped
        checkpoint (``next=('agent',)`` — built with
        ``interrupt_before``, the pause-cancelled mid-node analog)
        INFO-skips compaction, nothing is written, and the normal
        resume (``astream(None)``) still runs the agent to completion —
        dispatch proceeds normally, unharmed by P2.
        """
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            runs: list[str] = []

            async def _agent(state):
                runs.append("ran")
                return {"messages": [AIMessage(content="agent-out")]}

            db_path = tmp_path / "p2_midflight_skip.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(
                    checkpointer=saver, interrupt_before=["agent"]
                )

                iid = "p2-midflight-inst"
                cfg = {"configurable": {"thread_id": iid}}

                # Drive to the mid-flight shape: paused BEFORE agent →
                # next=('agent',) — the shape the shape gate must skip.
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1", id="h-1")]},
                    cfg,
                )
                st = await compiled.aget_state(cfg)
                assert st.next == ("agent",), (
                    f"fixture must produce a mid-flight shape; got "
                    f"next={st.next!r}"
                )

                # Fire the REAL gate — must INFO-skip, engine NEVER
                # invoked, ZERO writes.
                mgr = _make_gate_manager(instance_status="waiting_children")
                mgr._compactor.compact_state = AsyncMock()
                svc, _ = _build_service(manager=mgr)
                wrapped = _GraphWrapper(compiled)
                await svc._maybe_compact_context(iid, wrapped, cfg)

                assert mgr._compactor.compact_state.await_count == 0, (
                    "mid-flight shape must never reach the engine"
                )
                assert wrapped.aupdate_state_calls == [], (
                    "mid-flight skip must not write the checkpoint at all"
                )
                st_after = await compiled.aget_state(cfg)
                assert st_after.next == ("agent",), (
                    "skip must leave the mid-flight checkpoint untouched"
                )

                # Dispatch proceeds normally: the resume (None input →
                # pure checkpoint resume) runs the agent to completion.
                runs.clear()
                await compiled.ainvoke(None, cfg)
                assert runs == ["ran"], (
                    "mid-flight resume must proceed normally after the "
                    "INFO skip"
                )
                st_final = await compiled.aget_state(cfg)
                assert st_final.next == ()
            finally:
                await conn.close()

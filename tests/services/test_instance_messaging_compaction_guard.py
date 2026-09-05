"""Unit tests for the proactive-compaction gate in
:meth:`InstanceMessagingService._maybe_compact_context`.

Cycle 2 of ``feature/proactive-compaction-fix`` (review W-3) — these
tests pin the NEW gate polarity after the L1 fix:

* **Status gate** (T6 anti-drift — single ``COMPACT_REJECT_STATUSES``
  frozenset shared with the ``/compact`` dispatcher): instance status
  in ``{terminated, error, failed}`` → INFO skip, engine NEVER
  invoked.
* **Shape gate (inverted polarity)**: a QUIESCENT checkpoint
  (``state.next == ()``) is the REQUIRED precondition for
  compaction to proceed; a NON-quiescent checkpoint (any pending
  node) → INFO skip.
* **Variant-A persist** (T3 — shared seam at
  ``daemon/services/_compaction_persist_seam.py``, ``mid_turn=False``,
  ``abort_policy="fail_open"``): when the engine returns a non-None
  result, the seam is called (NOT a direct ``aupdate_state`` from
  the call site). The seam itself issues zero ``aupdate_state(as_node=...)``
  writes — that was the call-site bug that reset ``next=()`` on
  terminal-shaped between-turns checkpoints and broke the 2nd+
  reuse revive-on-send path.

The previous file pinned the OLD inverted polarity
(``TestTerminalCheckpointGuard`` / ``TestNonTerminalCheckpointCompacts``
— the same name pattern, opposite semantic). Those 6 tests
migrated to the new contract below. The migration preserves the
load-bearing invariants the original suite caught (no
``aupdate_state`` with ``as_node="agent"`` from the proactive site;
the shape gate is consistent with the shared helper
``_is_terminal_checkpoint``) while pinning the new engine + seam
plumbing that replaced the old direct-call pattern.

What this file covers
---------------------

* ``TestStatusRejects`` — when ``instance.status`` is in
  ``COMPACT_REJECT_STATUSES`` (``terminated`` / ``error`` /
  ``failed``), the gate short-circuits at INFO; the compactor and
  the graph are NEVER touched.
* ``TestNonQuiescentShapeSkips`` — when ``state.next != ()`` (the
  graph has a pending node), the gate short-circuits at INFO.
* ``TestQuiescentShapeProceeds`` — when ``state.next == ()`` AND
  the status is NOT in the reject set, the engine is invoked. A
  ``None`` compactor result returns silently. A non-``None`` result
  flows through the shared seam (Variant A — no ``as_node``).
* ``TestGuardRobustness`` — defensive coverage of the pre-existing
  early-return branches (``_compactor is None``, ``state is None``).

Test helpers follow the same patterns as
``tests/services/test_instance_messaging_skill_injection.py``
(``_make_service`` builds the service around a fully-mocked
manager) and ``tests/unit/test_graph_retry_integration.py`` (graph
state mocked with ``MagicMock`` + ``AsyncMock``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from daemon.services.command_dispatcher import COMPACT_REJECT_STATUSES
from daemon.services.instance_messaging import InstanceMessagingService

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _MockGraphState:
    """Mimics LangGraph's ``GetStateResult`` shape.

    ``values`` carries channel_values (messages, compacted_at, …);
    ``next`` is the tuple of next nodes LangGraph will execute —
    empty tuple signals a quiescent (between-turns / completed)
    checkpoint.
    """

    values: dict[str, Any] = field(default_factory=dict)
    next: tuple[str, ...] = ()


# Sentinel default for the ``_make_compactor(return_value=...)`` arg —
# declared at module top so the function signature can reference it
# as a default (Python evaluates defaults at def time, not at call
# time). Without this, the test couldn't distinguish "no return
# value given" from "return_value=None" (which exercises the engine
# no-replacement branch).
_SENTINEL = object()


def _make_graph(
    *,
    state: _MockGraphState | None,
) -> MagicMock:
    """Build a LangGraph mock whose ``aget_state`` returns ``state``.

    ``aupdate_state`` is recorded but has no side effect — the seam
    (the real writer) is patched out in this file; if a future test
    flips the seam back on, the mock's ``aupdate_state`` will
    silently no-op without the call count assertions firing.
    """
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=state)
    graph.aupdate_state = AsyncMock()
    return graph


def _make_compactor(
    *,
    replacement: list | None = None,
    return_value: "Any | None | object" = _SENTINEL,  # type: ignore[assignment]
) -> MagicMock:
    """Build a compactor mock.

    ``compact_state`` returns ``return_value`` if explicitly
    provided; otherwise it returns a ``CompactionResult`` with the
    supplied ``replacement_messages`` list (defaulting to a single
    summary). The sentinel default lets tests pass
    ``return_value=None`` to exercise the "no replacement" branch
    without colliding with "no return value given" (the default).
    """
    from daemon.compaction import CompactionResult

    compactor = MagicMock()
    if return_value is _SENTINEL:
        compactor.compact_state = AsyncMock(
            return_value=CompactionResult(
                replacement_messages=replacement or [
                    HumanMessage(content="summary", id="summary-1")
                ],
                tokens_before=1000,
                tokens_after=100,
                tokens_saved=900,
                messages_before=20,
                messages_after=1,
                compaction_type="summarization",
            )
        )
    else:
        compactor.compact_state = AsyncMock(return_value=return_value)
    return compactor


def _make_manager(
    *,
    compactor: MagicMock,
    instance_status: str | None = "running",
) -> MagicMock:
    """Build a manager mock with the minimum surface area
    ``_maybe_compact_context`` touches.

    ``_compactor`` is accessed through a property on
    ``InstanceMessagingService`` (``self._manager._compactor``), so
    the compactor is hung off the manager mock. ``config`` is
    supplied because the function reads ``self._config.llm.*`` and
    ``self._config.compaction.*`` (proactive_enabled gate).

    ``instance_status`` drives the status gate; ``None`` means the
    repository returns ``None`` (transient DB hiccup path → falls
    through to the shape gate).
    """
    manager = MagicMock()
    manager._compactor = compactor
    manager.config.llm.model = "test-model"
    manager.config.llm.base_url = "http://test"
    manager.config.llm.api_key = "sk-test"
    manager.config.llm.model_vision = None
    manager.config.llm.temperature = 0.0
    manager.config.llm.request_timeout = 60.0
    manager.config.compaction = MagicMock()
    # Default proactive_enabled ON (the gate short-circuits before
    # the engine call when this is False; individual tests flip it
    # off via patch when testing that branch).
    manager.config.compaction.proactive_enabled = True
    manager._instance_repository = MagicMock()
    if instance_status is None:
        manager._instance_repository.get = MagicMock(return_value=None)
    else:
        manager._instance_repository.get = MagicMock(
            return_value=MagicMock(status=instance_status)
        )
    return manager


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build an :class:`InstanceMessagingService` around ``manager``.

    ``InstanceMessagingService.__init__`` only requires ``manager``
    and ``cancellation_service``. The cancellation service is
    irrelevant for ``_maybe_compact_context``.
    """
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )


# ---------------------------------------------------------------------------
# Status gate — instance.status ∈ COMPACT_REJECT_STATUSES → INFO skip
# ---------------------------------------------------------------------------


class TestStatusRejects:
    """When ``instance.status`` is in ``COMPACT_REJECT_STATUSES``
    (sourced from :mod:`daemon.services.command_dispatcher` so the
    proactive path and the ``/compact`` dispatcher can never drift
    apart), the gate short-circuits at INFO.

    The gate runs BEFORE the engine call AND before the graph
    read — the engine + the LangGraph state are NEVER touched on a
    status-reject path. This is the fix for the 2nd+ reuse bug: on
    a terminal-status instance, no compaction write ever happens
    (regardless of the checkpoint's quiescent / non-quiescent
    shape), so the next ``astream`` for revive-on-send sees an
    intact graph.
    """

    @pytest.mark.parametrize("status", sorted(COMPACT_REJECT_STATUSES))
    @pytest.mark.asyncio
    async def test_status_in_reject_set_skips_engine(self, status, caplog):
        """Each status in the canonical reject set → INFO skip."""
        import logging

        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="x")] * 50},
                next=(),  # quiescent
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(
            compactor=compactor, instance_status=status
        )
        svc = _make_service(manager)

        with caplog.at_level(
            logging.INFO, logger="daemon.services.instance_messaging"
        ):
            await svc._maybe_compact_context(
                instance_id=f"inst-status-{status}",
                graph=graph,
                config={
                    "configurable": {"thread_id": f"inst-status-{status}"}
                },
            )

        # The compactor is NEVER invoked on a status-reject path —
        # the gate fires before the engine.
        compactor.compact_state.assert_not_awaited()
        # The graph is NEVER consulted on a status-reject path —
        # the gate fires before the shape read too (this is a
        # no-op call site; if a future edit moves the status gate
        # AFTER the shape read, this assertion catches it).
        graph.aget_state.assert_not_awaited()
        # And no checkpoint write.
        graph.aupdate_state.assert_not_awaited()
        # The skip log carries the documented INFO string.
        assert any(
            "terminal-status" in r.getMessage() and status in r.getMessage()
            for r in caplog.records
        ), (
            f"expected 'terminal-status' INFO log for status={status!r}; "
            f"got: {[r.getMessage() for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Shape gate (inverted polarity) — non-quiescent → INFO skip
# ---------------------------------------------------------------------------


class TestNonQuiescentShapeSkips:
    """When the checkpoint is NOT quiescent (``state.next`` is
    non-empty — the graph has a pending node), the gate short-
    circuits at INFO.

    This is the inverted-polarity form of the OLD terminal-shape
    guard. The OLD code skipped on the quiescent shape; the NEW
    code REQUIRES the quiescent shape (and treats any pending-node
    shape as a skip). The semantic flip is the load-bearing part
    of the L1 fix — the quiescent (between-turns) shape is the
    ONLY pre-dispatch checkpoint shape, so the NEW gate fires
    exactly when the engine is allowed to compact (no in-flight
    work to disrupt).
    """

    @pytest.mark.asyncio
    async def test_non_quiescent_shape_skips_engine(self, caplog):
        """``state.next = ("agent",)`` → INFO skip; engine NEVER
        invoked; the status lookup already happened (the status
        gate runs first), but the graph's aupdate_state is
        untouched.
        """
        import logging

        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 5},
                next=("agent",),  # NOT quiescent — has a pending node
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(
            compactor=compactor, instance_status="running"
        )
        svc = _make_service(manager)

        with caplog.at_level(
            logging.INFO, logger="daemon.services.instance_messaging"
        ):
            await svc._maybe_compact_context(
                instance_id="inst-nonquiescent",
                graph=graph,
                config={
                    "configurable": {"thread_id": "inst-nonquiescent"}
                },
            )

        # The compactor is NEVER invoked on a non-quiescent shape.
        compactor.compact_state.assert_not_awaited()
        # No checkpoint write on a non-quiescent shape.
        graph.aupdate_state.assert_not_awaited()
        # The skip log carries the documented INFO string.
        assert any(
            "non-quiescent" in r.getMessage() for r in caplog.records
        ), (
            f"expected 'non-quiescent' INFO log; got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_non_quiescent_skips_even_with_reject_set_status(self):
        """When BOTH the status gate AND the shape gate would fire,
        the status gate runs first (it's upstream). This test pins
        the ORDER: a status-reject never reaches the shape gate.

        The non-quiescent + status-reject combination is not a
        realistic case in production (terminal-status instances
        rarely have pending graph work), but the test guards
        against a future refactor that swaps the gate order and
        accidentally re-introduces a non-quiescent path's
        ``aupdate_state`` write.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 5},
                next=("agent",),  # NOT quiescent
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(
            compactor=compactor, instance_status="terminated"
        )
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-bad-state",
            graph=graph,
            config={"configurable": {"thread_id": "inst-bad-state"}},
        )

        compactor.compact_state.assert_not_awaited()
        # The graph was read for the status gate's path lookup? No
        # — the status gate doesn't read the graph. aget_state is
        # not invoked at all.
        graph.aget_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# Proceeds — quiescent + status ok → engine invoked → seam (Variant A)
# ---------------------------------------------------------------------------


class TestQuiescentShapeProceeds:
    """When ``state.next == ()`` (quiescent) AND the instance
    status is NOT in the reject set, the engine is invoked and any
    non-``None`` result flows through the shared seam with
    ``mid_turn=False`` (Variant A — TWO ``aupdate_state`` calls
    WITHOUT ``as_node``).

    This is the HAPPY path the original guard's invert was
    blocking. The pre-fix code skipped every quiescent checkpoint
    (so the gate never fired) and only ran on a non-quiescent
    shape (which is a mid-flight in-turn state — the worst time
    to compact). The new code flips the polarity: quiescent is
    the proceed condition, non-quiescent is the skip.
    """

    @pytest.mark.asyncio
    async def test_quiescent_with_none_compactor_result_returns_silently(self):
        """``state.next = ()`` + status ok + compactor returns
        ``None`` → engine invoked; function returns BEFORE the seam.

        The ``None`` branch is the pre-existing "no replacement"
        short-circuit — a real engine success path may legitimately
        return ``None`` (e.g. below the configured threshold). The
        test pins that the seam is NOT called when the engine has
        no result to persist.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={
                    "messages": [HumanMessage(content="m")] * 12,
                    "compacted_at": None,
                },
                next=(),  # quiescent — proceed condition
            ),
        )
        # Compactor returns None — short-circuits before the seam.
        compactor = _make_compactor(return_value=None)
        manager = _make_manager(
            compactor=compactor, instance_status="running"
        )
        # ``_get_system_prompt_tokens`` is the post-status pre-engine
        # DB call; with the default mock it would hit the
        # ``_instance_repository.get`` we already set up.
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ) as seam_mock:
            await svc._maybe_compact_context(
                instance_id="inst-quiescent-none",
                graph=graph,
                config={
                    "configurable": {"thread_id": "inst-quiescent-none"}
                },
            )

        # The engine was reached (the guard did NOT short-circuit).
        compactor.compact_state.assert_awaited_once()
        # None result → seam was NOT called. (Anti-refire stamp-only
        # path is reserved for non-None CompactionResult with empty
        # replacement_messages; the ``None`` path is just a "no
        # work to do" early-return.)
        seam_mock.assert_not_awaited()
        # aupdate_state is never touched by the function when the
        # seam isn't called.
        graph.aupdate_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_quiescent_with_compactor_result_calls_seam_variant_a(self):
        """``state.next = ()`` + status ok + compactor returns a
        non-``None`` result → seam is called with
        ``mid_turn=False`` and ``abort_policy="fail_open"``.

        The seam is the SINGLE shared writer across the proactive
        path, the ``/compact`` executor, and the P1b 95% pre-call
        hook. The proactive site MUST use ``mid_turn=False`` (Variant
        A — no ``as_node``) because the pre-dispatch shape is
        quiescent (between-turns), not mid-superstep. A future
        regression that flips the call to ``mid_turn=True`` would
        re-introduce the 2nd+ reuse bug: ``aupdate_state(..., as_node=
        "agent")`` on a quiescent checkpoint clears ``next=()``,
        and the next ``astream(graph_input)`` returns instantly
        without running the graph.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={
                    "messages": [HumanMessage(content="m")] * 12,
                    "compacted_at": None,
                },
                next=(),  # quiescent — proceed condition
            ),
        )
        compactor = _make_compactor(
            replacement=[HumanMessage(content="summary", id="summary-1")]
        )
        manager = _make_manager(
            compactor=compactor, instance_status="running"
        )
        svc = _make_service(manager)

        seam_mock = AsyncMock(return_value=True)
        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=seam_mock,
        ):
            await svc._maybe_compact_context(
                instance_id="inst-quiescent-replace",
                graph=graph,
                config={
                    "configurable": {"thread_id": "inst-quiescent-replace"}
                },
            )

        # The engine was reached.
        compactor.compact_state.assert_awaited_once()
        # The seam was called exactly once — single-writer
        # discipline; a future regression that bypasses the seam
        # (call-site direct ``aupdate_state``) would fail this
        # assertion and re-introduce the inverted-polarity bug.
        seam_mock.assert_awaited_once()
        call = seam_mock.await_args
        # The seam receives ``mid_turn=False`` (Variant A) and
        # ``abort_policy="fail_open"`` (proactive never raises).
        assert call.kwargs.get("mid_turn") is False, (
            "proactive site MUST use mid_turn=False (Variant A — no "
            "as_node); mid_turn=True would re-introduce the 2nd+ "
            "reuse bug on quiescent checkpoints"
        )
        assert call.kwargs.get("abort_policy") == "fail_open"
        # The manager is passed positionally; the instance_id and
        # result are keyword-only (seam signature).
        assert call.kwargs.get("instance_id") == "inst-quiescent-replace"
        assert call.kwargs.get("result") is not None

    @pytest.mark.asyncio
    async def test_completed_status_proceeds(self):
        """``state.next = ()`` + status="completed" → engine invoked.

        ``completed`` is NOT in ``COMPACT_REJECT_STATUSES`` (the
        C1 compact-on-COMPLETED policy; see
        ``.agents/shared/planning/compact-on-completed/``). The
        quiescent+completed combination is the multi-reuse case
        the proactive path was DESIGNED to handle.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 12},
                next=(),
            ),
        )
        compactor = _make_compactor(return_value=None)
        manager = _make_manager(
            compactor=compactor, instance_status="completed"
        )
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ) as seam_mock:
            await svc._maybe_compact_context(
                instance_id="inst-completed",
                graph=graph,
                config={"configurable": {"thread_id": "inst-completed"}},
            )

        compactor.compact_state.assert_awaited_once()
        # None result → seam was NOT called.
        seam_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idle_status_proceeds(self):
        """``state.next = ()`` + status="idle" → engine invoked
        (idle is NOT in the reject set — the live between-turns
        state for an instance that has no current task).
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 12},
                next=(),
            ),
        )
        compactor = _make_compactor(return_value=None)
        manager = _make_manager(
            compactor=compactor, instance_status="idle"
        )
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ) as seam_mock:
            await svc._maybe_compact_context(
                instance_id="inst-idle",
                graph=graph,
                config={"configurable": {"thread_id": "inst-idle"}},
            )

        compactor.compact_state.assert_awaited_once()
        seam_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_proactive_disabled_short_circuits_entire_gate(self):
        """``proactive_enabled=False`` → return BEFORE the status
        gate, shape gate, or engine call. The flag is the SOLE
        kill-switch for BOTH the proactive path AND the 95%
        pre-call hook (P1b A.8).

        This is the regression guard for the W-2 env-resolution
        fix: a flag the operator flipped via env must actually
        take effect, not be silently shadowed by a yaml default.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 12},
                next=(),
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(
            compactor=compactor, instance_status="running"
        )
        manager.config.compaction.proactive_enabled = False
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ) as seam_mock:
            await svc._maybe_compact_context(
                instance_id="inst-off",
                graph=graph,
                config={"configurable": {"thread_id": "inst-off"}},
            )

        # Nothing downstream of the kill-switch is touched.
        compactor.compact_state.assert_not_awaited()
        graph.aget_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()
        seam_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Edge cases — robustness of the gate
# ---------------------------------------------------------------------------


class TestGuardRobustness:
    """Small follow-up cases that pin down the gate's pre-existing
    early-return branches. These are not the primary regression —
    they exist so that future edits to the gate cannot silently
    regress the OTHER early-return conditions (``_compactor is
    None``, ``state is None``).
    """

    @pytest.mark.asyncio
    async def test_no_compactor_is_noop(self):
        """``self._compactor is None`` short-circuits the function.

        Compaction is opt-in (the config can disable it via the
        factory chain). When disabled, the property returns
        ``None`` and the function returns immediately — this is the
        pre-existing behavior the kill-switch + the new gate share.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": []}, next=("agent",)
            ),
        )
        manager = _make_manager(
            compactor=MagicMock(), instance_status="running"
        )
        # Explicit None — MagicMock auto-attrs are truthy and
        # would silently bypass the gate.
        manager._compactor = None
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ) as seam_mock:
            await svc._maybe_compact_context(
                instance_id="inst-noop",
                graph=graph,
                config={"configurable": {"thread_id": "inst-noop"}},
            )

        # No compactor → no engine, no aget_state, no seam.
        graph.aget_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()
        seam_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_state_is_noop(self):
        """``state is None`` (from ``aget_state``) short-circuits.

        A future edit that flips the order of the status gate and
        the state read would crash on a None state; this test
        keeps both early-returns covered.
        """
        graph = _make_graph(state=None)
        compactor = _make_compactor()
        manager = _make_manager(
            compactor=compactor, instance_status="running"
        )
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ) as seam_mock:
            await svc._maybe_compact_context(
                instance_id="inst-none-state",
                graph=graph,
                config={"configurable": {"thread_id": "inst-none-state"}},
            )

        compactor.compact_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()
        seam_mock.assert_not_awaited()

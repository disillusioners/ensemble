"""Unit tests for the terminal-checkpoint guard in
:meth:`InstanceMessagingService._maybe_compact_context`.

Background
----------

On 2nd+ reuse of a completed instance, accumulated messages
crossed the compaction threshold and triggered
``graph.aupdate_state(as_node="agent")``. That call clears the
terminal checkpoint's ``next=()``, causing ``astream(graph_input)``
to return instantly without running the graph. The
COMPLETED → RUNNING → COMPLETED cycle then collapsed to <100 ms so
the frontend never observed RUNNING (see
``daemon/services/instance_messaging.py`` — search for
``Terminal-checkpoint guard`` in ``_maybe_compact_context``).

The fix adds an early-return guard at the top of
``_maybe_compact_context``: when ``state.next`` is empty (i.e. the
checkpoint is terminal — graph has no pending nodes), compaction is
skipped without ever touching the checkpoint. The subsequent
``astream(graph_input)`` then sees an intact ``next=()`` for a
graph that should have advanced.

What this file covers
---------------------

* ``TestTerminalCheckpointGuard`` — when ``state.next`` is ``()``
  (terminal), ``_maybe_compact_context`` returns *before* invoking
  the compactor or calling ``aupdate_state``. This is the primary
  regression test for the 2nd+ reuse bug.
* ``TestNonTerminalCheckpointCompacts`` — inverse: when
  ``state.next = ("agent",)`` (active), compaction proceeds and
  ``aupdate_state`` runs exactly as the "happy-path" code expects.

These tests follow the same mocking patterns as
``tests/services/test_instance_messaging_skill_injection.py``
(``_make_service`` builds an ``InstanceMessagingService`` around a
fully-mocked manager) and
``tests/unit/test_graph_retry_integration.py`` (graph state mocked
with ``MagicMock`` + ``AsyncMock``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage

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
    empty tuple signals a terminal checkpoint.
    """

    values: dict[str, Any] = field(default_factory=dict)
    next: tuple[str, ...] = ()


def _make_graph(
    *,
    state: _MockGraphState | None,
) -> MagicMock:
    """Build a LangGraph mock whose ``aget_state`` returns ``state``.

    ``aupdate_state`` is recorded but has no side effect (the fix
    guards *against* it being called on terminal checkpoints).
    """
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=state)
    graph.aupdate_state = AsyncMock()
    return graph


_SENTINEL = object()


def _make_compactor(
    *,
    replacement: list | None = None,
    return_value: "Any | None" = _SENTINEL,  # type: ignore[assignment]
) -> MagicMock:
    """Build a compactor mock.

    ``compact_state`` returns ``return_value`` if explicitly
    provided; otherwise it returns a ``CompactionResult`` with the
    supplied ``replacement_messages`` list (defaulting to a single
    summary). The sentinel default lets tests pass
    ``return_value=None`` to exercise the "no replacement" branch
    without colliding with "no return value given".
    """
    from daemon.compaction import CompactionResult

    compactor = MagicMock()
    if return_value is not _SENTINEL:
        compactor.compact_state = AsyncMock(return_value=return_value)
    else:
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
            ),
        )
    return compactor


def _make_manager(*, compactor: MagicMock) -> MagicMock:
    """Build a manager mock with the minimum surface area
    ``_maybe_compact_context`` touches.

    ``_compactor`` is accessed through a property on
    ``InstanceMessagingService`` (``self._manager._compactor``), so
    the compactor is hung off the manager mock. ``config`` is
    supplied because the function reads ``self._config.llm.*``
    before deciding whether to compact.
    """
    manager = MagicMock()
    manager._compactor = compactor
    manager.config.llm.model = "test-model"
    manager.config.llm.base_url = "http://test"
    manager.config.llm.api_key = "sk-test"
    manager.config.llm.model_vision = None
    manager.config.llm.temperature = 0.0
    manager.config.llm.request_timeout = 60.0
    manager.config.compaction = MagicMock()  # threaded into CompactionContext
    return manager


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build an :class:`InstanceMessagingService` around ``manager``.

    ``InstanceMessagingService.__init__`` only requires
    ``manager`` and ``cancellation_service``. The cancellation
    service is irrelevant for ``_maybe_compact_context``.
    """
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )


# ---------------------------------------------------------------------------
# Terminal-checkpoint guard (the regression test for the 2nd+ reuse bug)
# ---------------------------------------------------------------------------


class TestTerminalCheckpointGuard:
    """When the graph checkpoint is terminal (no pending nodes),
    ``_maybe_compact_context`` MUST skip compaction.

    Regression for: on 2nd+ reuse of a completed instance,
    ``graph.aupdate_state(as_node="agent")`` cleared ``next=()`` and
    the next ``astream`` returned instantly. Status flipped
    COMPLETED → RUNNING → COMPLETED in <100 ms so the frontend
    never observed RUNNING. The fix is an early-return guard:

        if not state.next:
            return
    """

    @pytest.mark.asyncio
    async def test_terminal_checkpoint_skips_aupdate_state(self):
        """``state.next = ()`` → ``aupdate_state`` is NOT called.

        This is the core invariant: we must never call
        ``aupdate_state`` on a terminal checkpoint, since that's the
        call that resets ``next=()`` and breaks the follow-up
        ``astream``.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="x")] * 50},
                next=(),  # terminal
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-terminal-1",
            graph=graph,
            config={"configurable": {"thread_id": "inst-terminal-1"}},
        )

        # The compactor must NEVER be invoked on terminal checkpoints.
        compactor.compact_state.assert_not_awaited()
        # And — the critical assertion — aupdate_state is untouched.
        graph.aupdate_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_terminal_checkpoint_skips_compactor_invocation(self):
        """``state.next = ()`` → compactor ``compact_state`` not awaited.

        Separate from the ``aupdate_state`` assertion so a future
        regression that, say, calls ``compact_state`` but skips the
        ``aupdate_state`` (or vice versa) is caught distinctly. The
        intent is to short-circuit *before* any compaction work —
        that includes the LLM round-trip in ``compact_state``.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": []},
                next=(),
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-terminal-2",
            graph=graph,
            config={"configurable": {"thread_id": "inst-terminal-2"}},
        )

        compactor.compact_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_terminal_checkpoint_no_state_means_no_compact(self):
        """``state.next = None`` is also treated as terminal.

        LangGraph sometimes returns ``next = None`` instead of an
        empty tuple for graph end-states; ``not state.next`` covers
        both. This guards against a future LangGraph change that
        swaps the convention back to ``None``.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="hi")] * 12},
                next=None,  # type: ignore[arg-type]
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-terminal-3",
            graph=graph,
            config={"configurable": {"thread_id": "inst-terminal-3"}},
        )

        compactor.compact_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_terminal_checkpoint_does_not_read_instance_repo(self):
        """Skipping compaction must not touch the SQLAlchemy
        instance repository (``_get_system_prompt_tokens``).

        On terminal checkpoints the function returns before calling
        ``_get_system_prompt_tokens`` (which offloads to
        ``asyncio.to_thread``). Asserting ``_instance_repository.get``
        is not awaited catches a regression where the guard is moved
        *after* the system-prompt call — that would re-introduce the
        SQLAlchemy-on-event-loop risk the ``to_thread`` wrapper was
        added to fix.
        """
        graph = _make_graph(
            state=_MockGraphState(values={"messages": []}, next=()),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        # Wire a mock instance_repository so we can assert no access.
        manager._instance_repository = MagicMock()
        manager._instance_repository.get = MagicMock(
            return_value=None,
        )
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-terminal-4",
            graph=graph,
            config={"configurable": {"thread_id": "inst-terminal-4"}},
        )

        # The guard must short-circuit BEFORE the system prompt call.
        manager._instance_repository.get.assert_not_called()


# ---------------------------------------------------------------------------
# Inverse: non-terminal checkpoints still compact normally
# ---------------------------------------------------------------------------


class TestNonTerminalCheckpointCompacts:
    """When ``state.next`` is non-empty (graph has work to do),
    the normal compaction path runs — the guard must not over-fire.

    This is the negative control. Without it, a regression that
    *always* skips compaction (e.g. the guard condition breaks) would
    still pass the terminal tests above and silently disable
    compaction on active conversations. The "active" branch has its
    own contract that ``aupdate_state`` MUST be called when the
    compactor returns a non-None result with replacement messages.
    """

    @pytest.mark.asyncio
    async def test_non_terminal_checkpoint_runs_compactor(self):
        """``state.next = ("agent",)`` → ``compact_state`` is awaited.

        With messages below the compaction ``min_messages`` threshold
        (defaults to 10), the compactor may still be invoked and
        return ``None``/empty replacements. We assert *invocation*
        here; the inverse ``aupdate_state`` behavior is covered in
        the next test.
        """
        graph = _make_graph(
            state=_MockGraphState(
                values={
                    "messages": [HumanMessage(content="m")] * 12,
                    "compacted_at": None,
                },
                next=("agent",),  # active — has a pending node
            ),
        )
        # Compactor returns None (e.g. below threshold) — the function
        # short-circuits AFTER calling compact_state but BEFORE
        # aupdate_state. That covers both "compactor was reached" and
        # "no update issued" in a single realistic scenario.
        compactor = _make_compactor(return_value=None)
        manager = _make_manager(compactor=compactor)
        # _instance_repository.get is called by _get_system_prompt_tokens;
        # return None so the whole call falls through cleanly to
        # compactor.compact_state.
        manager._instance_repository = MagicMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-active-1",
            graph=graph,
            config={"configurable": {"thread_id": "inst-active-1"}},
        )

        # The compactor WAS invoked (the guard did not short-circuit).
        compactor.compact_state.assert_awaited_once()
        # Result was None → aupdate_state still NOT called (this is
        # the existing "no replacement" branch, not new behavior).
        graph.aupdate_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_terminal_checkpoint_writes_replacement(self):
        """Non-terminal + non-None compactor result → ``aupdate_state``
        is called exactly once with the replacement messages.

        This is the happy-path baseline: when an active conversation
        crosses the compaction threshold, the compactor returns
        replacement messages and we MUST write them back so the
        next LLM call sees the compacted history. If the new
        terminal-guard accidentally suppresses this, the active-
        path memory pressure behaviour would silently regress.
        """
        replacement = [HumanMessage(content="summary", id="summary-1")]
        graph = _make_graph(
            state=_MockGraphState(
                values={
                    "messages": [HumanMessage(content="m")] * 12,
                    "compacted_at": None,
                },
                next=("agent",),
            ),
        )
        compactor = _make_compactor(replacement=replacement)
        manager = _make_manager(compactor=compactor)
        manager._instance_repository = MagicMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-active-2",
            graph=graph,
            config={"configurable": {"thread_id": "inst-active-2"}},
        )

        compactor.compact_state.assert_awaited_once()
        # aupdate_state must be called for the messages; depending on
        # the compactor's ``compacted_at``, it may be called a second
        # time for the timestamp. We assert *at least once* with the
        # replacement payload.
        assert graph.aupdate_state.await_count >= 1, (
            "non-terminal checkpoint with non-None compactor result "
            "must write the replacement back via aupdate_state"
        )
        first_call = graph.aupdate_state.await_args_list[0]
        # Args: (config, values, ...) — ``as_node='agent'`` is passed
        # as a kwarg in source, not a third positional arg.
        assert first_call.args[1] == {"messages": replacement}
        # The ``as_node='agent'`` form is what the in-code comment in
        # ``_maybe_compact_context`` warns about. It MUST be the
        # form used here so the bug-investigation Finding (terminal
        # checkpoint cleared) doesn't get reintroduced for the
        # active-compaction path either.
        assert first_call.kwargs.get("as_node") == "agent"


# ---------------------------------------------------------------------------
# Edge cases — robustness of the guard
# ---------------------------------------------------------------------------


class TestGuardRobustness:
    """Small follow-up cases that pin down the guard's pre-existing
    early-return branches. These are not the primary regression —
    they exist so that future edits to the guard cannot silently
    regress the *other* early-return conditions
    (``self._compactor is None``, ``state is None``).
    """

    @pytest.mark.asyncio
    async def test_no_compactor_is_noop(self):
        """``self._compactor is None`` short-circuits the function.

        Compaction is opt-in (the config can disable it). When
        disabled, the property returns ``None`` and the function
        returns immediately — this is the existing behavior that
        ``_maybe_compact_context`` shares with the rest of the
        memory-management hooks.
        """
        graph = _make_graph(
            state=_MockGraphState(values={"messages": []}, next=("agent",)),
        )
        # Manager has no _compactor attribute → property returns the
        # MagicMock auto-attr, which is truthy. Explicit None is the
        # only way to test the "compaction disabled" path without
        # spinning up a real config.
        manager = _make_manager(compactor=MagicMock())
        manager._compactor = None
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-noop",
            graph=graph,
            config={"configurable": {"thread_id": "inst-noop"}},
        )

        # aget_state is never reached when compactor is None.
        graph.aget_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_state_is_noop(self):
        """``state is None`` (from ``aget_state``) short-circuits.

        The guard sits *below* the ``if not state: return`` check.
        A future edit that flips the order would crash; this test
        keeps both early-returns covered.
        """
        graph = _make_graph(state=None)
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-none-state",
            graph=graph,
            config={"configurable": {"thread_id": "inst-none-state"}},
        )

        compactor.compact_state.assert_not_awaited()
        graph.aupdate_state.assert_not_awaited()

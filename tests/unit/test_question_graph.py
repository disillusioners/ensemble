"""Tests for the question-tool graph wiring in ``daemon.graph``.

Covers the Phase 1 / F1 + F2 + F4 invariants plus the C2 deferred-pause fix:

  * ``build_instance_graph`` must call ``create_question_pause_node(manager)``
    when a ``manager`` is supplied (the regression that produced the
    ``NameError: name 'question_pause_node' is not defined`` bug).
  * ``create_post_tools_router`` must read the per-instance pause flag from
    the manager and route to ``"question_pause_node"`` when set, back to
    ``"agent"`` otherwise.
  * ``create_question_pause_node`` must:
      1. Set the per-instance deferred-pause marker via
         ``manager.set_deferred_question_pause(instance_id)`` —
         and MUST NOT call ``manager.pause_instance_cascade`` from inside
         the graph task (C2 torn-state fix: the cascade pops
         ``_graph_tasks[instance_id]`` and cancels the running graph task,
         interrupting its own DB write with ``CancelledError``).
      2. Clear the conditional-edge pause flag in ``finally`` (F2).
         The ``finally`` block must run on every exit path, including the
         case where ``set_deferred_question_pause`` itself raises.

The companion ``tests/unit/test_question_deferred_pause_callback.py``
exercises the actual post-graph completion path in
``daemon.services.instance_messaging`` where the deferred marker is popped
and ``pause_instance_cascade`` is invoked OUTSIDE the graph task — that is
the real C2 fix verification.

All tests mock out the LLM + LangGraph surface (same pattern as
``tests/unit/test_nudge_behavior.py``) so they run without a real server
and without touching the database.
"""

from __future__ import annotations

from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.graph import (
    build_instance_graph,
    create_post_tools_router,
    create_question_pause_node,
)


# =============================================================================
# Helpers
# =============================================================================


def make_manager() -> MagicMock:
    """Build a mock ``InstanceManager`` with the question-pause surface wired.

    Wires the four manager methods the question tool + graph node consume:

      * ``set_question_pause_requested(instance_id)`` — flag setter.
      * ``is_question_pause_requested(instance_id)`` — flag getter.
      * ``clear_question_pause_requested(instance_id)`` — flag clear,
        called from ``question_pause_node``'s ``finally`` block (F2).
      * ``set_deferred_question_pause(instance_id)`` — C2 deferred-pause
        marker setter, called from inside ``question_pause_node``.

    Flag state is backed by a real dict so ``is_question_pause_requested``
    after a ``set`` returns ``True`` and after a ``clear`` returns ``False``
    — matches the contract the production ``InstanceManager`` exposes.

    ``manager.pause_instance_cascade`` is left as a bare ``AsyncMock`` so
    tests can assert it is NOT called by the node (C2 invariant).

    Returns:
        A ``MagicMock`` with the four methods wired as described.
    """
    manager = MagicMock()
    flag_state: dict[str, bool] = {}

    def _set(instance_id: str) -> None:
        flag_state[instance_id] = True

    def _is_set(instance_id: str) -> bool:
        return flag_state.get(instance_id, False)

    def _clear(instance_id: str) -> None:
        flag_state.pop(instance_id, None)

    manager.set_question_pause_requested.side_effect = _set
    manager.is_question_pause_requested.side_effect = _is_set
    manager.clear_question_pause_requested.side_effect = _clear

    # Bare AsyncMock — tests that exercise the C2 contract assert this is
    # NOT called by the node itself; ``manager.set_deferred_question_pause``
    # is left as a MagicMock by default (we wrap the production semantics
    # in the dedicated manager-semantics tests below).
    manager.pause_instance_cascade = AsyncMock()
    manager.set_deferred_question_pause = MagicMock()

    return manager


def make_bare_manager() -> Any:
    """Build a bare ``InstanceManager`` instance with only the deferred-pause attrs.

    Uses ``InstanceManager.__new__`` to skip the heavy ``__init__`` (DB
    engine, MCP, lifecycle service, etc.) — the tests that exercise
    ``set_deferred_question_pause`` / ``pop_deferred_question_pause`` and
    ``_cleanup_instance_state`` only need the small surface those methods
    read or mutate.

    Returns:
        A real ``InstanceManager`` with ``_deferred_question_pause``
        initialised to a fresh ``set``. Tests can poke any other attribute
        they need onto the returned object before invoking the method.
    """
    from daemon.manager import InstanceManager

    manager = InstanceManager.__new__(InstanceManager)
    manager._deferred_question_pause = set()
    return manager


# =============================================================================
# build_instance_graph + manager regression
# =============================================================================


class TestBuildInstanceGraphWithManager:
    """``build_instance_graph`` must wire ``question_pause_node`` when given a manager.

    The original bug (``NameError: name 'question_pause_node' is not
    defined``) appeared whenever a manager was passed: the code at
    ``daemon/graph.py`` referenced an undefined local name instead of
    calling the ``create_question_pause_node(manager)`` factory. These
    tests pin the call site in place so a future refactor cannot
    regress.
    """

    def test_build_instance_graph_with_manager_does_not_raise_name_error(self):
        """Calling build_instance_graph with a manager must not raise NameError.

        Before the fix, the conditional post-tools branch referenced a
        bare ``question_pause_node`` symbol that was never defined inside
        ``build_instance_graph``'s scope. The test asserts the call
        succeeds end-to-end and that ``question_pause_node`` is among the
        nodes registered on the graph.
        """
        with patch('daemon.graph.ThinkingChatOpenAI') as mock_llm_class:
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance

            with patch('daemon.graph.StateGraph') as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance

                with patch('daemon.graph.ToolNode'):
                    manager = make_manager()
                    compiled = build_instance_graph(
                        tools=[],
                        checkpointer=MagicMock(),
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="x",
                        manager=manager,
                    )

        # Compiled mock returned.
        assert compiled is mock_compiled

        # ``question_pause_node`` must appear as the first positional arg
        # of an ``add_node(...)`` call (the node name is the string that
        # LangGraph uses internally for routing).
        add_node_calls = mock_graph_instance.add_node.call_args_list
        node_names = [call.args[0] for call in add_node_calls]
        assert "question_pause_node" in node_names, (
            f"Expected 'question_pause_node' in add_node calls, "
            f"got: {node_names}"
        )


# =============================================================================
# create_post_tools_router
# =============================================================================


class TestCreatePostToolsRouter:
    """The router reads ``manager.is_question_pause_requested`` and chooses the next node."""

    def test_router_returns_agent_when_flag_not_set(self):
        """When the pause flag is False, the router routes back to ``"agent"``."""
        manager = make_manager()
        # flag stays False — manager has never called ``set_question_pause_requested``.

        router = create_post_tools_router(manager)
        next_node = router({}, config={"configurable": {"thread_id": "iid"}})

        assert next_node == "agent"

    def test_router_returns_question_pause_node_when_flag_set(self):
        """When the pause flag is True, the router routes to ``"question_pause_node"``."""
        manager = make_manager()
        manager.set_question_pause_requested("iid")
        assert manager.is_question_pause_requested("iid") is True

        router = create_post_tools_router(manager)
        next_node = router({}, config={"configurable": {"thread_id": "iid"}})

        assert next_node == "question_pause_node"


# =============================================================================
# create_question_pause_node — C2 deferred-pause contract
# =============================================================================


class TestQuestionPauseNodeDeferred:
    """``question_pause_node`` must set the deferred marker (NOT the cascade).

    After the C2 fix, the node no longer calls ``pause_instance_cascade``
    from inside the graph task. Instead it sets
    ``manager.set_deferred_question_pause(instance_id)`` and the actual
    cascade is invoked from the post-graph completion path in
    ``daemon.services.instance_messaging`` (see
    ``test_question_deferred_pause_callback.py``).

    These tests pin the in-graph contract so a future refactor cannot
    accidentally re-introduce the self-cancel cascade.
    """

    async def test_node_sets_deferred_marker_and_returns_empty(self):
        """``question_pause_node`` sets the deferred marker and returns ``{}``.

        Verifies the C2 contract:

          * ``manager.set_deferred_question_pause`` was called once with
            ``instance_id``.
          * ``manager.pause_instance_cascade`` was NOT called (no
            self-cancel from inside the graph task).
          * ``manager.clear_question_pause_requested`` was called in the
            ``finally`` block so the conditional-edge flag does not stick.
          * Node return value is ``{}`` so LangGraph routes to END.
        """
        manager = make_manager()

        node = create_question_pause_node(manager)
        result = await node(
            {},
            config={"configurable": {"thread_id": "iid"}},
        )

        # C2: deferred marker set, cascade NOT called.
        manager.set_deferred_question_pause.assert_called_once_with("iid")
        manager.pause_instance_cascade.assert_not_called()

        # F2: conditional-edge flag cleared in finally.
        manager.clear_question_pause_requested.assert_called_once_with("iid")
        assert manager.is_question_pause_requested("iid") is False

        # LangGraph sees a normal return and routes to END.
        assert result == {}

    async def test_node_clears_flag_even_if_set_deferred_raises(self):
        """``clear_question_pause_requested`` must still run if the setter raises.

        The C2 implementation wraps ``set_deferred_question_pause`` in a
        ``try`` with ``clear_question_pause_requested`` in ``finally``. A
        failure to set the marker must not strand the conditional-edge
        flag (it would re-pause the instance on the next tool call).
        """
        manager = make_manager()
        manager.set_deferred_question_pause.side_effect = RuntimeError(
            "marker backend exploded"
        )

        node = create_question_pause_node(manager)

        with pytest.raises(RuntimeError, match="marker backend exploded"):
            await node({}, config={"configurable": {"thread_id": "iid"}})

        # F2 invariant: the conditional-edge flag is cleared even on
        # exception from the setter. ``pause_instance_cascade`` is still
        # untouched — a transient marker failure cannot silently trigger
        # the in-graph cascade (that would be the C2 bug).
        manager.clear_question_pause_requested.assert_called_once_with("iid")
        manager.pause_instance_cascade.assert_not_called()


class TestManagerDeferredPauseSemantics:
    """``InstanceManager.set_deferred_question_pause`` / ``pop_deferred_question_pause``.

    The two methods form an atomic check-and-remove pair on the per-
    instance deferred-pause marker set. The post-graph completion path in
    ``daemon.services.instance_messaging`` relies on ``pop`` returning
    ``True`` for an instance that should be paused and ``False`` for one
    that should not — these are the unit-level invariants those callers
    consume.
    """

    def test_set_then_pop_returns_true(self):
        """Pop on a fresh marker returns ``True`` (cascade should run)."""
        manager = make_bare_manager()

        manager.set_deferred_question_pause("iid")
        # Marker is observable on the backing set before pop.
        assert "iid" in manager._deferred_question_pause

        assert manager.pop_deferred_question_pause("iid") is True
        # Pop is destructive — the marker is gone afterwards.
        assert "iid" not in manager._deferred_question_pause

    def test_pop_without_set_returns_false(self):
        """Pop on an instance that never set a marker returns ``False``.

        No cascade should fire for an instance whose question_pause_node
        never ran (e.g. when a different tool produced the post-tools
        event, or the graph was cancelled before reaching the node).
        """
        manager = make_bare_manager()

        assert manager.pop_deferred_question_pause("iid") is False

    def test_pop_is_idempotent(self):
        """Calling ``pop`` twice in a row returns ``True`` then ``False``."""
        manager = make_bare_manager()
        manager.set_deferred_question_pause("iid")

        assert manager.pop_deferred_question_pause("iid") is True
        # Second pop sees an empty set — cascade does NOT run twice.
        assert manager.pop_deferred_question_pause("iid") is False

    def test_markers_are_per_instance(self):
        """Setting one instance's marker does not leak to another."""
        manager = make_bare_manager()

        manager.set_deferred_question_pause("a")
        manager.set_deferred_question_pause("b")

        assert manager.pop_deferred_question_pause("a") is True
        # ``b`` survived the ``a`` pop.
        assert "b" in manager._deferred_question_pause
        assert manager.pop_deferred_question_pause("b") is True
        assert manager.pop_deferred_question_pause("c") is False


class TestCleanupDropsDeferredMarker:
    """``_cleanup_instance_state`` must discard any pending deferred marker.

    Without this discard, a fresh instance that reuses the same
    ``instance_id`` (e.g. after daemon restart, or a manual hard-delete
    + recreate) could inherit a stuck "pause pending" state and silently
    trigger ``pause_instance_cascade`` on the next graph completion. The
    cleanup helper is the single place that resets all per-instance
    in-memory state.
    """

    def _make_cleanup_ready_manager(self) -> Any:
        """Bare manager with the minimum attrs ``_cleanup_instance_state`` touches.

        Uses ``__new__`` to skip the heavy ``__init__``; collaborators that
        ``_cleanup_instance_state`` calls are stubbed via MagicMock so the
        test focuses exclusively on the deferred-marker discard line.
        """
        manager = make_bare_manager()
        manager._graph_tasks = {}
        manager._pending_injections = {}
        manager._gii_throttle = {}
        # ``release_context_usage_cache`` is a method on the real manager;
        # stub it so the cleanup call doesn't reach real DB code.
        manager.release_context_usage_cache = MagicMock()
        # ``clear_question_pause_requested`` on the real manager mutates
        # ``self._question_pause_requested`` — stub it so we don't need to
        # seed that dict.
        manager.clear_question_pause_requested = MagicMock()
        # ``_question_manager.clear_question_pack`` is a method on the
        # QuestionManager; mock the whole question manager.
        manager._question_manager = MagicMock()
        return manager

    def test_cleanup_instance_state_discards_deferred_marker(self):
        """After ``_cleanup_instance_state``, ``pop_deferred_question_pause`` returns False."""
        from daemon.manager import InstanceManager

        manager = self._make_cleanup_ready_manager()
        # Pre-condition: marker is set (mimics what ``question_pause_node``
        # would do before cleanup ran).
        manager.set_deferred_question_pause("iid")
        assert manager.pop_deferred_question_pause("iid") is True
        # Re-set, since the assertion pop above already consumed it.
        manager.set_deferred_question_pause("iid")
        assert "iid" in manager._deferred_question_pause

        # The decisive call: centralised cleanup MUST drop the marker.
        cleanup_result = InstanceManager._cleanup_instance_state(manager, "iid")

        # Post-condition: the marker is gone — a subsequent graph
        # completion for a same-id fresh instance won't trigger a
        # stealth cascade.
        assert manager.pop_deferred_question_pause("iid") is False
        # The cleanup helper returned its standard contract dict so
        # callers (e.g. the pause-cascade path) can still forward the
        # cleared task / injection to SSE.
        assert isinstance(cleanup_result, dict)
        assert "graph_task" in cleanup_result
        assert "cleared_injection" in cleanup_result
        assert cleanup_result["context_usage_cleared"] is True

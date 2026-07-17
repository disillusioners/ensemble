"""Tests for the question-tool graph wiring in ``daemon.graph``.

Covers the Phase 1 / F1 + F2 + F4 invariants:

  * ``build_instance_graph`` must call ``create_question_pause_node(manager)``
    when a ``manager`` is supplied (the regression that produced the
    ``NameError: name 'question_pause_node' is not defined`` bug).
  * ``create_post_tools_router`` must read the per-instance pause flag from
    the manager and route to ``"question_pause_node"`` when set, back to
    ``"agent"`` otherwise.
  * ``create_question_pause_node`` must clear the pause flag from its
    ``finally`` block on both the ``CancelledError`` path (F2) and on any
    other exception (F4 / defense-in-depth), and must re-raise the
    underlying exception so LangGraph's cancellation contract is honored.

All tests mock out the LLM + LangGraph surface (same pattern as
``tests/unit/test_nudge_behavior.py``) so they run without a real server
and without touching the database.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

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


def make_manager(
    pause_behavior: Callable[[str], Awaitable[None]] | None = None,
) -> MagicMock:
    """Build a mock ``InstanceManager`` with the question-pause surface wired.

    Wires four manager methods exactly the way the question tool and the
    graph node consume them:

      * ``set_question_pause_requested(instance_id)`` — flag setter.
      * ``is_question_pause_requested(instance_id)`` — flag getter.
      * ``clear_question_pause_requested(instance_id)`` — flag clear,
        called from ``question_pause_node``'s ``finally`` block.
      * ``pause_instance_cascade(instance_id)`` — async method whose
        behavior is driven by the ``pause_behavior`` callable. When the
        callable returns normally the cascade is a no-op; when it raises
        the exception propagates through ``question_pause_node``.

    Flag state is backed by a real dict so ``is_question_pause_requested``
    after a ``set`` returns ``True`` and after a ``clear`` returns ``False``
    — matches the contract the production ``InstanceManager`` exposes.

    Args:
        pause_behavior: Optional async callable ``(instance_id) -> None``
            that will be assigned as the side effect of
            ``manager.pause_instance_cascade``. Tests that need the cascade
            to raise ``asyncio.CancelledError`` or ``RuntimeError`` pass a
            callable that raises; the default is a no-op async function.

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

    if pause_behavior is None:
        async def _no_op_pause(instance_id: str) -> None:
            return None
        pause_behavior = _no_op_pause

    # ``AsyncMock`` so ``await manager.pause_instance_cascade(...)`` works.
    # ``side_effect`` set to the user's callable — when that callable
    # raises, the AsyncMock re-raises it on ``await``.
    manager.pause_instance_cascade = AsyncMock(side_effect=pause_behavior)

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
# create_question_pause_node — finally-block invariants
# =============================================================================


class TestQuestionPauseNodeFinally:
    """``question_pause_node`` must clear the pause flag in ``finally`` and re-raise.

    Two paths are exercised:

      * The success path of ``pause_instance_cascade`` raises
        ``asyncio.CancelledError`` (because the cascade cancels the
        graph task mid-execution). The node must re-raise and still
        clear the flag in ``finally`` (F2).
      * Any other exception propagates after the flag is cleared (F4 /
        defense-in-depth so a transient cascade failure cannot leave
        the flag set and cause a stuck-pause loop on the next resume).
    """

    async def test_finally_clears_flag_on_cancelled_error(self):
        """``asyncio.CancelledError`` from the cascade must re-raise and clear the flag."""
        async def _raise_cancelled(instance_id: str) -> None:
            raise asyncio.CancelledError()

        manager = make_manager(pause_behavior=_raise_cancelled)
        # Set the flag so we can prove the finally block cleared it.
        manager.set_question_pause_requested("iid")
        assert manager.is_question_pause_requested("iid") is True

        node = create_question_pause_node(manager)

        with pytest.raises(asyncio.CancelledError):
            await node({}, config={"configurable": {"thread_id": "iid"}})

        # finally ran — flag was cleared despite the CancelledError.
        manager.clear_question_pause_requested.assert_called_with("iid")

    async def test_finally_clears_flag_on_regular_exception_and_propagates(self):
        """Any non-CancelledError from the cascade must clear the flag and re-raise."""
        async def _raise_boom(instance_id: str) -> None:
            raise RuntimeError("boom")

        manager = make_manager(pause_behavior=_raise_boom)
        manager.set_question_pause_requested("iid")
        assert manager.is_question_pause_requested("iid") is True

        node = create_question_pause_node(manager)

        with pytest.raises(RuntimeError):
            await node({}, config={"configurable": {"thread_id": "iid"}})

        # Non-CancelledError path also clears the flag (F4 defense-in-depth).
        manager.clear_question_pause_requested.assert_called_with("iid")
"""Legacy single-shot error contract for ``generate_chart``.

Pins the coverage gap left by
``test_generate_chart_handles_none_result_as_error``
(test_chart_tools.py:200), which covers only the ``None``-result case.
These tests pin that when ``invoke_agent_and_wait`` RAISES, the legacy
fresh path (no prior charter) still returns an ``"Error: ..."`` string —
never raising the exception into the LLM tool loop.

Harness mirrors ``tests/test_chart_tools.py::_make_manager``: empty
discovery (``get_children → []``) forces the legacy fresh path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def _make_manager() -> MagicMock:
    """Mock manager wired for the legacy fresh path (discovery empty)."""
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager._instance_repository.get_children = MagicMock(return_value=[])
    manager.enqueue_message = AsyncMock(return_value=MagicMock())
    return manager


async def test_invoke_raise_returns_error_string_not_exception():
    """``invoke_agent_and_wait`` RAISES → ``generate_chart`` returns 'Error:'."""
    from daemon.tools.chart_tools import create_chart_tools

    manager = _make_manager()
    mock_invoke = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
        tools = create_chart_tools(manager, "test-instance-id")
        result = await tools[0].coroutine(description="Graph")

    assert isinstance(result, str)
    assert result.startswith("Error:")
    # Spawn attempted exactly once (legacy path has no retry-on-raise).
    mock_invoke.assert_awaited_once()


async def test_invoke_none_result_returns_error_string():
    """Negative control: ``(None, id)`` → ``'Error: ...'`` (mirrors :200)."""
    from daemon.tools.chart_tools import create_chart_tools

    manager = _make_manager()
    mock_invoke = AsyncMock(return_value=(None, "child-id"))

    with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
        tools = create_chart_tools(manager, "test-instance-id")
        result = await tools[0].coroutine(description="Graph")

    assert isinstance(result, str)
    assert result.startswith("Error:")

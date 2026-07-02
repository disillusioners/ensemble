"""Tests for ``daemon.tools.chart_tools.create_chart_tools`` and ``generate_chart``.

Three coverage lanes:

  1. **Factory** — ``create_chart_tools(manager, current_instance_id)`` returns
     a list with exactly one tool, ``generate_chart``.
  2. **Registration** — the returned tool is registered under the
     ``"chart"`` category (via the ``_tool_category`` attribute set by
     ``@register_tool_category``), NOT the ``"instance"`` category. This is
     the category counterpart of the ``test_tool_filter.py`` security test
     that pins ``INNATE_SKILL_TOOL_CATEGORIES``.
  3. **Invocation** — calling ``generate_chart`` delegates to
     ``invoke_agent_and_wait`` with the correct parameters
     (``agent_id="charter"``, ``return_instance_id=True``, ``timeout=300.0``)
     and constructs a message containing the description and diagram_type.

The mocking pattern mirrors ``tests/test_spawn_team_members.py``: a
``MagicMock`` manager with a ``_instance_repository.get`` that returns
``None`` (no project context to keep tests deterministic), and
``invoke_agent_and_wait`` patched at the chart_tools module level.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_manager() -> MagicMock:
    """Build a mock manager wired for ``generate_chart`` invocation.

    The chart tool calls ``manager._instance_repository.get(...)`` to
    auto-inherit project_id; returning ``None`` keeps the project_id
    auto-injection path deterministic (no project context). The tool
    also passes the manager through to ``invoke_agent_and_wait``.
    """
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    return manager


class TestCreateChartToolsFactory:
    """Factory tests for ``create_chart_tools``."""

    def test_factory_returns_exactly_one_tool(self):
        """Factory returns a list containing exactly one tool."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        assert isinstance(tools, list)
        assert len(tools) == 1

    def test_factory_returns_generate_chart_tool(self):
        """The returned tool is ``generate_chart``."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        # ``@tool`` from langchain exposes the function name via .name
        assert tools[0].name == "generate_chart"

    def test_factory_creates_independent_tools_per_call(self):
        """Each factory call produces a fresh closure (no shared state).

        The two ``generate_chart`` tools should be distinct objects so that
        a per-instance tool list does not leak state between instances.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools_a = create_chart_tools(manager, "instance-a")
        tools_b = create_chart_tools(manager, "instance-b")

        # Same name, distinct objects (each call re-binds closures).
        assert tools_a[0] is not tools_b[0]
        assert tools_a[0].name == tools_b[0].name


class TestChartToolRegistration:
    """Registration tests for the chart tool category."""

    def test_generate_chart_registered_under_chart_category(self):
        """The tool is tagged with ``_tool_category == "chart"``.

        Set by ``@register_tool_category("chart")`` in ``chart_tools.py``.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) == "chart"

    def test_generate_chart_not_registered_under_instance_category(self):
        """SECURITY: the chart tool must NOT be tagged as ``"instance"``.

        Companion to the ``INNATE_SKILL_TOOL_CATEGORIES`` security test in
        ``test_tool_filter.py``. If this ever fails, a chart-enabled agent
        would be implicitly granted the full instance-management suite.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) != "instance"


class TestGenerateChartInvocation:
    """Invocation tests for ``generate_chart``."""

    async def test_generate_chart_delegates_to_invoke_agent_and_wait(self):
        """generate_chart calls invoke_agent_and_wait with the documented params.

        Verifies ``agent_id="charter"``, ``return_instance_id=True``,
        ``timeout=300.0``, and ``parent_id`` flowing from the closure.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=("mermaid output", "child-instance-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                description="User authentication flow",
                diagram_type="sequence",
            )

        # Called exactly once
        mock_invoke.assert_awaited_once()

        kwargs = mock_invoke.call_args.kwargs
        # Manager is passed through verbatim
        assert kwargs["manager"] is manager
        # Charter agent is the delegate
        assert kwargs["agent_id"] == "charter"
        # Always returns the (content, instance_id) tuple form
        assert kwargs["return_instance_id"] is True
        # 5-minute timeout (matches knowledge_tools.explore() default)
        assert kwargs["timeout"] == 300.0
        # parent_id is the calling instance
        assert kwargs["parent_id"] == "test-instance-id"

        # Message carries the description and diagram_type
        message = kwargs["message"]
        assert "User authentication flow" in message
        assert "sequence" in message

    async def test_generate_chart_default_diagram_type_is_flowchart(self):
        """When ``diagram_type`` is omitted, the message uses ``flowchart``."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=("output", "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            await tools[0].coroutine(description="Some flow")

        message = mock_invoke.call_args.kwargs["message"]
        assert "flowchart" in message

    async def test_generate_chart_returns_agent_response(self):
        """Tool returns the agent's response content string."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        expected = "```mermaid\ngraph TD\nA-->B\n```\n\nThis is the flow."
        mock_invoke = AsyncMock(return_value=(expected, "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(description="Graph")

        assert result == expected

    async def test_generate_chart_handles_none_result_as_error(self):
        """``None`` content from ``invoke_agent_and_wait`` → ``Error:`` string.

        Mirrors the contract in ``chart_tools.py``: when ``invoke_agent_and_wait``
        returns ``(None, instance_id)`` (the agent never produced a result),
        the tool returns a short error message rather than bubbling ``None``
        to the LLM (which would crash downstream parsing).
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=(None, "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(description="Graph")

        assert isinstance(result, str)
        assert result.startswith("Error:")

    async def test_generate_chart_propagates_explicit_project_id(self):
        """``project_id`` kwarg flows into the message + invoke_agent_and_wait."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=("output", "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                description="Architecture overview",
                diagram_type="flowchart",
                project_id="my-project-id",
            )

        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["project_id"] == "my-project-id"
        # Project id is included in the message for charter's context
        assert "my-project-id" in kwargs["message"]
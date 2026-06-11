"""Tests for MCP tool call timeout feature.

Covers:
- _wrap_with_timeout: timeout fires, success under timeout
- adapt_mcp_tools: timeout passthrough (wrapped) and disabled (not wrapped)
- McpPoolConfig.tool_call_timeout: validation (ge=0, default=120)
- ToolNode integration: timeout ToolException handled gracefully
"""
import sys
import asyncio

import pytest
from pydantic import BaseModel, ValidationError
from langchain_core.tools import StructuredTool, ToolException
from langchain_core.messages import AIMessage, ToolMessage

# ---------------------------------------------------------------------------
# Unmock daemon.mcp.tool_adapter so we import the REAL module (the conftest
# mock lacks _wrap_with_timeout and the timeout-aware adapt_mcp_tools).
# ---------------------------------------------------------------------------
_mock_tool_adapter = sys.modules.pop("daemon.mcp.tool_adapter", None)
from daemon.mcp.tool_adapter import adapt_mcp_tools, _wrap_with_timeout  # noqa: E402
if _mock_tool_adapter is not None:
    sys.modules["daemon.mcp.tool_adapter"] = _mock_tool_adapter

# ---------------------------------------------------------------------------
# Unmock langgraph so ToolNode is the real class (needed for integration test).
# ---------------------------------------------------------------------------
_langgraph_mocks = {}
for _key in list(sys.modules):
    if _key.startswith("langgraph"):
        _langgraph_mocks[_key] = sys.modules.pop(_key)
from langgraph.prebuilt import ToolNode  # noqa: E402
for _key, _mod in _langgraph_mocks.items():
    sys.modules[_key] = _mod

from daemon.config import McpPoolConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _NoArgsSchema(BaseModel):
    """Empty schema for tools that take no arguments.

    Required by ``StructuredTool``'s Pydantic validation, and accepted
    cleanly by ``ToolNode`` when the LLM emits ``args={}``.
    """


def _make_tool(name: str, coro, description: str = "test tool") -> StructuredTool:
    """Create a StructuredTool with an async coroutine and an empty args schema.

    The empty schema satisfies ``StructuredTool.__init__`` validation while
    keeping the tool callable with empty ``args={}`` (as produced by LLMs
    that invoke no-argument MCP tools). The returned tool still supports
    ``model_copy(update={...})`` used by ``_wrap_with_timeout`` and
    ``adapt_mcp_tools``.
    """
    return StructuredTool(
        name=name,
        description=description,
        args_schema=_NoArgsSchema,
        func=lambda **kwargs: "sync",
        coroutine=coro,
    )


class TestWrapWithTimeout:
    """Tests for _wrap_with_timeout."""

    @pytest.mark.asyncio
    async def test_timeout_fires(self):
        """ToolException raised when coroutine exceeds timeout."""
        async def slow(**kwargs):
            await asyncio.sleep(10)
            return "done"

        tool = _make_tool("slow_tool", slow)
        wrapped = _wrap_with_timeout(tool, 0.1)

        with pytest.raises(ToolException, match="timed out"):
            await wrapped.coroutine()

    @pytest.mark.asyncio
    async def test_success_under_timeout(self):
        """Result returned unchanged when coroutine finishes within timeout."""
        async def fast(**kwargs):
            return "fast_result"

        tool = _make_tool("fast_tool", fast)
        wrapped = _wrap_with_timeout(tool, 5.0)

        result = await wrapped.coroutine()
        assert result == "fast_result"


class TestAdaptMcpToolsTimeout:
    """Tests for adapt_mcp_tools timeout wrapping behavior."""

    def test_config_passthrough_wraps_coroutine(self):
        """When tool_call_timeout > 0, returned tool coroutine is wrapped."""
        async def coro(**kwargs):
            return "result"

        tool = _make_tool("my_tool", coro)
        adapted = adapt_mcp_tools("test_server", [tool], tool_call_timeout=5)

        assert len(adapted) == 1
        # The adapted tool's coroutine should differ from the original
        assert adapted[0].coroutine is not tool.coroutine

    def test_zero_timeout_does_not_wrap(self):
        """When tool_call_timeout == 0, coroutine is NOT wrapped."""
        async def coro(**kwargs):
            return "result"

        tool = _make_tool("my_tool", coro)
        adapted = adapt_mcp_tools("test_server", [tool], tool_call_timeout=0)

        assert len(adapted) == 1
        # The adapted tool's coroutine should be the SAME as the original
        assert adapted[0].coroutine is tool.coroutine


class TestMcpPoolConfigValidation:
    """Tests for McpPoolConfig tool_call_timeout field validation."""

    def test_zero_timeout_is_valid(self):
        """tool_call_timeout=0 means disabled and should not raise."""
        config = McpPoolConfig(tool_call_timeout=0)
        assert config.tool_call_timeout == 0

    def test_negative_timeout_raises(self):
        """Negative tool_call_timeout should raise ValidationError."""
        with pytest.raises(ValidationError):
            McpPoolConfig(tool_call_timeout=-1)

    def test_positive_timeout_works(self):
        """Positive tool_call_timeout should be accepted."""
        config = McpPoolConfig(tool_call_timeout=120)
        assert config.tool_call_timeout == 120

    def test_default_is_120(self):
        """Default tool_call_timeout should be 120."""
        config = McpPoolConfig()
        assert config.tool_call_timeout == 120


class TestToolNodeIntegration:
    """Integration test: ToolNode gracefully handles MCP tool timeout."""

    @pytest.mark.asyncio
    async def test_tool_node_handles_timeout(self):
        """ToolNode with handle_tool_errors=True catches timeout ToolException.

        The graph should continue execution (no exception raised) and return
        a ToolMessage with status='error' containing the timeout message.
        """
        # ToolNode.ainvoke dispatches to a RunnableCallable that requires a
        # `runtime` injected via config (normally supplied by the graph).
        from langgraph._internal._runnable import CONF, CONFIG_KEY_RUNTIME
        from langgraph.runtime import Runtime

        async def slow(**kwargs):
            await asyncio.sleep(10)
            return "done"

        tool = _make_tool("slow_tool", slow)
        adapted = adapt_mcp_tools("test", [tool], tool_call_timeout=0.1)

        tool_node = ToolNode(adapted, handle_tool_errors=True)

        adapted_name = adapted[0].name  # "mcp_test_slow_tool"
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": adapted_name,
                    "args": {},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )

        runtime = Runtime()
        result = await tool_node.ainvoke(
            {"messages": [ai_msg]},
            config={CONF: {CONFIG_KEY_RUNTIME: runtime}},
        )

        messages = result["messages"]
        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, ToolMessage)
        assert msg.status == "error"
        assert "timed out" in msg.content

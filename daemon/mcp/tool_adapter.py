"""Tool naming and adaptation utilities for MCP tools."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from langchain_core.tools import ToolException

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """
    Convert a name to a slug format.

    Converts to lowercase, replaces hyphens and spaces with underscores,
    and removes non-alphanumeric characters except underscores.

    Args:
        name: The name to slugify

    Returns:
        Slugified name with only alphanumeric characters and underscores
    """
    # Lowercase and replace hyphens/spaces with underscores
    slug = name.lower().replace("-", "_").replace(" ", "_")
    # Remove special characters (keep only alphanumeric and underscores)
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return slug


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """
    Generate a prefixed name for an MCP tool.

    Format: mcp_{slugified_server_name}_{tool_name}

    Args:
        server_name: Name of the MCP server
        tool_name: Name of the tool on that server

    Returns:
        Prefixed tool name in format mcp_{server}_{tool}
    """
    slugified_server = _slugify(server_name)
    return f"mcp_{slugified_server}_{tool_name}"


def is_mcp_tool(tool_name: str) -> bool:
    """
    Check if a tool name is an MCP tool.

    An MCP tool name must:
    - Start with 'mcp_'
    - Contain at least one underscore after 'mcp_'

    Args:
        tool_name: The tool name to check

    Returns:
        True if the tool name represents an MCP tool, False otherwise
    """
    if not tool_name.startswith("mcp_"):
        return False
    # Must have at least one underscore after 'mcp_'
    return "_" in tool_name[4:]


def _wrap_with_timeout(tool: BaseTool, timeout_seconds: float) -> BaseTool:
    """Wrap a tool's coroutine with an asyncio timeout.

    Returns a new tool whose coroutine is the original coroutine
    guarded by asyncio.timeout(). On TimeoutError, raises
    ToolException so LangGraph's ToolNode can handle it gracefully.
    """
    original_coroutine = tool.coroutine

    async def _timed_coroutine(**kwargs):
        try:
            async with asyncio.timeout(timeout_seconds):
                return await original_coroutine(**kwargs)
        except asyncio.TimeoutError:
            tool_name = getattr(tool, "name", "<unknown>")
            logger.warning(
                f"MCP tool '{tool_name}' exceeded timeout of {timeout_seconds}s"
            )
            raise ToolException(
                f"Tool '{tool_name}' timed out after {timeout_seconds}s. "
                f"The MCP server may be unresponsive."
            )

    return tool.model_copy(update={"coroutine": _timed_coroutine})


def adapt_mcp_tools(
    server_name: str,
    tools: list[BaseTool],
    tool_call_timeout: int = 120,
) -> list[BaseTool]:
    """
    Adapt MCP tools by prefixing their names and updating descriptions.

    Prefixes each tool name with 'mcp_{slugified_server_name}_' and
    adds '[MCP:server_name]' to the tool description.

    Args:
        server_name: Name of the MCP server
        tools: List of MCP tools to adapt
        tool_call_timeout: Per-tool call timeout in seconds. Set to 0
            to disable timeout wrapping. Defaults to 120s.

    Returns:
        List of adapted tools with prefixed names, updated descriptions,
        and timeout-wrapped coroutines.
    """
    if not tools:
        return tools

    slugified_server = _slugify(server_name)
    prefix = f"mcp_{slugified_server}_"
    description_suffix = f"[MCP:{server_name}]"

    adapted_tools: list[BaseTool] = []

    for tool in tools:
        # Create a copy of the tool with adapted name, description, and coroutine
        new_name = f"{prefix}{tool.name}"
        new_description = f"{tool.description} {description_suffix}"

        adapted_tool = tool.model_copy(
            update={"name": new_name, "description": new_description}
        )

        adapted_tools.append(adapted_tool)
        logger.debug(f"Adapted MCP tool: {tool.name} -> {new_name}")

    # Wrap with timeout (applies to all adapted tools)
    if tool_call_timeout > 0:
        adapted_tools = [
            _wrap_with_timeout(t, tool_call_timeout) for t in adapted_tools
        ]

    return adapted_tools

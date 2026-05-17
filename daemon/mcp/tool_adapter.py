"""Tool naming and adaptation utilities for MCP tools."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

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


def adapt_mcp_tools(server_name: str, tools: list[BaseTool]) -> list[BaseTool]:
    """
    Adapt MCP tools by prefixing their names and updating descriptions.

    Prefixes each tool name with 'mcp_{slugified_server_name}_' and
    adds '[MCP:server_name]' to the tool description.

    Args:
        server_name: Name of the MCP server
        tools: List of MCP tools to adapt

    Returns:
        List of adapted tools with prefixed names and updated descriptions
    """
    if not tools:
        return tools

    slugified_server = _slugify(server_name)
    prefix = f"mcp_{slugified_server}_"
    description_suffix = f"[MCP:{server_name}]"

    adapted_tools: list[BaseTool] = []

    for tool in tools:
        # Create a copy of the tool with adapted name
        new_name = f"{prefix}{tool.name}"
        new_description = f"{tool.description} {description_suffix}"

        # Clone the tool with new attributes
        adapted_tool = tool.copy()
        adapted_tool.name = new_name
        adapted_tool.description = new_description

        # Update the function schema if it exists
        if hasattr(adapted_tool, "args_schema") and adapted_tool.args_schema is not None:
            schema = adapted_tool.args_schema
            if hasattr(schema, "model_fields"):
                # Pydantic v2 model
                pass  # Schema inherits from tool, already copied
            elif hasattr(schema, "__fields__"):
                # Pydantic v1 model
                pass  # Schema inherits from tool, already copied

        adapted_tools.append(adapted_tool)
        logger.debug(f"Adapted MCP tool: {tool.name} -> {new_name}")

    return adapted_tools

"""Help tool for discovering and learning about available tools.

This module provides a tool_help() function that allows agents to:
- List all available tools grouped by category
- Get detailed documentation for a specific tool
- List tools by category

The help output is filtered based on the agent's tool configuration.
"""

import logging
from langchain_core.tools import tool

from ._tool_registry import (
    get_full_doc,
    get_tool_categories,
    get_category_doc,
    list_tools_by_category,
    scan_tools_for_full_docs,
    CATEGORY_MODULES,
    register_tool_category,
)

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Help"
CATEGORY_DOC = """\
Get help on available tools. List all tools, get docs for a specific tool, or list tools by category.

Usage:
- `tool_help()` — List all available tools
- `tool_help("tool_name")` — Detailed docs for a specific tool
- `tool_help(category="project")` — List tools by category
"""


def _get_allowed_tools(agent_id: str, mcp_tool_names: list[str] | None = None) -> set[str] | None:
    """Get the set of allowed tools for an agent.
    
    Args:
        agent_id: The agent identifier.
        mcp_tool_names: Optional list of MCP tool names for category expansion.
    
    Returns:
        Set of allowed tool names, or None if all tools are allowed.
    """
    from ..registry import get_registry
    from .instance import resolve_tool_filter, expand_allow_for_innate_skills

    registry = get_registry()
    agent_meta = registry.get_resolved(agent_id)

    if agent_meta is None or agent_meta.tools is None:
        return None

    # Build all_tool_names set for MCP category expansion
    all_tool_names: set[str] | None = None
    if mcp_tool_names:
        all_tool_names = set(mcp_tool_names)

    # Innate skills imply certain tool categories (see INNATE_SKILL_TOOL_CATEGORIES)
    effective_allow = expand_allow_for_innate_skills(
        agent_meta.tools.allow,
        agent_meta.innate_skills,
    )

    return resolve_tool_filter(
        allow=effective_allow,
        deny=agent_meta.tools.deny,
        all_tool_names=all_tool_names,
    )


def create_help_tool(all_tools: list, agent_id: str, mcp_tool_names: list[str] | None = None):
    """Create a help tool that provides filtered documentation for tools.
    
    This should be called AFTER all other tools are created, so it can
    scan them for documentation.
    
    Args:
        all_tools: List of all tool functions in the session.
        agent_id: The agent identifier for filtering.
        mcp_tool_names: Optional list of MCP tool names for category expansion.
    
    Returns:
        A tool_help tool function.
    """
    # Scan tools for _full_doc_ attributes
    scan_tools_for_full_docs(all_tools)
    
    # Build tool index for quick lookup
    tool_index = {}
    for t in all_tools:
        tool_name = getattr(t, 'name', None) or getattr(t, '__name__', str(t))
        short_doc = ""
        if hasattr(t, 'description'):
            short_doc = t.description.split('\n')[0]
        elif hasattr(t, '__doc__') and t.__doc__:
            short_doc = t.__doc__.split('\n')[0]
        
        tool_index[tool_name] = {
            "short_doc": short_doc,
            "func": t,
        }
    
    @register_tool_category("help")
    @tool
    def tool_help(tool_name: str | None = None, category: str | None = None) -> str:
        """Get help for tools. Call without args to list all tools.
        
        Args:
            tool_name: Specific tool to get detailed help for.
            category: List tools in a category (e.g., "project", "instance", "bash").
        
        Returns:
            Help text with tool documentation or tool list.
        
        Examples:
            tool_help()                    # List all tools
            tool_help("project_create")    # Get docs for project_create
            tool_help(category="project")  # List project tools
        """
        # Get allowed tools for this agent (pass MCP tool names for category expansion)
        allowed_tools = _get_allowed_tools(agent_id, mcp_tool_names)
        
        # Get help for specific tool
        if tool_name:
            # Check if tool is allowed
            if allowed_tools is not None and tool_name not in allowed_tools:
                return f"Tool '{tool_name}' not found or not available."
            
            full_doc = get_full_doc(tool_name)
            if full_doc:
                return f"## {tool_name}\n\n{full_doc}"
            
            # Fallback to short doc
            if tool_name in tool_index:
                short = tool_index[tool_name]["short_doc"]
                return f"## {tool_name}\n\n{short}\n\nNo detailed documentation available."
            
            # Suggest similar tools
            similar = [name for name in tool_index.keys() 
                      if tool_name.lower() in name.lower()]
            if similar:
                return f"Tool '{tool_name}' not found or not available."
            
            return f"Tool '{tool_name}' not found or not available."
        
        # List tools by category
        if category:
            # Normalize category key
            category_key = category.lower()
            
            # Check if category exists
            if category_key not in CATEGORY_MODULES:
                return f"Category '{category}' not found or not available."
            
            # Get tools in this category from registry
            all_category_tools = list_tools_by_category()
            category_tools = all_category_tools.get(category_key, [])
            
            # Filter by allowed tools
            if allowed_tools is not None:
                category_tools = [t for t in category_tools if t in allowed_tools]
            
            if not category_tools:
                return f"Category '{category}' not found or not available."
            
            # Get category documentation
            cat_result = get_category_doc(category_key)
            if cat_result:
                cat_name, cat_doc = cat_result
            else:
                cat_name = category.title()
                cat_doc = ""
            
            lines = [f"## {cat_name}"]
            if cat_doc:
                lines.append(f"\n{cat_doc}")
            lines.append("\n### Available tools:")
            lines.append("Use `tool_help(\"tool_name\")` for detailed docs.\n")
            
            for tool_nm in sorted(category_tools):
                if tool_nm in tool_index:
                    lines.append(f"- **{tool_nm}**: {tool_index[tool_nm]['short_doc']}")
            
            return "\n".join(lines)
        
        # List all tools grouped by category (filtered by allowed tools)
        categories = get_tool_categories(allowed_tools)
        
        if not categories:
            return "No tools available."
        
        lines = ["# Available Tools\n"]
        lines.append("Use `tool_help(\"tool_name\")` for detailed documentation.\n")
        lines.append("Use `tool_help(category=\"name\")` to list tools by category.\n")
        
        for cat_name in sorted(categories.keys()):
            tool_names = sorted(categories[cat_name])
            lines.append(f"\n## {cat_name}")
            for tool_nm in tool_names:
                if tool_nm in tool_index:
                    lines.append(f"- **{tool_nm}**: {tool_index[tool_nm]['short_doc']}")
        
        return "\n".join(lines)
    
    return tool_help

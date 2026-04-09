"""Help tool for discovering and learning about available tools.

This module provides a tool_help() function that allows agents to:
- List all available tools grouped by category
- Get detailed documentation for a specific tool
- List tools by category
"""

from langchain_core.tools import tool

from ._tool_registry import (
    get_full_doc,
    list_tools,
    list_tools_by_category,
    scan_tools_for_full_docs,
)


def create_help_tool(all_tools: list):
    """Create a help tool that provides documentation for all tools.
    
    This should be called AFTER all other tools are created, so it can
    scan them for documentation.
    
    Args:
        all_tools: List of all tool functions in the session.
    
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
        # Get help for specific tool
        if tool_name:
            full_doc = get_full_doc(tool_name)
            if full_doc:
                return f"## {tool_name}\n\n{full_doc}"
            
            # Fallback to short doc
            if tool_name in tool_index:
                short = tool_index[tool_name]["short_doc"]
                return f"## {tool_name}\n\n{short}\n\nNo detailed documentation available."
            
            # Suggest similar tools
            similar = [name for name in tool_index.keys() if tool_name.lower() in name.lower()]
            if similar:
                return f"Tool '{tool_name}' not found. Similar tools: {', '.join(similar[:5])}"
            
            return f"Tool '{tool_name}' not found. Use tool_help() to list available tools."
        
        # List tools by category
        if category:
            matches = {k: v for k, v in tool_index.items() 
                      if k.startswith(category + "_") or k == category}
            
            if not matches:
                available = sorted(set(k.split('_')[0] for k in tool_index.keys()))
                return f"No tools in category '{category}'. Available categories: {', '.join(available)}"
            
            lines = [f"## Tools in category '{category}'\n"]
            lines.append("Use `tool_help(\"tool_name\")` for detailed docs.\n")
            for name, info in sorted(matches.items()):
                lines.append(f"- **{name}**: {info['short_doc']}")
            return "\n".join(lines)
        
        # List all tools grouped by category
        categories = {}
        for name, info in tool_index.items():
            cat = name.split("_")[0] if "_" in name else "general"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append((name, info["short_doc"]))
        
        lines = ["# Available Tools\n"]
        lines.append("Use `tool_help(\"tool_name\")` for detailed documentation.\n")
        lines.append("Use `tool_help(category=\"name\")` to list tools by category.\n")
        
        for cat in sorted(categories.keys()):
            lines.append(f"\n## {cat.title()}")
            for name, short_doc in sorted(categories[cat]):
                lines.append(f"- **{name}**: {short_doc}")
        
        return "\n".join(lines)
    
    return tool_help

"""Tool registry for storing full documentation.

This module provides a registry for tool documentation that allows
tools to have short docstrings (for LLM context efficiency) while
still providing detailed documentation via the tool_help() function.
"""

from typing import Callable, Any

# Global registry: tool_name -> full documentation string
_full_docs: dict[str, str] = {}

# Tool metadata registry: tool_name -> {category, short_doc, full_doc}
_tool_metadata: dict[str, dict[str, Any]] = {}


def register_tool(
    tool_name: str,
    category: str = "general",
    short_doc: str = "",
    full_doc: str = "",
) -> Callable:
    """Decorator to register a tool with its documentation.
    
    Usage:
        @register_tool("project_create", category="project")
        @tool
        def project_create(...):
            ...
            project_create._full_doc_ = "..."
    
    Args:
        tool_name: Unique tool identifier.
        category: Tool category for grouping (e.g., "project", "session").
        short_doc: Brief one-line description.
        full_doc: Detailed documentation.
    
    Returns:
        Decorator function.
    """
    def decorator(func: Callable) -> Callable:
        _tool_metadata[tool_name] = {
            "category": category,
            "short_doc": short_doc,
            "full_doc": full_doc,
        }
        if full_doc:
            _full_docs[tool_name] = full_doc
        return func
    return decorator


def register_full_doc(tool_name: str, full_doc: str) -> None:
    """Register full documentation for a tool.
    
    Called automatically when a tool function has a _full_doc_ attribute.
    
    Args:
        tool_name: The tool identifier.
        full_doc: Full documentation string.
    """
    _full_docs[tool_name] = full_doc


def get_full_doc(tool_name: str) -> str | None:
    """Retrieve full documentation for a tool.
    
    Args:
        tool_name: The tool identifier.
    
    Returns:
        Full documentation string, or None if not found.
    """
    return _full_docs.get(tool_name)


def get_tool_metadata(tool_name: str) -> dict[str, Any] | None:
    """Get metadata for a specific tool.
    
    Args:
        tool_name: The tool identifier.
    
    Returns:
        Dictionary with category, short_doc, full_doc, or None.
    """
    return _tool_metadata.get(tool_name)


def list_tools() -> dict[str, dict[str, Any]]:
    """List all registered tools with their metadata.
    
    Returns:
        Dictionary mapping tool names to their metadata.
    """
    return dict(_tool_metadata)


def list_tools_by_category() -> dict[str, list[str]]:
    """List tools grouped by category.
    
    Returns:
        Dictionary mapping category names to lists of tool names.
    """
    categories: dict[str, list[str]] = {}
    for tool_name, meta in _tool_metadata.items():
        cat = meta.get("category", "general")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool_name)
    return categories


def clear_registry() -> None:
    """Clear all registered tools. Useful for testing."""
    _full_docs.clear()
    _tool_metadata.clear()


def scan_tools_for_full_docs(tools: list) -> None:
    """Scan a list of tools and register any with _full_doc_ attributes.
    
    This should be called after tools are created to pick up any
    _full_doc_ attributes that were set on tool functions.
    
    Args:
        tools: List of tool functions (from @tool decorator).
    """
    for tool_func in tools:
        tool_name = getattr(tool_func, 'name', None) or getattr(tool_func, '__name__', str(tool_func))
        
        # Check for _full_doc_ attribute
        if hasattr(tool_func, '_full_doc_'):
            register_full_doc(tool_name, tool_func._full_doc_)
        
        # Extract short doc from description or docstring
        short_doc = ""
        if hasattr(tool_func, 'description'):
            short_doc = tool_func.description.split('\n')[0]
        elif hasattr(tool_func, '__doc__') and tool_func.__doc__:
            short_doc = tool_func.__doc__.split('\n')[0]
        
        # Infer category from name (e.g., "project_create" -> "project")
        category = tool_name.split('_')[0] if '_' in tool_name else 'general'
        
        # Register metadata
        if tool_name not in _tool_metadata:
            _tool_metadata[tool_name] = {
                "category": category,
                "short_doc": short_doc,
                "full_doc": _full_docs.get(tool_name, ""),
            }
        else:
            # Update existing metadata
            _tool_metadata[tool_name]["short_doc"] = short_doc
            if tool_name in _full_docs:
                _tool_metadata[tool_name]["full_doc"] = _full_docs[tool_name]

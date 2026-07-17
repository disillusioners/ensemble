"""Tool registry for storing full documentation.

This module provides a registry for tool documentation that allows
tools to have short docstrings (for LLM context efficiency) while
still providing detailed documentation via the tool_help() function.
"""

from importlib import import_module
from typing import Callable, Any

# Global registry: tool_name -> full documentation string
_full_docs: dict[str, str] = {}

# Tool metadata registry: tool_name -> {category, short_doc, full_doc}
_tool_metadata: dict[str, dict[str, Any]] = {}


def register_tool_category(category: str):
    """Decorator to mark a tool with its category.
    
    Usage:
        @register_tool_category("filesystem")
        @tool
        def read_file(...):
            ...
    
    Args:
        category: The category key (e.g., "filesystem", "instance").
    
    Returns:
        Decorator function that sets _tool_category on the tool.
    """
    def decorator(func):
        func._tool_category = category
        return func
    return decorator


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
        category: Tool category for grouping (e.g., "project", "instance").
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
        
        # Infer category from _tool_category attribute or name fallback
        category = getattr(tool_func, '_tool_category', None)
        if category is None:
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


# Category module mapping: category_key -> full module path(s)
CATEGORY_MODULES: dict[str, str | list[str]] = {
    "bash": "daemon.tools.bash",
    "critical_notes": "daemon.tools.critical_notes",
    "project_history": "daemon.tools.project_history",
    "filesystem": "daemon.tools.filesystem",
    "time": "daemon.tools.time",
    "instance": "daemon.tools.instance",
    "self": ["daemon.tools.inner_soul", "daemon.tools.access_memory"],
    "project": "daemon.tools.project",
    "job": "daemon.tools.job_queue",
    "help": "daemon.tools.help",
    "mother": "daemon.tools.agent_mother",
    "knowledge": "daemon.tools.knowledge_tools",
    "chart": "daemon.tools.chart_tools",
    "image": "daemon.tools.image_tools",
    "todo": "daemon.tools.todo_tools",
    "question": "daemon.tools.question_tools",
    "rag": "daemon.tools.rag_tools",
    "mcp": "daemon.tools.mcp_tools",
    "external_opencode": "daemon.tools.external_opencode",
    "context": "daemon.tools.context_tools",
    "shared_context": "daemon.tools.shared_context_tools",
    "db": "daemon.tools.db_tools",
    "infra": "daemon.tools.infra",
    "system": "daemon.tools.system",
    "skill-evolution": "daemon.tools.skill_evolution_tools",
    "dynamic-skill": "daemon.tools.skill_tools",
    "language": "daemon.tools.language_tools",
}


def get_tool_categories(allowed_tools: set[str] | None = None) -> dict[str, list[str]]:
    """Get tools grouped by their category, using CATEGORY_NAME as keys.
    
    Args:
        allowed_tools: Optional set of tool names to filter by.
    
    Returns:
        Dictionary mapping CATEGORY_NAME to list of tool names.
    """
    categories: dict[str, list[str]] = {}
    
    for tool_name, meta in _tool_metadata.items():
        # Filter by allowed_tools if provided
        if allowed_tools is not None and tool_name not in allowed_tools:
            continue
        
        category_key = meta.get("category", "general")
        
        # Look up CATEGORY_NAME from the module
        if category_key in CATEGORY_MODULES:
            try:
                module_paths = CATEGORY_MODULES[category_key]
                # Handle both str and list[str] values
                first_module = module_paths[0] if isinstance(module_paths, list) else module_paths
                module = import_module(first_module)
                category_name = getattr(module, "CATEGORY_NAME", category_key)
            except ImportError:
                category_name = category_key
        else:
            category_name = category_key
        
        if category_name not in categories:
            categories[category_name] = []
        categories[category_name].append(tool_name)
    
    return categories


def get_category_doc(category_key: str) -> tuple[str, str] | None:
    """Get CATEGORY_NAME and CATEGORY_DOC for a category.
    
    Args:
        category_key: The category key (e.g., "bash", "filesystem").
    
    Returns:
        Tuple of (CATEGORY_NAME, CATEGORY_DOC), or None if category doesn't exist.
    """
    if category_key not in CATEGORY_MODULES:
        return None
    
    try:
        module_paths = CATEGORY_MODULES[category_key]
        # Handle both str and list[str] values
        if isinstance(module_paths, list):
            category_name = None
            category_docs: list[str] = []
            for path in module_paths:
                module = import_module(path)
                if category_name is None:
                    category_name = getattr(module, "CATEGORY_NAME", category_key)
                doc = getattr(module, "CATEGORY_DOC", "")
                if doc:
                    category_docs.append(doc)
            return (category_name or category_key, "\n\n".join(category_docs))
        else:
            module = import_module(module_paths)
            return (
                getattr(module, "CATEGORY_NAME", category_key),
                getattr(module, "CATEGORY_DOC", ""),
            )
    except ImportError:
        return None

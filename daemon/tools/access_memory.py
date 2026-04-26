"""Memory access tool — deprecated in favor of explore()."""

from pathlib import Path
from langchain_core.tools import tool

from ._tool_registry import register_tool_category

CATEGORY_NAME = "Memory Access (DEPRECATED)"
CATEGORY_DOC = """\
Read memory files from your memories/ directory.

**DEPRECATED**: This tool is deprecated. Use explore() to query project knowledge
and experience() to record new knowledge.
"""

_FULL_DOC = """Read a specific memory file from your memories/ directory.

**DEPRECATED**: This tool is deprecated. Use explore() to query project knowledge
and experience() to record new knowledge.

Args:
    filename: The exact filename (e.g., "20260401_1430-remember-user-prefers-terse-replies.md")

Returns:
    A deprecation message directing agents to use explore() instead.
"""


def create_access_memory_tool(agent_id: str):
    """Create deprecated access_memory tool bound to specific agent."""

    @register_tool_category("self")
    @tool
    def access_memory(filename: str) -> str:
        """Read a memory file from your memories/ directory. Use tool_help("access_memory") for details."""
        return "⚠️ DEPRECATED: access_memory is deprecated. Use explore() to query project knowledge and experience() to record new knowledge."

    access_memory._full_doc_ = _FULL_DOC
    return access_memory

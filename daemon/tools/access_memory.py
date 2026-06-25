"""Memory access tool — read specific memory files from memories/ directory."""

import re
from pathlib import Path
from langchain_core.tools import tool

from ._tool_registry import register_tool_category

ARCHIVE_PATTERN = re.compile(r'^(\d{4})/(\d{2})/[a-zA-Z0-9_\-]+\.md$')

CATEGORY_NAME = "Memory Access"
CATEGORY_DOC = """\
Read memory files from your memories/ directory.

**Filename convention**: `{date}-{descriptive-title}.md`
- Example: `2026-04-01-k8s-db-connection.md`
"""

_FULL_DOC = """Read a specific memory file from your memories/ directory.

You see your recent memory filenames in the '## Recent Memories' section of your system prompt.
Use this tool to read the full content of any of those files.

Args:
    filename: The exact filename (e.g., "20260401_1430-remember-user-prefers-terse-replies.md")

Returns:
    The full content of the memory file, or an error message if not found.
"""


def create_access_memory_tool(agent_id: str):
    """Create access_memory tool bound to specific agent."""
    from ..registry import get_registry

    registry = get_registry()
    agent_meta = registry.get_resolved(agent_id)
    agent_path = agent_meta.path if agent_meta else Path(agent_id)

    @register_tool_category("self")
    @tool
    def access_memory(filename: str) -> str:
        """Read a memory file from your memories/ directory. Use tool_help("access_memory") for details."""
        memories_dir = agent_path / "memories"

        # Check if this is an archive access
        if filename.startswith("archive/"):
            remainder = filename[len("archive/"):]
            if ARCHIVE_PATTERN.match(remainder):
                # Validated archive path: YYYY/MM/<safe_name>.md
                # Use archive subdirectory, preserve validated path components
                memories_dir = agent_path / "memories" / "archive"
                safe_name = remainder  # Keep full validated path: 2026/01/file.md
            else:
                # Invalid archive path — sanitize to filename only
                safe_name = Path(remainder).name
        else:
            # Normal access — strip any path components
            safe_name = Path(filename).name

        # Check memories directory exists
        if not memories_dir.exists() or not memories_dir.is_dir():
            return "No memories/ directory found."

        filepath = (memories_dir / safe_name).resolve()
        if not str(filepath).startswith(str(memories_dir.resolve())):
            return "Access denied"

        # Explicit symlink check for defense-in-depth
        if filepath.is_symlink():
            return "Access denied"

        if not filepath.is_file():
            try:
                available = sorted([f.name for f in memories_dir.iterdir() if f.suffix == ".md"])
            except (PermissionError, OSError):
                return "Unable to list memories: permission denied."
            if available:
                return f"Memory file '{filename}' not found. Available:\n" + "\n".join(f"- {n}" for n in available[-10:])
            return f"Memory file '{filename}' not found. No memories exist yet."

        return filepath.read_text(encoding="utf-8")

    access_memory._full_doc_ = _FULL_DOC
    return access_memory

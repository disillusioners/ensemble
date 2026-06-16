"""LangChain tool category for the shared context directory.

Exposes two internal tools that mirror the hosted MCP equivalents:

- ``list_context(context_key)`` — enumerate ``.md`` files in the shared
  context directory for a given context key.
- ``read_context(context_key, filename)`` — read a specific context file.

Both tools require an explicit ``context_key`` (the tree-root instance id)
read from the ``## Context Key`` section of the agent's system prompt.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from daemon.services.context_tools import list_context_files, read_context_file
from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Context"
CATEGORY_DOC = """\
Context tools for reading the shared markdown context directory
keyed by a context_key (the tree-root instance id of the caller).

Internal agents can call list_context and read_context directly. External
agent systems connected via the hosted MCP can call the equivalent
ensemble_context_list and ensemble_context_read tools.

The auto-preload behaviour (top-3 matches prepended to outbound prompts) is
handled separately by the daemon and does not require these tools.
"""


def create_context_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create the Context tool category tools.

    Args:
        manager: The InstanceManager instance (unused but kept for parity
            with the other tool factories).
        current_instance_id: The ID of the current instance (unused but kept
            for parity with the other tool factories).

    Returns:
        List of two tool functions: ``[list_context, read_context]``.
    """

    @register_tool_category("context")
    @tool
    async def list_context(context_key: str, query: str = "") -> str:
        """List all .md files in the shared context directory for `context_key`.

        Each entry includes a multi-line `concise_preview` (up to ~300 chars)
        that combines the title heading (if any) with a few content lines, so
        you can see what the file is about without reading it in full.

        Args:
            context_key: The context key (CONTEXT_KEY from your system prompt,
                or the tree-root instance id). Required.
            query: Optional case-insensitive filter. When non-empty, only files
                whose filename, slug, concise_preview, or full content contains
                the query are returned. When empty (default), all files are
                returned. Use this to narrow down a long list to the files
                relevant to your task.

        Returns:
            JSON string: list of {filename, slug, size_bytes, modified_at,
            concise_preview}. Returns "[]" if no files exist or the filter
            matches nothing.
        """
        if not context_key or not context_key.strip():
            return "Error: context_key is required."
        try:
            files = await asyncio.to_thread(list_context_files, context_key, query)
            return json.dumps(files, indent=2)
        except Exception as e:
            logger.warning("list_context failed for %s: %s", context_key, e)
            return json.dumps([])

    @register_tool_category("context")
    @tool
    async def read_context(context_key: str, filename: str) -> str:
        """Read a specific context file by filename.

        Use this to fetch the full body of a file you found via
        :func:`list_context` — pass the exact `filename` from that listing.

        Args:
            context_key: The context key. Required.
            filename: The bare filename returned by list_context. No path
                separators allowed.

        Returns:
            File contents (utf-8) or an error string.
        """
        try:
            content = await asyncio.to_thread(read_context_file, context_key, filename)
        except Exception as e:
            logger.warning("read_context failed for %s/%s: %s", context_key, filename, e)
            return f"Error: Failed to read context file: {e}"
        if content is None:
            return (
                f"Error: Could not read '{filename}' from context_key='{context_key}'. "
                "The file may not exist, the filename may be invalid, or it failed a security check."
            )
        return content

    return [list_context, read_context]

"""Tool naming and adaptation utilities for MCP tools.

Also defines the lazy-init building blocks:
- ``McpSessionProvider`` protocol (the single dependency the lazy
  coroutine needs for session resolution).
- ``create_lazy_mcp_tools`` factory that returns ``StructuredTool``
  instances whose coroutine defers session acquisition until first
  call.
- Imported ``_convert_call_tool_result`` (with ImportError fallback)
  for translating MCP ``CallToolResult`` into LangChain's
  ``(content, artifact)`` shape.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from langchain_core.tools import BaseTool, StructuredTool, ToolException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result conversion — imported from langchain_mcp_adapters with a safe
# fallback. The library version handles AudioContent, ResourceLink,
# EmbeddedResource, and structuredContent; the fallback only covers
# text. If the import path changes the fallback keeps things working
# but logs a clear signal in the import site.
# ---------------------------------------------------------------------------
try:
    from langchain_mcp_adapters.tools import _convert_call_tool_result
except ImportError:  # pragma: no cover - defensive
    def _convert_call_tool_result(result):  # type: ignore[no-redef]
        """Minimal fallback for MCP result conversion.

        Covers text-only results. If the library path moved, update the
        import above; this fallback exists to avoid hard import failures
        at startup.

        W2 (error handling): check ``isError`` BEFORE iterating
        ``content``. The pre-fix code happily iterated and stringified
        non-text content (``image``, ``audio``, ``resource_link``,
        ``embedded_resource``) into something like ``"<ImageContent
        object at 0x...>"`` which loses the error payload entirely.
        Now we collect the message faithfully — text items go in
        verbatim, non-text items get a type-tagged placeholder — and
        raise ``ToolException`` with the full message.
        """
        if not hasattr(result, 'content'):
            return [str(result)], None

        # W2: short-circuit on ``isError`` so the error path doesn't
        # walk past the first text item. We still need to extract
        # whatever message the server put in ``content`` — a single
        # text item is the common case but multi-part errors exist.
        if getattr(result, 'isError', False):
            parts: list[str] = []
            for item in result.content:
                if hasattr(item, 'text'):
                    parts.append(item.text)
                else:
                    # Tag the unknown content type so the error
                    # message retains a hint of the original payload
                    # shape (e.g. ``<ImageContent>``) rather than the
                    # default ``str(item)`` which would render the
                    # object repr.
                    type_name = type(item).__name__
                    parts.append(f"<{type_name}>")
            message = "\n".join(parts) if parts else "MCP tool returned an error"
            raise ToolException(message)

        # Non-error path: normal text extraction.
        content = []
        for item in result.content:
            if hasattr(item, 'text'):
                content.append({"type": "text", "text": item.text})
            else:
                content.append(str(item))
        return content, None


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


def _build_timed_coroutine(
    tool: BaseTool, timeout_seconds: float
):
    """Build a timeout-wrapped coroutine for a tool.

    Returns None if the tool has no coroutine (caller should skip wrapping).
    On TimeoutError, the wrapped coroutine raises ToolException so
    LangGraph's ToolNode can handle it gracefully.
    """
    if tool.coroutine is None:
        return None

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

    return _timed_coroutine


def adapt_mcp_tools(
    server_name: str,
    tools: list[BaseTool],
    tool_call_timeout: int = 120,
    tool_name_prefix: str | None = None,
) -> list[BaseTool]:
    """
    Adapt MCP tools by prefixing their names and updating descriptions.

    Prefixes each tool name with 'mcp_{slugified_server_name}_' and
    adds '[MCP:server_name]' to the tool description.

    When ``tool_name_prefix`` is provided (e.g. ``"plane"``), the
    ``mcp_{server}_`` prefix is replaced with ``{prefix}_`` — for
    built-in servers whose tools should feel native to specific
    agents. The description suffix is preserved regardless, so
    ``[MCP:plane]`` still tags the tool as MCP-backed.

    The MCP dispatch is unaffected: this function only mutates
    ``StructuredTool.name`` and ``description``. The underlying
    coroutine is unchanged and still calls
    ``session.call_tool(<original name>, kwargs)``.

    Args:
        server_name: Name of the MCP server
        tools: List of MCP tools to adapt
        tool_call_timeout: Per-tool call timeout in seconds. Set to 0
            to disable timeout wrapping. Defaults to 120s.
        tool_name_prefix: Optional override for the tool name
            prefix. When ``None`` (default), uses
            ``mcp_{slugified_server_name}_``. When set, uses
            ``{tool_name_prefix}_`` instead.

    Returns:
        List of adapted tools with prefixed names, updated descriptions,
        and timeout-wrapped coroutines.
    """
    if not tools:
        return tools

    if tool_name_prefix is not None:
        prefix = f"{tool_name_prefix}_"
    else:
        slugified_server = _slugify(server_name)
        prefix = f"mcp_{slugified_server}_"
    description_suffix = f"[MCP:{server_name}]"

    adapted_tools: list[BaseTool] = []

    for tool in tools:
        # Build adapted name and description
        new_name = f"{prefix}{tool.name}"
        new_description = f"{tool.description} {description_suffix}"

        # When timeout is enabled and the tool has a coroutine, build the
        # wrapped coroutine so we can set name, description, and coroutine
        # in a single model_copy call. Otherwise, just set name and
        # description in one copy.
        update: dict = {"name": new_name, "description": new_description}
        if tool_call_timeout > 0:
            timed_coroutine = _build_timed_coroutine(tool, tool_call_timeout)
            if timed_coroutine is not None:
                update["coroutine"] = timed_coroutine

        adapted_tool = tool.model_copy(update=update)
        adapted_tools.append(adapted_tool)
        logger.debug(f"Adapted MCP tool: {tool.name} -> {new_name}")

    return adapted_tools


# =============================================================================
# Lazy tool factory — defers MCP session establishment until the tool is
# actually called by the LLM. Used by the lazy init path in
# ``McpService.preload_mcp_tools`` (Task 5).
# =============================================================================


@runtime_checkable
class McpSessionProvider(Protocol):
    """Protocol for lazy MCP session resolution.

    The lazy coroutine in ``_build_lazy_coroutine`` only depends on this
    single interface. The concrete implementation
    (``_McpSessionProviderImpl`` on ``McpService``) is injected by the
    caller, keeping the coroutine testable and decoupled from
    ``McpService`` itself.

    Implementations must be safe to call concurrently from multiple
    coroutines. The lazy coroutine already double-check-locks around
    the provider, so the provider doesn't need its own lock.
    """

    async def get_session(self, server_name: str) -> Any:
        """Get or create a session for ``server_name``.

        Args:
            server_name: Name of the MCP server to resolve a session for.

        Returns:
            A live MCP client session (e.g. ``ClientSession`` subclass).

        Raises:
            ToolException: If the server is unknown or unreachable.
        """
        ...


def create_lazy_mcp_tools(
    server_name: str,
    schemas: list[dict],
    session_provider: McpSessionProvider,
    shared_session_cache: dict[str, Any],
    shared_session_lock: asyncio.Lock,
    tool_call_timeout: int = 120,
    tool_name_prefix: str | None = None,
) -> list[BaseTool]:
    """Create lazy MCP tools that defer connection until first call.

    For each schema, returns a ``StructuredTool`` whose coroutine
    resolves the MCP session on first invocation (and caches it for
    the rest of the instance's life). ``shared_session_cache`` and
    ``shared_session_lock`` are passed in by the caller and **shared
    across all tools for the same instance+server** — so N tools
    produce N lazy wrappers but only one underlying session.

    When ``tool_name_prefix`` is provided (e.g. ``"plane"``), tools
    are exposed as ``{prefix}_{tool_name}`` (e.g. ``plane_list_issues``)
    instead of the standard ``mcp_{server}_{tool_name}``. This is for
    essential built-in servers whose tools should feel native to
    specific agents. The description suffix
    ``[MCP:{server_name}]`` is preserved for consistency.

    Dispatch safety: the lazy coroutine built by
    ``_build_lazy_coroutine`` closes over the ORIGINAL
    ``original_tool_name`` (the name as the MCP server knows it, e.g.
    ``list_issues``) and calls ``session.call_tool(original_tool_name,
    kwargs)``. The exposed ``adapted_name`` (e.g. ``plane_list_issues``)
    is only used for ``StructuredTool(name=...)`` — it does NOT reach
    the MCP server. The prefix override therefore only renames the
    tool surface; it does NOT break dispatch.

    Args:
        server_name: MCP server name (used to build the tool name
            prefix and the description suffix).
        schemas: List of tool schemas. Each dict must have
            ``name`` (str), ``description`` (str), and
            ``input_schema`` (dict).
        session_provider: Provider used to lazily resolve the MCP
            session.
        shared_session_cache: Dict shared across ALL tools for this
            instance+server; stores the resolved session so the
            second+ call short-circuits.
        shared_session_lock: Lock shared across ALL tools for this
            instance+server; used for double-check locking.
        tool_call_timeout: Per-call timeout in seconds. ``0`` disables
            timeout wrapping. Default ``120``.
        tool_name_prefix: Optional override for the tool name
            prefix. When ``None`` (default), uses
            ``mcp_{slugified_server_name}_``. When set (e.g.
            ``"plane"``), uses ``{tool_name_prefix}_`` instead.

    Returns:
        List of ``StructuredTool`` instances. Empty list if
        ``schemas`` is empty.
    """
    if not schemas:
        return []

    if tool_name_prefix is not None:
        prefix = f"{tool_name_prefix}_"
    else:
        slugified_server = _slugify(server_name)
        prefix = f"mcp_{slugified_server}_"
    description_suffix = f"[MCP:{server_name}]"

    lazy_tools: list[BaseTool] = []

    for schema in schemas:
        tool_name = schema["name"]
        adapted_name = f"{prefix}{tool_name}"
        description = f"{schema['description']} {description_suffix}"

        coroutine = _build_lazy_coroutine(
            server_name=server_name,
            original_tool_name=tool_name,
            session_provider=session_provider,
            shared_session_cache=shared_session_cache,
            shared_session_lock=shared_session_lock,
            timeout_seconds=tool_call_timeout if tool_call_timeout > 0 else None,
        )

        tool = StructuredTool(
            name=adapted_name,
            description=description,
            args_schema=schema.get("input_schema", {}),
            coroutine=coroutine,
            response_format="content_and_artifact",
        )
        lazy_tools.append(tool)

    return lazy_tools


def _build_lazy_coroutine(
    server_name: str,
    original_tool_name: str,
    session_provider: McpSessionProvider,
    shared_session_cache: dict[str, Any],
    shared_session_lock: asyncio.Lock,
    timeout_seconds: float | None,
) -> Callable:
    """Build a coroutine that lazily creates an MCP session on first call.

    Concurrency guard (double-check locking):
        * Fast path: read ``shared_session_cache`` without a lock —
          once a session is cached, subsequent calls never contend.
        * Slow path: take the lock, re-check the cache, then call
          ``session_provider.get_session``. The second arrival on the
          lock finds the session already cached and skips the
          provider call.

    ``shared_session_cache`` and ``shared_session_lock`` are owned by
    the caller (``McpService.preload_mcp_tools``) and shared across
    all tools for the same instance+server, which is what guarantees
    N tools → 1 session.

    Args:
        server_name: MCP server name (used by the provider).
        original_tool_name: Un-prefixed tool name as the MCP server
            knows it.
        session_provider: ``McpSessionProvider`` for resolution.
        shared_session_cache: See above.
        shared_session_lock: See above.
        timeout_seconds: Per-call timeout in seconds, or ``None`` to
            disable.

    Returns:
        Async coroutine suitable for ``StructuredTool(coroutine=...)``.
    """

    async def _get_session() -> Any:
        """Get or create the session using double-check locking.

        W7 (concurrency guard): this function is the **sole**
        concurrency guard for first-time session resolution per
        instance+server. Two concurrent tool calls for the same server
        serialize on ``shared_session_lock``; the second arrival finds
        the session already cached on the re-check and short-circuits,
        so the underlying ``session_provider.get_session`` is called
        at most once per instance+server.

        The pattern is:
            1. Fast path: read ``shared_session_cache`` without a lock
               — once a session is cached, subsequent calls never
               contend.
            2. Slow path: acquire ``shared_session_lock``, re-check the
               cache, then call ``session_provider.get_session`` only
               if the cache is still empty.
        """
        # Fast path — no lock needed for already-cached sessions.
        if server_name in shared_session_cache:
            return shared_session_cache[server_name]

        async with shared_session_lock:
            # Double-check after acquiring the lock.
            if server_name in shared_session_cache:
                return shared_session_cache[server_name]

            session = await session_provider.get_session(server_name)
            shared_session_cache[server_name] = session
            return session

    async def _lazy_coroutine(**kwargs):
        """Lazy MCP tool coroutine — connects on first call."""
        try:
            session = await _get_session()

            # LangGraph may inject a `runtime` kwarg via InjectedToolArg;
            # the MCP server doesn't know about it, so strip it.
            kwargs.pop("runtime", None)

            if timeout_seconds is not None:
                async with asyncio.timeout(timeout_seconds):
                    result = await session.call_tool(original_tool_name, kwargs)
            else:
                result = await session.call_tool(original_tool_name, kwargs)

            return _convert_call_tool_result(result)

        except asyncio.TimeoutError:
            raise ToolException(
                f"Tool '{original_tool_name}' on server '{server_name}' "
                f"timed out after {timeout_seconds}s. The MCP server may "
                f"be unresponsive."
            )
        except ToolException:
            raise  # Re-raise our own ToolExceptions unchanged.
        except Exception as e:
            raise ToolException(
                f"MCP tool call failed for '{original_tool_name}' on "
                f"'{server_name}': {e}"
            )

    return _lazy_coroutine

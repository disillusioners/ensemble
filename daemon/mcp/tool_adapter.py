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

if TYPE_CHECKING:
    from daemon.mcp.resilience import ResilienceManager

logger = logging.getLogger(__name__)


def _fallback_content(fallback_message: str) -> tuple[list[dict], None]:
    """Wrap a fallback JSON string in the ``(content, artifact)`` shape.

    The legacy path returns ``(content, artifact)`` from
    ``_convert_call_tool_result`` — a list of content blocks + an
    optional artifact. The resilience path needs to match that shape
    so callers can ``return _convert_call_tool_result(...)`` or
    ``return _fallback_content(...)`` interchangeably.

    ``StructuredTool`` with ``response_format="content_and_artifact"``
    expects this exact shape; wrapping the JSON string in a single
    text content block keeps the agent's downstream parsing uniform
    whether the result came from the live server or the fallback.

    Args:
        fallback_message: A JSON string (typically pre-serialized via
            ``json.dumps`` in ``PlaneServerDefinition.resilience_config``).

    Returns:
        ``([{"type": "text", "text": <fallback>}], None)`` — same shape
        as ``_convert_call_tool_result`` on the happy path.
    """
    return [{"type": "text", "text": fallback_message}], None


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
    resilience_manager: "ResilienceManager | None" = None,
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

    Resilience opt-in (Phase 4): when ``resilience_manager`` is
    provided AND the manager has a config registered for
    ``server_name``, the lazy coroutine runs the full resilience flow
    (cache → circuit breaker → retry → fallback). When either is
    missing (``resilience_manager=None`` or no config registered),
    the lazy coroutine behaves EXACTLY as before — single attempt,
    timeout, surface exception as ``ToolException``. This preserves
    zero-regression for context7, webfetch, and any other MCP server
    that hasn't opted in.

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
        resilience_manager: Optional ``ResilienceManager`` wired by
            ``McpService.preload_mcp_tools`` for servers that opt
            into retry / caching / circuit breaking. ``None`` (or no
            config registered for ``server_name``) preserves the
            legacy no-resilience path.

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
            adapted_tool_name=adapted_name,
            session_provider=session_provider,
            shared_session_cache=shared_session_cache,
            shared_session_lock=shared_session_lock,
            timeout_seconds=tool_call_timeout if tool_call_timeout > 0 else None,
            resilience_manager=resilience_manager,
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
    adapted_tool_name: str | None = None,
    resilience_manager: "ResilienceManager | None" = None,
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

    Resilience opt-in (Phase 4): when ``resilience_manager`` is
    provided AND has a config registered for ``server_name``, the
    returned coroutine runs the full resilience flow (cache →
    circuit breaker → retry → fallback). Otherwise it falls back to
    the legacy single-attempt path — same behavior as before this
    feature, so existing tests / non-resilient servers keep working
    untouched.

    Args:
        server_name: MCP server name (used by the provider).
        original_tool_name: Un-prefixed tool name as the MCP server
            knows it.
        session_provider: ``McpSessionProvider`` for resolution.
        shared_session_cache: See above.
        shared_session_lock: See above.
        timeout_seconds: Per-call timeout in seconds, or ``None`` to
            disable.
        adapted_tool_name: The full prefixed name (e.g.
            ``plane_list_issues``). Used by the resilience layer
            for cache-key construction and read/write tool
            classification. ``None`` disables resilience regardless
            of whether ``resilience_manager`` is set — the legacy
            path is then identical to the pre-resilience behavior.
        resilience_manager: Optional ``ResilienceManager``. When set
            AND ``server_name`` has a registered config, the coroutine
            runs through cache / circuit breaker / retry / fallback.

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

    # ------------------------------------------------------------------
    # Resolve resilience config ONCE at coroutine-build time. This is
    # cheap (dict lookup) and avoids re-resolving on every call. The
    # legacy no-resilience path stays branch-free inside the hot loop.
    # ------------------------------------------------------------------
    _resilience_config = (
        resilience_manager.get_config(server_name)
        if resilience_manager is not None and adapted_tool_name is not None
        else None
    )
    _use_resilience = _resilience_config is not None

    async def _lazy_coroutine(**kwargs):
        """Lazy MCP tool coroutine — connects on first call.

        Two paths:

        1. **Legacy (no resilience)** — single ``session.call_tool``
           under the configured timeout, surface any failure as a
           ``ToolException``. Identical to the pre-Phase-4 behavior.
        2. **Resilience (Plane)** — cache hit short-circuit → circuit
           breaker check → retry-wrapped ``session.call_tool`` →
           exception classification → cache + circuit state update →
           fallback JSON on degradation.
        """
        # ----- Legacy / no-resilience path ---------------------------
        if not _use_resilience:
            try:
                session = await _get_session()

                # LangGraph may inject a `runtime` kwarg via InjectedToolArg;
                # the MCP server doesn't know about it, so strip it.
                kwargs.pop("runtime", None)

                if timeout_seconds is not None:
                    async with asyncio.timeout(timeout_seconds):
                        result = await session.call_tool(
                            original_tool_name, kwargs
                        )
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

        # ----- Resilience path ---------------------------------------
        # Local imports: avoid pulling the resilience module (which
        # imports CircuitBreaker) into the import graph of callers
        # that don't opt in. Cheap enough for per-call work.
        from daemon.mcp.errors import (
            McpAuthError,
            McpTransientError,
            McpError,
        )
        from daemon.mcp.resilience import is_read_tool

        # Re-bind for the inner closures — keeps the names short
        # and the logic readable. Guaranteed non-None by the
        # ``_use_resilience`` gate above.
        config = _resilience_config
        assert config is not None  # for type checkers

        # Tool classification: read vs write (drives caching +
        # invalidation). Stripped-name matching handles both
        # ``plane_list_issues`` and bare ``list_issues``.
        is_read = is_read_tool(adapted_tool_name, config)  # type: ignore[arg-type]

        # Strip the LangGraph runtime injection regardless of path
        # — the resilience flow also doesn't want it forwarded.
        kwargs.pop("runtime", None)

        cache = resilience_manager.get_cache(server_name)
        cb = resilience_manager.get_circuit_breaker(server_name)

        # ---- 1. Cache check (read tools only) ----------------------
        if is_read and cache is not None:
            cached, hit = await cache.get(server_name, adapted_tool_name, kwargs)  # type: ignore[arg-type]
            if hit:
                logger.debug(
                    f"MCP cache HIT: {server_name}/{adapted_tool_name}"
                )
                return cached

        # ---- 2. Circuit breaker check ------------------------------
        # ``can_execute`` is the on-demand probe: when the circuit is
        # OPEN and ``recovery_timeout`` has elapsed, it transitions
        # to HALF_OPEN and returns True — the call below is the
        # probe. No background task needed.
        if cb is not None:
            can_call = await cb.can_execute()
            if not can_call:
                logger.warning(
                    f"MCP circuit OPEN for {server_name}, "
                    f"returning fallback"
                )
                if config.fallback_message:
                    # Return as (content, artifact) tuple to match
                    # the ``_convert_call_tool_result`` shape that
                    # ``StructuredTool(response_format='content_and_artifact')``
                    # expects. The string content lets the agent
                    # surface the JSON to the user directly.
                    return _fallback_content(config.fallback_message)
                # No fallback configured → propagate as ToolException
                # so the caller still gets a structured error.
                raise ToolException(
                    f"MCP server '{server_name}' is currently "
                    f"unavailable (circuit breaker open)."
                )
            # can_execute True: either CLOSED (normal) or HALF_OPEN
            # (this call IS the probe). The probe path uses the
            # shorter probe_timeout when in HALF_OPEN — see
            # _do_call_with_retry below.

        # ---- 3. Execute (with retry) -------------------------------
        try:
            result, used_probe_timeout = await _do_call_with_retry(
                original_tool_name=original_tool_name,
                kwargs=kwargs,
                timeout_seconds=timeout_seconds,
                probe_timeout=config.probe_timeout,
                cb=cb,
                retry_policy=config.retry_policy,
                is_read=is_read,
            )

        except McpAuthError:
            # Auth errors: do NOT record a circuit-breaker failure
            # (a bad API key isn't a server outage), do NOT cache,
            # do NOT return fallback (the operator needs to know).
            # Raise ToolException so LangGraph's ToolNode routes it
            # to the agent as a structured error.
            raise ToolException(
                f"Authentication failed for MCP server "
                f"'{server_name}' — check the configured "
                f"credentials."
            )
        except (McpTransientError, asyncio.TimeoutError):
            # All retries exhausted (or single attempt failed for
            # non-retry policy). Record a circuit-breaker failure
            # and degrade gracefully.
            if cb is not None:
                await cb.record_failure()
            logger.warning(
                f"MCP transient failure for "
                f"{server_name}/{adapted_tool_name} after retries"
            )
            if config.fallback_message:
                return _fallback_content(config.fallback_message)
            raise ToolException(
                f"MCP server '{server_name}' tool "
                f"'{original_tool_name}' is currently unavailable."
            )
        except McpError as e:
            # Non-retryable McpError subclasses (McpToolError, etc.)
            # — don't trip the circuit (the server is healthy, the
            # tool just reported an error). Surface the message.
            raise ToolException(
                f"MCP tool '{original_tool_name}' on "
                f"'{server_name}' failed: {e}"
            ) from e
        except Exception as e:
            # Truly unexpected — classify for telemetry, then degrade
            # when a fallback is configured. Never silently swallow.
            from daemon.mcp.resilience import classify_exception

            classified = classify_exception(e)
            logger.warning(
                f"MCP unexpected error for "
                f"{server_name}/{adapted_tool_name}: "
                f"{type(e).__name__}: {e} (classified as "
                f"{type(classified).__name__})"
            )
            if isinstance(classified, McpTransientError) and cb is not None:
                await cb.record_failure()
            if config.fallback_message:
                return _fallback_content(config.fallback_message)
            raise ToolException(
                f"MCP tool '{original_tool_name}' on "
                f"'{server_name}' failed: {e}"
            )

        # ---- 4. Success: record + cache/invalidate ----------------
        if cb is not None:
            await cb.record_success()
        resilience_manager.record_success(server_name)

        if is_read and cache is not None:
            await cache.set(
                server_name,
                adapted_tool_name,  # type: ignore[arg-type]
                kwargs,
                result,
                config.cache_ttl,
            )
        elif not is_read and cache is not None:
            # Write tool success → entire server cache is now stale.
            # Simple, safe, avoids reasoning about which specific
            # resources the write affected.
            await cache.invalidate_server(server_name)

        return result

    async def _do_call_with_retry(
        original_tool_name: str,
        kwargs: dict,
        timeout_seconds: float | None,
        probe_timeout: float,
        cb: Any | None,
        retry_policy: Any | None,
        is_read: bool = True,
    ) -> tuple[Any, bool]:
        """Single MCP call wrapped in ``RetryPolicy.execute`` if configured.

        Returns ``(result, used_probe_timeout)`` — the boolean is
        ``True`` when this call used the shorter ``probe_timeout``
        (because the circuit is HALF_OPEN), ``False`` when it used
        the regular ``timeout_seconds``. The flag is reserved for
        future telemetry; today it has no behavioral effect.

        Raises ``McpAuthError`` / ``McpToolError`` immediately on
        non-retryable errors. Re-raises ``McpTransientError`` after
        exhausting retries.

        Implementation note: the probe timeout logic is inlined in
        the retry loop because the choice of timeout can change
        between attempts (HALF_OPEN → CLOSED via success). The
        loop uses ``cb.get_state()`` to pick the right timeout per
        attempt.

        CR-6: when ``is_read=False`` and the caller's
        ``retry_policy.retry_writes`` is ``False`` (the default),
        the retry policy is dropped to a single attempt — writes
        that would retry on a transient error can create duplicate
        side effects (e.g. duplicate issue creation). The write
        never consumes more than one attempt slot.

        T-1: when a classified exception is being re-raised after
        exhausting retries, ``raise classified from e`` preserves
        the original cause while propagating the classified
        ``McpTransientError`` so the outer handler records a
        circuit-breaker failure correctly. The previous bare
        ``raise`` re-raised ``e`` directly, which could be a raw
        transport exception that the outer ``except`` clause
        didn't recognize.

        Tidier-1: the per-attempt delay math is delegated to
        ``retry_policy._compute_delay(attempt)`` so the two retry
        paths (``RetryPolicy.execute`` and this inlined loop)
        cannot drift on the exponential-backoff / jitter formula.
        """
        from daemon.mcp.errors import (
            McpAuthError,
            McpTransientError,
        )
        from daemon.mcp.resilience import classify_exception

        session = await _get_session()

        # No retry policy → single attempt, configured timeout.
        if retry_policy is None:
            to = timeout_seconds if timeout_seconds is not None else 120
            async with asyncio.timeout(to):
                raw = await session.call_tool(original_tool_name, kwargs)
            return _convert_call_tool_result(raw), False

        # CR-6: writes never retry by default. A transient blip on a
        # create_/update_/delete_ call could create duplicate issues
        # or double-assign. We drop the retry policy so the loop
        # below runs exactly once.
        if not is_read and not getattr(retry_policy, "retry_writes", False):
            effective_policy = None
        else:
            effective_policy = retry_policy

        # With retry: classify each attempt's exception, retry only
        # transient ones, propagate auth/tool errors immediately.
        last_exc: Exception | None = None
        max_attempts = (
            effective_policy.max_attempts if effective_policy is not None else 1
        )
        for attempt in range(1, max_attempts + 1):
            # Pick timeout per attempt: probe when HALF_OPEN, otherwise
            # the configured timeout. ``cb`` may be None when the
            # server didn't opt into circuit breaking.
            use_probe = False
            if cb is not None:
                try:
                    use_probe = cb.get_state() == "half_open"
                except Exception:
                    use_probe = False
            if use_probe:
                to = probe_timeout
            elif timeout_seconds is not None:
                to = timeout_seconds
            else:
                to = 120

            try:
                async with asyncio.timeout(to):
                    raw = await session.call_tool(original_tool_name, kwargs)
                return _convert_call_tool_result(raw), use_probe
            except (asyncio.TimeoutError, McpTransientError) as e:
                last_exc = e
                if attempt >= max_attempts:
                    # Out of retries — re-raise so the outer handler
                    # can record a circuit failure + degrade.
                    raise
                # Tidier-1: reuse the same delay math as
                # ``RetryPolicy.execute`` so the two paths can't
                # diverge.
                delay = effective_policy._compute_delay(attempt)
                logger.debug(
                    f"MCP retry {attempt}/{max_attempts} "
                    f"for {adapted_tool_name} after {delay:.2f}s "
                    f"({type(e).__name__})"
                )
                await asyncio.sleep(delay)
            except McpAuthError:
                raise  # Non-retryable — bubble to outer handler.
            except Exception as e:
                # Classify the unknown exception. If it's transient,
                # we treat it like one (re-raise as transient to
                # consume a retry slot). Otherwise raise as-is.
                classified = classify_exception(e)
                if isinstance(classified, McpAuthError):
                    raise McpAuthError(str(e)) from e
                if isinstance(classified, McpTransientError):
                    last_exc = classified
                    if attempt >= max_attempts:
                        # T-1: raise the classified exception with
                        # ``from e`` so the original cause is
                        # preserved AND the outer handler sees the
                        # classified ``McpTransientError`` /
                        # ``McpError`` and records a circuit
                        # failure correctly. The bare ``raise``
                        # here used to re-raise ``e`` (the raw
                        # transport exception), which the outer
                        # ``except (McpTransientError,
                        # asyncio.TimeoutError)`` clause could
                        # miss.
                        raise classified from e
                    # Tidier-1: same delay helper.
                    delay = effective_policy._compute_delay(attempt)
                    await asyncio.sleep(delay)
                    continue
                # Non-retryable McpError (tool error, etc.) or
                # anything else → propagate immediately.
                raise classified from e

        # Defensive: loop exited without return/raise. Should never
        # happen because max_attempts >= 1 always runs at least one
        # attempt, but the type checker wants an explicit raise.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            "_do_call_with_retry exited without result"
        )

    return _lazy_coroutine

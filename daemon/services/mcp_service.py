"""MCP service — manages MCP tool lifecycle for agent instances.

Consolidates all MCP business logic:
- Connects to MCP servers and discovers tools (async)
- Converts MCP tools to LangChain BaseTool format with mcp_ prefix
- Caches discovered tools per instance for sync retrieval
- Cleans up MCP connections on instance termination

The lazy init path (Phase 1) replaces the eager preload with:
- ``get_schemas_for_server`` reads (or one-time discovers) tool
  schemas — no connection is opened for the preload itself.
- ``create_lazy_mcp_tools`` builds ``StructuredTool`` objects whose
  coroutine defers session establishment to the first invocation.
- ``_McpSessionProviderImpl`` resolves the session on first call,
  trying the warmup pool first then falling back to a cold
  ``connect_instance`` per the pool-first / cold-start design.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import ToolException

from daemon.mcp import get_mcp_connection_manager
from daemon.mcp.models import McpToolSchema
from daemon.mcp.tool_adapter import (
    McpSessionProvider,
    create_lazy_mcp_tools,
)

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from daemon.manager import InstanceManager
    from daemon.mcp.warmup_pool import McpWarmupPool, PooledConnection
    from daemon.repositories.mcp_server.models import McpServer

logger = logging.getLogger(__name__)


class _McpSessionProviderImpl:
    """Pool-aware lazy session resolver bound to a specific instance.

    Implements the ``McpSessionProvider`` protocol. Created once per
    preload by ``McpService._create_session_provider`` and shared by
    every lazy tool for the same instance+server (Decision D3 + S-NEW2).

    Resolution order (Decision D4 + D8):
        1. ``connection_manager.get_session`` — fast path for an
           already-open session (e.g. transferred from the pool or
           created by a previous call).
        2. ``warmup_pool.acquire`` + ``transfer_session`` for pooled
           built-in STDIO servers.
        3. ``connection_manager.connect_instance(instance_id, [server])``
           for the cold-start fallback — reuses the existing flow
           instead of duplicating transport dispatch (C2 fix).
    """

    def __init__(self, mcp_service: "McpService", instance_id: str) -> None:
        self._service = mcp_service
        self._instance_id = instance_id

    async def get_session(self, server_name: str) -> Any:
        """Resolve a session for ``server_name`` on this instance.

        Raises:
            ToolException: If the server is unknown or unreachable.
        """
        conn_mgr = get_mcp_connection_manager()

        # 1. Fast path: an existing session is tracked by the manager.
        existing = conn_mgr.get_session(self._instance_id, server_name)
        if existing is not None:
            return existing

        # 2. Pooled servers: try the warmup pool before cold start.
        #    W5 (pool size=1 implication): with the default pool size
        #    of 1, the first instance to call a tool on a pooled server
        #    acquires the single pooled connection. Subsequent instances
        #    fall through to the cold-start path below. This is
        #    acceptable — the warmup pool is an optimization, not a
        #    guarantee. Operators can raise the pool size via the
        #    ``MCP_POOL_SERVERS`` env var to allow more concurrent
        #    instances to benefit from pre-warmed connections.
        pool = self._service._warmup_pool
        if pool is not None and pool.is_pooled_server(server_name):
            conn: PooledConnection | None = None
            try:
                conn = await pool.acquire(server_name)
            except Exception as e:
                logger.warning(
                    f"Pool acquire failed for {server_name}: {e}, "
                    f"falling back to cold start"
                )
            else:
                if conn is not None:
                    try:
                        await conn_mgr.transfer_session(
                            self._instance_id,
                            server_name,
                            conn.session,
                            conn.stream_cm,
                        )
                    except Exception as e:
                        logger.warning(
                            f"transfer_session failed for {server_name}: {e}, "
                            f"falling back to cold start"
                        )
                        # CLEANUP: transfer_session failed, so the
                        # manager did NOT take ownership of the
                        # pooled connection. Delegate cleanup to
                        # the pool so subprocess teardown + stream
                        # CM aexit live in one place (avoids
                        # leaking the pooled connection or
                        # duplicating pool internals here).
                        try:
                            await pool.release_connection(conn)
                        except Exception:
                            pass
                    else:
                        return conn.session

        # 3. Cold start: connect_instance handles transport dispatch
        #    for all 3 transports (stdio / SSE / streamable HTTP).
        #    W3 (STDIO timeout): the ``per_server_timeout`` arg below is
        #    overridden by ``STDIO_DEFAULT_TIMEOUT`` (30s) for built-in
        #    STDIO servers unless the server's own config specifies a
        #    ``timeout`` field. The 15s value is the effective timeout
        #    for SSE and streamable-HTTP transports; STDIO connections
        #    have more headroom for subprocess + handshake setup.
        server = self._service._get_server_by_name(server_name)
        if server is None:
            raise ToolException(
                f"MCP server '{server_name}' not found. "
                f"It may have been removed."
            )

        await conn_mgr.connect_instance(
            self._instance_id,
            [server],
            per_server_timeout=15.0,
        )
        session = conn_mgr.get_session(self._instance_id, server_name)
        if session is None:
            raise ToolException(
                f"Failed to connect to MCP server '{server_name}'. "
                f"The server may be unavailable."
            )
        return session


class McpService:
    """Service for managing MCP tool integration with agent instances."""

    def __init__(self, manager: "InstanceManager") -> None:
        """Initialize the MCP service.

        Args:
            manager: The InstanceManager facade.
        """
        self._manager = manager
        self._tools_cache: dict[str, list[BaseTool]] = {}
        self._preload_locks: dict[str, asyncio.Lock] = {}
        self._preload_lock = asyncio.Lock()  # Protects _preload_locks dict
        self._warmup_pool: McpWarmupPool | None = None
        # Per-server schema cache. Keyed by server.name so it's shared across
        # all instances and survives cold-starts. Used by the lazy init path
        # to populate StructuredTool schemas without opening a connection.
        self._schema_cache: dict[str, list[McpToolSchema]] = {}
        # Guards _schema_cache reads/writes so concurrent first-time
        # discoveries for the same server don't both cold-start.
        self._schema_cache_lock = asyncio.Lock()
        # Per-instance session state for lazy resolution. Keyed first by
        # instance_id, then by server_name. Each entry holds a shared
        # "cache" dict (server_name -> session) and a lock used for
        # double-check locking inside the lazy coroutine. Populated by the
        # rewritten preload_mcp_tools (Task 5); declared here so the schema
        # cache work doesn't need to revisit this file's state shape.
        self._session_caches: dict[str, dict[str, dict]] = {}

    def set_warmup_pool(self, pool: "McpWarmupPool") -> None:
        """Inject the warm-up pool (called after initialization)."""
        self._warmup_pool = pool

    # ------------------------------------------------------------------
    # Schema cache — populated lazily, shared across instances.
    # Used by the lazy init path to register tool schemas at instance
    # creation time without opening a connection.
    # ------------------------------------------------------------------

    async def get_schemas_for_server(self, server: McpServer) -> list[McpToolSchema]:
        """Get tool schemas for a server. Uses cache or discovers on miss.

        Resolution order:
            1. In-memory schema cache (fast path — no I/O).
            2. Warmup pool's public ``get_cached_tool_schemas`` for
               built-in / pooled servers (still no I/O — pool's
               discovery cache was filled at warmup time).
            3. Cold discovery via a throwaway connection, cached
               thereafter.

        The whole check is guarded by ``_schema_cache_lock`` so two
        concurrent first-time calls for the same server only open one
        discovery connection.

        Args:
            server: The MCP server config to fetch schemas for.

        Returns:
            List of ``McpToolSchema`` for the server's tools. May be
            empty if discovery failed; never raises.
        """
        cache_key = server.name

        # Fast path: already cached — no lock needed.
        if cache_key in self._schema_cache:
            return self._schema_cache[cache_key]

        # Slow path: serialize cold discovery per-server.
        async with self._schema_cache_lock:
            # Re-check under lock (another coroutine may have populated it).
            if cache_key in self._schema_cache:
                return self._schema_cache[cache_key]

            # For built-in (pooled) servers, ask the warmup pool first.
            # Use pool.is_pooled_server() as the authoritative membership
            # check — server.is_builtin is a DB column that can drift
            # to False (e.g. before bootstrap completes), and the session
            # provider already uses pool.is_pooled_server() for the same
            # routing decision. Aligning the schema lookup with the
            # session provider avoids builtin STDIO tools becoming
            # invisible to agents when the DB column drifts.
            if self._warmup_pool and self._warmup_pool.is_pooled_server(server.name):
                cached_schemas = self._warmup_pool.get_cached_tool_schemas(
                    server.name
                )
                if cached_schemas is not None:
                    self._schema_cache[cache_key] = cached_schemas
                    return cached_schemas

            # Cold start: one-time connection just to discover schemas.
            schemas = await self._discover_schemas_cold(server)
            self._schema_cache[cache_key] = schemas
            return schemas

    async def _discover_schemas_cold(self, server: McpServer) -> list[McpToolSchema]:
        """One-time schema discovery for non-pooled servers.

        Opens a temporary connection via ``McpConnectionManager``,
        calls ``list_tools``, and tears the connection down. Failures
        are logged and result in an empty list — never raises.

        Args:
            server: The MCP server config to connect to.

        Returns:
            Discovered schemas, or ``[]`` on failure.
        """
        conn_mgr = get_mcp_connection_manager()
        # Use a synthetic, per-server instance id so we don't collide
        # with real instance caches AND so two concurrent discoveries
        # for different servers don't clobber each other's sessions
        # in the connection manager's tracking dict. The connection
        # manager creates and tears down the session in this method's
        # finally clause.
        # S4 (collision safety): the ``_schema_discovery:`` prefix is
        # reserved synthetic namespace — real instance IDs are UUID4
        # strings (``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``) which
        # cannot contain a colon, so a real instance id will never
        # look like ``_schema_discovery:foo``. MCP server names are
        # user-provided; the leading underscore + colon prefix
        # guarantees we stay in our own namespace even if a user names
        # a server after a UUID.
        discovery_id = f"_schema_discovery:{server.name}"
        try:
            await conn_mgr.connect_instance(
                discovery_id, [server], per_server_timeout=15.0
            )
            session = conn_mgr.get_session(discovery_id, server.name)
            if session is None:
                logger.warning(
                    f"Schema discovery: no session for MCP server "
                    f"'{server.name}'"
                )
                return []
            mcp_tools = await session.list_tools()
            return [
                McpToolSchema(
                    name=t.name,
                    description=t.description or "",
                    # W3 (None guard): some MCP servers omit
                    # inputSchema entirely, which the spec permits.
                    # ``or {}`` substitutes an empty JSON Schema so the
                    # downstream StructuredTool still gets a valid
                    # dict — a None would crash Pydantic validation
                    # and the entire discovery would fail.
                    input_schema=t.inputSchema or {},
                    server_name=server.name,
                )
                for t in mcp_tools.tools
            ]
        except Exception as e:
            logger.warning(
                f"Schema discovery failed for {server.name}: {e}"
            )
            return []
        finally:
            # Always tear down the throwaway connection.
            try:
                await conn_mgr.close_instance(discovery_id)
            except Exception as e:
                logger.debug(
                    f"Error closing schema discovery connection for "
                    f"{server.name}: {e}"
                )

    def invalidate_schema_cache(self, server_name: str | None = None) -> None:
        """Invalidate schema cache entries.

        Called from MCP server CRUD routes so a create/update/delete
        on a server forces re-discovery on the next preload.

        Args:
            server_name: If given, drop just that server's entry.
                If ``None``, drop everything.
        """
        if server_name is not None:
            self._schema_cache.pop(server_name, None)
        else:
            self._schema_cache.clear()

    async def eager_warm_schemas(self) -> int:
        """Prime the in-memory schema cache for every active MCP server.

        Called once from ``InstanceManager._warmup_and_report`` after the
        pool warmup task completes. The point is to absorb the per-server
        cost of ``_discover_schemas_cold`` (subprocess spawn + JSON-RPC
        ``list_tools``) into the background warmup window so the **first**
        ``preload_mcp_tools`` call from a user-initiated spawn is a
        in-memory dict lookup instead of a blocking cold discovery.

        Pooled servers hit the warmup pool's discovery cache (free).
        Non-pooled servers fall through to ``_discover_schemas_cold``
        (slow but only once per server, and now off the user path).

        Returns:
            Number of servers successfully primed (logged for visibility).
        """
        try:
            servers = self._manager._mcp_server_repository.list_mcp_servers(
                is_active=True
            )
        except Exception as e:
            logger.warning(f"eager_warm_schemas: failed to list servers: {e}")
            return 0

        if not servers:
            return 0

        primed = 0
        # Serialize with the per-instance preload lock semantics: ``asyncio.gather``
        # is safe here because ``get_schemas_for_server`` is itself guarded by
        # ``_schema_cache_lock`` — concurrent first-time calls for the same
        # server will only open one discovery connection.
        results = await asyncio.gather(
            *(self.get_schemas_for_server(s) for s in servers),
            return_exceptions=True,
        )
        for server, res in zip(servers, results, strict=False):
            if isinstance(res, Exception):
                logger.debug(
                    f"eager_warm_schemas: {server.name} failed: {res}"
                )
                continue
            primed += 1

        logger.info(
            f"eager_warm_schemas: primed {primed}/{len(servers)} MCP server schema(s)"
        )
        return primed

    async def preload_mcp_tools(self, instance_id: str) -> None:
        """Build lazy MCP tool wrappers for an instance — no connections.

        Replaces the eager pre-connect path: this method now only reads
        (or one-time discovers) tool schemas and builds
        ``StructuredTool`` objects whose coroutine defers session
        acquisition to the first call. The first call lands in
        ``_McpSessionProviderImpl.get_session``, which tries the
        warmup pool first and falls back to ``connect_instance``.

        Non-fatal: logs errors and caches an empty list on failure.
        Per-instance locking prevents concurrent preload races.
        The same instance_id short-circuits on the second call.
        """
        async with self._preload_lock:
            if instance_id not in self._preload_locks:
                self._preload_locks[instance_id] = asyncio.Lock()
            lock = self._preload_locks[instance_id]

        async with lock:
            # Idempotency: an in-flight or completed preload for this
            # instance is a no-op.
            if instance_id in self._tools_cache:
                return

            try:
                servers = self._manager._mcp_server_repository.list_mcp_servers(
                    is_active=True
                )
            except Exception as e:
                logger.error(
                    f"Failed to list MCP servers for instance "
                    f"{instance_id[:8]}: {e}"
                )
                self._tools_cache[instance_id] = []
                self._session_caches[instance_id] = {}
                return

            if not servers:
                logger.debug(
                    f"No active MCP servers for instance {instance_id[:8]}"
                )
                self._tools_cache[instance_id] = []
                self._session_caches[instance_id] = {}
                return

            tool_call_timeout = self._get_tool_call_timeout()

            # S-NEW2: Create the session provider ONCE for this
            # instance — every lazy tool for every server on this
            # instance shares the same provider.
            session_provider: McpSessionProvider = self._create_session_provider(
                instance_id
            )

            # Per-instance session state. Keyed by server_name so that
            # every tool for the same instance+server shares the same
            # cache + lock — N tools → 1 session, not N (D9, C1).
            instance_session_state: dict[str, dict[str, Any]] = {}

            all_tools: list[BaseTool] = []

            for server in servers:
                # get_schemas_for_server is connection-free for pooled
                # / cached servers; the cold path opens a throwaway
                # connection that is torn down in `_discover_schemas_cold`.
                try:
                    schemas = await self.get_schemas_for_server(server)
                except Exception as e:
                    logger.warning(
                        f"Schema lookup failed for '{server.name}': {e}"
                    )
                    continue

                if not schemas:
                    continue

                # Shared cache + lock for all tools of this server.
                instance_session_state[server.name] = {
                    "cache": {},
                    "lock": asyncio.Lock(),
                }

                # create_lazy_mcp_tools expects plain dicts, not
                # McpToolSchema dataclasses.
                schema_dicts = [
                    {
                        "name": s.name,
                        "description": s.description,
                        "input_schema": s.input_schema,
                    }
                    for s in schemas
                ]

                lazy_tools = create_lazy_mcp_tools(
                    server_name=server.name,
                    schemas=schema_dicts,
                    session_provider=session_provider,
                    shared_session_cache=instance_session_state[
                        server.name
                    ]["cache"],
                    shared_session_lock=instance_session_state[
                        server.name
                    ]["lock"],
                    tool_call_timeout=tool_call_timeout,
                )
                all_tools.extend(lazy_tools)

            self._tools_cache[instance_id] = all_tools
            self._session_caches[instance_id] = instance_session_state

            logger.info(
                f"Lazy-loaded {len(all_tools)} MCP tool schemas "
                f"from {len(servers)} server(s) for instance "
                f"{instance_id[:8]}"
            )

    def _create_session_provider(self, instance_id: str) -> McpSessionProvider:
        """Build a session provider bound to ``instance_id``.

        Called once per preload (S-NEW2). The provider implements
        ``McpSessionProvider`` and is shared by every lazy tool
        produced for this instance.
        """
        return _McpSessionProviderImpl(self, instance_id)

    def _get_server_by_name(self, server_name: str) -> "McpServer | None":
        """Look up a server config by name from the active set.

        Used by ``_McpSessionProviderImpl`` for the cold-start path.
        """
        try:
            servers = self._manager._mcp_server_repository.list_mcp_servers(
                is_active=True
            )
        except Exception as e:
            logger.warning(
                f"Failed to list MCP servers while resolving "
                f"'{server_name}': {e}"
            )
            return None
        for s in servers:
            if s.name == server_name:
                return s
        return None

    def _get_tool_call_timeout(self) -> int:
        """Return the configured tool-call timeout (seconds).

        Falls back to the same default (``120s``) as
        ``McpPoolConfig.tool_call_timeout`` so a missing config never
        disables timeouts.
        """
        manager = self._manager
        config = getattr(manager, "config", None)
        mcp_pool = getattr(config, "mcp_pool", None) if config is not None else None
        if mcp_pool is not None and hasattr(mcp_pool, "tool_call_timeout"):
            return mcp_pool.tool_call_timeout
        return 120

    def get_mcp_tools(self, instance_id: str) -> list[BaseTool]:
        """Get cached MCP tools for an instance (sync).

        Returns:
            List of MCP tools. Empty list if not preloaded or on error.
        """
        return self._tools_cache.get(instance_id, [])

    async def close_connections(self, instance_id: str) -> None:
        """Close all MCP connections for an instance.

        Pops caches FIRST, then closes connections.
        If close fails, caches are already removed — no orphan.
        Cleans up per-instance lock and per-instance session state to
        prevent memory leaks.
        """
        self._tools_cache.pop(instance_id, None)
        self._session_caches.pop(instance_id, None)
        # Clean up per-instance lock
        async with self._preload_lock:
            self._preload_locks.pop(instance_id, None)
        try:
            conn_mgr = get_mcp_connection_manager()
            await conn_mgr.close_instance(instance_id)
            logger.debug(
                f"Closed MCP connections for instance {instance_id[:8]}"
            )
        except Exception as e:
            logger.warning(
                f"Error closing MCP connections for "
                f"instance {instance_id[:8]}: {e}"
            )

    async def close_all_connections(self) -> None:
        """Close all MCP connections (shutdown cleanup)."""
        self._tools_cache.clear()
        self._session_caches.clear()
        self._preload_locks.clear()
        try:
            conn_mgr = get_mcp_connection_manager()
            await conn_mgr.close_all()
        except Exception as e:
            logger.warning(f"Error closing all MCP connections: {e}")

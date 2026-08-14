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
from daemon.mcp.resilience import ResilienceManager

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
        # Resilience (Phase 4): single shared ``ResilienceManager`` for
        # all servers that opt in via ``BuiltinServerDefinition.resilience_config``.
        # The manager owns per-server circuit breakers + result caches
        # (both in-process, in-memory). Lazy tools receive the manager
        # in ``create_lazy_mcp_tools`` and look up state by server_name
        # on each call — see ``_lazy_coroutine`` in tool_adapter.py.
        self._resilience = ResilienceManager()

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

    async def preload_mcp_tools(
        self,
        instance_id: str,
        *,
        agent_id: str | None = None,
        version_tag: str | None = None,
    ) -> None:
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

        Args:
            instance_id: The instance being preloaded.
            agent_id: Optional base agent identifier. When supplied,
                the matching ``AgentMetadata`` is resolved (via the
                versioned-agent convention ``get_version`` then
                ``get_resolved``) and threaded into the CR-3
                read-only filter so ``mcp_full_access`` opt-outs are
                honored. ``None`` disables the opt-out — every server
                applies its built-in ``read_only_tools`` declaration.
            version_tag: Optional agent version tag. ``None`` selects
                the base agent (matches ``get_version`` semantics).
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

            # CR-3 opt-out (Approach B): resolve the agent's metadata
            # exactly ONCE for the entire server loop so the per-server
            # ``_get_read_only_tools`` calls share a single resolved
            # object. Fail closed: any lookup failure → ``agent_meta``
            # is ``None`` and the CR-3 strip applies as declared by the
            # built-in. The agent boots read-only rather than
            # write-open if identity cannot be resolved. Logging a
            # WARNING (not DEBUG) makes silent identity failures visible
            # at the operational level without spamming debug streams
            # when ``agent_id`` was intentionally not provided.
            agent_meta: Any = None
            if agent_id is not None:
                try:
                    from ..registry import get_registry as _get_agent_registry
                    _agent_registry = _get_agent_registry()
                    agent_meta = _agent_registry.get_version(
                        agent_id, version_tag
                    )
                    if agent_meta is None:
                        # Fallback to alias-resolved base meta. With
                        # ``AGENT_ID_ALIASES`` empty this is equivalent
                        # to ``get(agent_id)``; it's retained for
                        # future-proofing (renames can populate the
                        # alias map without touching this site).
                        agent_meta = _agent_registry.get_resolved(agent_id)
                    if agent_meta is None:
                        logger.warning(
                            f"preload_mcp_tools: agent_id={agent_id!r} "
                            f"version_tag={version_tag!r} could not be "
                            f"resolved; CR-3 read-only filter applies "
                            f"with no per-agent opt-out"
                        )
                except Exception as _resolve_err:
                    logger.warning(
                        f"preload_mcp_tools: registry lookup failed for "
                        f"agent_id={agent_id!r}: {_resolve_err}; "
                        f"CR-3 read-only filter applies with no "
                        f"per-agent opt-out"
                    )
                    agent_meta = None

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

                # Per-server tool-call timeout override: builtin
                # definitions (e.g. a long-running agent-execution tool
                # that may run for several minutes) declare their own
                # ``tool_call_timeout``. STDIO-pooled servers reach
                # create_lazy_mcp_tools via the warmup pool and never
                # hit the long-running path, but HTTP/SSE remote-mode
                # servers (and any non-pooled server) DO land here in
                # the cold-discovery lazy creation path and need the
                # override applied at wrap time. ``is not None`` is
                # used so a definition can explicitly return ``0`` to
                # disable timeout wrapping entirely (handled by
                # ``tool_adapter``) — never coerce 0 to the global
                # default.
                server_timeout = self._get_per_server_timeout(server.name)
                effective_timeout = (
                    server_timeout if server_timeout is not None
                    else tool_call_timeout
                )

                # Per-server tool-name prefix override: essential
                # built-ins (e.g. Plane) declare ``tool_name_prefix``
                # so their tools are exposed as ``plane_*`` (native)
                # instead of ``mcp_plane_*`` (an add-on MCP tool).
                # This only changes the EXPOSED ``StructuredTool.name``
                # — the lazy coroutine's ``original_tool_name`` closure
                # is unaffected, so MCP dispatch keeps working.
                server_prefix = self._get_tool_name_prefix(server.name)

                # Phase 4 resilience: look up the builtin server's
                # resilience config (Plane: retry + cache + circuit
                # breaker + fallback; context7 / webfetch: ``None``).
                # We always pass the manager — ``create_lazy_mcp_tools``
                # itself only opts in when the manager has a config
                # registered for this server. When the config is
                # ``None`` (the common case), the legacy no-resilience
                # path runs unchanged.
                #
                # T-2: guard the call. ``_get_resilience_config`` parses
                # env vars (e.g. ``PLANE_RETRY_MAX_ATTEMPTS``) and
                # builds a ``ResilienceConfig`` dataclass — a bad env
                # var (e.g. ``PLANE_RETRY_MAX_ATTEMPTS=abc``) used to
                # raise ``ValueError`` from ``int(...)`` and crash
                # ``preload_mcp_tools`` for the entire instance, so
                # every MCP server for that instance failed to load.
                # We now log a warning and fall through with
                # ``resilience_config = None`` so the rest of the
                # preload (and the other servers on the instance)
                # still works — the failing server simply runs
                # without resilience. Operators see the warning and
                # can fix the env var.
                try:
                    resilience_config = self._get_resilience_config(
                        server.name
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to register resilience for "
                        f"'{server.name}': {e}. Server will use legacy "
                        f"(no-resilience) path."
                    )
                    resilience_config = None

                # CR-3: read-only tool filtering. When the builtin
                # declares ``read_only_tools = True`` (Plane today),
                # drop write tools from the schema list BEFORE
                # ``create_lazy_mcp_tools`` sees them. The agent's
                # tool list never contains writes — the LLM can't
                # call them. Pattern-matching uses the resilience
                # config's ``read_tool_patterns`` /
                # ``write_tool_patterns`` so the two classifiers
                # (read/write detection here, read vs write in the
                # resilience cache logic) can't disagree.
                #
                # The check is gated on ``resilience_config`` being
                # non-None: a server that hasn't opted into
                # resilience doesn't have patterns, so we can't
                # classify — and the CR-3 fix is a hardening on
                # opted-in servers, not a forced opt-in.
                #
                # Per-agent opt-out (Approach B): when ``agent_meta``
                # is not None and ``server.name`` is in
                # ``agent_meta.mcp_full_access``, ``_get_read_only_tools``
                # returns False — the strip is skipped and the full
                # schema list is passed to ``create_lazy_mcp_tools``.
                if (
                    resilience_config is not None
                    and self._get_read_only_tools(server.name, agent_meta)
                ):
                    # Local import: keep ``is_read_tool`` out of the
                    # module-level import graph when the server
                    # doesn't trigger this branch.
                    from daemon.mcp.resilience import is_read_tool

                    # Build the adapted prefix used by ``create_lazy_mcp_tools``
                    # so the schema name matches the ``is_read_tool`` input
                    # contract (adapted, not bare — see the docstring on
                    # ``is_read_tool``). The prefix mirrors the call
                    # below to ``create_lazy_mcp_tools``.
                    effective_prefix = (
                        f"{server_prefix}_" if server_prefix is not None
                        else f"mcp_{server.name}_"
                    )
                    schema_dicts = [
                        s for s in schema_dicts
                        if is_read_tool(
                            f"{effective_prefix}{s['name']}",
                            resilience_config,
                        )
                    ]
                    logger.info(
                        f"CR-3: filtered {server.name} schemas to "
                        f"{len(schema_dicts)} read-only tool(s)"
                    )

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
                    tool_call_timeout=effective_timeout,
                    tool_name_prefix=server_prefix,
                    resilience_manager=self._resilience,
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

    def _get_per_server_timeout(self, server_name: str) -> int | None:
        """Return a builtin server's ``tool_call_timeout`` override, if any.

        Looks up the ``BuiltinServerDefinition`` in the global registry
        and reads its ``tool_call_timeout`` property. Returns ``None``
        when the server is not a builtin, or when the builtin's
        definition returns ``None`` (the base-class default). Callers
        fall back to ``_get_tool_call_timeout()`` on ``None``.

        The ``0`` sentinel is preserved — a definition that explicitly
        returns ``0`` is requesting "disable timeout wrapping entirely"
        (handled by ``create_lazy_mcp_tools`` / ``tool_adapter``), not
        "use the default". Use ``is not None`` at the call site; do NOT
        collapse to ``server_timeout or tool_call_timeout``.

        Args:
            server_name: The MCP server's name (matches
                ``McpServer.name`` and ``BuiltinServerDefinition.name``).

        Returns:
            The override in seconds, or ``None`` to use the default.
        """
        # Function-local import: keeps the coupling narrow and avoids
        # loading the builtin registry module if this helper is never
        # called (e.g. in unit tests that don't touch builtins).
        from daemon.mcp.builtin_servers import get_registry

        definition = get_registry().get_by_name(server_name)
        if definition is None:
            return None
        # ``tool_call_timeout`` is a property on the abstract base class
        # (returns None by default) and may be overridden by subclasses
        # (e.g. a long-running builtin may return a value up to ~900s).
        # ``getattr`` is defensive against future subclasses that might
        # not override the property.
        return getattr(definition, "tool_call_timeout", None)

    def _get_tool_name_prefix(self, server_name: str) -> str | None:
        """Return a builtin server's ``tool_name_prefix`` override, if any.

        Looks up the ``BuiltinServerDefinition`` in the global registry
        and reads its ``tool_name_prefix`` property. Returns ``None``
        when the server is not a builtin, or when the builtin's
        definition returns ``None`` (the base-class default — the
        standard ``mcp_{server}_`` prefix is used by ``tool_adapter``).

        Currently the only override is ``PlaneServerDefinition``
        (``tool_name_prefix = "plane"``), which makes Plane's tools
        appear as ``plane_*`` (native) instead of ``mcp_plane_*`` (an
        add-on MCP tool that would be caught by ``tools.deny: ["mcp"]``).

        Args:
            server_name: The MCP server's name (matches
                ``McpServer.name`` and ``BuiltinServerDefinition.name``).

        Returns:
            The prefix override (e.g. ``"plane"``) or ``None`` to use
            the standard ``mcp_{server}_`` prefix.
        """
        # Function-local import mirrors ``_get_per_server_timeout`` —
        # keeps the coupling narrow and avoids loading the builtin
        # registry module if this helper is never called.
        from daemon.mcp.builtin_servers import get_registry

        definition = get_registry().get_by_name(server_name)
        if definition is None:
            return None
        # ``tool_name_prefix`` is a property on the abstract base class
        # (returns None by default) and may be overridden by subclasses.
        # ``getattr`` is defensive against future subclasses that might
        # not override the property.
        return getattr(definition, "tool_name_prefix", None)

    def _get_read_only_tools(
        self,
        server_name: str,
        agent_meta: Any = None,
    ) -> bool:
        """Return a builtin server's ``read_only_tools`` flag.

        CR-3: looks up the ``BuiltinServerDefinition`` in the global
        registry and reads its ``read_only_tools`` property. Returns
        ``False`` (the base-class default) for servers that haven't
        opted in — the legacy "all tools exposed, deny at the agent
        layer" path is preserved.

        When the flag is ``True`` (Plane today), ``preload_mcp_tools``
        filters the schema list to read tools only BEFORE
        ``create_lazy_mcp_tools`` wraps them, so the agent's tool
        list never contains writes.

        Per-agent opt-out (Approach B): when ``agent_meta`` is provided
        AND ``server_name`` appears in ``agent_meta.mcp_full_access``,
        the read-only strip is SKIPPED — the agent receives the FULL
        tool surface for that server. ``agent_meta`` may be ``None``
        (the caller's lookup failed, or the identity was never
        supplied) — in that case we always return the builtin's
        declared value, never the opt-out. Fail closed: an empty /
        unset ``mcp_full_access`` list also falls through to the
        builtin's value.

        Args:
            server_name: The MCP server's name (matches
                ``McpServer.name`` and ``BuiltinServerDefinition.name``).
            agent_meta: Optional ``AgentMetadata`` for the calling
                instance. When provided, ``agent_meta.mcp_full_access``
                is consulted for the opt-out.

        Returns:
            ``True`` iff the read-only strip should apply to this
            (agent, server) pair. ``False`` means "expose all tools".
        """
        # Function-local import mirrors the other ``_get_*`` helpers —
        # keeps the coupling narrow.
        from daemon.mcp.builtin_servers import get_registry

        definition = get_registry().get_by_name(server_name)
        if definition is None:
            return False
        # ``read_only_tools`` is a property added in CR-3; it returns
        # ``False`` by default (preserves legacy behavior).
        # ``getattr`` is defensive against future subclasses that might
        # not override the property.
        definition_read_only = bool(
            getattr(definition, "read_only_tools", False)
        )
        # No opt-out flag set: defer entirely to the builtin's default.
        if not definition_read_only:
            # A server that didn't opt into CR-3 strips nothing regardless
            # of any opt-out — preserves legacy behavior. Returning the
            # declaration value (False) is intentional, NOT an early
            # exit: callers expect ``False`` to mean "don't filter".
            return False
        if agent_meta is None:
            return True
        # Per-agent opt-out: the agent's ``mcp_full_access`` list names
        # the servers for which it receives the full surface. We check
        # membership AFTER confirming the server actually wants the
        # filter — never let an opt-out disable the safety on a server
        # that didn't ask for it in the first place.
        full_access = getattr(agent_meta, "mcp_full_access", None) or []
        if server_name in full_access:
            return False
        return True

    def _get_resilience_config(self, server_name: str):
        """Return a builtin server's ``resilience_config``, or ``None``.

        Mirrors the pattern of ``_get_per_server_timeout`` and
        ``_get_tool_name_prefix`` — looks up the builtin registry, reads
        the optional ``resilience_config`` property. ``None`` means
        "no resilience" (the legacy path stays unchanged). For Plane,
        returns the ``ResilienceConfig`` built from env vars.

        The returned config is registered with the shared
        ``ResilienceManager`` so ``_lazy_coroutine`` can look it up by
        ``server_name``. Idempotent — re-registering with an equal
        config is a no-op (the manager overwrites, which is safe).

        Args:
            server_name: The MCP server's name.

        Returns:
            The ``ResilienceConfig`` (already registered), or ``None``
            if the server is not a builtin or hasn't opted in.
        """
        # Function-local import: same coupling-narrow pattern as the
        # other ``_get_*`` helpers.
        from daemon.mcp.builtin_servers import get_registry

        definition = get_registry().get_by_name(server_name)
        if definition is None:
            return None
        # ``resilience_config`` is a property added in Phase 4; it
        # returns ``None`` by default (preserving the no-resilience
        # path). ``getattr`` is defensive in case a future subclass
        # forgets to override it.
        config = getattr(definition, "resilience_config", None)
        if config is None:
            return None
        # Register with the shared manager. Plane's config is rebuilt
        # on every call (env vars can change at runtime via tests);
        # the manager overwrites cleanly.
        self._resilience.register(server_name, config)
        return config

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

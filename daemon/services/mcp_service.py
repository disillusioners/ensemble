"""MCP service — manages MCP tool lifecycle for agent instances.

Consolidates all MCP business logic:
- Connects to MCP servers and discovers tools (async)
- Converts MCP tools to LangChain BaseTool format with mcp_ prefix
- Caches discovered tools per instance for sync retrieval
- Cleans up MCP connections on instance termination
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from daemon.mcp import get_mcp_connection_manager
from daemon.mcp.tool_adapter import adapt_mcp_tools

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from daemon.manager import InstanceManager
    from daemon.mcp.warmup_pool import McpWarmupPool
    from daemon.repositories.mcp_server.models import McpServer

logger = logging.getLogger(__name__)


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

    def set_warmup_pool(self, pool: "McpWarmupPool") -> None:
        """Inject the warm-up pool (called after initialization)."""
        self._warmup_pool = pool

    def _is_builtin_stdio(self, server) -> bool:
        """Check if a server is a built-in STDIO server."""
        from daemon.mcp.builtin_servers import get_registry
        registry = get_registry()
        definition = registry.get_by_name(server.name)
        if definition is None:
            return False
        config = definition.get_base_config()
        if config.get("transport") != "stdio":
            return False
        # Also verify the server's own config transport matches
        server_transport = server.config.get("transport") if isinstance(server.config, dict) else None
        if server_transport is not None and server_transport != "stdio":
            return False
        return True

    async def _probe_connection(self, conn, timeout: float = 3.0) -> bool:
        """Quick liveness probe — MCP protocol ping with short timeout."""
        try:
            await asyncio.wait_for(
                conn.session.send_ping(),
                timeout=timeout,
            )
            return True
        except Exception:
            return False

    async def preload_mcp_tools(self, instance_id: str) -> None:
        """Connect to MCP servers and discover tools for an instance.

        Called from async context BEFORE sync spawn_instance/get_instance.
        Results are cached for sync retrieval by get_mcp_tools().

        Non-fatal: logs errors and caches empty list on failure.
        Uses per-instance locking to prevent concurrent preload races.
        First tries warm-up pool for built-in STDIO servers.
        """
        # Get or create per-instance lock
        async with self._preload_lock:
            if instance_id not in self._preload_locks:
                self._preload_locks[instance_id] = asyncio.Lock()
            lock = self._preload_locks[instance_id]

        async with lock:
            try:
                servers = self._manager._mcp_server_repository.list_mcp_servers(
                    is_active=True
                )

                if not servers:
                    logger.debug(f"No active MCP servers for instance {instance_id[:8]}")
                    self._tools_cache[instance_id] = []
                    return

                conn_mgr = get_mcp_connection_manager()
                pool = self._warmup_pool

                # Split servers into pooled vs cold-start
                pooled_servers = []
                cold_servers = []
                for server in servers:
                    if pool and self._is_builtin_stdio(server):
                        pooled_servers.append(server)
                    else:
                        cold_servers.append(server)

                tools = []

                # Handle pooled servers (from warm-up pool)
                for server in pooled_servers:
                    try:
                        conn = await pool.acquire(server.name)
                    except Exception as e:
                        logger.warning(f"Pool acquire failed for {server.name}: {e}")
                        cold_servers.append(server)
                        continue
                    if conn:
                        # Liveness probe: verify connection is still alive before transfer
                        alive = await self._probe_connection(conn)
                        if alive:
                            # Transfer ownership to connection manager
                            await conn_mgr.transfer_session(
                                instance_id, server.name, conn.session, conn.stream_cm
                            )
                            tools.extend(conn.tools)  # Pre-discovered tools!
                            continue
                        else:
                            # Stale connection — close it, fall back to cold-start
                            logger.warning(f"Stale pooled connection for '{server.name}', falling back")
                            try:
                                await conn.session.close()
                                await conn.stream_cm.__aexit__(None, None, None)
                            except Exception:
                                pass
                    # Pool empty or stale — fall back to cold start for this server
                    cold_servers.append(server)

                # Handle cold-start servers (existing flow)
                if cold_servers:
                    await conn_mgr.connect_instance(instance_id, cold_servers)
                    results = await asyncio.gather(
                        *[self._discover_server_tools(instance_id, s) for s in cold_servers],
                        return_exceptions=True,
                    )
                    for server, result in zip(cold_servers, results):
                        if isinstance(result, Exception):
                            logger.warning(
                                f"Failed to discover tools from MCP server "
                                f"'{server.name}': {result}"
                            )
                        else:
                            tools.extend(result)

                logger.info(
                    f"Discovered {len(tools)} MCP tools from {len(servers)} "
                    f"server(s) for instance {instance_id[:8]}"
                )
                self._tools_cache[instance_id] = tools

            except Exception as e:
                logger.error(
                    f"Failed to preload MCP tools for instance "
                    f"{instance_id[:8]}: {e}"
                )
                self._tools_cache[instance_id] = []

    def get_mcp_tools(self, instance_id: str) -> list[BaseTool]:
        """Get cached MCP tools for an instance (sync).

        Returns:
            List of MCP tools. Empty list if not preloaded or on error.
        """
        return self._tools_cache.get(instance_id, [])

    async def _discover_server_tools(
        self, instance_id: str, server: McpServer
    ) -> list[BaseTool]:
        """Discover tools from a single MCP server and convert to LangChain tools.

        Uses langchain-mcp-adapters for tool conversion.
        Namespaces tool names as mcp_{server_name}_{tool_name}.

        Args:
            instance_id: The instance ID.
            server: The MCP server configuration.

        Returns:
            List of adapted MCP tools.
        """
        conn_mgr = get_mcp_connection_manager()
        session = conn_mgr.get_session(instance_id, server.name)
        if session is None:
            logger.warning(
                f"No session found for MCP server '{server.name}' "
                f"in instance {instance_id[:8]}"
            )
            return []

        # Use langchain-mcp-adapters to list and convert tools
        from langchain_mcp_adapters.tools import load_mcp_tools

        mcp_tools = await load_mcp_tools(session)

        # Namespace tool names using adapt_mcp_tools from tool_adapter
        return adapt_mcp_tools(server.name, mcp_tools)

    async def close_connections(self, instance_id: str) -> None:
        """Close all MCP connections for an instance.

        Pops cache FIRST, then closes connections.
        If close fails, cache is already removed — no orphan.
        Cleans up per-instance lock to prevent memory leaks.
        """
        self._tools_cache.pop(instance_id, None)
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
        self._preload_locks.clear()
        try:
            conn_mgr = get_mcp_connection_manager()
            await conn_mgr.close_all()
        except Exception as e:
            logger.warning(f"Error closing all MCP connections: {e}")

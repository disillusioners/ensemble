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

    async def preload_mcp_tools(self, instance_id: str) -> None:
        """Connect to MCP servers and discover tools for an instance.

        Called from async context BEFORE sync spawn_instance/get_instance.
        Results are cached for sync retrieval by get_mcp_tools().

        Non-fatal: logs errors and caches empty list on failure.
        Uses per-instance locking to prevent concurrent preload races.
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
                await conn_mgr.connect_instance(instance_id, servers)

                # Discover tools from all connected servers (parallel)
                results = await asyncio.gather(
                    *[
                        self._discover_server_tools(instance_id, server)
                        for server in servers
                    ],
                    return_exceptions=True,
                )

                tools = []
                for server, result in zip(servers, results):
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

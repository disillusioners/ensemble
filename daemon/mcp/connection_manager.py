"""MCP client connection manager for managing server sessions per instance."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from daemon.mcp.config import (
    McpSseConfig,
    McpServerConfig,
    McpStdioConfig,
    McpStreamableHttpConfig,
    validate_mcp_server_config,
)
from daemon.repositories.mcp_server.models import McpServer

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Module-level singleton
_mcp_connection_manager: McpConnectionManager | None = None


class McpConnectionManager:
    """
    Manages MCP client sessions per agent instance.

    Handles connection lifecycle for multiple MCP servers across multiple
    instances, providing thread-safe access to client sessions.
    """

    def __init__(self) -> None:
        """Initialize the connection manager with empty connections."""
        self._connections: dict[str, dict[str, ClientSession]] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Get or create the asyncio lock (lazy initialization)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def connect_instance(
        self,
        instance_id: str,
        servers: list[McpServer],
        per_server_timeout: float = 5.0,
    ) -> None:
        """
        Connect all MCP servers for an instance in parallel.

        Args:
            instance_id: Unique identifier for the agent instance
            servers: List of MCP server configurations
            per_server_timeout: Timeout in seconds for each connection attempt
        """
        if not servers:
            return

        lock = self._get_lock()
        async with lock:
            if instance_id not in self._connections:
                self._connections[instance_id] = {}

        async def connect_server(server: McpServer) -> tuple[str, ClientSession]:
            """Connect to a single server and return (name, session)."""
            session = await self._create_session(server, timeout=per_server_timeout)
            return (server.name, session)

        try:
            results = await asyncio.gather(
                *[connect_server(server) for server in servers],
                return_exceptions=True,
            )

            async with lock:
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Failed to connect MCP server: {result}")
                    else:
                        server_name, session = result
                        self._connections[instance_id][server_name] = session

        except Exception as e:
            logger.error(f"Failed to connect instance {instance_id} to MCP servers: {e}")
            raise

    async def _create_session(self, server: McpServer, timeout: float = 5.0) -> ClientSession:
        """
        Create an MCP client session for a server.

        Args:
            server: MCP server configuration
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession

        Raises:
            ValueError: If transport type is unsupported
            asyncio.TimeoutError: If connection times out
        """
        config = validate_mcp_server_config(server.config)

        if isinstance(config, McpStdioConfig):
            return await self._create_stdio_session(config, timeout)
        elif isinstance(config, McpSseConfig):
            return await self._create_sse_session(config, timeout)
        elif isinstance(config, McpStreamableHttpConfig):
            return await self._create_streamable_http_session(config, timeout)
        else:
            raise ValueError(f"Unsupported transport type: {type(config)}")

    async def _create_stdio_session(self, config: McpStdioConfig, timeout: float) -> ClientSession:
        """
        Create a session using STDIO transport.

        Args:
            config: STDIO transport configuration
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession
        """
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )

        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream = await mcp.stdio_client(server_params)
                session = ClientSession(read_stream, write_stream)
                await session.initialize()
                return session
        except asyncio.TimeoutError:
            logger.error(f"STDIO connection timed out for command: {config.command}")
            raise
        except Exception as e:
            logger.error(f"Failed to create STDIO session: {e}")
            raise

    async def _create_sse_session(self, config: McpSseConfig, timeout: float) -> ClientSession:
        """
        Create a session using SSE transport.

        Args:
            config: SSE transport configuration
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession
        """
        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream = await sse_client(
                    config.url,
                    headers=config.headers or {},
                )
                session = ClientSession(read_stream, write_stream)
                await session.initialize()
                return session
        except asyncio.TimeoutError:
            logger.error(f"SSE connection timed out for URL: {config.url}")
            raise
        except Exception as e:
            logger.error(f"Failed to create SSE session: {e}")
            raise

    async def _create_streamable_http_session(
        self,
        config: McpStreamableHttpConfig,
        timeout: float,
    ) -> ClientSession:
        """
        Create a session using Streamable HTTP transport.

        Args:
            config: Streamable HTTP transport configuration
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession
        """
        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream, _ = await streamablehttp_client(
                    config.url,
                    headers=config.headers or {},
                )
                session = ClientSession(read_stream, write_stream)
                await session.initialize()
                return session
        except asyncio.TimeoutError:
            logger.error(f"Streamable HTTP connection timed out for URL: {config.url}")
            raise
        except Exception as e:
            logger.error(f"Failed to create Streamable HTTP session: {e}")
            raise

    def get_session(self, instance_id: str, server_name: str) -> ClientSession | None:
        """
        Get an MCP session for a specific instance and server.

        Args:
            instance_id: Unique identifier for the agent instance
            server_name: Name of the MCP server

        Returns:
            ClientSession if found, None otherwise
        """
        return self._connections.get(instance_id, {}).get(server_name)

    async def close_instance(self, instance_id: str) -> None:
        """
        Close all MCP sessions for an instance.

        Args:
            instance_id: Unique identifier for the agent instance
        """
        lock = self._get_lock()
        async with lock:
            sessions = self._connections.pop(instance_id, {})

        for server_name, session in sessions.items():
            try:
                await session.close()
                logger.debug(f"Closed MCP session for {server_name}")
            except Exception as e:
                logger.warning(f"Error closing MCP session {server_name}: {e}")

    async def close_all(self) -> None:
        """Close all MCP sessions and clean up resources."""
        lock = self._get_lock()
        async with lock:
            all_sessions = list(self._connections.values())
            self._connections.clear()

        for sessions in all_sessions:
            for server_name, session in sessions.items():
                try:
                    await session.close()
                    logger.debug(f"Closed MCP session for {server_name}")
                except Exception as e:
                    logger.warning(f"Error closing MCP session {server_name}: {e}")


def get_mcp_connection_manager() -> McpConnectionManager:
    """
    Get the module-level singleton McpConnectionManager instance.

    Returns:
        The singleton McpConnectionManager
    """
    global _mcp_connection_manager
    if _mcp_connection_manager is None:
        _mcp_connection_manager = McpConnectionManager()
    return _mcp_connection_manager

"""MCP client connection manager for managing server sessions per instance."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from daemon.mcp.config import (
    McpSseConfig,
    McpStdioConfig,
    McpStreamableHttpConfig,
    validate_mcp_server_config,
)
from daemon.repositories.mcp_server.models import McpServer

logger = logging.getLogger(__name__)

# STDIO: Needs time for subprocess spawn (npx/uvx package resolution + download) + handshake
# On cold start, npx needs 8-15s and uvx needs 5-10s
STDIO_DEFAULT_TIMEOUT = 30.0

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
        self._stream_contexts: dict[str, dict[str, Any]] = {}  # instance_id → server_name → stream_cm
        self._lock = asyncio.Lock()  # Eager initialization

    async def _close_session_with_stream(
        self, server_name: str, session: ClientSession, stream_cm: Any = None
    ) -> None:
        """Close a session and its associated stream context manager."""
        try:
            await session.close()
        except Exception as e:
            logger.warning(f"Error closing session for '{server_name}': {e}")
        if stream_cm is not None:
            try:
                await stream_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing streams for '{server_name}': {e}")

    async def _open_and_track_session(
        self,
        streams_cm: Any,
        read_stream: Any,
        write_stream: Any,
        instance_id: str,
        server_name: str,
    ) -> ClientSession:
        """Open a session from streams, track contexts, handle errors."""
        try:
            session = ClientSession(read_stream, write_stream)
            await session.initialize()
            # Track stream context manager for cleanup
            if instance_id not in self._stream_contexts:
                self._stream_contexts[instance_id] = {}
            self._stream_contexts[instance_id][server_name] = streams_cm
            return session
        except Exception as e:
            logger.error(f"Failed to create session for '{server_name}': {e}")
            try:
                await streams_cm.__aexit__(None, None, None)
            except Exception:
                pass
            raise

    async def connect_instance(
        self,
        instance_id: str,
        servers: list[McpServer],
        per_server_timeout: float = 15.0,
    ) -> None:
        """
        Connect all MCP servers for an instance in parallel.

        Args:
            instance_id: Unique identifier for the agent instance
            servers: List of MCP server configurations
            per_server_timeout: Timeout in seconds for each connection attempt (default: 15s)
        """
        if not servers:
            return

        async with self._lock:
            if instance_id not in self._connections:
                self._connections[instance_id] = {}

        async def connect_server(server: McpServer) -> tuple[str, ClientSession]:
            """Connect to a single server and return (name, session)."""
            session = await self._create_session(
                server,
                instance_id=instance_id,
                server_name=server.name,
                timeout=per_server_timeout,
            )
            return (server.name, session)

        try:
            results = await asyncio.gather(
                *[connect_server(server) for server in servers],
                return_exceptions=True,
            )

            async with self._lock:
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Failed to connect MCP server: {result}")
                    else:
                        server_name, session = result
                        self._connections[instance_id][server_name] = session

        except Exception as e:
            logger.error(f"Failed to connect instance {instance_id} to MCP servers: {e}")
            raise

    async def _create_session(
        self,
        server: McpServer,
        instance_id: str,
        server_name: str,
        timeout: float = 5.0,
    ) -> ClientSession:
        """
        Create an MCP client session for a server.

        Args:
            server: MCP server configuration
            instance_id: Unique identifier for the agent instance (for stream tracking)
            server_name: Name of the MCP server (for stream tracking)
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession

        Raises:
            ValueError: If transport type is unsupported
            asyncio.TimeoutError: If connection times out
        """
        config = validate_mcp_server_config(server.config)

        if isinstance(config, McpStdioConfig):
            # STDIO needs longer timeout for subprocess spawn + handshake
            # Use config timeout if specified, otherwise use STDIO default
            effective_timeout = config.timeout if config.timeout is not None else STDIO_DEFAULT_TIMEOUT
            return await self._create_stdio_session(
                config, instance_id, server_name, effective_timeout
            )
        elif isinstance(config, McpSseConfig):
            return await self._create_sse_session(config, instance_id, server_name, timeout)
        elif isinstance(config, McpStreamableHttpConfig):
            return await self._create_streamable_http_session(config, instance_id, server_name, timeout)
        else:
            raise ValueError(f"Unsupported transport type: {type(config)}")

    async def _create_stdio_session(
        self,
        config: McpStdioConfig,
        instance_id: str,
        server_name: str,
        timeout: float,
    ) -> ClientSession:
        """
        Create a session using STDIO transport.

        Args:
            config: STDIO transport configuration
            instance_id: Unique identifier for the agent instance (for stream tracking)
            server_name: Name of the MCP server (for stream tracking)
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession
        """
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )
        streams_cm = mcp.stdio_client(server_params)
        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream = await streams_cm.__aenter__()
                return await self._open_and_track_session(
                    streams_cm, read_stream, write_stream, instance_id, server_name
                )
        except asyncio.TimeoutError:
            command_str = f"{config.command} {' '.join(config.args)}"
            logger.error(
                f"STDIO connection timed out after {timeout}s for command: {command_str}. "
                f"This may be due to cold start (npx/uvx package resolution) taking longer than expected. "
                f"Consider increasing the timeout in the server config (e.g., timeout: 45) or "
                f"ensure the MCP server package is cached."
            )
            # Try to get stderr from the process if available
            try:
                await streams_cm.__aexit__(None, None, None)
            except Exception:
                pass
            raise

    async def _create_sse_session(
        self,
        config: McpSseConfig,
        instance_id: str,
        server_name: str,
        timeout: float,
    ) -> ClientSession:
        """
        Create a session using SSE transport.

        Args:
            config: SSE transport configuration
            instance_id: Unique identifier for the agent instance (for stream tracking)
            server_name: Name of the MCP server (for stream tracking)
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession
        """
        streams_cm = sse_client(config.url, headers=config.headers or {})
        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream = await streams_cm.__aenter__()
                return await self._open_and_track_session(
                    streams_cm, read_stream, write_stream, instance_id, server_name
                )
        except asyncio.TimeoutError:
            logger.error(f"SSE connection timed out for URL: {config.url}")
            raise

    async def _create_streamable_http_session(
        self,
        config: McpStreamableHttpConfig,
        instance_id: str,
        server_name: str,
        timeout: float,
    ) -> ClientSession:
        """
        Create a session using Streamable HTTP transport.

        Args:
            config: Streamable HTTP transport configuration
            instance_id: Unique identifier for the agent instance (for stream tracking)
            server_name: Name of the MCP server (for stream tracking)
            timeout: Connection timeout in seconds

        Returns:
            Initialized MCP ClientSession
        """
        streams_cm = streamablehttp_client(config.url, headers=config.headers or {})
        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream, _ = await streams_cm.__aenter__()
                return await self._open_and_track_session(
                    streams_cm, read_stream, write_stream, instance_id, server_name
                )
        except asyncio.TimeoutError:
            logger.error(f"Streamable HTTP connection timed out for URL: {config.url}")
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

    async def transfer_session(
        self,
        instance_id: str,
        server_name: str,
        session: ClientSession,
        stream_cm: Any,
    ) -> None:
        """Transfer an externally-managed session into this manager's tracking."""
        async with self._lock:
            if instance_id not in self._connections:
                self._connections[instance_id] = {}
            if instance_id not in self._stream_contexts:
                self._stream_contexts[instance_id] = {}
            self._connections[instance_id][server_name] = session
            self._stream_contexts[instance_id][server_name] = stream_cm
        logger.debug(f"Transferred pooled session for '{server_name}' to instance {instance_id[:8]}")

    async def close_instance(self, instance_id: str) -> None:
        """
        Close all MCP sessions and stream context managers for an instance.

        Args:
            instance_id: Unique identifier for the agent instance
        """
        async with self._lock:
            sessions = self._connections.pop(instance_id, {})
            streams = self._stream_contexts.pop(instance_id, {})

        await asyncio.gather(
            *[
                self._close_session_with_stream(name, sess, streams.get(name))
                for name, sess in sessions.items()
            ],
            return_exceptions=True,
        )

    async def close_all(self) -> None:
        """Close all MCP sessions and stream context managers, cleaning up all resources."""
        async with self._lock:
            all_instances = list(self._connections.keys())
            all_sessions = dict(self._connections)
            all_streams = dict(self._stream_contexts)
            self._connections.clear()
            self._stream_contexts.clear()

        # Collect all sessions and streams to close in parallel
        close_tasks = []
        for instance_id in all_instances:
            sessions = all_sessions.get(instance_id, {})
            streams = all_streams.get(instance_id, {})
            for server_name, session in sessions.items():
                close_tasks.append(
                    self._close_session_with_stream(server_name, session, streams.get(server_name))
                )

        await asyncio.gather(*close_tasks, return_exceptions=True)


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

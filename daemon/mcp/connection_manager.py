"""MCP client connection manager for managing server sessions per instance."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from daemon.mcp.config import (
    McpSseConfig,
    McpStdioConfig,
    McpStreamableHttpConfig,
    validate_mcp_server_config,
)
from daemon.mcp.managed_session import ManagedClientSession
from daemon.mcp.stdio_wrapper import TaskScopedContextManager, TaskScopedStdioClient
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
        self._connections: dict[str, dict[str, ManagedClientSession]] = {}
        self._stream_contexts: dict[str, dict[str, Any]] = {}  # instance_id → server_name → stream_cm
        self._lock = asyncio.Lock()  # Eager initialization

    async def _close_session_with_stream(
        self, server_name: str, session: ManagedClientSession, stream_cm: Any = None
    ) -> None:
        """Close a session and its associated stream context manager."""
        # Stop the session's receive loop first
        try:
            await session.stop()
        except Exception as e:
            logger.warning(f"Error stopping session for '{server_name}': {e}")
        # Handle tuple from _open_and_track_session: (streams_cm, session)
        actual_stream_cm = stream_cm
        if isinstance(stream_cm, tuple):
            actual_stream_cm = stream_cm[0]
        # Close the stream context manager
        if actual_stream_cm is not None:
            try:
                await actual_stream_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing streams for '{server_name}': {e}")

    async def _open_and_track_session(
        self,
        streams_cm: Any,
        read_stream: Any,
        write_stream: Any,
        instance_id: str,
        server_name: str,
    ) -> ManagedClientSession:
        """Open a session from streams, track contexts, handle errors."""
        # Use ManagedClientSession so we can control task group lifecycle
        session = ManagedClientSession(read_stream, write_stream)
        try:
            # Start the receive loop (required for ClientSession to work)
            await session.start()
            await session.initialize()
            # Track stream context manager for cleanup
            if instance_id not in self._stream_contexts:
                self._stream_contexts[instance_id] = {}
            self._stream_contexts[instance_id][server_name] = (streams_cm, session)
            return session
        except Exception as e:
            logger.error(f"Failed to create session for '{server_name}': {e}")
            try:
                await session.stop()
            except Exception as e:
                logger.debug("session stop error: %s", e)
            try:
                await streams_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("streams cleanup error: %s", e)
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
    ) -> ManagedClientSession:
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
    ) -> ManagedClientSession:
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
        streams_cm = TaskScopedStdioClient(server_params)
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
    ) -> ManagedClientSession:
        """
        Create a session using SSE transport.

        Args:
            config: SSE transport configuration
            instance_id: Unique identifier for the agent instance (for stream tracking)
            server_name: Name of the MCP server (for stream tracking)
            timeout: Connection timeout in seconds (default: 5s)

        Returns:
            Initialized MCP ManagedClientSession
        """
        # ``sse_client`` is anyio-task-group backed, so closing the raw
        # ``streams_cm`` from a different task (instance termination /
        # pool health check / replenish / test-helper disconnect) would
        # raise ``RuntimeError: Attempted to exit cancel scope in a
        # different task``. ``TaskScopedContextManager`` owns the inner
        # CM in a dedicated background task, mirroring the STDIO fix
        # in commit ``c025480`` and its SSE/streamable-HTTP extension.
        streams_cm = TaskScopedContextManager(
            factory=lambda: sse_client(config.url, headers=config.headers or {}),
            name="mcp-sse-client",
        )
        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream = await streams_cm.__aenter__()
                return await self._open_and_track_session(
                    streams_cm, read_stream, write_stream, instance_id, server_name
                )
        except asyncio.TimeoutError:
            logger.error(f"SSE connection timed out for URL: {config.url}")
            try:
                await streams_cm.__aexit__(None, None, None)
            except Exception:
                pass
            raise

    async def _create_streamable_http_session(
        self,
        config: McpStreamableHttpConfig,
        instance_id: str,
        server_name: str,
        timeout: float,
    ) -> ManagedClientSession:
        """
        Create a session using Streamable HTTP transport.

        Args:
            config: Streamable HTTP transport configuration
            instance_id: Unique identifier for the agent instance (for stream tracking)
            server_name: Name of the MCP server (for stream tracking)
            timeout: Connection timeout in seconds (default: 5s)

        Returns:
            Initialized MCP ManagedClientSession
        """
        # ``streamablehttp_client`` is anyio-task-group backed and
        # exhibits the same cross-task cancel-scope error as
        # ``sse_client`` / ``stdio_client`` (see ``_create_sse_session``
        # and commit ``c025480`` for the original STDIO fix). The
        # wrapper owns the inner CM in a dedicated background task so
        # ``__aenter__`` and ``__aexit__`` always run in the same task.
        streams_cm = TaskScopedContextManager(
            factory=lambda: streamablehttp_client(config.url, headers=config.headers or {}),
            name="mcp-streamable-http-client",
        )
        try:
            async with asyncio.timeout(timeout):
                read_stream, write_stream, _ = await streams_cm.__aenter__()
                return await self._open_and_track_session(
                    streams_cm, read_stream, write_stream, instance_id, server_name
                )
        except asyncio.TimeoutError:
            logger.error(f"Streamable HTTP connection timed out for URL: {config.url}")
            try:
                await streams_cm.__aexit__(None, None, None)
            except Exception:
                pass
            raise

    def get_session(self, instance_id: str, server_name: str) -> ManagedClientSession | None:
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
            # For transferred sessions, stream_cm is just the streams_cm (not a tuple)
            self._stream_contexts[instance_id][server_name] = (stream_cm, session)
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

    async def create_test_session(
        self,
        config: dict[str, Any],
        timeout: float = 15.0,
    ) -> tuple[ManagedClientSession, Any]:
        """
        Create a temporary MCP session for testing connectivity.

        This method creates a session WITHOUT tracking it in the connection manager,
        allowing for clean test-and-disconnect workflows.

        Args:
            config: MCP server configuration dict (transport + transport-specific fields)
            timeout: Connection timeout in seconds (default: 15s)

        Returns:
            Tuple of (ManagedClientSession, stream_context_manager)

        Raises:
            McpConfigValidationError: If config is invalid
            ValueError: If transport type is unsupported
            asyncio.TimeoutError: If connection times out
            Exception: For other connection errors
        """
        validated_config = validate_mcp_server_config(config)

        if isinstance(validated_config, McpStdioConfig):
            effective_timeout = (
                validated_config.timeout if validated_config.timeout is not None else STDIO_DEFAULT_TIMEOUT
            )
            return await self._create_test_stdio_session(validated_config, effective_timeout)
        elif isinstance(validated_config, McpSseConfig):
            return await self._create_test_sse_session(validated_config, timeout)
        elif isinstance(validated_config, McpStreamableHttpConfig):
            return await self._create_test_streamable_http_session(validated_config, timeout)
        else:
            raise ValueError(f"Unsupported transport type: {type(validated_config)}")

    async def _create_test_stdio_session(
        self,
        config: McpStdioConfig,
        timeout: float,
    ) -> tuple[ManagedClientSession, Any]:
        """Create a test session for STDIO transport."""
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )
        streams_cm = TaskScopedStdioClient(server_params)
        return await self._create_test_session_from_streams(streams_cm, timeout, is_streamable_http=False)

    async def _create_test_sse_session(
        self,
        config: McpSseConfig,
        timeout: float,
    ) -> tuple[ManagedClientSession, Any]:
        """Create a test session for SSE transport."""
        streams_cm = TaskScopedContextManager(
            factory=lambda: sse_client(config.url, headers=config.headers or {}),
            name="mcp-sse-client",
        )
        return await self._create_test_session_from_streams(streams_cm, timeout, is_streamable_http=False)

    async def _create_test_streamable_http_session(
        self,
        config: McpStreamableHttpConfig,
        timeout: float,
    ) -> tuple[ManagedClientSession, Any]:
        """Create a test session for Streamable HTTP transport."""
        streams_cm = TaskScopedContextManager(
            factory=lambda: streamablehttp_client(config.url, headers=config.headers or {}),
            name="mcp-streamable-http-client",
        )
        return await self._create_test_session_from_streams(streams_cm, timeout, is_streamable_http=True)

    async def _create_test_session_from_streams(
        self,
        streams_cm: Any,
        timeout: float,
        is_streamable_http: bool = False,
    ) -> tuple[ManagedClientSession, Any]:
        """
        Shared helper to create a test session from streams context manager.

        Note: For asyncio.TimeoutError, we let it propagate without cleanup because
        the asyncio.timeout context already handles cancellation properly. Calling
        session.stop() on an already-cancelled scope can cause issues.
        """
        session = None
        try:
            async with asyncio.timeout(timeout):
                result = await streams_cm.__aenter__()
                read_stream, write_stream = (result[0], result[1]) if is_streamable_http else result
                session = ManagedClientSession(read_stream, write_stream)
                await session.start()
                await session.initialize()
                return (session, streams_cm)
        except asyncio.TimeoutError:
            # Don't cleanup here - asyncio.timeout context handles cancellation
            raise
        except Exception:
            if session is not None:
                try:
                    await session.stop()
                except Exception as e:
                    logger.debug("session stop error: %s", e)
            try:
                await streams_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("streams cleanup error: %s", e)
            raise


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

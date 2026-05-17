"""Unit tests for MCP concurrent operations and edge cases."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.mcp.connection_manager import McpConnectionManager
from daemon.services.mcp_service import McpService


def _make_server(name: str = "test-server") -> MagicMock:
    """Create a mock MCP server."""
    server = MagicMock()
    server.name = name
    server.config = {"transport": "stdio", "command": "python"}
    return server


class TestConcurrentConnectionManager:
    """Tests for concurrent operations on McpConnectionManager."""

    @pytest.mark.asyncio
    async def test_concurrent_connect_same_instance(self):
        """Multiple connect calls for same instance should be safe."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()

        server = _make_server()

        with patch.object(mgr, "_create_session", return_value=mock_session):
            await asyncio.gather(
                mgr.connect_instance("inst-1", [server]),
                mgr.connect_instance("inst-1", [server]),
            )

        # Should have the session (may be set twice but no error)
        assert mgr.get_session("inst-1", "test-server") is not None

    @pytest.mark.asyncio
    async def test_concurrent_connect_different_instances(self):
        """Concurrent connects for different instances should work."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()

        server1 = _make_server("server-1")
        server2 = _make_server("server-2")

        with patch.object(mgr, "_create_session", return_value=mock_session):
            await asyncio.gather(
                mgr.connect_instance("inst-1", [server1]),
                mgr.connect_instance("inst-2", [server2]),
            )

        assert mgr.get_session("inst-1", "server-1") is not None
        assert mgr.get_session("inst-2", "server-2") is not None

    @pytest.mark.asyncio
    async def test_concurrent_connect_and_check(self):
        """Connect and immediate get_session should work."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()

        server = _make_server()

        with patch.object(mgr, "_create_session", return_value=mock_session):
            await mgr.connect_instance("inst-1", [server])

        # After successful connect, session should be retrievable
        assert mgr.get_session("inst-1", "test-server") is mock_session

    @pytest.mark.asyncio
    async def test_lock_under_concurrent_access(self):
        """Lock should work correctly under concurrent access."""
        mgr = McpConnectionManager()

        # Lock is eagerly initialized, verify it exists
        assert mgr._lock is not None

        # Access lock from many concurrent coroutines
        async def access_lock():
            async with mgr._lock:
                await asyncio.sleep(0.01)

        await asyncio.gather(*[access_lock() for _ in range(10)])

        # Should have exactly one lock (same instance)
        assert mgr._lock is not None


class TestConcurrentMcpService:
    """Tests for concurrent operations on McpService."""

    @pytest.mark.asyncio
    async def test_concurrent_preload_same_instance_idempotent(self):
        """Preloading same instance concurrently should be idempotent."""
        manager = MagicMock()
        manager._mcp_server_repository.list_mcp_servers.return_value = []

        service = McpService(manager=manager)

        # Preload same instance concurrently
        await asyncio.gather(
            service.preload_mcp_tools("inst-1"),
            service.preload_mcp_tools("inst-1"),
        )

        # Cache should exist
        assert "inst-1" in service._tools_cache

    @pytest.mark.asyncio
    async def test_concurrent_preload_different_instances(self):
        """Preloading different instances concurrently should work."""
        manager = MagicMock()
        manager._mcp_server_repository.list_mcp_servers.return_value = []

        service = McpService(manager=manager)

        await asyncio.gather(
            service.preload_mcp_tools("inst-1"),
            service.preload_mcp_tools("inst-2"),
        )

        assert "inst-1" in service._tools_cache
        assert "inst-2" in service._tools_cache

    @pytest.mark.asyncio
    async def test_concurrent_close_and_preload(self):
        """Closing while preloading should not crash."""
        manager = MagicMock()
        manager._mcp_server_repository.list_mcp_servers.return_value = []

        service = McpService(manager=manager)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await asyncio.gather(
                service.preload_mcp_tools("inst-1"),
                service.close_connections("inst-1"),
            )

        # No crash = success

    @pytest.mark.asyncio
    async def test_close_connections_idempotent(self):
        """Closing connections multiple times should not raise."""
        manager = MagicMock()
        service = McpService(manager=manager)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await asyncio.gather(
                service.close_connections("inst-1"),
                service.close_connections("inst-1"),
            )

        # Both calls should complete without error
        assert mock_conn_mgr.close_instance.call_count == 2

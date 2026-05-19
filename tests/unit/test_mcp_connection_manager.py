"""Unit tests for MCP connection manager."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.mcp.connection_manager import McpConnectionManager, get_mcp_connection_manager


class TestMcpConnectionManagerInit:
    """Tests for McpConnectionManager initialization."""

    def test_lock_created_eagerly_in_init(self):
        """Lock should be created eagerly in __init__."""
        mgr = McpConnectionManager()
        assert mgr._lock is not None
        assert isinstance(mgr._lock, asyncio.Lock)

    def test_connections_empty_after_init(self):
        """Connections dict should be empty after __init__."""
        mgr = McpConnectionManager()
        assert mgr._connections == {}

    def test_stream_contexts_empty_after_init(self):
        """Stream contexts dict should be empty after __init__."""
        mgr = McpConnectionManager()
        assert mgr._stream_contexts == {}


class TestGetSession:
    """Tests for get_session method."""

    def test_returns_none_when_not_connected(self):
        """Should return None when instance has no connections."""
        mgr = McpConnectionManager()
        assert mgr.get_session("inst-1", "server-1") is None

    def test_returns_none_for_unknown_instance(self):
        """Should return None for unknown instance ID."""
        mgr = McpConnectionManager()
        mgr._connections["other-inst"] = {"server-1": MagicMock()}
        assert mgr.get_session("inst-1", "server-1") is None

    def test_returns_session_when_connected(self):
        """Should return session when it exists."""
        mgr = McpConnectionManager()
        session = MagicMock()
        mgr._connections["inst-1"] = {"server-1": session}
        assert mgr.get_session("inst-1", "server-1") is session


class TestConnectInstance:
    """Tests for connect_instance method."""

    @pytest.mark.asyncio
    async def test_creates_sessions_for_all_servers(self):
        """Should create sessions for all provided servers."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()

        server1 = MagicMock()
        server1.name = "server-1"
        server2 = MagicMock()
        server2.name = "server-2"

        async def mock_create(server, instance_id=None, server_name=None, timeout=None):
            return mock_session

        with patch.object(mgr, "_create_session", side_effect=mock_create):
            await mgr.connect_instance("inst-1", [server1, server2])

        assert mgr.get_session("inst-1", "server-1") is mock_session
        assert mgr.get_session("inst-1", "server-2") is mock_session

    @pytest.mark.asyncio
    async def test_handles_failure_gracefully(self):
        """Failed connections should not crash and should return None for that server."""
        mgr = McpConnectionManager()

        server = MagicMock()
        server.name = "bad-server"

        async def mock_create(server, instance_id=None, server_name=None, timeout=None):
            raise Exception("Connection refused")

        with patch.object(mgr, "_create_session", side_effect=mock_create):
            await mgr.connect_instance("inst-1", [server])

        assert mgr.get_session("inst-1", "bad-server") is None

    @pytest.mark.asyncio
    async def test_partial_failure_stores_successful(self):
        """Successful connections should be stored even if some fail."""
        mgr = McpConnectionManager()
        good_session = AsyncMock()

        good_server = MagicMock()
        good_server.name = "good-server"
        bad_server = MagicMock()
        bad_server.name = "bad-server"

        async def mock_create(server, instance_id=None, server_name=None, timeout=None):
            if server.name == "bad-server":
                raise Exception("Failed")
            return good_session

        with patch.object(mgr, "_create_session", side_effect=mock_create):
            await mgr.connect_instance("inst-1", [good_server, bad_server])

        assert mgr.get_session("inst-1", "good-server") is good_session
        assert mgr.get_session("inst-1", "bad-server") is None

    @pytest.mark.asyncio
    async def test_empty_servers_list(self):
        """Empty servers list should return early without creating entry."""
        mgr = McpConnectionManager()
        await mgr.connect_instance("inst-1", [])
        # Method returns early for empty list, no entry created
        assert mgr._connections.get("inst-1") is None


class TestCloseInstance:
    """Tests for close_instance method."""

    @pytest.mark.asyncio
    async def test_removes_sessions_and_streams(self):
        """Should remove all sessions and stream contexts for an instance."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()
        mgr._connections["inst-1"] = {"server-1": mock_session}

        # Add stream context
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aexit__ = AsyncMock()
        mgr._stream_contexts["inst-1"] = {"server-1": mock_stream_cm}

        await mgr.close_instance("inst-1")

        assert "inst-1" not in mgr._connections
        assert "inst-1" not in mgr._stream_contexts
        mock_session.close.assert_awaited_once()
        mock_stream_cm.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idempotent_close(self):
        """Closing non-existent instance should not raise."""
        mgr = McpConnectionManager()
        # Should not raise
        await mgr.close_instance("non-existent")

    @pytest.mark.asyncio
    async def test_handles_close_error(self):
        """Session should be removed even if close() raises."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()
        mock_session.close.side_effect = Exception("Close failed")
        mgr._connections["inst-1"] = {"server-1": mock_session}

        # Should not raise
        await mgr.close_instance("inst-1")

        # Session should be removed even if close failed
        assert "inst-1" not in mgr._connections

    @pytest.mark.asyncio
    async def test_closes_streams_even_if_session_close_fails(self):
        """Stream contexts should be closed even if session close fails."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()
        mock_session.close.side_effect = Exception("Close failed")
        mgr._connections["inst-1"] = {"server-1": mock_session}

        mock_stream_cm = MagicMock()
        mock_stream_cm.__aexit__ = AsyncMock()
        mgr._stream_contexts["inst-1"] = {"server-1": mock_stream_cm}

        await mgr.close_instance("inst-1")

        # Stream should still be closed
        mock_stream_cm.__aexit__.assert_awaited_once()


class TestCloseAll:
    """Tests for close_all method."""

    @pytest.mark.asyncio
    async def test_closes_all_connections(self):
        """Should close all sessions across all instances."""
        mgr = McpConnectionManager()
        s1 = AsyncMock()
        s2 = AsyncMock()
        mgr._connections["inst-1"] = {"server-1": s1}
        mgr._connections["inst-2"] = {"server-2": s2}

        # Add stream contexts
        mock_stream_cm1 = MagicMock()
        mock_stream_cm1.__aexit__ = AsyncMock()
        mock_stream_cm2 = MagicMock()
        mock_stream_cm2.__aexit__ = AsyncMock()
        mgr._stream_contexts["inst-1"] = {"server-1": mock_stream_cm1}
        mgr._stream_contexts["inst-2"] = {"server-2": mock_stream_cm2}

        await mgr.close_all()

        assert mgr._connections == {}
        assert mgr._stream_contexts == {}
        s1.close.assert_awaited_once()
        s2.close.assert_awaited_once()
        mock_stream_cm1.__aexit__.assert_awaited_once()
        mock_stream_cm2.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_close_errors(self):
        """Should handle errors in individual close() calls."""
        mgr = McpConnectionManager()
        s1 = AsyncMock()
        s1.close.side_effect = Exception("Failed")
        mgr._connections["inst-1"] = {"server-1": s1}

        await mgr.close_all()
        assert mgr._connections == {}
        assert mgr._stream_contexts == {}


class TestTransferSession:
    """Tests for transfer_session method."""

    @pytest.mark.asyncio
    async def test_transfer_session_registers_connection(self):
        """Should register session and stream_cm in tracking dicts."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aexit__ = AsyncMock()

        await mgr.transfer_session("inst-1", "server-1", mock_session, mock_stream_cm)

        assert mgr._connections["inst-1"]["server-1"] is mock_session
        assert mgr._stream_contexts["inst-1"]["server-1"] is mock_stream_cm

    @pytest.mark.asyncio
    async def test_transfer_session_integrates_with_close(self):
        """Transferred sessions should be cleaned up by close_instance."""
        mgr = McpConnectionManager()
        mock_session = AsyncMock()
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aexit__ = AsyncMock()

        await mgr.transfer_session("inst-1", "server-1", mock_session, mock_stream_cm)
        await mgr.close_instance("inst-1")

        # Session should be closed and removed
        mock_session.close.assert_awaited_once()
        mock_stream_cm.__aexit__.assert_awaited_once()
        assert "inst-1" not in mgr._connections
        assert "inst-1" not in mgr._stream_contexts


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_manager_returns_instance(self):
        """get_mcp_connection_manager should return singleton instance."""
        # Reset singleton
        import daemon.mcp.connection_manager as cm

        cm._mcp_connection_manager = None

        mgr = get_mcp_connection_manager()
        assert isinstance(mgr, McpConnectionManager)

        # Same instance returned
        mgr2 = get_mcp_connection_manager()
        assert mgr is mgr2

        # Cleanup
        cm._mcp_connection_manager = None

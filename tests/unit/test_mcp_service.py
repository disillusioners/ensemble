"""Unit tests for MCP service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.services.mcp_service import McpService


def _make_server(name: str = "test-server", config: dict = None, is_active: bool = True):
    """Create a mock MCP server."""
    server = MagicMock()
    server.name = name
    server.config = config or {"transport": "stdio", "command": "python"}
    server.is_active = is_active
    return server


def _make_tool(name: str = "echo", description: str = "Echo tool"):
    """Create a mock LangChain tool."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.copy = MagicMock(return_value=tool)
    return tool


@pytest.fixture
def manager():
    """Create a mock manager with MCP server repository."""
    mgr = MagicMock()
    mgr._mcp_server_repository = MagicMock()
    return mgr


@pytest.fixture
def service(manager):
    """Create an McpService instance with mock manager."""
    return McpService(manager=manager)


class TestPreloadMcpTools:
    """Tests for preload_mcp_tools method."""

    @pytest.mark.asyncio
    async def test_caches_tools_from_servers(self, service, manager):
        """Preload discovers and caches tools from active servers."""
        tool = _make_tool(name="echo", description="Echo tool")
        server = _make_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_session = MagicMock()
        mock_conn_mgr.get_session.return_value = mock_session
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[tool]
        ), patch(
            "daemon.mcp.tool_adapter.adapt_mcp_tools",
            return_value=[tool]
        ):
            await service.preload_mcp_tools("inst-1")

        cached = service.get_mcp_tools("inst-1")
        assert len(cached) == 1

    @pytest.mark.asyncio
    async def test_empty_servers_caches_empty(self, service, manager):
        """No active servers results in empty cache."""
        manager._mcp_server_repository.list_mcp_servers.return_value = []

        await service.preload_mcp_tools("inst-1")

        assert service.get_mcp_tools("inst-1") == []
        manager._mcp_server_repository.list_mcp_servers.assert_called_once_with(is_active=True)

    @pytest.mark.asyncio
    async def test_all_servers_failing_caches_empty(self, service, manager):
        """Connection failure for all servers results in empty cache."""
        server = _make_server()
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session.return_value = None  # No session = failed

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.preload_mcp_tools("inst-1")

        assert service.get_mcp_tools("inst-1") == []

    @pytest.mark.asyncio
    async def test_partial_failure_caches_partial_tools(self, service, manager):
        """Some servers failing doesn't affect working servers."""
        good_tool = _make_tool(name="good_tool")
        server1 = _make_server(name="good-server")
        server2 = _make_server(name="bad-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server1, server2]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        def get_session(inst_id, server_name):
            if server_name == "good-server":
                return MagicMock()
            return None

        mock_conn_mgr.get_session.side_effect = get_session

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[good_tool]
        ), patch(
            "daemon.mcp.tool_adapter.adapt_mcp_tools",
            return_value=[good_tool]
        ):
            await service.preload_mcp_tools("inst-1")

        tools = service.get_mcp_tools("inst-1")
        assert len(tools) == 1  # Only from good server

    @pytest.mark.asyncio
    async def test_calls_connect_instance(self, service, manager):
        """Preload calls connect_instance with correct servers."""
        server = _make_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session.return_value = MagicMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[]
        ), patch(
            "daemon.mcp.tool_adapter.adapt_mcp_tools",
            return_value=[]
        ):
            await service.preload_mcp_tools("inst-1")

        mock_conn_mgr.connect_instance.assert_awaited_once_with("inst-1", [server])

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self, service, manager):
        """Exception during preload results in empty cache, not raised."""
        manager._mcp_server_repository.list_mcp_servers.side_effect = RuntimeError(
            "Database error"
        )

        await service.preload_mcp_tools("inst-1")

        assert service.get_mcp_tools("inst-1") == []


class TestGetMcpTools:
    """Tests for get_mcp_tools method (sync)."""

    def test_returns_empty_when_not_cached(self, service):
        """Unknown instance returns empty list."""
        assert service.get_mcp_tools("nonexistent") == []

    def test_returns_cached_tools(self, service):
        """Known instance returns cached tools."""
        tool = MagicMock()
        service._tools_cache["inst-1"] = [tool]
        assert service.get_mcp_tools("inst-1") == [tool]

    def test_does_not_modify_cache(self, service):
        """get_mcp_tools is read-only, doesn't clear cache."""
        tool = MagicMock()
        service._tools_cache["inst-1"] = [tool]
        result = service.get_mcp_tools("inst-1")
        assert result == [tool]
        assert "inst-1" in service._tools_cache


class TestCloseConnections:
    """Tests for close_connections method."""

    @pytest.mark.asyncio
    async def test_pops_cache_before_close(self, service):
        """Cache is popped BEFORE closing connections."""
        tool = MagicMock()
        service._tools_cache["inst-1"] = [tool]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_connections("inst-1")

        # Cache should be empty immediately
        assert "inst-1" not in service._tools_cache
        mock_conn_mgr.close_instance.assert_awaited_once_with("inst-1")

    @pytest.mark.asyncio
    async def test_handles_close_error(self, service):
        """Close error is logged but cache is still popped."""
        service._tools_cache["inst-1"] = [MagicMock()]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock(side_effect=Exception("Close failed"))

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_connections("inst-1")

        # Cache should still be popped even on error
        assert "inst-1" not in service._tools_cache

    @pytest.mark.asyncio
    async def test_idempotent_close(self, service):
        """Closing non-existent instance doesn't raise."""
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_connections("nonexistent")

        mock_conn_mgr.close_instance.assert_awaited_once_with("nonexistent")
        assert service._tools_cache == {}


class TestCloseAllConnections:
    """Tests for close_all_connections method."""

    @pytest.mark.asyncio
    async def test_clears_all_caches(self, service):
        """All caches are cleared on close_all."""
        service._tools_cache["inst-1"] = [MagicMock()]
        service._tools_cache["inst-2"] = [MagicMock()]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_all = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_all_connections()

        assert service._tools_cache == {}
        mock_conn_mgr.close_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_close_all_error(self, service):
        """Error during close_all doesn't prevent cache clear."""
        service._tools_cache["inst-1"] = [MagicMock()]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_all = AsyncMock(side_effect=Exception("Close failed"))

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_all_connections()

        # Cache should still be cleared
        assert service._tools_cache == {}

    @pytest.mark.asyncio
    async def test_close_all_empty_cache(self, service):
        """Close all with no cached instances doesn't raise."""
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_all = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_all_connections()

        mock_conn_mgr.close_all.assert_awaited_once()

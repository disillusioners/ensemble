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


class TestProbeConnection:
    """Tests for _probe_connection method."""

    @pytest.mark.asyncio
    async def test_probe_connection_live(self, service):
        """Should return True for live sessions."""
        # _probe_connection expects conn with .session attribute
        mock_conn = MagicMock()
        mock_conn.session.send_ping = AsyncMock()

        result = await service._probe_connection(mock_conn)

        assert result is True
        mock_conn.session.send_ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_probe_connection_dead(self, service):
        """Should return False for dead sessions."""
        # _probe_connection expects conn with .session attribute
        mock_conn = MagicMock()
        mock_conn.session.send_ping = AsyncMock(side_effect=RuntimeError("Connection dead"))

        result = await service._probe_connection(mock_conn)

        assert result is False


class TestIsBuiltinStdio:
    """Tests for _is_builtin_stdio method."""

    def test_is_builtin_stdio_context7(self, service):
        """Should return True for context7 (registered STDIO server)."""
        mock_registry = MagicMock()
        mock_definition = MagicMock()
        mock_definition.get_base_config.return_value = {"transport": "stdio"}
        mock_registry.get_by_name.return_value = mock_definition

        with patch("daemon.mcp.builtin_servers.get_registry", return_value=mock_registry):
            server = _make_server(name="context7", config={"transport": "stdio"})
            assert service._is_builtin_stdio(server) is True

    def test_is_builtin_stdio_webfetch(self, service):
        """Should return True for webfetch (registered STDIO server)."""
        mock_registry = MagicMock()
        mock_definition = MagicMock()
        mock_definition.get_base_config.return_value = {"transport": "stdio"}
        mock_registry.get_by_name.return_value = mock_definition

        with patch("daemon.mcp.builtin_servers.get_registry", return_value=mock_registry):
            server = _make_server(name="webfetch", config={"transport": "stdio"})
            assert service._is_builtin_stdio(server) is True

    def test_is_builtin_stdio_user_defined(self, service):
        """Should return False for user-defined servers not in registry."""
        mock_registry = MagicMock()
        mock_registry.get_by_name.return_value = None

        with patch("daemon.mcp.builtin_servers.get_registry", return_value=mock_registry):
            server = _make_server(name="my-custom-server", config={"transport": "stdio"})
            assert service._is_builtin_stdio(server) is False

    def test_is_builtin_stdio_sse_server(self, service):
        """Should return False for SSE servers."""
        mock_registry = MagicMock()
        mock_definition = MagicMock()
        mock_definition.get_base_config.return_value = {"transport": "sse"}
        mock_registry.get_by_name.return_value = mock_definition

        with patch("daemon.mcp.builtin_servers.get_registry", return_value=mock_registry):
            server = _make_server(name="context7", config={"transport": "sse"})
            assert service._is_builtin_stdio(server) is False


class TestPoolAwarePreload:
    """Tests for pool-aware preload functionality."""

    @pytest.fixture
    def mock_pool(self):
        """Create a mock warmup pool."""
        from daemon.mcp.warmup_pool import PooledConnection
        import time

        pool = AsyncMock()
        pool.acquire = AsyncMock(return_value=None)  # Default: pool empty
        return pool

    @pytest.fixture
    def mock_pooled_connection(self):
        """Create a mock PooledConnection."""
        from daemon.mcp.warmup_pool import PooledConnection
        import time

        return PooledConnection(
            session=AsyncMock(),
            stream_cm=MagicMock(),
            tools=[_make_tool(name="pooled_tool")],
            server_name="context7",
            created_at=time.monotonic(),
        )

    @pytest.mark.asyncio
    async def test_preload_uses_pool_when_available(self, service, manager, mock_pool, mock_pooled_connection):
        """Pool connection should be used when available."""
        mock_pool.acquire = AsyncMock(return_value=mock_pooled_connection)
        service.set_warmup_pool(mock_pool)

        server = _make_server(name="context7", config={"transport": "stdio"})
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.transfer_session = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.preload_mcp_tools("inst-1")

        # Should have acquired from pool and transferred
        mock_pool.acquire.assert_awaited_once_with("context7")
        mock_conn_mgr.transfer_session.assert_awaited_once()

        # Should have cached the tools from pool
        tools = service.get_mcp_tools("inst-1")
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_preload_falls_back_when_pool_empty(self, service, manager, mock_pool):
        """Cold-start fallback should occur when pool is empty."""
        mock_pool.acquire = AsyncMock(return_value=None)  # Pool empty
        service.set_warmup_pool(mock_pool)

        server = _make_server(name="context7", config={"transport": "stdio"})
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
            return_value=[_make_tool(name="cold_tool")]
        ), patch(
            "daemon.mcp.tool_adapter.adapt_mcp_tools",
            return_value=[_make_tool(name="cold_tool")]
        ):
            await service.preload_mcp_tools("inst-1")

        # Should fall back to cold start
        mock_conn_mgr.connect_instance.assert_awaited_once()
        tools = service.get_mcp_tools("inst-1")
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_preload_falls_back_when_pool_not_configured(self, service, manager):
        """Should work with pool=None (no pool configured)."""
        service.set_warmup_pool(None)  # No pool

        server = _make_server(name="custom-server", config={"transport": "stdio"})
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
            return_value=[_make_tool(name="tool")]
        ), patch(
            "daemon.mcp.tool_adapter.adapt_mcp_tools",
            return_value=[_make_tool(name="tool")]
        ):
            await service.preload_mcp_tools("inst-1")

        # Should still work via cold start
        mock_conn_mgr.connect_instance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_preload_falls_back_on_stale_pooled_connection(self, service, manager, mock_pool, mock_pooled_connection):
        """Stale pool connection should fall back to cold-start."""
        # Simulate stale connection (probe fails)
        mock_pooled_connection.session.send_ping = AsyncMock(side_effect=RuntimeError("Stale"))
        mock_pool.acquire = AsyncMock(return_value=mock_pooled_connection)
        service.set_warmup_pool(mock_pool)

        server = _make_server(name="context7", config={"transport": "stdio"})
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
            return_value=[_make_tool(name="fallback_tool")]
        ), patch(
            "daemon.mcp.tool_adapter.adapt_mcp_tools",
            return_value=[_make_tool(name="fallback_tool")]
        ):
            await service.preload_mcp_tools("inst-1")

        # Should fall back to cold start after detecting stale connection
        mock_conn_mgr.connect_instance.assert_awaited_once()

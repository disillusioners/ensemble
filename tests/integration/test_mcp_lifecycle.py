"""Integration tests for MCP lifecycle — spawn, restore, cleanup, resilience."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_tool(name: str = "echo", description: str = "Echo input"):
    """Create a mock LangChain tool with required attributes."""
    tool = MagicMock()
    tool.name = name
    tool.description = description

    def copy():
        copied = MagicMock()
        copied.name = tool.name
        copied.description = tool.description
        copied.args_schema = getattr(tool, "args_schema", None)
        return copied

    tool.copy = copy
    return tool


def _make_mock_server(name: str = "test-server", config: dict = None):
    """Create a mock MCP server model with required attributes."""
    server = MagicMock()
    server.name = name
    server.config = config or {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "test"],
    }
    server.is_active = True
    return server


def _make_harness():
    """Create a McpService wired to a mock manager, ready for patching."""
    from daemon.services.mcp_service import McpService

    manager = MagicMock()
    manager._mcp_server_repository = MagicMock()
    manager._mcp_service = McpService(manager=manager)
    return manager


# -------------------------------------------------------------------
# Spawn
# -------------------------------------------------------------------

class TestSpawnWithMcp:
    """Test that spawning with MCP servers injects tools."""

    @pytest.mark.asyncio
    async def test_spawn_with_mcp_server_injects_tools(self):
        """Servers are listed, connected, tools discovered and cached."""
        manager = _make_harness()
        server = _make_mock_server()
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        echo_tool = _make_mock_tool(name="echo", description="Echo input")

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session = MagicMock(return_value=MagicMock())

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[echo_tool],
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1
        # adapt_mcp_tools prefixes name with mcp_<server>_
        assert tools[0].name.startswith("mcp_test_server_echo")
        assert "[MCP:test-server]" in tools[0].description

    @pytest.mark.asyncio
    async def test_spawn_without_mcp_servers(self):
        """No active servers → empty tools list."""
        manager = _make_harness()
        manager._mcp_server_repository.list_mcp_servers.return_value = []

        await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert tools == []

    @pytest.mark.asyncio
    async def test_spawn_preload_failure_continues(self):
        """Preload error is caught; instance still usable with empty tools."""
        manager = _make_harness()
        server = _make_mock_server()
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert tools == []


# -------------------------------------------------------------------
# Restore
# -------------------------------------------------------------------

class TestRestoreWithMcp:
    """Test that restore correctly loads MCP tools."""

    @pytest.mark.asyncio
    async def test_restore_loads_mcp_tools(self):
        """Restore (preload) populates the cache correctly."""
        manager = _make_harness()
        server = _make_mock_server()
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        echo_tool = _make_mock_tool(name="echo", description="Echo input")

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session = MagicMock(return_value=MagicMock())

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[echo_tool],
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_restore_after_terminate_has_mcp_tools(self):
        """After terminate (close_connections), preload again — tools available."""
        manager = _make_harness()
        server = _make_mock_server()
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        echo_tool = _make_mock_tool(name="echo", description="Echo input")

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session = MagicMock(return_value=MagicMock())
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[echo_tool],
        ):
            # Initial preload
            await manager._mcp_service.preload_mcp_tools("inst-1")
            assert len(manager._mcp_service.get_mcp_tools("inst-1")) == 1

            # Terminate (close connections + clear cache)
            await manager._mcp_service.close_connections("inst-1")
            assert manager._mcp_service.get_mcp_tools("inst-1") == []

            # Restore (preload again)
            await manager._mcp_service.preload_mcp_tools("inst-1")
            assert len(manager._mcp_service.get_mcp_tools("inst-1")) == 1


# -------------------------------------------------------------------
# Cleanup
# -------------------------------------------------------------------

class TestCleanup:
    """Test MCP connection cleanup."""

    @pytest.mark.asyncio
    async def test_terminate_closes_mcp_connections(self):
        """close_connections calls connection_manager.close_instance."""
        manager = _make_harness()

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await manager._mcp_service.close_connections("inst-1")

        mock_conn_mgr.close_instance.assert_awaited_once_with("inst-1")

    @pytest.mark.asyncio
    async def test_shutdown_closes_all(self):
        """close_all_connections clears cache and calls close_all."""
        manager = _make_harness()
        manager._mcp_service._tools_cache["inst-1"] = [MagicMock()]
        manager._mcp_service._tools_cache["inst-2"] = [MagicMock()]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_all = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await manager._mcp_service.close_all_connections()

        assert manager._mcp_service._tools_cache == {}
        mock_conn_mgr.close_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self):
        """Calling close_connections twice does not crash."""
        manager = _make_harness()

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await manager._mcp_service.close_connections("inst-1")
            await manager._mcp_service.close_connections("inst-1")

        assert mock_conn_mgr.close_instance.await_count == 2


# -------------------------------------------------------------------
# Resilience
# -------------------------------------------------------------------

class TestResilience:
    """Test failure scenarios."""

    @pytest.mark.asyncio
    async def test_unreachable_server_doesnt_block(self):
        """Session unavailable → empty tools, no crash."""
        manager = _make_harness()
        server = _make_mock_server()
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session = MagicMock(return_value=None)  # Not connected

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        assert manager._mcp_service.get_mcp_tools("inst-1") == []

    @pytest.mark.asyncio
    async def test_mixed_connection_failures_partial_tools(self):
        """2 servers fail, 1 succeeds → partial tools available."""
        manager = _make_harness()
        good_tool = _make_mock_tool(name="good_tool", description="Good tool")
        servers = [
            _make_mock_server("bad-1"),
            _make_mock_server("good"),
            _make_mock_server("bad-2"),
        ]
        manager._mcp_server_repository.list_mcp_servers.return_value = servers

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        def get_session(inst_id, server_name):
            if server_name == "good":
                return MagicMock()
            return None

        mock_conn_mgr.get_session = MagicMock(side_effect=get_session)

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[good_tool],
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_all_servers_down_no_crash(self):
        """All sessions unavailable → empty tools, no crash."""
        manager = _make_harness()
        servers = [_make_mock_server("s1"), _make_mock_server("s2")]
        manager._mcp_server_repository.list_mcp_servers.return_value = servers

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session = MagicMock(return_value=None)

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        assert manager._mcp_service.get_mcp_tools("inst-1") == []

    @pytest.mark.asyncio
    async def test_invalid_config_graceful_error(self):
        """Server with invalid config should not crash preload."""
        manager = _make_harness()
        bad_server = _make_mock_server("bad")
        bad_server.config = {"invalid": True}  # Missing required transport field

        manager._mcp_server_repository.list_mcp_servers.return_value = [bad_server]

        # Should not raise — errors are caught and logged
        await manager._mcp_service.preload_mcp_tools("inst-1")

        assert manager._mcp_service.get_mcp_tools("inst-1") == []

    @pytest.mark.asyncio
    async def test_tool_names_have_correct_prefix(self):
        """Adapted tools have mcp_<server> prefix and [MCP:server] in description."""
        manager = _make_harness()
        server = _make_mock_server("github")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        create_tool = _make_mock_tool(
            name="create_issue", description="Create issue"
        )

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session = MagicMock(return_value=MagicMock())

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[create_tool],
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1
        assert tools[0].name == "mcp_github_create_issue"
        assert "[MCP:github]" in tools[0].description

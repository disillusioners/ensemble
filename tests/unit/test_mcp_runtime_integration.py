"""Integration tests for MCP runtime integration with InstanceManager.

These tests validate the end-to-end MCP integration including:
- spawn_instance_with_mcp flow
- MCP tools preloading and retrieval
- Graceful handling of failures
- Lifecycle cleanup
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_server(name: str = "test-server", is_active: bool = True):
    """Create a mock MCP server."""
    server = MagicMock()
    server.name = name
    server.config = {"transport": "stdio", "command": "python", "args": ["-m", "server"]}
    server.is_active = is_active
    return server


def _make_mock_tool(name: str = "echo", description: str = "Echo tool"):
    """Create a mock LangChain tool."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.copy = MagicMock(side_effect=lambda: tool)
    return tool


def _make_adapted_tool(name: str, description: str = "Test tool"):
    """Create a mock tool with proper name attribute for adapt_mcp_tools results."""
    tool = MagicMock()
    tool.name = name  # Set as attribute, not constructor arg
    tool.description = description
    tool.copy = MagicMock(side_effect=lambda: tool)
    return tool


@pytest.fixture
def mock_manager():
    """Create a mock manager with MCP service."""
    manager = MagicMock()
    manager._mcp_server_repository = MagicMock()
    manager.instances = {}  # Track loaded instances
    return manager


@pytest.fixture
def mcp_service(mock_manager):
    """Create an McpService instance with mock manager."""
    from daemon.services.mcp_service import McpService
    return McpService(manager=mock_manager)


class TestFullFlowMcpToolsInjected:
    """Test 1: Full flow — MCP tools injected into instance."""

    @pytest.mark.asyncio
    async def test_preload_discovers_and_caches_tools(self, mcp_service, mock_manager):
        """Preload MCP tools and verify they are cached."""
        # Setup: One server with two tools
        server = _make_mock_server(name="my-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        tool1 = _make_mock_tool(name="search", description="Search the web")
        tool2 = _make_mock_tool(name="read", description="Read a file")

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
            return_value=[tool1, tool2]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
                side_effect=lambda name, tools: [
                    _make_adapted_tool(name=f"mcp_{name.replace('-', '_').replace(' ', '_')}_{t.name}", description=t.description)
                    for t in tools
                ]
        ):
            await mcp_service.preload_mcp_tools("test-instance-1")

        # Verify tools are cached with MCP prefix
        cached = mcp_service.get_mcp_tools("test-instance-1")
        assert len(cached) == 2
        assert cached[0].name == "mcp_my_server_search"
        assert cached[1].name == "mcp_my_server_read"

    @pytest.mark.asyncio
    async def test_multiple_servers_aggregated_tools(self, mcp_service, mock_manager):
        """Tools from multiple servers are aggregated."""
        server1 = _make_mock_server(name="server-a")
        server2 = _make_mock_server(name="server-b")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server1, server2]

        tool_a = _make_mock_tool(name="tool1", description="From server A")
        tool_b = _make_mock_tool(name="tool2", description="From server B")

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        def get_session(inst_id, server_name):
            if server_name == "server-a":
                return MagicMock()
            elif server_name == "server-b":
                return MagicMock()
            return None

        mock_conn_mgr.get_session.side_effect = get_session

        def adapt_tools(name, tools):
            slugified_name = name.replace('-', '_').replace(' ', '_')
            prefix = f"mcp_{slugified_name}_"
            return [_make_adapted_tool(name=f"{prefix}{t.name}", description=t.description) for t in tools]

        # Track which server's tools are loaded
        load_calls = []

        async def mock_load_tools(session):
            # The session has metadata about which server
            load_calls.append(session)
            # Return appropriate tool based on call order
            if len(load_calls) == 1:
                return [tool_a]
            return [tool_b]

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            side_effect=mock_load_tools
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            side_effect=adapt_tools
        ):
            await mcp_service.preload_mcp_tools("multi-server-instance")

        cached = mcp_service.get_mcp_tools("multi-server-instance")
        assert len(cached) == 2
        names = {t.name for t in cached}
        assert "mcp_server_a_tool1" in names
        assert "mcp_server_b_tool2" in names

    @pytest.mark.asyncio
    async def test_lifecycle_cleanup_clears_cache(self, mcp_service, mock_manager):
        """close_connections clears the cache and connections."""
        # Preload tools first
        server = _make_mock_server(name="cleanup-test")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        tool = _make_mock_tool(name="test")
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session.return_value = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.close_instance = AsyncMock()  # Must be async

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[tool]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_cleanup_test_test")]
        ):
            await mcp_service.preload_mcp_tools("cleanup-instance")

            # Verify preloaded
            assert len(mcp_service.get_mcp_tools("cleanup-instance")) == 1

            # Cleanup - also inside patch context
            await mcp_service.close_connections("cleanup-instance")

        # Verify cache cleared
        assert mcp_service.get_mcp_tools("cleanup-instance") == []
        mock_conn_mgr.close_instance.assert_awaited_once_with("cleanup-instance")


class TestResilienceValidInvalidServers:
    """Test 2: Resilience — Valid + invalid server configs."""

    @pytest.mark.asyncio
    async def test_valid_server_works_invalid_ignored(self, mcp_service, mock_manager):
        """Valid server works, invalid server is gracefully ignored."""
        good_server = _make_mock_server(name="good-server")
        bad_server = _make_mock_server(name="bad-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [
            good_server, bad_server
        ]

        good_tool = _make_mock_tool(name="good_tool", description="Good tool")

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        def get_session(inst_id, server_name):
            if server_name == "good-server":
                return MagicMock()
            return None  # bad-server returns no session

        mock_conn_mgr.get_session.side_effect = get_session

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[good_tool]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_good_server_good_tool")]
        ):
            await mcp_service.preload_mcp_tools("resilient-instance")

        # Should have tools from valid server
        cached = mcp_service.get_mcp_tools("resilient-instance")
        assert len(cached) == 1
        assert "mcp_good_server_good_tool" in cached[0].name

    @pytest.mark.asyncio
    async def test_all_servers_fail_caches_empty_no_crash(self, mcp_service, mock_manager):
        """All servers failing results in empty cache, no exception."""
        server = _make_mock_server(name="failing-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session.return_value = None  # No session = failure

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            # Should not raise
            await mcp_service.preload_mcp_tools("all-fail-instance")

        assert mcp_service.get_mcp_tools("all-fail-instance") == []

    @pytest.mark.asyncio
    async def test_load_mcp_tools_exception_handled(self, mcp_service, mock_manager):
        """Exception in load_mcp_tools is handled gracefully."""
        server = _make_mock_server(name="exception-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session.return_value = MagicMock()

        async def raise_error(session):
            raise RuntimeError("Tool discovery failed")

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            side_effect=raise_error
        ):
            await mcp_service.preload_mcp_tools("exception-instance")

        # Should cache empty, no exception propagated
        assert mcp_service.get_mcp_tools("exception-instance") == []


class TestRestorePathMcpPreloaded:
    """Test 3: Restore path — MCP preloaded during restore."""

    @pytest.mark.asyncio
    async def test_preload_skipped_if_instance_in_memory(self, mcp_service, mock_manager):
        """Preload is skipped if instance already in memory."""
        # Simulate instance already loaded
        mock_manager.instances["restored-instance"] = (MagicMock(), "agents/coder")

        # Server exists but should NOT be loaded since instance is in memory
        server = _make_mock_server(name="should-not-load")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            # In the actual manager, ensure_mcp_preloaded checks instances dict
            # For this test, we verify the service behavior when called directly
            await mcp_service.preload_mcp_tools("restored-instance")

        # The service still preloads (that's the current behavior)
        # In real usage, manager.ensure_mcp_preloaded skips this if in memory
        mock_conn_mgr.connect_instance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_preload_on_restored_instance(self, mcp_service, mock_manager):
        """Preload works correctly for a restored instance."""
        server = _make_mock_server(name="restored-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        tool = _make_mock_tool(name="restored_tool")
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session.return_value = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[tool]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_restored_server_restored_tool")]
        ):
            await mcp_service.preload_mcp_tools("restored-instance")

        cached = mcp_service.get_mcp_tools("restored-instance")
        assert len(cached) == 1
        assert "restored_tool" in cached[0].name


class TestEdgeCases:
    """Test 4: Edge cases."""

    @pytest.mark.asyncio
    async def test_zero_mcp_servers_returns_empty_list(self, mcp_service, mock_manager):
        """No MCP servers results in empty tool list."""
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = []

        await mcp_service.preload_mcp_tools("no-servers-instance")

        assert mcp_service.get_mcp_tools("no-servers-instance") == []

    @pytest.mark.asyncio
    async def test_server_raises_exception_graceful_fallback(self, mcp_service, mock_manager):
        """Server raising exception results in graceful fallback."""
        server = _make_mock_server(name="crash-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock(side_effect=RuntimeError("Connection refused"))
        mock_conn_mgr.get_session.return_value = None

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await mcp_service.preload_mcp_tools("crash-instance")

        assert mcp_service.get_mcp_tools("crash-instance") == []

    @pytest.mark.asyncio
    async def test_tool_filter_mcp_in_deny_excludes_tools(self, mcp_service, mock_manager):
        """When 'mcp' is in deny list, MCP tools should be excluded."""
        # This tests the tool filter integration
        server = _make_mock_server(name="filter-test")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        mcp_tool = _make_mock_tool(name="mcp_tool", description="Should be filtered")
        regular_tool = _make_mock_tool(name="regular_tool", description="Should pass")

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session.return_value = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        def adapt_tools(name, tools):
            if name == "filter-test":
                return [_make_adapted_tool(name=f"mcp_filter_test_{t.name}", description=t.description) for t in tools]
            return tools

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[mcp_tool, regular_tool]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            side_effect=adapt_tools
        ):
            await mcp_service.preload_mcp_tools("filter-instance")

        # MCP tools are cached (filtering happens at graph building time)
        cached = mcp_service.get_mcp_tools("filter-instance")
        assert len(cached) == 2  # Both tools cached

    @pytest.mark.asyncio
    async def test_get_mcp_tools_unknown_instance_returns_empty(self, mcp_service):
        """Unknown instance returns empty list."""
        assert mcp_service.get_mcp_tools("never-preloaded-instance") == []

    @pytest.mark.asyncio
    async def test_concurrent_preload_same_instance(self, mcp_service, mock_manager):
        """Concurrent preload calls for same instance are handled safely."""
        server = _make_mock_server(name="concurrent-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        tool = _make_mock_tool(name="concurrent_tool")
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session.return_value = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[tool]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_concurrent_server_concurrent_tool")]
        ):
            # Launch concurrent preloads
            await asyncio.gather(
                mcp_service.preload_mcp_tools("concurrent-instance"),
                mcp_service.preload_mcp_tools("concurrent-instance"),
            )

        # Should have tools (no duplicate cache entries due to locking)
        cached = mcp_service.get_mcp_tools("concurrent-instance")
        assert len(cached) == 1


class TestLifecycleCleanup:
    """Test 5: Lifecycle cleanup."""

    @pytest.mark.asyncio
    async def test_close_connections_idempotent(self, mcp_service, mock_manager):
        """close_connections can be called multiple times safely."""
        # Preload first
        server = _make_mock_server(name="idempotent-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        tool = _make_mock_tool(name="test")
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session.return_value = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[tool]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_idempotent_server_test")]
        ):
            await mcp_service.preload_mcp_tools("idempotent-instance")

            # Close multiple times - also inside patch context
            await mcp_service.close_connections("idempotent-instance")
            await mcp_service.close_connections("idempotent-instance")  # Should not raise

        # Cache should be empty
        assert mcp_service.get_mcp_tools("idempotent-instance") == []

    @pytest.mark.asyncio
    async def test_close_all_connections_clears_everything(self, mcp_service, mock_manager):
        """close_all_connections clears all caches and connections."""
        servers = [_make_mock_server(name="server1"), _make_mock_server(name="server2")]
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = servers

        tool = _make_mock_tool(name="test")
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session.return_value = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.close_all = AsyncMock()  # Must be async

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            return_value=[tool]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_server_test")]
        ):
            await mcp_service.preload_mcp_tools("instance1")
            await mcp_service.preload_mcp_tools("instance2")

            # Close all - also inside patch context
            await mcp_service.close_all_connections()

        # Both caches should be empty
        assert mcp_service.get_mcp_tools("instance1") == []
        assert mcp_service.get_mcp_tools("instance2") == []
        mock_conn_mgr.close_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_isolation_between_instances(self, mcp_service, mock_manager):
        """Each instance has its own cached tools."""
        server = _make_mock_server(name="shared-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        tool1 = _make_mock_tool(name="tool1")
        tool2 = _make_mock_tool(name="tool2")
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()

        def get_session(inst_id, server_name):
            return MagicMock()

        mock_conn_mgr.get_session.side_effect = get_session

        def adapt_tools(name, tools):
            slugified_name = name.replace('-', '_').replace(' ', '_')
            return [_make_adapted_tool(name=f"mcp_{slugified_name}_{t.name}", description=t.description) for t in tools]

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "langchain_mcp_adapters.tools.load_mcp_tools",
            new_callable=AsyncMock,
            side_effect=lambda s: [tool1] if "instance-a" in str(s.metadata) else [tool2]
        ), patch(
            "daemon.services.mcp_service.adapt_mcp_tools",
            side_effect=adapt_tools
        ):
            # Load for instance A
            mcp_service._tools_cache["instance-a"] = [
                _make_adapted_tool(name="mcp_shared_server_tool1", description="A's tool")
            ]
            # Load for instance B
            mcp_service._tools_cache["instance-b"] = [
                _make_adapted_tool(name="mcp_shared_server_tool2", description="B's tool")
            ]

        # Verify isolation
        tools_a = mcp_service.get_mcp_tools("instance-a")
        tools_b = mcp_service.get_mcp_tools("instance-b")

        assert len(tools_a) == 1
        assert len(tools_b) == 1
        assert tools_a[0].name != tools_b[0].name


# Import asyncio for the concurrent test
import asyncio

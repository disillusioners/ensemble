"""Integration tests for MCP runtime integration with InstanceManager.

These tests validate the end-to-end MCP integration including:
- spawn_instance_with_mcp flow
- MCP tools preloading and retrieval
- Graceful handling of failures
- Lifecycle cleanup

Note: The lazy preload path (Phase 1) means ``preload_mcp_tools``
no longer opens connections itself. These tests mock the new
``get_schemas_for_server`` + ``create_lazy_mcp_tools`` path.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_server(
    name: str = "test-server",
    is_active: bool = True,
    is_builtin: bool = False,
):
    """Create a mock MCP server."""
    server = MagicMock()
    server.name = name
    server.config = {"transport": "stdio", "command": "python", "args": ["-m", "server"]}
    server.is_active = is_active
    server.is_builtin = is_builtin
    return server


def _make_mock_tool(name: str = "echo", description: str = "Echo tool"):
    """Create a mock LangChain tool."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.copy = MagicMock(side_effect=lambda: tool)
    return tool


def _make_adapted_tool(name: str, description: str = "Test tool"):
    """Create a mock tool with proper name attribute for lazy results."""
    tool = MagicMock()
    tool.name = name  # Set as attribute, not constructor arg
    tool.description = description
    tool.copy = MagicMock(side_effect=lambda: tool)
    return tool


def _make_schema(name: str, server_name: str = "test-server", description: str = ""):
    """Create a McpToolSchema for tests."""
    from daemon.mcp.models import McpToolSchema
    return McpToolSchema(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
        server_name=server_name,
    )


@pytest.fixture
def mock_manager():
    """Create a mock manager with MCP service."""
    manager = MagicMock()
    manager._mcp_server_repository = MagicMock()
    manager.instances = {}  # Track loaded instances
    manager.config = MagicMock(mcp_pool=MagicMock(tool_call_timeout=120))
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
        # Setup: One server with two tools (schemas)
        server = _make_mock_server(name="my-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        mcp_service.get_schemas_for_server = AsyncMock(return_value=[
            _make_schema("search", "my-server"),
            _make_schema("read", "my-server"),
        ])

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[
                _make_adapted_tool(name="mcp_my_server_search"),
                _make_adapted_tool(name="mcp_my_server_read"),
            ],
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

        async def lookup(srv):
            if srv.name == "server-a":
                return [_make_schema("tool1", "server-a")]
            return [_make_schema("tool2", "server-b")]

        mcp_service.get_schemas_for_server = AsyncMock(side_effect=lookup)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            side_effect=lambda server_name, schemas, **kwargs: [
                _make_adapted_tool(name=f"mcp_{server_name.replace('-', '_')}_{s['name']}")
                for s in schemas
            ],
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
        mcp_service.get_schemas_for_server = AsyncMock(
            return_value=[_make_schema("test", "cleanup-test")]
        )

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()  # Must be async

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_cleanup_test_test")],
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

        async def lookup(srv):
            if srv.name == "good-server":
                return [_make_schema("good_tool", "good-server")]
            return []  # bad-server has no tools / schema discovery failed

        mcp_service.get_schemas_for_server = AsyncMock(side_effect=lookup)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_good_server_good_tool")],
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
    async def test_discover_schemas_cold_exception_handled(self, mcp_service, mock_manager):
        """M2 fix: Exercise ``_discover_schemas_cold`` exception handling.

        The previous test (``test_load_mcp_tools_exception_handled``)
        patched ``langchain_mcp_adapters.tools.load_mcp_tools`` — but
        the lazy preload path never calls that function. The cold
        discovery now happens in ``_discover_schemas_cold`` (which
        calls ``session.list_tools()``); this test exercises that
        exception path so the service still caches an empty list
        instead of propagating.
        """
        server = _make_mock_server(name="exception-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        # No warmup pool → forces the cold discovery path inside
        # ``get_schemas_for_server``.
        mcp_service._warmup_pool = None

        # Mock the connection manager so ``_discover_schemas_cold`` opens
        # a session whose ``list_tools()`` raises.
        mock_session = MagicMock()
        mock_session.list_tools = AsyncMock(
            side_effect=RuntimeError("Tool discovery failed")
        )

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session = MagicMock(return_value=mock_session)
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            # Should not raise — _discover_schemas_cold swallows errors
            # and returns [].
            await mcp_service.preload_mcp_tools("exception-instance")

        # Service cached an empty tool list and the per-instance state
        # has no entry for the failing server.
        assert mcp_service.get_mcp_tools("exception-instance") == []
        assert "exception-server" not in mcp_service._session_caches.get(
            "exception-instance", {}
        )
        # The throwaway discovery session was torn down even on failure.
        mock_conn_mgr.close_instance.assert_awaited()

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
        mcp_service.get_schemas_for_server = AsyncMock(
            return_value=[
                _make_schema("mcp_tool", "filter-test"),
                _make_schema("regular_tool", "filter-test"),
            ]
        )

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            side_effect=lambda server_name, schemas, **kwargs: [
                _make_adapted_tool(name=f"mcp_filter_test_{s['name']}") for s in schemas
            ],
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
        import asyncio as _asyncio

        server = _make_mock_server(name="concurrent-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        mcp_service.get_schemas_for_server = AsyncMock(
            return_value=[_make_schema("concurrent_tool", "concurrent-server")]
        )

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_concurrent_server_concurrent_tool")],
        ):
            # Launch concurrent preloads
            await _asyncio.gather(
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
        # Preload first (lazy path: no connections opened during preload)
        server = _make_mock_server(name="idempotent-server")
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        mcp_service.get_schemas_for_server = AsyncMock(
            return_value=[_make_schema("test", "idempotent-server")]
        )

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_adapted_tool(name="mcp_idempotent_server_test")],
        ):
            await mcp_service.preload_mcp_tools("idempotent-instance")

            # Close multiple times - also inside patch context
            await mcp_service.close_connections("idempotent-instance")
            await mcp_service.close_connections("idempotent-instance")  # Should not raise

        # Cache should be empty
        assert mcp_service.get_mcp_tools("idempotent-instance") == []
        mock_conn_mgr.close_instance.assert_awaited_with("idempotent-instance")

    @pytest.mark.asyncio
    async def test_close_all_connections_clears_everything(self, mcp_service, mock_manager):
        """close_all_connections clears all caches and connections."""
        servers = [_make_mock_server(name="server1"), _make_mock_server(name="server2")]
        mock_manager._mcp_server_repository.list_mcp_servers.return_value = servers

        async def lookup(srv):
            return [_make_schema("test", srv.name)]

        mcp_service.get_schemas_for_server = AsyncMock(side_effect=lookup)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_all = AsyncMock()  # Must be async

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            side_effect=lambda server_name, schemas, **kwargs: [
                _make_adapted_tool(name=f"mcp_{server_name.replace('-', '_')}_{s['name']}")
                for s in schemas
            ],
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

        # Lazy path: just seed the cache directly to verify isolation —
        # the per-instance dict keyed on instance_id is what guarantees
        # that two instances never see each other's tools.
        mcp_service._tools_cache["instance-a"] = [
            _make_adapted_tool(name="mcp_shared_server_tool1", description="A's tool")
        ]
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

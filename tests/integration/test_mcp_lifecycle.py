"""Integration tests for MCP lifecycle — spawn, restore, cleanup, resilience.

These tests follow the lazy init path (Phase 1) used by the new
``McpService``:
- ``preload_mcp_tools`` does NOT call ``load_mcp_tools`` or open
  connections — it only reads (or one-time discovers) tool schemas and
  builds ``StructuredTool`` wrappers.
- The connection manager is only consulted by the test if it forces the
  cold path (e.g. failure scenarios) — most assertions just check that
  the schema list is passed through and that ``create_lazy_mcp_tools``
  produced a tool per schema with the right prefix.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_schema(name: str, server_name: str = "test-server", description: str = ""):
    """Create a ``McpToolSchema`` for the lazy-path tests."""
    from daemon.mcp.models import McpToolSchema

    return McpToolSchema(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
        server_name=server_name,
    )


def _make_lazy_tool(name: str, description: str = "Test tool"):
    """Create a mock lazy ``StructuredTool`` for ``create_lazy_mcp_tools``."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    return tool


def _make_mock_server(
    name: str = "test-server",
    config: dict = None,
    is_builtin: bool = False,
):
    """Create a mock MCP server model with required attributes."""
    server = MagicMock()
    server.name = name
    server.config = config or {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "test"],
    }
    server.is_active = True
    server.is_builtin = is_builtin
    return server


def _make_harness():
    """Create a ``McpService`` wired to a mock manager, ready for patching."""
    from daemon.services.mcp_service import McpService

    manager = MagicMock()
    manager._mcp_server_repository = MagicMock()
    manager.config = MagicMock(mcp_pool=MagicMock(tool_call_timeout=120))
    manager._mcp_service = McpService(manager=manager)
    return manager


# -------------------------------------------------------------------
# Spawn
# -------------------------------------------------------------------

class TestSpawnWithMcp:
    """Test that spawning with MCP servers injects tools."""

    @pytest.mark.asyncio
    async def test_spawn_with_mcp_server_injects_tools(self):
        """Lazy path: schemas are listed and ``create_lazy_mcp_tools`` builds wrappers."""
        manager = _make_harness()
        server = _make_mock_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        schemas = [_make_schema("echo", "test-server", description="Echo input")]
        manager._mcp_service.get_schemas_for_server = AsyncMock(return_value=schemas)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_lazy_tool(name="mcp_test_server_echo")],
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1
        # Lazy path produces the prefixed name exactly as the factory returned it
        # — the service no longer does the prefixing itself.
        assert tools[0].name == "mcp_test_server_echo"
        # The schema was passed to the factory, so descriptions flow through
        # the factory call rather than the service.
        assert tools[0].description == "Test tool"

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
        """Preload error from get_schemas_for_server is caught per-server.

        The lazy preload never opens a connection itself, so a
        ``connect_instance`` failure isn't the right failure mode here —
        we exercise a schema lookup exception, which the service logs
        and skips.
        """
        manager = _make_harness()
        server = _make_mock_server(name="failing-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        async def raise_lookup(srv):
            raise RuntimeError("Schema discovery failed")

        manager._mcp_service.get_schemas_for_server = AsyncMock(side_effect=raise_lookup)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools"
        ) as mock_create:
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert tools == []
        # No tools built for the failing server
        mock_create.assert_not_called()


# -------------------------------------------------------------------
# Restore
# -------------------------------------------------------------------

class TestRestoreWithMcp:
    """Test that restore correctly loads MCP tools."""

    @pytest.mark.asyncio
    async def test_restore_loads_mcp_tools(self):
        """Restore (preload) populates the cache correctly via the lazy path."""
        manager = _make_harness()
        server = _make_mock_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        schemas = [_make_schema("echo", "test-server", description="Echo input")]
        manager._mcp_service.get_schemas_for_server = AsyncMock(return_value=schemas)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_lazy_tool(name="mcp_test_server_echo")],
        ):
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1
        assert tools[0].name == "mcp_test_server_echo"

    @pytest.mark.asyncio
    async def test_restore_after_terminate_has_mcp_tools(self):
        """After terminate (close_connections), preload again — tools available."""
        manager = _make_harness()
        server = _make_mock_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        schemas = [_make_schema("echo", "test-server", description="Echo input")]
        manager._mcp_service.get_schemas_for_server = AsyncMock(return_value=schemas)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_lazy_tool(name="mcp_test_server_echo")],
        ), patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
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
        """Schema discovery returns empty list → empty tools, no crash."""
        manager = _make_harness()
        server = _make_mock_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        # get_schemas_for_server returning [] simulates a server whose
        # cold discovery failed (logged + empty list per
        # ``_discover_schemas_cold``).
        manager._mcp_service.get_schemas_for_server = AsyncMock(return_value=[])

        await manager._mcp_service.preload_mcp_tools("inst-1")

        assert manager._mcp_service.get_mcp_tools("inst-1") == []

    @pytest.mark.asyncio
    async def test_mixed_connection_failures_partial_tools(self):
        """2 servers fail, 1 succeeds → partial tools available.

        In the lazy path, "failure" means ``get_schemas_for_server``
        returns an empty list for the bad servers. The good server
        returns its schemas, and only those produce lazy tools.
        """
        manager = _make_harness()
        servers = [
            _make_mock_server("bad-1"),
            _make_mock_server("good"),
            _make_mock_server("bad-2"),
        ]
        manager._mcp_server_repository.list_mcp_servers.return_value = servers

        async def lookup(srv):
            if srv.name == "good":
                return [_make_schema("good_tool", "good", description="Good tool")]
            return []  # bad-1, bad-2 fail schema discovery

        manager._mcp_service.get_schemas_for_server = AsyncMock(side_effect=lookup)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_lazy_tool(name="mcp_good_good_tool")],
        ) as mock_create:
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1
        # Only the "good" server triggered a factory call
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_servers_down_no_crash(self):
        """All schema lookups fail → empty tools, no crash."""
        manager = _make_harness()
        servers = [_make_mock_server("s1"), _make_mock_server("s2")]
        manager._mcp_server_repository.list_mcp_servers.return_value = servers

        manager._mcp_service.get_schemas_for_server = AsyncMock(return_value=[])

        await manager._mcp_service.preload_mcp_tools("inst-1")

        assert manager._mcp_service.get_mcp_tools("inst-1") == []

    @pytest.mark.asyncio
    async def test_invalid_config_graceful_error(self):
        """Server with invalid config should not crash preload.

        In the lazy path, this manifests as ``get_schemas_for_server``
        raising — the service catches the per-server exception and
        continues with the rest (or no tools if it's the only server).
        """
        manager = _make_harness()
        bad_server = _make_mock_server("bad")
        bad_server.config = {"invalid": True}  # Missing required transport field
        manager._mcp_server_repository.list_mcp_servers.return_value = [bad_server]

        async def raise_lookup(srv):
            raise RuntimeError("Invalid config")

        manager._mcp_service.get_schemas_for_server = AsyncMock(side_effect=raise_lookup)

        # Should not raise — errors are caught and logged
        await manager._mcp_service.preload_mcp_tools("inst-1")

        assert manager._mcp_service.get_mcp_tools("inst-1") == []

    @pytest.mark.asyncio
    async def test_tool_names_have_correct_prefix(self):
        """Lazy factory is called with schemas; resulting tools have the prefix.

        The factory (``create_lazy_mcp_tools``) is responsible for
        prefixing names in the lazy path. Here we verify the service
        passes the right inputs to the factory and that the factory's
        output is what ends up in the cache.
        """
        manager = _make_harness()
        server = _make_mock_server("github")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]

        schemas = [_make_schema("create_issue", "github", description="Create issue")]
        manager._mcp_service.get_schemas_for_server = AsyncMock(return_value=schemas)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_lazy_tool(name="mcp_github_create_issue")],
        ) as mock_create:
            await manager._mcp_service.preload_mcp_tools("inst-1")

        tools = manager._mcp_service.get_mcp_tools("inst-1")
        assert len(tools) == 1
        assert tools[0].name == "mcp_github_create_issue"

        # The factory was called exactly once with the right server
        # name and the un-prefixed schema list (the factory does the
        # prefixing internally).
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["server_name"] == "github"
        # The service converts McpToolSchema → dicts before calling the
        # factory, so the schema payload should carry the original
        # (un-prefixed) name.
        assert call_kwargs["schemas"][0]["name"] == "create_issue"

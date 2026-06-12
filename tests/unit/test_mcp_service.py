"""Unit tests for MCP service — lazy preload + session provider."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.tools import ToolException

from daemon.mcp.models import McpToolSchema
from daemon.services.mcp_service import McpService, _McpSessionProviderImpl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(
    name: str = "test-server",
    config: dict = None,
    is_active: bool = True,
    is_builtin: bool = False,
):
    """Create a mock MCP server."""
    server = MagicMock()
    server.name = name
    server.config = config or {"transport": "stdio", "command": "python"}
    server.is_active = is_active
    server.is_builtin = is_builtin
    return server


def _make_tool(name: str = "echo", description: str = "Echo tool"):
    """Create a mock LangChain tool."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.copy = MagicMock(return_value=tool)
    return tool


def _make_schema(
    name: str,
    server_name: str = "test-server",
    description: str = "",
    input_schema: dict = None,
):
    """Create a McpToolSchema dataclass for tests."""
    return McpToolSchema(
        name=name,
        description=description,
        input_schema=input_schema or {"type": "object", "properties": {}},
        server_name=server_name,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    """Mock manager with MCP repository and mcp_pool config."""
    mgr = MagicMock()
    mgr._mcp_server_repository = MagicMock()
    mgr.config = MagicMock(mcp_pool=MagicMock(tool_call_timeout=120))
    return mgr


@pytest.fixture
def service(manager):
    """McpService instance with mock manager."""
    return McpService(manager=manager)


# ---------------------------------------------------------------------------
# TestPreloadMcpTools — lazy preload (no connections during preload)
# ---------------------------------------------------------------------------

class TestPreloadMcpTools:
    """Tests for the lazy preload_mcp_tools method."""

    @pytest.mark.asyncio
    async def test_caches_lazy_tools_from_servers(self, service, manager):
        """Preload builds lazy tool wrappers from active servers' schemas."""
        schemas = [
            _make_schema("echo", "test-server"),
            _make_schema("ping", "test-server"),
        ]
        server = _make_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        service.get_schemas_for_server = AsyncMock(return_value=schemas)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[
                _make_tool(name="mcp_test_server_echo"),
                _make_tool(name="mcp_test_server_ping"),
            ],
        ):
            await service.preload_mcp_tools("inst-1")

        cached = service.get_mcp_tools("inst-1")
        assert len(cached) == 2

    @pytest.mark.asyncio
    async def test_empty_servers_caches_empty(self, service, manager):
        """No active servers results in empty cache and empty session state."""
        manager._mcp_server_repository.list_mcp_servers.return_value = []

        await service.preload_mcp_tools("inst-1")

        assert service.get_mcp_tools("inst-1") == []
        assert service._session_caches["inst-1"] == {}
        manager._mcp_server_repository.list_mcp_servers.assert_called_once_with(is_active=True)

    @pytest.mark.asyncio
    async def test_session_caches_populated_per_server(self, service, manager):
        """_session_caches[instance_id] has one entry per server with cache+lock."""
        schemas = [_make_schema("t1", "test-server")]
        server = _make_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        service.get_schemas_for_server = AsyncMock(return_value=schemas)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_tool(name="mcp_test_server_t1")],
        ):
            await service.preload_mcp_tools("inst-1")

        session_state = service._session_caches["inst-1"]
        assert "test-server" in session_state
        entry = session_state["test-server"]
        assert entry["cache"] == {}
        assert isinstance(entry["lock"], asyncio.Lock)

    @pytest.mark.asyncio
    async def test_session_caches_one_entry_per_server(self, service, manager):
        """With multiple servers, _session_caches has one entry per server."""
        s1, s2 = _make_server(name="s1"), _make_server(name="s2")
        manager._mcp_server_repository.list_mcp_servers.return_value = [s1, s2]
        service.get_schemas_for_server = AsyncMock(
            side_effect=lambda srv: [_make_schema("a1", srv.name)]
        )

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_tool()],
        ):
            await service.preload_mcp_tools("inst-1")

        session_state = service._session_caches["inst-1"]
        assert "s1" in session_state
        assert "s2" in session_state
        # Cache and lock are independent per server
        assert session_state["s1"]["cache"] is not session_state["s2"]["cache"]
        assert session_state["s1"]["lock"] is not session_state["s2"]["lock"]

    @pytest.mark.asyncio
    async def test_schema_lookup_failure_skips_server(self, service, manager):
        """Server that raises on schema lookup is skipped; others succeed."""
        s1 = _make_server(name="good")
        s2 = _make_server(name="bad")
        manager._mcp_server_repository.list_mcp_servers.return_value = [s1, s2]

        async def lookup(srv):
            if srv.name == "good":
                return [_make_schema("t1", srv.name)]
            raise RuntimeError("Schema lookup failed")

        service.get_schemas_for_server = AsyncMock(side_effect=lookup)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_tool(name="mcp_good_t1")],
        ):
            await service.preload_mcp_tools("inst-1")

        cached = service.get_mcp_tools("inst-1")
        assert len(cached) == 1
        assert "good" in service._session_caches["inst-1"]
        assert "bad" not in service._session_caches["inst-1"]

    @pytest.mark.asyncio
    async def test_empty_schema_list_skips_server(self, service, manager):
        """Server with no tools is skipped."""
        server = _make_server(name="empty-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        service.get_schemas_for_server = AsyncMock(return_value=[])

        await service.preload_mcp_tools("inst-1")

        assert service.get_mcp_tools("inst-1") == []
        assert service._session_caches.get("inst-1", {}) == {}

    @pytest.mark.asyncio
    async def test_does_not_call_connect_instance_during_preload(self, service, manager):
        """Preload does NOT call connect_instance (lazy path — no connections)."""
        server = _make_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        service.get_schemas_for_server = AsyncMock(
            return_value=[_make_schema("t1", "test-server")]
        )

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.get_session.return_value = MagicMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ), patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_tool(name="mcp_test_server_t1")],
        ):
            await service.preload_mcp_tools("inst-1")

        # connect_instance must NOT be awaited during preload
        mock_conn_mgr.connect_instance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_list_servers_exception(self, service, manager):
        """Exception from list_mcp_servers is caught; empty cache + session state."""
        manager._mcp_server_repository.list_mcp_servers.side_effect = RuntimeError(
            "Database error"
        )

        await service.preload_mcp_tools("inst-1")

        assert service.get_mcp_tools("inst-1") == []
        assert service._session_caches.get("inst-1") == {}

    @pytest.mark.asyncio
    async def test_idempotent_second_preload(self, service, manager):
        """Second preload for same instance is a no-op (idempotency)."""
        server = _make_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        service.get_schemas_for_server = AsyncMock(
            return_value=[_make_schema("t1", "test-server")]
        )

        lazy_tools = [_make_tool(name="mcp_test_server_t1")]

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=lazy_tools,
        ):
            await service.preload_mcp_tools("inst-1")
            # Second call with same instance_id — idempotency check
            await service.preload_mcp_tools("inst-1")

        # create_lazy_mcp_tools should have been called exactly once (not twice)
        # We can verify via the cache
        assert len(service.get_mcp_tools("inst-1")) == 1

    @pytest.mark.asyncio
    async def test_tool_timeout_passed_to_create_lazy_tools(self, service, manager):
        """The configured tool_call_timeout is propagated to create_lazy_mcp_tools."""
        schemas = [_make_schema("t1", "test-server")]
        server = _make_server(name="test-server")
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        service.get_schemas_for_server = AsyncMock(return_value=schemas)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_tool()],
        ) as mock_create:
            await service.preload_mcp_tools("inst-1")

        # Verify tool_call_timeout kwarg was passed
        call_kwargs = mock_create.call_args.kwargs
        assert "tool_call_timeout" in call_kwargs
        assert call_kwargs["tool_call_timeout"] == 120


# ---------------------------------------------------------------------------
# TestGetMcpTools — sync cache read (unchanged)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# TestCloseConnections — updated for _session_caches
# ---------------------------------------------------------------------------

class TestCloseConnections:
    """Tests for close_connections method."""

    @pytest.mark.asyncio
    async def test_pops_both_caches_before_close(self, service):
        """Both _tools_cache and _session_caches are popped BEFORE closing connections."""
        service._tools_cache["inst-1"] = [MagicMock()]
        service._session_caches["inst-1"] = {"s1": {"cache": {}, "lock": asyncio.Lock()}}

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_connections("inst-1")

        assert "inst-1" not in service._tools_cache
        assert "inst-1" not in service._session_caches
        mock_conn_mgr.close_instance.assert_awaited_once_with("inst-1")

    @pytest.mark.asyncio
    async def test_handles_close_error(self, service):
        """Close error is logged but both caches are still popped."""
        service._tools_cache["inst-1"] = [MagicMock()]
        service._session_caches["inst-1"] = {}

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_instance = AsyncMock(side_effect=Exception("Close failed"))

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_connections("inst-1")

        assert "inst-1" not in service._tools_cache
        assert "inst-1" not in service._session_caches

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


# ---------------------------------------------------------------------------
# TestCloseAllConnections — updated for _session_caches
# ---------------------------------------------------------------------------

class TestCloseAllConnections:
    """Tests for close_all_connections method."""

    @pytest.mark.asyncio
    async def test_clears_all_caches(self, service):
        """All caches are cleared on close_all."""
        service._tools_cache["inst-1"] = [MagicMock()]
        service._session_caches["inst-1"] = {}

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.close_all = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            await service.close_all_connections()

        assert service._tools_cache == {}
        assert service._session_caches == {}
        mock_conn_mgr.close_all.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestProbeConnection — DEAD CODE (Phase 2 Task 3): ``_probe_connection`` and
# ``_is_builtin_stdio`` were removed from ``McpService`` in the lazy-init
# refactor. The associated test classes below are kept removed alongside
# the production code.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestSchemaCache — get_schemas_for_server / invalidate_schema_cache
# ---------------------------------------------------------------------------

class TestSchemaCache:
    """Tests for the schema cache (get_schemas_for_server / invalidate)."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_without_calling_warmup_or_discovery(self, service):
        """Cache hit returns from _schema_cache without calling warmup pool or cold discovery."""
        cached_schemas = [_make_schema("t1", "test-server")]
        service._schema_cache["test-server"] = cached_schemas

        mock_pool = MagicMock()
        mock_pool.get_cached_tool_schemas = MagicMock()
        service._warmup_pool = mock_pool
        service._discover_schemas_cold = AsyncMock()

        server = _make_server(name="test-server", is_builtin=True)

        result = await service.get_schemas_for_server(server)

        assert result is cached_schemas
        mock_pool.get_cached_tool_schemas.assert_not_called()
        service._discover_schemas_cold.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_miss_builtin_uses_warmup_pool(self, service):
        """Cache miss for built-in server uses pool's get_cached_tool_schemas."""
        pool_schemas = [_make_schema("t1", "test-server")]
        mock_pool = MagicMock()
        mock_pool.get_cached_tool_schemas = MagicMock(return_value=pool_schemas)
        service._warmup_pool = mock_pool
        service._discover_schemas_cold = AsyncMock()

        server = _make_server(name="test-server", is_builtin=True)

        result = await service.get_schemas_for_server(server)

        assert result is pool_schemas
        mock_pool.get_cached_tool_schemas.assert_called_once_with("test-server")
        service._discover_schemas_cold.assert_not_awaited()
        # Should be cached now
        assert service._schema_cache["test-server"] is pool_schemas

    @pytest.mark.asyncio
    async def test_cache_miss_non_builtin_calls_cold_discovery(self, service):
        """Cache miss for non-built-in server calls _discover_schemas_cold."""
        cold_schemas = [_make_schema("t1", "test-server")]
        server = _make_server(name="test-server", is_builtin=False)
        service._discover_schemas_cold = AsyncMock(return_value=cold_schemas)

        result = await service.get_schemas_for_server(server)

        assert result is cold_schemas
        service._discover_schemas_cold.assert_awaited_once_with(server)
        assert service._schema_cache["test-server"] is cold_schemas

    @pytest.mark.asyncio
    async def test_cold_discovery_failure_returns_empty_list(self, service):
        """Cold discovery failure (connect raises) returns [] — not raised."""
        server = _make_server(name="test-server", is_builtin=False)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock(side_effect=RuntimeError("Connection refused"))
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            result = await service.get_schemas_for_server(server)

        assert result == []
        assert service._schema_cache["test-server"] == []

    @pytest.mark.asyncio
    async def test_invalidate_schema_cache_specific_server(self, service):
        """invalidate_schema_cache(name) drops that server's entry only."""
        service._schema_cache["s1"] = [_make_schema("t1", "s1")]
        service._schema_cache["s2"] = [_make_schema("t2", "s2")]

        service.invalidate_schema_cache("s1")

        assert "s1" not in service._schema_cache
        assert "s2" in service._schema_cache

    @pytest.mark.asyncio
    async def test_invalidate_schema_cache_all(self, service):
        """invalidate_schema_cache(None) clears the entire cache."""
        service._schema_cache["s1"] = [_make_schema("t1", "s1")]
        service._schema_cache["s2"] = [_make_schema("t2", "s2")]

        service.invalidate_schema_cache(None)

        assert service._schema_cache == {}

    @pytest.mark.asyncio
    async def test_concurrent_first_time_calls_only_one_discovery(self, service):
        """Two concurrent first-time calls open only one cold connection."""
        service._warmup_pool = None  # Force cold discovery path

        call_count = 0

        async def slow_discover(server):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # Yield to let the other coroutine also reach this
            return [_make_schema("t1", server.name)]

        server = _make_server(name="test-server", is_builtin=False)

        with patch.object(service, "_discover_schemas_cold", new=slow_discover):
            results = await asyncio.gather(
                service.get_schemas_for_server(server),
                service.get_schemas_for_server(server),
            )

        assert len(results) == 2
        assert all(len(r) == 1 for r in results)
        assert call_count == 1


# ---------------------------------------------------------------------------
# TestMcpSessionProvider — _McpSessionProviderImpl
# ---------------------------------------------------------------------------

class TestMcpSessionProvider:
    """Tests for _McpSessionProviderImpl.get_session()."""

    @pytest.mark.asyncio
    async def test_fast_path_returns_existing_session(self, service):
        """get_session returns an existing session from conn_mgr — no further work."""
        provider = _McpSessionProviderImpl(service, "inst-1")
        existing_session = MagicMock()

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(return_value=existing_session)

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            result = await provider.get_session("test-server")

        assert result is existing_session
        mock_conn_mgr.get_session.assert_called_once_with("inst-1", "test-server")

    @pytest.mark.asyncio
    async def test_pool_path_acquires_and_transfers(self, service):
        """Pool server: pool.acquire + transfer_session returns the pooled session."""
        provider = _McpSessionProviderImpl(service, "inst-1")

        pooled_session = MagicMock()
        pooled_stream = MagicMock()

        mock_pool = MagicMock()
        mock_pool.is_pooled_server = MagicMock(return_value=True)
        pooled_conn = MagicMock(session=pooled_session, stream_cm=pooled_stream)
        mock_pool.acquire = AsyncMock(return_value=pooled_conn)
        service._warmup_pool = mock_pool

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(return_value=None)  # No fast path
        mock_conn_mgr.transfer_session = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            result = await provider.get_session("test-server")

        assert result is pooled_session
        mock_pool.acquire.assert_awaited_once_with("test-server")
        mock_conn_mgr.transfer_session.assert_awaited_once_with(
            "inst-1", "test-server", pooled_session, pooled_stream
        )

    @pytest.mark.asyncio
    async def test_pool_acquire_raises_falls_back_to_cold(self, service):
        """pool.acquire raising falls back to cold start."""
        provider = _McpSessionProviderImpl(service, "inst-1")

        mock_pool = MagicMock()
        mock_pool.is_pooled_server = MagicMock(return_value=True)
        mock_pool.acquire = AsyncMock(side_effect=RuntimeError("Pool failed"))
        service._warmup_pool = mock_pool

        server = _make_server(name="test-server")
        service._get_server_by_name = MagicMock(return_value=server)

        cold_session = MagicMock()
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(side_effect=[None, cold_session])
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            result = await provider.get_session("test-server")

        assert result is cold_session
        mock_conn_mgr.connect_instance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pool_acquire_returns_none_falls_back_to_cold(self, service):
        """pool.acquire returning None falls back to cold start."""
        provider = _McpSessionProviderImpl(service, "inst-1")

        mock_pool = MagicMock()
        mock_pool.is_pooled_server = MagicMock(return_value=True)
        mock_pool.acquire = AsyncMock(return_value=None)
        service._warmup_pool = mock_pool

        server = _make_server(name="test-server")
        service._get_server_by_name = MagicMock(return_value=server)

        cold_session = MagicMock()
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(side_effect=[None, cold_session])
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            result = await provider.get_session("test-server")

        assert result is cold_session
        mock_conn_mgr.connect_instance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_transfer_session_fails_falls_back_to_cold(self, service):
        """transfer_session raising falls back to cold start.

        Also verifies the m1 fix: the pooled connection is closed
        (no leak) before falling through to cold start. The
        implementation delegates to ``pool._close_connection`` so the
        subprocess + stream cleanup stays in one place.
        """
        provider = _McpSessionProviderImpl(service, "inst-1")

        pooled_session = MagicMock()
        pooled_stream_cm = MagicMock()
        pooled_conn = MagicMock(session=pooled_session, stream_cm=pooled_stream_cm)

        mock_pool = MagicMock()
        mock_pool.is_pooled_server = MagicMock(return_value=True)
        mock_pool.acquire = AsyncMock(return_value=pooled_conn)
        mock_pool._close_connection = AsyncMock()
        service._warmup_pool = mock_pool

        server = _make_server(name="test-server")
        service._get_server_by_name = MagicMock(return_value=server)

        cold_session = MagicMock()
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(side_effect=[None, cold_session])
        mock_conn_mgr.connect_instance = AsyncMock()
        mock_conn_mgr.transfer_session = AsyncMock(side_effect=RuntimeError("Transfer failed"))

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            result = await provider.get_session("test-server")

        assert result is cold_session
        mock_conn_mgr.connect_instance.assert_awaited_once()
        # m1 fix: the pooled connection was closed (no leak) because
        # transfer_session raised before the connection manager
        # adopted it. We delegate to ``pool._close_connection`` so the
        # subprocess + stream cleanup stays in one place.
        mock_pool._close_connection.assert_awaited_once_with(pooled_conn)

    @pytest.mark.asyncio
    async def test_cold_start_path(self, service):
        """No pool: connect_instance is called, then get_session returns the session."""
        provider = _McpSessionProviderImpl(service, "inst-1")
        service._warmup_pool = None

        server = _make_server(name="test-server")
        service._get_server_by_name = MagicMock(return_value=server)

        cold_session = MagicMock()
        # First get_session call (fast path) returns None to force cold start;
        # second call (after connect_instance) returns the cold session.
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(side_effect=[None, cold_session])
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            result = await provider.get_session("test-server")

        assert result is cold_session
        mock_conn_mgr.connect_instance.assert_awaited_once_with(
            "inst-1", [server], per_server_timeout=15.0
        )

    @pytest.mark.asyncio
    async def test_cold_start_session_none_raises(self, service):
        """connect_instance succeeds but get_session returns None → ToolException."""
        provider = _McpSessionProviderImpl(service, "inst-1")
        service._warmup_pool = None

        server = _make_server(name="test-server")
        service._get_server_by_name = MagicMock(return_value=server)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(return_value=None)
        mock_conn_mgr.connect_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            with pytest.raises(ToolException, match="Failed to connect"):
                await provider.get_session("test-server")

    @pytest.mark.asyncio
    async def test_unknown_server_raises(self, service):
        """_get_server_by_name returns None → ToolException."""
        provider = _McpSessionProviderImpl(service, "inst-1")
        service._warmup_pool = None
        service._get_server_by_name = MagicMock(return_value=None)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.get_session = MagicMock(return_value=None)

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            with pytest.raises(ToolException, match="not found"):
                await provider.get_session("unknown-server")


# ---------------------------------------------------------------------------
# TestSessionProviderHelpers — small helper methods
# ---------------------------------------------------------------------------

class TestSessionProviderHelpers:
    """Tests for _create_session_provider, _get_server_by_name, _get_tool_call_timeout."""

    @pytest.mark.asyncio
    async def test_create_session_provider_returns_impl(self, service):
        """_create_session_provider returns a _McpSessionProviderImpl bound to instance."""
        provider = service._create_session_provider("inst-x")
        assert isinstance(provider, _McpSessionProviderImpl)

    def test_get_server_by_name_returns_matching_server(self, service, manager):
        """_get_server_by_name returns the server when found."""
        s1, s2 = _make_server(name="foo"), _make_server(name="bar")
        manager._mcp_server_repository.list_mcp_servers.return_value = [s1, s2]

        result = service._get_server_by_name("bar")

        assert result is s2

    def test_get_server_by_name_returns_none_when_not_found(self, service, manager):
        """_get_server_by_name returns None for unknown name."""
        manager._mcp_server_repository.list_mcp_servers.return_value = []

        result = service._get_server_by_name("missing")

        assert result is None

    def test_get_tool_call_timeout_default(self, service):
        """Returns 120 when config is missing."""
        assert service._get_tool_call_timeout() == 120

    def test_get_tool_call_timeout_configured(self, manager):
        """Returns the configured mcp_pool.tool_call_timeout."""
        manager.config.mcp_pool.tool_call_timeout = 90
        service = McpService(manager=manager)
        assert service._get_tool_call_timeout() == 90

    def test_get_tool_call_timeout_default_when_config_none(self, manager):
        """Returns 120 when manager.config is None."""
        manager.config = None
        service = McpService(manager=manager)
        assert service._get_tool_call_timeout() == 120

    def test_get_tool_call_timeout_default_when_mcp_pool_missing(self, manager):
        """Returns 120 when config has no mcp_pool attribute."""
        manager.config = MagicMock(spec=[])  # No mcp_pool attribute
        service = McpService(manager=manager)
        assert service._get_tool_call_timeout() == 120

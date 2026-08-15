"""Unit tests for MCP service — lazy preload + session provider."""

import asyncio
import logging
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import daemon.services.mcp_service as mcp_service_module
from langchain_core.tools import ToolException

from daemon.mcp.models import McpToolSchema
from daemon.services.mcp_service import (
    EMPTY_DISCOVERY_RETRY_THROTTLE_S,
    McpService,
    _McpSessionProviderImpl,
)


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


def _make_discovery_session(tool_names: list[str]):
    """Create a mock MCP session for cold-discovery tests.

    ``_discover_schemas_cold`` calls ``await session.list_tools()`` and
    reads ``.tools`` off the result — each tool exposing ``name``,
    ``description`` and ``inputSchema``. Note: ``name`` is assigned as
    an attribute AFTER construction — passing ``name=`` to the
    MagicMock constructor sets the mock's repr name, not a ``.name``
    attribute.
    """
    tools = []
    for n in tool_names:
        t = MagicMock()
        t.name = n
        t.description = f"Tool {n}"
        t.inputSchema = {"type": "object", "properties": {}}
        tools.append(t)
    session = MagicMock()
    session.list_tools = AsyncMock(return_value=MagicMock(tools=tools))
    return session


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
        ) as mock_create:
            await service.preload_mcp_tools("inst-1")
            # Second call with same instance_id — idempotency check
            await service.preload_mcp_tools("inst-1")

        # W5 (single-execution invariant): the second preload must
        # short-circuit BEFORE the per-server tool-building loop runs,
        # so ``create_lazy_mcp_tools`` is invoked exactly once. Without
        # this guard, the second call would re-discover schemas and
        # rebuild all tool wrappers for the same instance.
        assert mock_create.call_count == 1
        # Sanity: the cache is populated with the result of that one call.
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

    @pytest.mark.asyncio
    async def test_per_server_timeout_overrides_global_default(self, service, manager):
        """Per-server override (test builtin=900) wins over global default (120)."""
        # Use a test builtin defined inline so the override is test-controlled
        # and doesn't depend on any specific production builtin being present.
        override_definition = MagicMock()
        override_definition.tool_call_timeout = 900

        with patch(
            "daemon.mcp.builtin_servers.get_registry",
            return_value=MagicMock(get_by_name=MagicMock(return_value=override_definition)),
        ):
            server = _make_server(name="test-server-with-override", is_builtin=True)
            manager._mcp_server_repository.list_mcp_servers.return_value = [server]
            service.get_schemas_for_server = AsyncMock(
                return_value=[_make_schema("execute_task", "test-server-with-override")]
            )

            with patch(
                "daemon.services.mcp_service.create_lazy_mcp_tools",
                return_value=[_make_tool(name="mcp_test_server_with_override_execute_task")],
            ) as mock_create:
                await service.preload_mcp_tools("inst-1")

            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["tool_call_timeout"] == 900

    @pytest.mark.asyncio
    async def test_zero_sentinel_preserved_through_preload(self, service, manager):
        """``0`` (disable timeout) is preserved through preload — not coerced to default."""
        server = _make_server(name="custom-server", is_builtin=True)
        manager._mcp_server_repository.list_mcp_servers.return_value = [server]
        service.get_schemas_for_server = AsyncMock(
            return_value=[_make_schema("t1", "custom-server")]
        )

        # Patch the builtin registry to return a definition with tool_call_timeout=0
        sentinel_def = MagicMock()
        sentinel_def.tool_call_timeout = 0
        with patch(
            "daemon.mcp.builtin_servers.get_registry",
            return_value=MagicMock(get_by_name=MagicMock(return_value=sentinel_def)),
        ), patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[_make_tool()],
        ) as mock_create:
            await service.preload_mcp_tools("inst-1")

        call_kwargs = mock_create.call_args.kwargs
        # ``0`` is a valid "disable timeout wrap" sentinel — must NOT be replaced
        # with the global default of 120.
        assert call_kwargs["tool_call_timeout"] == 0
        assert call_kwargs["tool_call_timeout"] is not None


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
    async def test_cold_discovery_failure_returns_empty_list(
        self, service, monkeypatch
    ):
        """Cold discovery failure (connect raises) returns [] — not raised.

        Fix 1: the empty result must NOT be cached (negative-cache
        poisoning) — only the throttle timestamp is recorded.
        """
        # Zero the retry delay: this test sits outside
        # TestSchemaDiscoveryRetry (whose autouse _fast_retry_delay
        # fixture applies there), and 'Connection refused' is non-auth,
        # so without this the single retry would take a real 1.5s sleep.
        monkeypatch.setattr(
            mcp_service_module,
            "SCHEMA_DISCOVERY_CONNECT_RETRY_DELAY_S",
            0,
        )
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
        # Empty discovery is never cached...
        assert "test-server" not in service._schema_cache
        # ...but the attempt IS remembered for the retry throttle.
        assert "test-server" in service._last_empty_discovery

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
# TestSchemaNegativeCache — Fix 1: empty discovery results are never cached
# ---------------------------------------------------------------------------

class TestSchemaNegativeCache:
    """Fix 1 regression tests — negative-cache poisoning.

    A transient cold-discovery failure previously wrote [] into
    ``_schema_cache`` permanently, making ``preload_mcp_tools`` skip
    the server (``if not schemas: continue``) for the daemon's
    lifetime. Empty results must never be cached; only a
    seconds-scale throttle timestamp is kept.
    """

    @pytest.mark.asyncio
    async def test_empty_discovery_not_cached_second_call_re_discovers(self, service):
        """Discovery returning [] once must NOT poison the cache.

        First call discovers [] (not cached); after the throttle
        window elapses, the SECOND call must re-attempt discovery and
        succeed. Simulates production: transient 'Connection closed'
        at boot, server healthy moments later.
        """
        server = _make_server(name="test-server", is_builtin=False)
        good_schemas = [_make_schema("t1", "test-server")]
        discover = AsyncMock(side_effect=[[], good_schemas])
        service._discover_schemas_cold = discover

        # First call: empty discovery, nothing cached.
        result1 = await service.get_schemas_for_server(server)
        assert result1 == []
        assert "test-server" not in service._schema_cache

        # Simulate the throttle window elapsing (Fix 1 throttle is
        # seconds-scale; tests must not sleep 30s).
        service._last_empty_discovery["test-server"] = (
            time.monotonic() - EMPTY_DISCOVERY_RETRY_THROTTLE_S - 1.0
        )

        # Second call: re-attempts discovery, gets the good schemas,
        # and caches them.
        result2 = await service.get_schemas_for_server(server)
        assert result2 == good_schemas
        assert discover.await_count == 2
        assert service._schema_cache["test-server"] is good_schemas

    @pytest.mark.asyncio
    async def test_nonempty_discovery_cached_no_rediscovery(self, service):
        """Success path: non-empty discovery result is cached; a second
        call is served from cache without re-discovering."""
        server = _make_server(name="test-server", is_builtin=False)
        good_schemas = [_make_schema("t1", "test-server")]
        discover = AsyncMock(return_value=good_schemas)
        service._discover_schemas_cold = discover

        result1 = await service.get_schemas_for_server(server)
        result2 = await service.get_schemas_for_server(server)

        assert result1 is good_schemas
        assert result2 is good_schemas
        discover.assert_awaited_once_with(server)
        assert service._schema_cache["test-server"] is good_schemas

    @pytest.mark.asyncio
    async def test_throttle_blocks_immediate_rediscovery(self, service):
        """Second call inside the throttle window returns [] without
        re-attempting discovery — no hot-looping a dead server."""
        server = _make_server(name="dead-server", is_builtin=False)
        discover = AsyncMock(return_value=[])
        service._discover_schemas_cold = discover

        result1 = await service.get_schemas_for_server(server)
        result2 = await service.get_schemas_for_server(server)

        assert result1 == []
        assert result2 == []
        # Only ONE discovery attempt — the second call was throttled.
        discover.assert_awaited_once_with(server)

    @pytest.mark.asyncio
    async def test_throttle_cleared_after_success(self, service):
        """A successful discovery clears the throttle marker — a later
        empty result (after invalidation) starts a fresh window."""
        server = _make_server(name="test-server", is_builtin=False)
        discover = AsyncMock(
            side_effect=[
                [_make_schema("t1", "test-server")],
                [],
            ]
        )
        service._discover_schemas_cold = discover

        await service.get_schemas_for_server(server)
        assert "test-server" not in service._last_empty_discovery

        service.invalidate_schema_cache("test-server")

        result = await service.get_schemas_for_server(server)
        assert result == []
        # Fresh empty window recorded after invalidation.
        assert "test-server" in service._last_empty_discovery


# ---------------------------------------------------------------------------
# TestSchemaDiscoveryRetry — Fix 2: one bounded retry on session acquisition
# ---------------------------------------------------------------------------

class TestSchemaDiscoveryRetry:
    """Fix 2 tests — cold-discovery session acquisition retry.

    The retry lives in ``_acquire_discovery_session``, mocked at the
    connection-manager layer (never at the live-session / API layer).
    The retry delay is patched to 0 to keep tests fast.
    """

    @pytest.fixture(autouse=True)
    def _fast_retry_delay(self, monkeypatch):
        """Shrink the 1.5s retry delay to zero for tests."""
        monkeypatch.setattr(
            mcp_service_module,
            "SCHEMA_DISCOVERY_CONNECT_RETRY_DELAY_S",
            0.0,
        )

    @pytest.mark.asyncio
    async def test_transient_connect_failure_retries_once_then_succeeds(self, service):
        """Fix 2 retry-success: first session acquisition fails once,
        retry succeeds → schemas discovered."""
        server = _make_server(name="flaky-server", is_builtin=False)
        session = _make_discovery_session(tool_names=["echo", "ping"])

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock(
            side_effect=[RuntimeError("Connection closed"), None]
        )
        mock_conn_mgr.get_session = MagicMock(return_value=session)
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            schemas = await service.get_schemas_for_server(server)

        # Retry succeeded — schemas discovered and cached.
        assert [s.name for s in schemas] == ["echo", "ping"]
        assert mock_conn_mgr.connect_instance.await_count == 2
        assert service._schema_cache["flaky-server"] is schemas
        # The failed attempt's throwaway connection was torn down
        # before the retry (clean-state reconnect).
        mock_conn_mgr.close_instance.assert_awaited()

    @pytest.mark.asyncio
    async def test_persistent_connect_failure_no_excess_retry(self, service):
        """A persistently dead server attempts exactly twice (initial +
        one retry) — bounded, never a hot loop."""
        server = _make_server(name="dead-server", is_builtin=False)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock(
            side_effect=RuntimeError("Connection refused")
        )
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            schemas = await service.get_schemas_for_server(server)

        assert schemas == []
        assert mock_conn_mgr.connect_instance.await_count == 2
        assert "dead-server" not in service._schema_cache

    @pytest.mark.asyncio
    async def test_auth_failure_fails_fast_no_retry(self, service):
        """Fix 2 no-retry-on-auth: a 401-style error fails fast with a
        single attempt — retrying credentials is pointless."""
        server = _make_server(name="locked-server", is_builtin=False)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock(
            side_effect=RuntimeError("HTTP 401 Unauthorized")
        )
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            schemas = await service.get_schemas_for_server(server)

        assert schemas == []
        # Exactly ONE attempt — auth failures never retry.
        assert mock_conn_mgr.connect_instance.await_count == 1
        assert "locked-server" not in service._schema_cache

    @pytest.mark.asyncio
    async def test_session_none_retries_once_then_succeeds(self, service):
        """The production signature: connect_instance returns OK but no
        session is tracked (swallowed 'Connection closed') — retry
        yields a session on the second attempt."""
        server = _make_server(name="ghost-server", is_builtin=False)
        session = _make_discovery_session(tool_names=["probe"])

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.connect_instance = AsyncMock(return_value=None)

        def _get_session(instance_id, server_name):
            calls.append(1)
            if len(calls) >= 2:
                return session
            return None

        calls: list[int] = []
        mock_conn_mgr.get_session = MagicMock(side_effect=_get_session)
        mock_conn_mgr.close_instance = AsyncMock()

        with patch(
            "daemon.services.mcp_service.get_mcp_connection_manager",
            return_value=mock_conn_mgr,
        ):
            schemas = await service.get_schemas_for_server(server)

        assert [s.name for s in schemas] == ["probe"]
        assert mock_conn_mgr.connect_instance.await_count == 2
        mock_conn_mgr.close_instance.assert_awaited()

    @pytest.mark.asyncio
    async def test_auth_classifier(self, service):
        """Classifier unit check: 401/403/unauthorized/forbidden/
        authentication markers match; connection errors do not."""
        for msg in (
            "HTTP 401 Unauthorized",
            "403 Forbidden",
            "Unauthorized",
            "forbidden",
            "Authentication required",
        ):
            assert McpService._is_auth_failure(RuntimeError(msg)), msg
        for msg in (
            "Connection closed",
            "Connection refused",
            "timeout",
        ):
            assert not McpService._is_auth_failure(RuntimeError(msg)), msg


# ---------------------------------------------------------------------------
# TestEagerWarmSchemas — Fix 3: honest primed counting
# ---------------------------------------------------------------------------

class TestEagerWarmSchemas:
    """Fix 3 tests — empty schema sets must not count as primed."""

    @pytest.mark.asyncio
    async def test_empty_set_not_counted_as_primed(self, service, manager, caplog):
        """A server with an empty schema set is NOT primed; the failed
        server is visible by name with '0 tools' in the log."""
        good = _make_server(name="good-server", is_builtin=False)
        bad = _make_server(name="plane", is_builtin=False)
        manager._mcp_server_repository.list_mcp_servers.return_value = [good, bad]

        async def _lookup(server):
            if server.name == "good-server":
                return [_make_schema("t1", server.name)]
            return []

        service.get_schemas_for_server = AsyncMock(side_effect=_lookup)

        with caplog.at_level(logging.INFO, logger="daemon.services.mcp_service"):
            primed = await service.eager_warm_schemas()

        assert primed == 1
        assert any(
            "plane: 0 tools" in rec.message for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_all_empty_returns_zero(self, service, manager, caplog):
        """All-empty is the production incident shape: honest 0/N, never
        a silent 'primed N/N'."""
        dead1 = _make_server(name="plane", is_builtin=False)
        dead2 = _make_server(name="other", is_builtin=False)
        manager._mcp_server_repository.list_mcp_servers.return_value = [dead1, dead2]

        service.get_schemas_for_server = AsyncMock(return_value=[])

        with caplog.at_level(logging.INFO, logger="daemon.services.mcp_service"):
            primed = await service.eager_warm_schemas()

        assert primed == 0
        assert any(
            "primed 0/2" in rec.message for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_all_good_counts_per_server_tools(self, service, manager, caplog):
        """Happy path: per-server tool counts are visible in the log."""
        s1 = _make_server(name="alpha", is_builtin=False)
        s2 = _make_server(name="beta", is_builtin=False)
        manager._mcp_server_repository.list_mcp_servers.return_value = [s1, s2]

        async def _lookup(server):
            if server.name == "alpha":
                return [
                    _make_schema("a1", server.name),
                    _make_schema("a2", server.name),
                ]
            return [_make_schema("b1", server.name)]

        service.get_schemas_for_server = AsyncMock(side_effect=_lookup)

        with caplog.at_level(logging.INFO, logger="daemon.services.mcp_service"):
            primed = await service.eager_warm_schemas()

        assert primed == 2
        assert any("alpha: 2 tool(s)" in rec.message for rec in caplog.records)
        assert any("beta: 1 tool(s)" in rec.message for rec in caplog.records)


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

        Also verifies the W1 fix: the pooled connection is closed
        (no leak) before falling through to cold start. The
        implementation delegates to ``pool.release_connection`` so the
        subprocess + stream cleanup stays in one place and the pool's
        encapsulation boundary is respected.
        """
        provider = _McpSessionProviderImpl(service, "inst-1")

        pooled_session = MagicMock()
        pooled_stream_cm = MagicMock()
        pooled_conn = MagicMock(session=pooled_session, stream_cm=pooled_stream_cm)

        mock_pool = MagicMock()
        mock_pool.is_pooled_server = MagicMock(return_value=True)
        mock_pool.acquire = AsyncMock(return_value=pooled_conn)
        mock_pool.release_connection = AsyncMock()
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
        # W1 fix: the pooled connection was closed (no leak) because
        # transfer_session raised before the connection manager
        # adopted it. We delegate to ``pool.release_connection`` so the
        # subprocess + stream cleanup stays in one place AND the pool's
        # encapsulation boundary is respected (no reaching into
        # ``_close_connection`` from outside the class).
        mock_pool.release_connection.assert_awaited_once_with(pooled_conn)

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

    # ------------------------------------------------------------------
    # _get_per_server_timeout — builtin lookup for per-server override
    # ------------------------------------------------------------------

    def test_get_per_server_timeout_returns_none_for_non_builtin(self, service):
        """Unknown / non-builtin server names return None (fall back to global)."""
        assert service._get_per_server_timeout("not-a-builtin") is None

    def test_get_per_server_timeout_builtin_no_override_returns_none(self, service):
        """Builtins without a tool_call_timeout override return None.

        webfetch / context7 inherit the base class's ``None`` default,
        so the caller falls back to the global timeout — same as a
        non-builtin.
        """
        assert service._get_per_server_timeout("webfetch") is None
        assert service._get_per_server_timeout("context7") is None

    def test_get_per_server_timeout_preserves_zero_sentinel(self, service):
        """``0`` (disable timeout wrapping) is preserved — NOT coerced to default.

        Simulates a hypothetical builtin definition that explicitly opts
        out of timeout wrapping. The helper must return ``0`` so the
        caller passes it through verbatim; ``is not None`` at the call
        site guarantees this contract holds.
        """
        sentinel_definition = MagicMock()
        sentinel_definition.tool_call_timeout = 0

        # Patch where the symbol is looked up (function-local import),
        # not where it's called from. ``daemon.services.mcp_service``
        # has no module-level ``get_registry`` binding.
        with patch(
            "daemon.mcp.builtin_servers.get_registry",
            return_value=MagicMock(get_by_name=MagicMock(return_value=sentinel_definition)),
        ):
            result = service._get_per_server_timeout("any-name")

        assert result == 0
        assert result is not None  # explicit: zero is not "missing"

    def test_get_per_server_timeout_missing_attribute_returns_none(self, service):
        """If a definition has no ``tool_call_timeout`` attribute, returns None.

        Defensive path: the abstract base provides the property, but a
        future subclass might skip it. ``getattr`` with a default
        protects against that without raising AttributeError.
        """
        # A MagicMock with spec=[] has no tool_call_timeout attribute.
        bare_definition = MagicMock(spec=[])

        with patch(
            "daemon.mcp.builtin_servers.get_registry",
            return_value=MagicMock(get_by_name=MagicMock(return_value=bare_definition)),
        ):
            result = service._get_per_server_timeout("future-builtin")

        assert result is None

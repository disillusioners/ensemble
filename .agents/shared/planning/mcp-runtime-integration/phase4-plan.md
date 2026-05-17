# Phase 4: Testing & Resilience

## Objective
Create comprehensive tests for the MCP integration — unit tests for config parsing, connection management, and tool conversion, plus integration tests with mock MCP servers. Harden error handling for edge cases.

## Coupling
- **Depends on**: Phase 1 + Phase 2 + Phase 3 (all implementation complete)
- **Coupling type**: **loose** — tests verify behavior but don't modify implementation files
- **Shared files with other phases**: Test files only; reads from all implementation modules
- **Shared APIs/interfaces**: Tests exercise all public APIs from previous phases
- **Why this coupling**: Testing phase — validates the complete integration without modifying production code.

## Context
- **Test framework**: pytest with pytest-asyncio (already in `pyproject.toml`)
- **Test patterns** (from `tests/`):
  - `tests/conftest.py` — patches `langgraph` module for unit tests
  - `engine()` fixture — in-memory SQLite for repository tests
  - `TestClient` pattern for FastAPI integration testing
  - `pytest.mark.skipif` for tests needing API keys
- **Mock patterns**: Services receive manager facade, making them easy to mock
- **MCP test server**: The `mcp` Python SDK includes test utilities for creating mock MCP servers

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Unit tests: Config schema** | Test `McpStdioConfig`, `McpSseConfig`, `McpStreamableHttpConfig` parsing and validation. Test: valid configs, invalid transport type, missing required fields, extra fields ignored. Test discriminated union resolution. | `tests/unit/test_mcp_config.py` (new) |
| 2 | **Unit tests: Connection manager** | Test `McpConnectionManager` lifecycle: connect_instance, get_session, close_instance, close_all. Mock the MCP client sessions. Test: concurrent access safety, double-close, close non-existent instance, lazy lock initialization. | `tests/unit/test_mcp_connection_manager.py` (new) |
| 3 | **Unit tests: McpService** | Test `McpService` with mocked connection manager and repository. Test: preload flow, cache behavior, error resilience, cleanup ordering (pop before close). | `tests/unit/test_mcp_service.py` (new) |
| 4 | **Unit tests: Tool filter fix** | Test `resolve_tool_filter()` with MCP tools. Test: `deny: ["mcp"]` blocks all `mcp_*` tools, `allow: ["mcp"]` allows only `mcp_*` tools, `allow: ["*"]` includes MCP tools, mixed allow/deny, empty MCP tools edge case. | `tests/unit/test_mcp_tool_filter.py` (new) |
| 5 | **Integration test: Spawn with MCP** | End-to-end test: configure an MCP server in DB, spawn an instance, verify MCP tools appear in the tool list, verify tool can be invoked by the LLM. Use a simple mock MCP server (echo/ping server from MCP SDK). | `tests/integration/test_mcp_spawn.py` (new) |
| 6 | **Integration test: Restore path** | Test that `_restore_instance()` correctly loads MCP tools. Spawn instance, terminate it, then trigger restore via `get_instance()`. Verify MCP tools are present in restored graph. | `tests/integration/test_mcp_restore.py` (new) |
| 7 | **Integration test: Cleanup** | Test that terminating an instance closes MCP connections. Verify no connection leaks by checking connection manager state after terminate. Test child cascade terminates MCP for children too. | `tests/integration/test_mcp_cleanup.py` (new) |
| 8 | **Resilience test: Server failures** | Test spawn behavior when: (a) MCP server process exits immediately, (b) MCP server returns malformed responses, (c) MCP server takes too long to respond (exceeds per-server timeout), (d) All MCP servers are unreachable. Verify instance still starts with built-in tools. | `tests/integration/test_mcp_resilience.py` (new) |
| 9 | **Resilience test: Concurrent operations** | Test: concurrent spawn+terminate for different instances, concurrent preloads for same instance (idempotency), lock initialization under concurrent access. | `tests/unit/test_mcp_concurrent.py` (new) |
| 10 | **Edge case: Tool invocation after disconnect** | Test that calling an MCP tool after the server disconnects produces a clear error in ToolNode (doesn't crash the graph). Document as known limitation. | `tests/integration/test_mcp_disconnect.py` (new) |
| 11 | **Edge case: Mixed connection failures** | Test with 3 MCP servers where 2 fail and 1 succeeds. Verify partial tool list is injected (tools from successful server only). | `tests/integration/test_mcp_resilience.py` (included in task 8) |

## Key Files

### New Files
| File | Purpose |
|------|---------|
| `tests/unit/test_mcp_config.py` | Config schema unit tests |
| `tests/unit/test_mcp_connection_manager.py` | Connection lifecycle tests |
| `tests/unit/test_mcp_service.py` | Service layer tests |
| `tests/unit/test_mcp_tool_filter.py` | Tool filter fix tests |
| `tests/unit/test_mcp_concurrent.py` | Concurrent operations tests |
| `tests/integration/test_mcp_spawn.py` | End-to-end spawn test |
| `tests/integration/test_mcp_restore.py` | Restore path test |
| `tests/integration/test_mcp_cleanup.py` | Cleanup verification test |
| `tests/integration/test_mcp_resilience.py` | Failure scenario tests |
| `tests/integration/test_mcp_disconnect.py` | Post-disconnect invocation test |

## Detailed Design

### Test Fixtures (`tests/conftest.py` additions)

```python
@pytest.fixture
def mock_mcp_server():
    """Create a mock MCP server entry for testing."""
    return McpServer(
        id="test-server-1",
        name="test-server",
        description="Test MCP server",
        config={"transport": "stdio", "command": "python", "args": ["-m", "test_server"]},
        is_active=True,
    )

@pytest.fixture
def mcp_server_db(engine):
    """Create an in-memory DB with test MCP servers."""
    # Use existing engine fixture pattern
    ...

@pytest.fixture
def mcp_service(engine):
    """Create an McpService with mocked manager."""
    manager = MagicMock()
    manager._mcp_server_repository = SQLModelMcpServerRepository(engine)
    return McpService(manager=manager)
```

### Test Cases by Category

#### Config Schema Tests (`test_mcp_config.py`)
```
test_stdio_config_valid        — valid stdio config parses correctly
test_stdio_config_defaults     — transport defaults to "stdio"
test_sse_config_valid          — valid SSE config with URL
test_streamable_http_config    — valid streamable-http config
test_invalid_transport         — unknown transport raises ValidationError
test_missing_command_stdio     — stdio without command raises error
test_missing_url_sse           — SSE without url raises error
test_extra_fields_ignored      — extra fields don't cause errors
test_config_from_db_field      — parse config from McpServer.config dict
test_config_validation_at_crud — CRUD rejects invalid config
```

#### Connection Manager Tests (`test_mcp_connection_manager.py`)
```
test_connect_instance_creates_sessions
test_connect_instance_handles_failure_gracefully
test_connect_instance_parallel_with_timeout
test_get_session_returns_connected
test_get_session_returns_none_when_not_connected
test_close_instance_removes_sessions
test_close_instance_idempotent
test_close_instance_pops_before_close
test_close_all_cleans_everything
test_concurrent_connect_same_instance
test_lazy_lock_initialization
test_lock_not_created_in_init
```

#### MCP Service Tests (`test_mcp_service.py`)
```
test_preload_caches_tools
test_preload_connects_and_discovers
test_preload_parallel_server_discovery
test_get_mcp_tools_returns_cached
test_get_mcp_tools_empty_when_not_preloaded
test_close_connections_pops_cache_first
test_close_connections_closes_sessions
test_close_all_connections_clears_everything
test_preload_handles_no_servers
test_preload_handles_all_servers_failing
test_preload_handles_partial_failure
test_tool_names_have_mcp_prefix
test_slugify_converts_server_names
```

#### Tool Filter Tests (`test_mcp_tool_filter.py`)
```
test_deny_mcp_blocks_all_mcp_tools
test_allow_mcp_allows_only_mcp_tools
test_allow_wildcard_includes_mcp_tools
test_no_filter_includes_mcp_tools
test_mixed_allow_deny_with_mcp
test_mcp_category_empty_when_no_mcp_tools
test_mcp_filter_with_dynamic_tools_only
```

#### Integration: Spawn (`test_mcp_spawn.py`)
```
test_spawn_with_mcp_server_injects_tools
test_spawn_without_mcp_servers_works_normally
test_spawn_preload_failure_continues_with_builtin_tools
test_mcp_tools_callable_by_agent
test_mcp_tools_appear_in_help
```

#### Integration: Restore (`test_mcp_restore.py`)
```
test_restore_loads_mcp_tools
test_restore_after_terminate_has_mcp_tools
test_restore_preload_called_before_get_instance
```

#### Integration: Cleanup (`test_mcp_cleanup.py`)
```
test_terminate_closes_mcp_connections
test_terminate_child_cascades_mcp_cleanup
test_shutdown_closes_all_connections
test_cleanup_pops_cache_before_close
test_cleanup_idempotent
```

#### Integration: Resilience (`test_mcp_resilience.py`)
```
test_unreachable_server_doesnt_block_spawn
test_malformed_config_doesnt_block_spawn
test_server_crash_during_tool_call_handled
test_timeout_on_connect_doesnt_block_spawn
test_mixed_connection_failures_partial_tools
test_all_servers_down_no_tools_blocked
test_concurrent_spawn_different_instances
test_concurrent_preload_same_instance_idempotent
```

#### Integration: Post-Disconnect (`test_mcp_disconnect.py`)
```
test_tool_invocation_after_disconnect_produces_error
test_tool_error_doesnt_crash_graph
test_disconnect_logged_with_server_name
```

## Testing Strategy

### Mock MCP Server
Use the `mcp` Python SDK's built-in test server utilities to create a simple echo server for integration tests:

```python
from mcp.server import Server
from mcp.server.sse import SseServerTransport

# Create a simple test server with one tool: "echo"
test_server = Server("test")
@test_server.list_tools()
async def list_tools():
    return [Tool(name="echo", description="Echo input", inputSchema={"type": "object", "properties": {"message": {"type": "string"}}})]

@test_server.call_tool()
async def call_tool(name, arguments):
    return [TextContent(type="text", text=arguments["message"])]
```

### Isolation
- Unit tests use mocked MCP sessions (no network, no subprocesses)
- Integration tests use in-process mock MCP server on random port
- No external dependencies required for test suite to pass

## Constraints
- All tests must pass without external MCP servers running
- Tests must not leave orphaned subprocesses
- Integration tests should complete in < 30 seconds each
- Follow existing test naming: `test_{scenario}_{expected_behavior}`
- Test the lazy lock pattern specifically (no `asyncio.Lock()` in `__init__`)

## Deliverables
- [ ] All 10 test files created with test cases listed above
- [ ] `pytest tests/unit/test_mcp_*.py` passes
- [ ] `pytest tests/integration/test_mcp_*.py` passes
- [ ] Code coverage for `daemon/mcp/` module ≥ 80%
- [ ] Code coverage for `daemon/services/mcp_service.py` ≥ 80%
- [ ] All edge cases from resilience tests documented
- [ ] Lazy lock initialization tested (no event loop at init time)
- [ ] Cache pop-before-close ordering tested
- [ ] Restore path MCP tools tested
- [ ] Tool filter fix tested for all MCP scenarios

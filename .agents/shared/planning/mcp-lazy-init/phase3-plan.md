# Phase 3: Tests + Validation

## Objective

Update existing tests to work with the new lazy initialization flow, add new tests for lazy-specific behaviors (shared session cache, concurrent first-call, first-tool-call latency), and validate the end-to-end latency improvement.

## Coupling

- **Depends on**: Phase 1, Phase 2
- **Coupling type**: tight — tests validate the complete lazy flow
- **Shared files with other phases**: All files modified in Phase 1 & 2

## Context

Existing tests mock `load_mcp_tools` and `adapt_mcp_tools` extensively. The new lazy flow uses `create_lazy_mcp_tools` instead, so test mocks need updating. Key test files:

| Test File | What It Tests | Mock Update Needed |
|-----------|---------------|-------------------|
| `tests/unit/test_mcp_service.py` | Preload caching, failures, pool awareness | Yes — mock `create_lazy_mcp_tools` instead of `adapt_mcp_tools` |
| `tests/unit/test_mcp_tool_timeout.py` | Timeout wrapping, McpPoolConfig | Yes — test lazy coroutine timeout |
| `tests/unit/test_mcp_warmup_pool.py` | Pool lifecycle, tool discovery cache | Minor — test new `get_cached_tool_schemas()` |
| `tests/unit/test_mcp_cold_load_race.py` | Preload ordering vs restore | Yes — preload is now fast, test that it's not blocking |
| `tests/unit/test_mcp_runtime_integration.py` | E2E preload + tools injection, cleanup | Yes — validate lazy tools work end-to-end |
| `tests/unit/test_mcp_concurrent.py` | Concurrent preload, close-while-loading | Minor — should mostly work |
| `tests/integration/test_mcp_lifecycle.py` | Spawn with/without MCP, resilience | Yes — validate lazy connection on tool call |

## Tasks

### Task 1: Update unit tests for `mcp_service.py`

**File:** `tests/unit/test_mcp_service.py`

**Changes:**
- Mock `create_lazy_mcp_tools` instead of `adapt_mcp_tools`
- Mock `get_schemas_for_server` to return test schemas
- Verify `preload_mcp_tools()` completes quickly (no blocking)
- Verify tools are cached with correct names (`mcp_{server}_{tool}`)
- Verify partial failure: one server's schemas fail, others succeed
- Verify full failure: all schemas fail, cache set to `[]`
- Verify cache hit: calling preload twice returns same tools
- Verify `_session_caches` is populated per instance with shared cache/lock per server

### Task 2: Update unit tests for `tool_adapter.py`

**File:** `tests/unit/test_mcp_tool_timeout.py`

**Changes:**
- Add tests for `create_lazy_mcp_tools()`:
  - Correct name prefix (`mcp_{server}_{tool}`)
  - Description includes `[MCP:server]` suffix
  - Timeout wrapping works (simulate slow `session.call_tool`)
  - `tool_call_timeout=0` disables timeout wrapping
- Add tests for `_build_lazy_coroutine()`:
  - First call creates session, second call reuses (shared cache verification)
  - Session creation failure raises `ToolException`
  - Tool call failure raises `ToolException`
  - Timeout raises `ToolException`
- Add tests for shared session cache (C1 verification):
  - Create 3 tools for same server, call all 3 → exactly 1 session created
  - Create 3 tools for same server, call concurrently → exactly 1 session created (lock guards)
  - Create tools for 2 different servers → 2 sessions created

### Task 3: Update integration/lifecycle tests

**File:** `tests/integration/test_mcp_lifecycle.py`, `tests/unit/test_mcp_runtime_integration.py`

**Changes:**
- Mock `session.call_tool()` to simulate tool execution
- Verify lazy connection is triggered on first tool call
- Verify subsequent calls reuse the session
- Verify cleanup closes lazy-created sessions
- Verify `spawn_instance_with_mcp()` returns immediately (no blocking)
- Verify concurrent tool calls to same server share session

### Task 4: Add new lazy-specific tests

**File:** `tests/unit/test_mcp_lazy_init.py` (NEW)

Test cases:
1. **Schema cache hit:** Second preload for same server skips discovery
2. **Schema cache invalidation:** After server update, schemas are re-discovered
3. **Cold server schema discovery:** Temporary connection created and closed
4. **Pooled server schema extraction:** Uses `pool.get_cached_tool_schemas()` public API
5. **Lazy session creation on first call:** Session not created until tool called
6. **Lazy session reuse:** Second tool call uses cached session
7. **Lazy session with pool available:** Prefers pool over cold start
8. **Lazy session pool exhaustion:** Falls back to cold start when pool empty (W5)
9. **Lazy session failure:** Returns ToolException naturally
10. **Multiple servers, one fails:** Other servers' tools still work
11. **Cleanup after lazy init:** `close_connections` closes lazily-created sessions
12. **Concurrent first-call to same server:** Exactly 1 session created (W7 concurrency guard)
13. **Concurrent first-call to different servers:** Both sessions created independently
14. **Instance restore with lazy tools:** Fresh session caches after restore (W8)

### Task 5: Add first-tool-call latency measurement (W9)

**File:** `tests/unit/test_mcp_lazy_init.py`

```python
async def test_first_tool_call_latency(self):
    """Measure latency shifted from instance creation to first tool call.
    
    The lazy design shifts connection latency from spawn to first tool use.
    This test documents that shift and ensures it stays within expected bounds.
    """
    # Setup: create lazy tools with mocked session provider
    # The session provider's get_session() simulates connection delay
    mock_provider = MockMcpSessionProvider(connect_delay=2.0)
    
    tools = create_lazy_mcp_tools(
        server_name="test_server",
        schemas=[{"name": "tool1", "description": "test", "input_schema": {}}],
        session_provider=mock_provider,
        shared_session_cache={},
        shared_session_lock=asyncio.Lock(),
    )
    
    # First call — includes connection setup time
    start = time.monotonic()
    await tools[0].coroutine(arg1="value")
    first_call_duration = time.monotonic() - start
    assert first_call_duration >= 2.0  # includes connection delay
    
    # Second call — session cached, should be fast
    start = time.monotonic()
    await tools[0].coroutine(arg1="value2")
    second_call_duration = time.monotonic() - start
    assert second_call_duration < 0.1  # no connection overhead
```

**Document the latency trade-off:**
- Instance creation: <500ms (down from ~13s) ✅
- First tool call per server: 1-15s (same as old preload cost, but only for servers actually used)
- Subsequent tool calls: <100ms (session cached)

### Task 6: Update conftest mocking

**File:** `tests/conftest.py`

Ensure the module-level mocking in conftest (lines 50-107, 196-229) is compatible:
- `daemon.mcp.tool_adapter` mock should include `create_lazy_mcp_tools`, `McpSessionProvider`, and `_build_lazy_coroutine`
- Existing `adapt_mcp_tools` mock should still work for any code that uses it
- `langchain_mcp_adapters.tools` mock remains for `_discover_schemas_cold` path
- `_convert_call_tool_result` import should be mocked to return a simple conversion

### Task 7: Validate latency improvement

**Manual validation:**
1. Configure 4 MCP servers
2. Time `POST /instances` before and after change
3. Verify instance creation returns in <500ms
4. Verify first tool call triggers lazy connection and succeeds
5. Verify subsequent tool calls are fast (session cached)
6. Document first-tool-call latency per transport type

**Automated validation:**
- Add a test that times `preload_mcp_tools` and asserts it completes in <100ms when schemas are cached (no connections established)
- Add a test measuring first-call vs subsequent-call latency (W9)

## Key Files

- `tests/unit/test_mcp_lazy_init.py` — NEW: lazy-specific tests including latency and concurrency
- `tests/unit/test_mcp_service.py` — Updated mocks and assertions
- `tests/unit/test_mcp_tool_timeout.py` — Updated for lazy coroutine tests + shared cache tests
- `tests/integration/test_mcp_lifecycle.py` — Updated for lazy flow
- `tests/unit/test_mcp_runtime_integration.py` — Updated for lazy flow
- `tests/conftest.py` — Updated mocking

## Constraints

- All existing tests must pass after changes
- Mock patterns should remain compatible with `conftest.py` infrastructure
- Tests should NOT require real MCP servers (mocked)
- Test coverage should remain at or above current levels
- Shared session cache tests (C1) must verify exactly 1 session per server, not N

## Deliverables

- [ ] All existing tests updated and passing
- [ ] New `test_mcp_lazy_init.py` with 14+ test cases
- [ ] Shared session cache tests proving N tools → 1 connection (C1)
- [ ] Concurrent first-call tests proving lock guards work (W7)
- [ ] First-tool-call latency measurement test (W9)
- [ ] Instance restore test with lazy tools (W8)
- [ ] Pool exhaustion fallback test (W5)
- [ ] Updated conftest mocking
- [ ] Manual latency validation results documented
- [ ] CI passing with all tests green

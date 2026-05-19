# Phase 4: Testing

## Objective
Write comprehensive tests for the warm-up pool and its integration with the MCP layer. Verify that existing MCP tests continue to pass unchanged (regression safety). Cover unit-level pool behavior and integration-level lifecycle.

## Coupling
- **Depends on**: Phases 1-3 (complete implementation)
- **Coupling type**: loose — tests depend on interfaces/contracts, not implementation details
- **Shared files with other phases**: imports from all implementation modules
- **Shared APIs/interfaces**: `McpWarmupPool`, `McpConnectionManager.transfer_session()`, `McpService` pool-aware logic
- **Why this coupling**: Tests must run after implementation is complete

## Context
The existing test suite has strong patterns:
- Unit tests mock external dependencies (subprocess, DB, MCP SDK)
- Integration tests use mock MCP servers
- Tests are in `tests/unit/` and `tests/integration/`
- Key files: `test_mcp_connection_manager.py` (244 lines), `test_mcp_service.py` (298 lines), `test_mcp_runtime_integration.py` (549 lines), `test_mcp_lifecycle.py` (365 lines)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `tests/unit/test_mcp_warmup_pool.py` | Unit tests for `McpWarmupPool` class. Mock `mcp.stdio_client`, `ClientSession`, `load_mcp_tools`. | `tests/unit/test_mcp_warmup_pool.py` (new) |
| 2 | Test pool warmup | Verify `warmup()` creates correct number of connections per server. Test parallel warmup of multiple servers. Test warmup failure for one server doesn't block others. | `tests/unit/test_mcp_warmup_pool.py` |
| 3 | Test pool acquire | Verify `acquire()` returns a `PooledConnection` when available. Returns `None` when pool empty. Returns `None` when pool not running. Triggers replenishment after successful acquire. | `tests/unit/test_mcp_warmup_pool.py` |
| 4 | Test pool replenish | Verify `_replenish()` creates new connection after acquire. Verify per-server lock prevents concurrent replenish. Verify `_replenish_semaphore` caps total concurrent replenish. Test replenish failure is logged but not raised. Verify replenish task is tracked in `_replenish_tasks` set. | `tests/unit/test_mcp_warmup_pool.py` |
| 5 | Test pool drain | Verify `drain()` closes all sessions and stream context managers. Verify health check task is cancelled. Verify ALL tracked `_replenish_tasks` are cancelled (even mid-execution). Verify `_running` set to False. Verify no orphaned processes after drain. | `tests/unit/test_mcp_warmup_pool.py` |
| 6 | Test health check | Verify health check acquires per-server lock (blocks acquire during check). Verify dead connections are removed and healthy ones restored. Verify queue is NOT left empty during check (lock prevents starvation). Verify replenishment triggered for evicted connections. Test timeout on health check. Verify no private SDK internals are accessed. | `tests/unit/test_mcp_warmup_pool.py` |
| 7 | Test `transfer_session()` on connection_manager | Verify session is registered in `_connections` and `_stream_contexts`. Verify it integrates with `close_instance()` — transferred sessions are cleaned up properly. | `tests/unit/test_mcp_connection_manager.py` (add tests) |
| 8 | Test liveness probe in preload path | Verify `_probe_connection()` returns True for live sessions, False for dead ones. Verify stale pooled connection triggers cold-start fallback (not transferred to connection_manager). Verify stale connection is properly closed (session.close + stream_cm.__aexit__). | `tests/unit/test_mcp_service.py` (add tests) |
| 9 | Test pool-aware `preload_mcp_tools()` | Verify pool connection used when available. Verify cold-start fallback when pool empty. Verify cold-start fallback when pool not configured (`None`). Verify mixed scenario: some pooled, some cold-started. Verify stale connection falls back to cold-start. | `tests/unit/test_mcp_service.py` (add tests) |
| 10 | Test `_is_builtin_stdio()` helper | Verify returns True for context7 and webfetch. Returns False for user-defined STDIO servers. Returns False for SSE/streamable-http servers. | `tests/unit/test_mcp_service.py` (add tests) |
| 11 | Create integration test `test_mcp_warmup_pool_lifecycle.py` | End-to-end test: pool warmup → instance acquire → liveness probe → transfer → instance use → instance close → pool replenish. Use mock MCP servers (not real npx/uvx). Include test for stale connection detection mid-transfer. | `tests/integration/test_mcp_warmup_pool_lifecycle.py` (new) |
| 12 | Run existing test suite — regression verification | Run ALL existing MCP tests to verify no breakage. Specific files to check: `test_mcp_connection_manager.py`, `test_mcp_service.py`, `test_mcp_runtime_integration.py`, `test_mcp_lifecycle.py`, `test_mcp_concurrent.py`, `test_mcp_stdio_timeout.py`, `test_builtin_mcp_servers.py`. Document any failures and root cause. | All existing test files |

## Key Files
- `tests/unit/test_mcp_warmup_pool.py` — **NEW** — Pool unit tests
- `tests/unit/test_mcp_connection_manager.py` — Add `transfer_session` tests
- `tests/unit/test_mcp_service.py` — Add pool-aware preload tests
- `tests/integration/test_mcp_warmup_pool_lifecycle.py` — **NEW** — Integration lifecycle test

## Constraints
- All new tests must mock MCP subprocess connections (no real npx/uvx in CI)
- Tests must pass with pool enabled AND disabled (verify fallback path)
- Existing tests must pass without any modification
- Follow existing test patterns in the project (check `@pytest.fixture`, `AsyncMock` usage, etc.)

## Test Design Patterns

### Mocking the Pool in mcp_service Tests
```python
@pytest.fixture
def mock_pool():
    pool = AsyncMock(spec=McpWarmupPool)
    pool.acquire = AsyncMock(return_value=None)  # Default: pool empty → cold start
    return pool

async def test_preload_uses_pool_when_available(mock_pool, ...):
    pooled_conn = PooledConnection(
        session=mock_session,
        stream_cm=mock_stream_cm,
        tools=[mock_tool],
        server_name="context7",
        created_at=time.monotonic(),
    )
    mock_pool.acquire = AsyncMock(return_value=pooled_conn)

    mcp_service.set_warmup_pool(mock_pool)
    await mcp_service.preload_mcp_tools(instance_id)

    # Verify pool was checked
    mock_pool.acquire.assert_called_once_with("context7")
    # Verify tools came from pool
    tools = mcp_service.get_mcp_tools(instance_id)
    assert mock_tool in tools
```

### Testing Graceful Degradation
```python
async def test_preload_falls_back_when_pool_empty(mock_pool, ...):
    mock_pool.acquire = AsyncMock(return_value=None)  # Pool empty
    mcp_service.set_warmup_pool(mock_pool)

    await mcp_service.preload_mcp_tools(instance_id)

    # Verify cold-start path was used (connect_instance called)
    mock_conn_mgr.connect_instance.assert_called_once()
```

### Testing Stale Connection Detection (S4)
```python
async def test_preload_falls_back_on_stale_pooled_connection(mock_pool, ...):
    """Pool returns a connection that died between health check and transfer."""
    stale_conn = PooledConnection(
        session=mock_session,  # session that will fail ping
        stream_cm=mock_stream_cm,
        tools=[mock_tool],
        server_name="context7",
        created_at=time.monotonic(),
    )
    mock_pool.acquire = AsyncMock(return_value=stale_conn)
    # Liveness probe fails
    mock_session.send_ping = AsyncMock(side_effect=ConnectionError("process exited"))

    mcp_service.set_warmup_pool(mock_pool)
    await mcp_service.preload_mcp_tools(instance_id)

    # Verify stale connection was cleaned up (session.close + stream_cm.__aexit__)
    mock_session.close.assert_called_once()
    mock_stream_cm.__aexit__.assert_called_once()
    # Verify cold-start fallback was used
    mock_conn_mgr.connect_instance.assert_called_once()
    # Verify tools came from cold-start, not stale pool
    tools = mcp_service.get_mcp_tools(instance_id)
    assert mock_tool not in tools  # stale tools discarded
```

### Testing Drain Cancels Replenish Tasks (C2b)
```python
async def test_drain_cancels_in_flight_replenish():
    """Verify drain() cancels replenish tasks that are mid-execution."""
    pool = McpWarmupPool()
    pool.register_server("context7", stdio_config, pool_size=1)
    pool._running = True

    # Simulate a replenish task that's mid-execution
    replenish_started = asyncio.Event()
    async def slow_replenish():
        replenish_started.set()
        await asyncio.sleep(100)  # Would run forever

    task = asyncio.create_task(slow_replenish())
    pool._replenish_tasks.add(task)

    # Drain should cancel the task
    await pool.drain()
    assert task.cancelled() or task.done()
    assert len(pool._replenish_tasks) == 0
```

### Integration Lifecycle Test
```python
async def test_pool_acquire_transfer_use_release():
    """Full lifecycle: warmup → acquire → transfer → use → close → replenish"""
    pool = McpWarmupPool()
    pool.register_server("context7", stdio_config, pool_size=1)

    # Mock the subprocess creation
    with patch("daemon.mcp.warmup_pool.mcp.stdio_client") as mock_stdio:
        mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(read_stream, write_stream))
        await pool.warmup()

    # Acquire for instance
    conn = await pool.acquire("context7")
    assert conn is not None

    # Transfer to connection manager
    conn_mgr = get_mcp_connection_manager()
    await conn_mgr.transfer_session("inst-1", "context7", conn.session, conn.stream_cm)

    # Verify tools available
    assert len(conn.tools) > 0

    # Close instance (normal cleanup)
    await conn_mgr.close_instance("inst-1")

    # Verify pool replenished
    await asyncio.sleep(0.1)  # Let replenish task run
    assert not pool._pools["context7"].empty()

    # Cleanup
    await pool.drain()
```

## Deliverables
- [ ] `tests/unit/test_mcp_warmup_pool.py` with 12+ test cases (including drain-cancels-replenish, semaphore caps concurrent, health-check-lock-prevents-starvation)
- [ ] Added tests for `transfer_session()` in connection manager test file
- [ ] Added tests for `_probe_connection()` liveness probe in mcp_service test file
- [ ] Added test for stale pooled connection → cold-start fallback (S4)
- [ ] Added tests for pool-aware preload in mcp_service test file
- [ ] `tests/integration/test_mcp_warmup_pool_lifecycle.py` with lifecycle + stale detection test
- [ ] All existing MCP tests pass without modification
- [ ] Test report documenting coverage and results

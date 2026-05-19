# Phase 1: Pool Infrastructure

## Objective
Create the `McpWarmupPool` class (`daemon/mcp/warmup_pool.py`) — a standalone, self-contained pool that manages pre-warmed STDIO MCP connections with lifecycle methods for warming up, acquiring, replenishing, health checking, and draining.

## Coupling
- **Depends on**: None (standalone module)
- **Coupling type**: — (root phase)
- **Shared files with other phases**: None yet (new file)
- **Shared APIs/interfaces**: `McpWarmupPool` public methods consumed by Phase 2
- **Why this coupling**: Pool must exist before it can be integrated

## Context
This phase creates a new module with no modifications to existing code. The pool is designed to be consumed by Phase 2, which wires it into the existing MCP layer.

### Key Design Decisions
1. **Pool stores `PooledConnection` dataclasses** — containing (session, stream_cm, tools, server_name, created_at)
2. **Per-server asyncio.Queue** — thread-safe FIFO for connection acquisition
3. **Per-server asyncio.Lock** — prevents concurrent replenish AND protects queue during health check
4. **Replenish concurrency limiter** — `asyncio.Semaphore` caps total concurrent replenish tasks across all servers
5. **Tracked replenish tasks** — `set[asyncio.Task]` tracks all in-flight replenish tasks, cancelled during drain
6. **Singleton pattern** — `get_mcp_warmup_pool()` factory function
7. **Health check via MCP protocol ping** — uses `session.send_ping()` with short timeout, never accesses private SDK internals
8. **Background replenishment** — `asyncio.create_task` after acquire, tracked for cleanup
9. **Create-with-cleanup** — `_create_pooled_connection` always cleans up subprocess on any failure

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `PooledConnection` dataclass | Fields: `session: ClientSession`, `stream_cm: Any`, `tools: list[BaseTool]`, `server_name: str`, `created_at: float` | `daemon/mcp/warmup_pool.py` (new) |
| 2 | Create `McpWarmupPool` class skeleton | `__init__` with `_pools: dict[str, asyncio.Queue[PooledConnection]]`, `_locks: dict[str, asyncio.Lock]`, `_configs: dict[str, McpStdioConfig]`, `_pool_sizes: dict[str, int]`, `_tool_discovery_cache: dict[str, list[BaseTool]]`, `_running: bool`, `_health_task: asyncio.Task \| None`, `_replenish_tasks: set[asyncio.Task]` (tracks all in-flight replenish tasks for drain cleanup), `_replenish_semaphore: asyncio.Semaphore(2)` (caps concurrent replenish across all servers) | `daemon/mcp/warmup_pool.py` (new) |
| 3 | Implement `warmup()` method | For each built-in STDIO server config, spawn N subprocesses in parallel, complete handshake, discover tools, store as `PooledConnection` in queue. Accept optional `pool_size: dict[str, int]` param (default 1 per server). Log progress. Non-fatal: log errors, don't raise. | `daemon/mcp/warmup_pool.py` |
| 4 | Implement `_create_pooled_connection()` | Private method: creates STDIO subprocess, handshake, tool discovery. Returns `PooledConnection`. **CRITICAL**: Must clean up subprocess on any failure — wrap in try/except that calls `streams_cm.__aexit__(exc_type, exc_val, tb)` on failure to prevent orphaned processes. See Implementation Notes for the pattern. | `daemon/mcp/warmup_pool.py` |
| 5 | Implement `acquire(server_name) -> PooledConnection \| None` | Non-blocking: try to get from queue immediately (`get_nowait()`). If empty, return `None`. On successful acquire, fire-and-forget tracked replenish task. **No private SDK internals** — liveness is validated at health check time and by the caller's liveness probe (Phase 2), not in `acquire()`. | `daemon/mcp/warmup_pool.py` |
| 6 | Implement `_replenish(server_name)` | Async background task: acquires `_replenish_semaphore` (caps concurrent replenish across all servers), then per-server lock, creates one new `PooledConnection`, puts it in queue. Logs errors but doesn't propagate. Only runs if `_running` is True and queue size < pool_size. Task is tracked in `_replenish_tasks` set via `_track_replenish_task()` helper (adds on create, removes on done callback). | `daemon/mcp/warmup_pool.py` |
| 7 | Implement `register_server(server_name, config, pool_size=1)` | Registers a built-in STDIO server with its config and desired pool size. Creates queue and lock for that server. Called before `warmup()`. | `daemon/mcp/warmup_pool.py` |
| 8 | Implement `health_check()` | **Per-server**: acquires per-server `asyncio.Lock`, snapshots queue with `get_nowait()` into a temp list, checks each via MCP protocol ping (NOT private SDK internals), puts healthy back, closes dead ones, releases lock. **This prevents acquire() from seeing an empty queue** — callers wait for lock instead of getting `None`. Triggers tracked replenishment for evicted connections. See Implementation Notes for the lock-protected pattern. | `daemon/mcp/warmup_pool.py` |
| 9 | Implement `_health_check_loop(interval)` | Background task: runs `health_check()` every N seconds. Only runs while `_running` is True. | `daemon/mcp/warmup_pool.py` |
| 10 | Implement `drain()` | Sets `_running = False`. Cancels all tracked `_replenish_tasks` and awaits them (prevents orphaned subprocesses from mid-flight replenish). Cancels health check task. Closes all remaining pooled sessions and stream context managers. Awaits completion with overall timeout (15s). Logs each closed connection. | `daemon/mcp/warmup_pool.py` |
| 11 | Implement `get_status() -> dict` | Returns pool status for observability: `{server_name: {"available": N, "pool_size": N, "healthy": bool}}`. Useful for logging and debugging. | `daemon/mcp/warmup_pool.py` |
| 12 | Create `get_mcp_warmup_pool()` singleton factory | Module-level singleton pattern (same as `get_mcp_connection_manager()`). Returns existing or creates new. | `daemon/mcp/warmup_pool.py` |

## Key Files
- `daemon/mcp/warmup_pool.py` — **NEW** — entire pool implementation

## Constraints
- No imports from `daemon.services` or `daemon.manager` (pool is infrastructure, not service layer)
- May import from `daemon.mcp.config` (for `McpStdioConfig`), `daemon.mcp.builtin_servers` (for server definitions)
- May import `mcp` SDK (for `ClientSession`, `stdio_client`, `StdioServerParameters`)
- May import `langchain_mcp_adapters.tools` (for `load_mcp_tools`)
- Must be usable without the pool being active (graceful None returns)

## Implementation Notes

### `_create_pooled_connection()` Pattern (with cleanup on failure)
```python
async def _create_pooled_connection(self, server_name: str) -> PooledConnection:
    config = self._configs[server_name]
    server_params = StdioServerParameters(command=config.command, args=config.args, env=config.env)
    streams_cm = mcp.stdio_client(server_params)
    session = None
    try:
        read_stream, write_stream = await streams_cm.__aenter__()
        session = ClientSession(read_stream, write_stream)
        await session.initialize()
        tools = await load_mcp_tools(session)
        return PooledConnection(
            session=session,
            stream_cm=streams_cm,
            tools=tools,
            server_name=server_name,
            created_at=time.monotonic(),
        )
    except Exception:
        # Clean up to prevent orphaned subprocess
        try:
            if session is not None:
                await session.close()
        except Exception:
            pass
        try:
            await streams_cm.__aexit__(*sys.exc_info())
        except Exception:
            pass
        raise
```

### `acquire()` Pattern (no private SDK access)
```python
async def acquire(self, server_name: str) -> PooledConnection | None:
    if not self._running:
        return None
    pool = self._pools.get(server_name)
    if pool is None:
        return None
    try:
        conn = pool.get_nowait()
    except asyncio.QueueEmpty:
        return None
    # Trigger tracked background replenishment
    self._start_tracked_replenish(server_name)
    return conn

def _start_tracked_replenish(self, server_name: str) -> None:
    """Fire-and-forget replenish with task tracking for drain cleanup."""
    if not self._running:
        return
    task = asyncio.create_task(self._replenish(server_name))
    self._replenish_tasks.add(task)
    task.add_done_callback(self._replenish_tasks.discard)
```

### Health Check Pattern (lock-protected, no queue starvation)
```python
async def health_check(self) -> None:
    for server_name, pool in self._pools.items():
        lock = self._locks[server_name]
        async with lock:  # <-- Blocks acquire() via lock, NOT via empty queue
            # Snapshot queue atomically (no await between gets)
            snapshot = []
            while True:
                try:
                    snapshot.append(pool.get_nowait())
                except asyncio.QueueEmpty:
                    break

            healthy = []
            for conn in snapshot:
                try:
                    # Protocol-level ping — no private SDK internals
                    await asyncio.wait_for(
                        conn.session.send_ping(),
                        timeout=5.0,
                    )
                    healthy.append(conn)
                except Exception:
                    logger.warning(f"Health check failed for pooled {server_name}, discarding")
                    await self._close_connection(conn)

            # Put healthy ones back
            for conn in healthy:
                await pool.put(conn)

            # Replenish if below pool size
            if len(healthy) < self._pool_sizes.get(server_name, 1):
                self._start_tracked_replenish(server_name)
```

### `_replenish()` Pattern (with semaphore and early exit)
```python
async def _replenish(self, server_name: str) -> None:
    if not self._running:
        return
    pool = self._pools.get(server_name)
    if pool is None or pool.qsize() >= self._pool_sizes.get(server_name, 1):
        return  # Already at capacity
    async with self._replenish_semaphore:  # Cap total concurrent replenish
        if not self._running:  # Re-check after await
            return
        async with self._locks[server_name]:
            try:
                conn = await self._create_pooled_connection(server_name)
                await pool.put(conn)
                logger.debug(f"Replenished pool for {server_name}")
            except Exception as e:
                logger.warning(f"Failed to replenish pool for {server_name}: {e}")
```

### `drain()` Pattern (with replenish task cancellation)
```python
async def drain(self) -> None:
    self._running = False

    # Cancel all in-flight replenish tasks to prevent orphaned processes
    for task in self._replenish_tasks:
        task.cancel()
    if self._replenish_tasks:
        await asyncio.gather(*self._replenish_tasks, return_exceptions=True)
    self._replenish_tasks.clear()

    # Cancel health check loop
    if self._health_task and not self._health_task.done():
        self._health_task.cancel()
        try:
            await self._health_task
        except asyncio.CancelledError:
            pass

    # Close all remaining pooled connections
    close_tasks = []
    for server_name, pool in self._pools.items():
        while True:
            try:
                conn = pool.get_nowait()
                close_tasks.append(self._close_connection(conn))
            except asyncio.QueueEmpty:
                break
    if close_tasks:
        await asyncio.wait_for(
            asyncio.gather(*close_tasks, return_exceptions=True),
            timeout=10.0,
        )
    logger.info("MCP warm-up pool drained")
```

## Deliverables
- [ ] `daemon/mcp/warmup_pool.py` with complete `McpWarmupPool` class
- [ ] `PooledConnection` dataclass
- [ ] `get_mcp_warmup_pool()` singleton factory
- [ ] All methods: `register_server`, `warmup`, `acquire`, `drain`, `health_check`, `get_status`
- [ ] `_create_pooled_connection()` with subprocess cleanup on failure (no orphaned processes)
- [ ] `_replenish_tasks: set[asyncio.Task]` tracking with cancellation in `drain()`
- [ ] `_replenish_semaphore: asyncio.Semaphore` capping concurrent replenish tasks
- [ ] Health check uses per-server lock (prevents queue starvation for acquire)
- [ ] No access to private MCP SDK internals (no `_transport`, `_process`, etc.)
- [ ] Module can be imported without side effects

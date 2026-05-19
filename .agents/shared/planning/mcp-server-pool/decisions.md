# Architecture Decisions: MCP STDIO Server Warm-Up Pool

## Decision 1: Transfer Ownership Model
**Decision**: Pool creates connections, transfers ownership to connection_manager on acquire.

**Alternatives Considered**:
1. **Proxy/bridge model**: Pool acts as a proxy between instances and servers. Rejected: adds latency, complexity, single point of failure.
2. **Connection sharing**: Multiple instances share one subprocess. Rejected: STDIO is inherently 1:1.
3. **Tool-level caching only**: Cache just the tool definitions, not connections. Rejected: doesn't eliminate subprocess startup time, which is the main bottleneck.

**Rationale**: Transfer model means connection_manager sees the same data structures. Minimal changes to existing code. Clean separation of concerns.

## Decision 2: asyncio.Queue for Pool Management
**Decision**: Use `asyncio.Queue[PooledConnection]` per server, not a list with locks.

**Rationale**: Queue provides built-in FIFO ordering, thread-safe get/put, and `empty()` / `get_nowait()` for non-blocking acquire. Simpler than managing a list with manual locks.

## Decision 3: Non-Blocking Acquire with Fallback
**Decision**: `acquire()` is non-blocking — returns `None` immediately if pool is empty. Caller falls back to cold-start.

**Alternatives Considered**:
1. **Blocking acquire with timeout**: Wait up to N seconds for a connection. Rejected: adds latency unpredictably, defeats the purpose of instant startup.
2. **Awaitable acquire with replenish**: Block until replenishment creates a new connection. Rejected: same latency issue, and if npx is slow, we'd wait 8-15s anyway.

**Rationale**: Non-blocking with fallback gives the best of both worlds: instant when pool has connections, graceful degradation when it doesn't.

## Decision 4: Background Replenishment (Fire-and-Forget)
**Decision**: After `acquire()`, trigger `asyncio.create_task(replenish())` in the background.

**Rationale**: Keeps the acquire path fast. Replenishment runs asynchronously and the next acquire will find a fresh connection waiting. If replenishment fails, next acquire falls back to cold-start.

## Decision 5: Pool is Enabled by Default, Can Be Disabled
**Decision**: Pool is enabled by default via configuration but can be explicitly disabled. When disabled or unavailable, all code works exactly as before.

**Clarification**: This is NOT "opt-in" — it is "enabled by default, opt-out". The pool activates automatically for all built-in STDIO servers unless the user sets `mcp_pool.enabled: false` in config.

**Rationale**: Zero risk of breaking existing behavior — pool is an optimization that gracefully degrades. Users who want to disable it can do so explicitly. This avoids the confusion of "opt-in" implying disabled by default.

## Decision 6: Health Check via MCP Protocol Ping
**Decision**: Use `session.send_ping()` for health checks.

**Alternatives Considered**:
1. **Process liveness check**: Check `subprocess.returncode`. Rejected: process might be alive but MCP session is dead.
2. **List tools call**: Re-discover tools as health check. Rejected: heavier than ping, unnecessary if tools were already discovered.

**Rationale**: MCP protocol supports ping as a standard method. It's lightweight and validates both process and session health.

## Decision 7: Async Warmup at Startup (Non-Blocking)
**Decision**: Pool warmup runs as `asyncio.create_task()` during daemon startup, not blocking the startup sequence.

**Rationale**: Warmup takes 5-15s. Blocking startup would delay the daemon being ready to accept requests. The pool becomes available as connections are created. First instance spawn might cold-start if pool isn't ready yet, which is acceptable degradation.

## Decision 8: Lock-Protected Health Check (Not Queue-Draining)
**Decision**: Health check acquires per-server `asyncio.Lock` before snapshotting the queue, checks connections, then restores healthy ones — all while holding the lock. `acquire()` callers wait for the lock instead of seeing an empty queue.

**Alternatives Considered**:
1. **Drain queue, check, put back** (original plan): During the drain window (5+ seconds per connection), all `acquire()` calls see an empty queue and fall back to cold-start — defeating the pool's purpose.
2. **Copy-on-write with double-buffer**: Maintain two queues, swap atomically. Rejected: complex, doubles memory.

**Rationale**: Per-server lock is the simplest fix. `acquire()` callers block briefly on the lock during health check (typically <1s total for 1-2 connections) rather than degrading to cold-start. Lock contention is minimal since health checks run infrequently (every 60s).

## Decision 9: Tracked Replenish Tasks with Cancellation
**Decision**: All replenish tasks are tracked in `set[asyncio.Task]` and cancelled during `drain()`. A `asyncio.Semaphore(2)` caps total concurrent replenish tasks across all servers.

**Rationale**: Without tracking, a `drain()` call could leave orphaned subprocesses — a replenish task spawned just before drain starts could create a new subprocess that never gets cleaned up. Semaphore prevents resource exhaustion from too many concurrent npx/uvx processes.

## Decision 10: Liveness Probe Before Transfer
**Decision**: After `pool.acquire()`, perform a quick MCP protocol ping (2-3s timeout) before transferring to connection_manager. If ping fails, close the stale connection and fall back to cold-start.

**Rationale**: A pooled process might die between the last health check and the acquire. Without a probe, a dead session gets transferred to connection_manager and the instance fails when it tries to use it. The 2-3s probe cost is acceptable since it only runs for pooled connections (which are already warm) and catches the race condition.

## Decision 11: Subprocess Cleanup in _create_pooled_connection
**Decision**: Wrap the entire connection creation in try/except with `streams_cm.__aexit__()` on failure.

**Rationale**: If handshake or tool discovery fails, the subprocess was already spawned but the session/stream_cm references would be lost. The try/except ensures the subprocess is always terminated on failure, preventing orphaned processes.

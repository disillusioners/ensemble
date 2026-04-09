# Architecture Decisions: Message Queue Redesign

## Decision Log

### AD-1: SQLite Atomic Claim Pattern (vs FOR UPDATE SKIP LOCKED)

**Decision**: Use `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING *` instead of `FOR UPDATE SKIP LOCKED`.

**Context**: The design doc proposes PostgreSQL-style `FOR UPDATE SKIP LOCKED` for the worker pool. SQLite does not support this syntax — `FOR UPDATE` is silently ignored.

**Alternatives Considered**:
1. `FOR UPDATE SKIP LOCKED` — Not available on SQLite
2. Application-level locking (threading.Lock per instance) — Reintroduces in-memory state
3. `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING *` — **Chosen**
4. `BEGIN IMMEDIATE` + `SELECT` + `UPDATE` — Two-step, still has TOCTOU window (smaller)

**Rationale**: The atomic UPDATE-RETURNING pattern is a single statement under SQLite's write lock. Two concurrent workers cannot both claim the same row because:
- SQLite serializes writes (only one writer at a time via WAL + busy_timeout)
- The inner SELECT sees the latest committed state
- The UPDATE's WHERE clause ensures only status='pending' rows are claimed

**Consequences**: 
- PostgreSQL-compatible (RETURNING supported since PG 8.2)
- SQLite 3.35+ required for RETURNING clause
- No performance penalty vs the alternative
- One statement = one round-trip to DB

---

### AD-2: Workers as Threads with Async Bridge (vs asyncio Tasks)

**Decision**: Workers run as daemon threads that bridge to the asyncio event loop via `asyncio.run_coroutine_threadsafe()`.

**Context**: The current system mixes asyncio and threading (Flaw 5 in design doc). Core processing is async (`async for event in graph.astream()`, `await broadcaster.broadcast()`, `asyncio.Semaphore`). Workers must call async code from threads.

**Alternatives Considered**:
1. asyncio tasks with `asyncio.to_thread()` for DB — More complex, event loop dependency
2. Pure sync threads — Won't work: LangGraph execution, event broadcasting are all async
3. **Threads with async bridge via `run_coroutine_threadsafe()`** — Chosen
4. multiprocessing workers — Overkill for SQLite (single writer anyway)

**Rationale**: 
- SQLAlchemy SQLModel sessions are synchronous (DB claim is sync)
- LangGraph execution and event broadcasting are async
- `asyncio.run_coroutine_threadsafe()` is already the established pattern in the codebase (`manager.py:349`, `events.py:294-322`)
- Workers do sync DB claim, then bridge to async for processing

**Consequences**:
- Worker needs reference to main asyncio event loop (`self._main_loop`)
- TaskProcessor methods must be `async def process_async(task)` not `def process(task)`
- Timeout on `future.result()` prevents stuck workers
- Same pattern already proven in production code <!-- FIX: C1 -->

---

### AD-3: Hybrid SSE (DB + In-Memory for Streaming)

**Decision**: Use database for lifecycle events, in-memory notification for streaming events (content_chunk, thinking, tool_call, tool_complete).

**Context**: Storing every content_chunk in the database would be too expensive (hundreds of events per message). But lifecycle events must survive restart.

**Alternatives Considered**:
1. All events in DB — Too many writes for streaming events
2. All events in memory — Lost on restart (current problem)
3. **Hybrid: DB for lifecycle, memory for streaming** — Chosen
4. Periodic snapshots — Complex, not worth it

**Rationale**: 
- Lifecycle events (message_received, processing_completed, etc.) are low-frequency and critical
- Streaming events (content_chunk) are high-frequency and ephemeral
- Losing a content_chunk is acceptable (client sees gap); losing a completion event is not
- Hybrid gives us best of both: persistence for important events, speed for streaming

**Consequences**:
- SSE implementation more complex (two sources)
- Reconnection can replay lifecycle events but not streaming events
- Need clear documentation of what's persisted vs ephemeral
- Events use cursor-based delivery (`Last-Event-ID`) — no `delivered` boolean, enabling multi-client SSE (multiple browser tabs each track their own position) <!-- FIX: C2 -->

---

### AD-4: Global Feature Flag Cutover (vs Big Bang)

**Decision**: Use a global feature flag (`use_worker_pool`) to switch between old and new message flow for all instances simultaneously.

**Context**: The migration touches core message processing. A big-bang cutover is risky. Per-instance flagging adds complexity with little benefit since all instances share the same processing infrastructure.

**Alternatives Considered**:
1. Big bang — Remove old code, replace with new — Too risky
2. Dual-write (write to both paths) — Double processing risk
3. Feature flag per-instance — Too complex: mixing old/new consumers for different instances in the same process
4. **Global feature flag** — Chosen <!-- FIX: W2 -->
5. Shadow mode (process both, compare results) — Too complex

**Rationale**: 
- Global flag is simpler to implement and reason about
- All instances share the same consumer/worker infrastructure — can't easily mix old and new per instance
- Feature flag allows instant rollback if issues found
- Old path remains available as fallback
- Clean separation between old and new code paths

**Consequences**:
- All instances switch at once (no gradual per-instance rollout)
- Temporary code duplication during migration
- Need to test both paths
- Flag removed in Phase 5

---

### AD-5: Keep InstanceHierarchy Table as Canonical (with denormalized children cache)

**Decision**: Keep the existing `instance_hierarchy` junction table as the **canonical** source of parent-child relationships. Add a `children` TEXT column on the instance as a denormalized cache, updated via application-level hook.

**Context**: The design doc proposes replacing the junction table with a TEXT[] column. SQLite doesn't have native array support, and existing code queries the junction table.

**Alternatives Considered**:
1. TEXT[] stored as JSON array — Awkward queries in SQLite
2. Keep junction table only — No array column
3. **Both: junction table for queries, children array for convenience** — Chosen
4. Replace junction table entirely — Breaking change

**Rationale**:
- SQLite doesn't support TEXT[] natively; would need JSON serialization
- Junction table already works and has queries built around it
- Adding a children column is convenient for quick checks (avoid JOIN) but not primary
- Migration is simpler (add column, don't remove table)
- **instance_hierarchy is canonical** — children column is updated by application-level hook on spawn/complete to stay in sync

**Consequences**:
- Two ways to track children — junction table is authoritative, children[] is a cache
- Application-level hook must update both on spawn and complete
- Junction table can be removed in a future cleanup once all code uses the cache
- Simpler migration path <!-- FIX: W6 -->

---

### AD-6: Poll Interval 0.5s (vs Push-Based)

**Decision**: Workers poll the database every 0.5 seconds.

**Context**: PostgreSQL supports LISTEN/NOTIFY for push-based notification. SQLite does not.

**Alternatives Considered**:
1. PostgreSQL LISTEN/NOTIFY — Not available on SQLite
2. Filesystem watch on SQLite file — Unreliable
3. **Poll every 0.5s** — Chosen
4. Poll every 0.1s — Too aggressive on DB load

**Rationale**:
- 0.5s is responsive enough for user-facing operations (user perceives <1s as instant)
- With WAL mode, polling is read-only and doesn't block writers
- Can tune down if latency is unacceptable
- Simpler than any push mechanism

**Consequences**:
- 0.5s minimum latency for task pickup
- Constant DB read load (mitigated by WAL mode)
- Can be reduced to 0.1s for critical tasks with priority-based polling

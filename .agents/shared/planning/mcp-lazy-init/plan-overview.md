# Plan Overview: MCP Lazy Connection Initialization

## Objective

Implement lazy MCP server connection initialization: register all MCP tool schemas at instance creation time (so the LLM can see and decide to call them), but defer actual server connection/session establishment until the first tool call for that server. This eliminates the ~13s cold-start blocking wait on the instance creation path.

## Scope Assessment

**SMALL** — The change is conceptually simple (defer connection, add a lazy proxy), affects a narrow set of files with clear boundaries, and leverages existing patterns in the codebase. Primarily touches `mcp_service.py`, `tool_adapter.py`, and `connection_manager.py`.

## Approach Summary

**Decouple tool schema registration from server connection.** Currently, `preload_mcp_tools()` blocks for ~13s connecting to all MCP servers and binding sessions into tool coroutines before the instance can be created. Instead:

1. **Schema registration (at instance creation, fast):** Get tool schemas from warmup pool cache (pooled STDIO) or a new persistent schema cache (cold servers). Create "lazy tools" — `StructuredTool` objects whose coroutine defers session resolution to first call.

2. **Connection on first call:** When the LLM calls a tool, the lazy coroutine resolves or creates the MCP session, caches it for subsequent calls, then executes. If connection fails, the error returns naturally to the LLM.

3. **Cleanup unchanged:** `close_connections()` still closes all sessions (including lazily-initialized ones) via `connection_manager.close_instance()`.

This approach **subsumes** all 3 changes from the existing `docs/plans/mcp-cold-start-latency.md`:
- ✅ No blocking wait (Change 1: async preload) — but simpler, no SSE events needed
- ✅ Schema caching built-in (Change 2) — lazy tools are created from cached schemas
- ✅ No probe needed (Change 3) — dead sessions caught naturally at first call

## Context

- **Project:** agents-ensemble
- **Working Directory:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Supersedes:** `docs/plans/mcp-cold-start-latency.md` (proposed 3 changes, more complex)
- **Key constraint:** Session is bound via Python closure in `langchain_mcp_adapters.tools.convert_mcp_tool_to_langchain_tool` — cannot rebind. Must create new tools with lazy coroutines.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Schema Cache + Lazy Tools Core | Build schema cache and lazy tool factory; wire into preload flow | None | — | 3h |
| 2 | Connection Resolution + Cleanup | Implement lazy session resolution on first tool call; update cleanup | Phase 1 | tight | 2h |
| 3 | Tests + Validation | Update existing tests, add new tests, validate latency improvement | Phase 1, 2 | tight | 2h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|-----------|----------|-----------|
| Phase 1 → 2 | **tight** | Phase 2's `McpSessionProvider` is the runtime counterpart to Phase 1's lazy coroutines; they share the session cache/lock |
| Phase 2 → 3 | **tight** | Tests validate the full lazy flow end-to-end |

All phases are sequential — the total change is small enough that sequential is appropriate.

## Architecture Decision: Lazy Coroutine Proxy

### Problem
`langchain_mcp_adapters` binds the MCP session into the tool coroutine via closure: `convert_mcp_tool_to_langchain_tool(session, tool)` creates a `call_tool` coroutine that captures `session` in its closure. We cannot rebind this later.

### Solution: Create tools WITHOUT sessions

Instead of calling `load_mcp_tools(session)` (which requires a live session), we:

1. **Get schemas independently:** From warmup pool's public `get_cached_tool_schemas()` (pooled) or by calling `session.list_tools()` once and caching (cold). We DO NOT call `convert_mcp_tool_to_langchain_tool`.

2. **Build lazy tools manually:** For each schema, create a `StructuredTool` with:
   - `name`: `mcp_{server}_{tool}` (from adapter)
   - `description`: schema description + `[MCP:server]` suffix
   - `args_schema`: from the MCP tool's `inputSchema`
   - `coroutine`: a new lazy function that resolves the session on first call via `McpSessionProvider`

3. **The lazy coroutine** (key innovation):
   ```python
   async def _lazy_mcp_coroutine(**kwargs):
       session = await session_provider.get_session(server_name)
       result = await session.call_tool(tool_name, kwargs)
       return _convert_call_tool_result(result)
   ```

### Shared session cache (critical for correctness)
All tools for the same instance+server **must** share a single session cache dict and asyncio.Lock. This is passed into `_build_lazy_coroutine` from the caller — never created inside. This ensures N tools for the same server = 1 connection, not N.

### Concurrency guard
The double-check-locking pattern in the lazy coroutine (check cache → acquire lock → check cache again → create) is the sole concurrency guard. Two concurrent tool calls to the same server will serialize on the lock; the second will find the session already cached and skip creation.

### Why this works
- **LangGraph only needs schemas** at graph build time (for `llm.bind_tools()`) — it doesn't care about the coroutine's internals
- **Tool invocation** goes through `await tool.coroutine(**args)` — our lazy coroutine handles session resolution transparently
- **Error handling** is natural: if connection fails, the coroutine raises `ToolException`, which LangGraph's `handle_tool_errors=True` catches gracefully

### Why NOT use `convert_mcp_tool_to_langchain_tool` with a lazy session?
Because that function's `call_tool` closure captures `session` at construction time. If we pass `None`, it tries `create_session(connection)` per call — which creates a new session every time, defeating caching. Our approach gives us full control over session lifecycle.

## Key Files

| File | Role | Change Type |
|------|------|-------------|
| `daemon/mcp/tool_adapter.py` | Add lazy tool factory alongside existing `adapt_mcp_tools` | **Modify** |
| `daemon/services/mcp_service.py` | Schema cache + `McpSessionProvider` + simplified preload | **Modify** |
| `daemon/mcp/connection_manager.py` | No new methods needed — reuse `connect_instance()` | **Unchanged** |
| `daemon/tools/instance.py` | Unchanged — `_load_mcp_tools` reads cache as before | **Unchanged** |
| `daemon/manager.py` | Minimal — `spawn_instance_with_mcp` unchanged | **Unchanged** |
| `daemon/mcp/warmup_pool.py` | Add public `get_cached_tool_schemas()` method | **Minor modify** |
| `daemon/routers/mcp_servers.py` | Wire schema cache invalidation into CRUD | **Minor modify** |

## Behavioral Change: Pool Exhaustion Under Lazy Init

**Pool size=1 implication:** The warmup pool defaults to 1 connection per server. With the old eager approach, that single pooled connection was transferred to the instance at preload time. With lazy init, the first tool call acquires from the pool. If a second instance calls a tool for the same server while the first instance holds the connection, the pool will be empty and the second instance falls back to cold-starting its own connection.

This is acceptable because:
- The warmup pool is an **optimization**, not a requirement
- Cold-start on fallback is the same latency as the current eager path (which already cold-starts for non-pooled servers)
- The pool already handles exhaustion via `_replenish` in the background
- If this becomes a bottleneck, operators can increase `MCP_POOL_DEFAULT_POOL_SIZE` or per-server overrides in `Mcp_POOL_SERVERS`

**Future consideration:** Cross-instance session sharing (singleton per server) would eliminate this entirely, but is deferred as it requires reference counting and is a larger refactor.

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Result conversion differs from langchain_mcp_adapters | Med | Low | Import `_convert_call_tool_result` directly from library with `try/except ImportError` fallback; full AudioContent/ResourceLink/EmbeddedResource/structuredContent support |
| Lazy connection fails on first call | Low | Med | Error returned naturally to LLM via ToolException — user's stated preference |
| Schema cache stale after server update | Med | Low | Invalidate on MCP server CRUD via router hooks (concrete wiring in Phase 1 Task 1) |
| Break existing test mocking patterns | Med | Med | Phase 3 dedicates to test updates; conftest mocking stays compatible |
| First tool call latency higher than before | Low | High | Expected — latency shifts from instance creation to first tool call. Measured in Phase 3. User sees instance immediately; first tool call pays connection cost |
| Pool exhaustion under concurrent instances | Low | Med | Falls back to cold-start (same as current behavior for non-pooled). Document pool size tuning. |

## Success Criteria

- [ ] `spawn_instance_with_mcp()` returns in <500ms (no blocking connection wait)
- [ ] First tool call to each MCP server succeeds (lazy connection works)
- [ ] First tool call latency measured and documented (expected: 1-15s depending on transport)
- [ ] Subsequent tool calls reuse the cached session (no re-connection, <100ms)
- [ ] Failed connections return error to LLM naturally (ToolException)
- [ ] All existing MCP tests pass (with appropriate updates)
- [ ] `close_connections()` properly cleans up lazily-initialized sessions
- [ ] Works for all transports: stdio, sse, httpstreamable
- [ ] `tool_call_timeout` still functions correctly
- [ ] Instance restore/recovery works with lazy tools (no stale state)
- [ ] N tools for same server share exactly 1 session (not N connections)

## Tracking

- Created: 2026-06-09
- Last Updated: 2026-06-09 (rev 2 — reviewer feedback)
- Status: draft

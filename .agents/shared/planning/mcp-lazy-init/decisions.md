# Design Decisions: MCP Lazy Connection Initialization

## Decision 1: Create tools WITHOUT using `convert_mcp_tool_to_langchain_tool`

### Context
`langchain_mcp_adapters.tools.convert_mcp_tool_to_langchain_tool(session, tool)` binds the session into the `call_tool` coroutine via closure. We cannot rebind this later.

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **A: Build StructuredTool manually** (chosen) | Full control over session lifecycle; clean separation | Must handle result conversion |
| **B: Pass `connection` dict instead of `session`** | Reuses langchain_mcp_adapters code | Creates new session per call (no caching); relies on library internals |
| **C: Monkey-patch session after creation** | Minimal code change | Fragile; breaks Pydantic model_copy semantics |

### Decision
**Option A.** Build `StructuredTool` manually with our own lazy coroutine.

### Rationale
- Full control over session lifecycle and caching
- No dependency on langchain_mcp_adapters internal closure structure
- The lazy coroutine is simpler and more testable than the langchain adapter's chain
- Result conversion imported from library (see Decision 7)

---

## Decision 2: Schema cache keyed by `server_name` (not config hash)

### Context
The existing plan (`mcp-cold-start-latency.md` Change 2) proposes `_schema_cache` keyed by `(server_name, hash(server.config))` with TTL.

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **A: `server_name` only** (chosen) | Simpler; cache invalidation on CRUD is straightforward | Stale if config changes without CRUD (shouldn't happen) |
| **B: `(server_name, config_hash)` + TTL** | More robust against config drift | More complex; TTL adds timer infrastructure |

### Decision
**Option A.** Key by `server_name` only, invalidate on MCP server CRUD operations (create/update/delete/toggle active).

### Rationale
- MCP server configs only change through the API — CRUD hooks cover all cases
- No config hash infrastructure needed
- Simpler to implement and test
- If someone changes config outside the API, they can restart the daemon

---

## Decision 3: `McpSessionProvider` protocol — single dependency for lazy coroutines

### Context
The lazy coroutine needs access to session resolution (pool + cold start). We must pick ONE pattern.

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **A: Pass callables** (`conn_mgr_getter`, `server_config_getter`) | No coupling to McpService class; testable with lambdas | Many parameters; composition is messy |
| **B: McpSessionProvider protocol** (chosen) | Clean single interface; easy to mock; one dependency | New protocol/class |
| **C: Pass McpService reference directly** | Simplest; full access | Tight coupling; harder to test |

### Decision
**Option B.** Define a `McpSessionProvider` protocol with a single method `get_session(server_name) -> session`. Implement it as `_McpSessionProviderImpl` on `McpService`. The lazy coroutine receives the provider as its single session dependency.

### Rationale
- Single dependency instead of 3+ callables — cleaner `create_lazy_mcp_tools()` signature
- Clear interface: `provider.get_session(server_name) -> session`
- Easy to mock in tests (just mock `get_session`)
- Protocol-based: `_McpSessionProviderImpl` is the concrete implementation, but any mock satisfies the duck type

---

## Decision 4: Pooled servers use warmup pool on first call (optimization)

### Context
For pooled STDIO servers, the warmup pool has pre-warmed connections. The lazy path could either:
1. Try the pool first, then cold-start
2. Always cold-start (simpler but slower)

### Decision
Try pool first, then cold-start. This preserves the performance benefit of the warmup pool for built-in STDIO servers.

### Rationale
- Pool acquire is fast (`queue.get_nowait()`) — no downside to trying
- If pool is empty, fall back to cold start transparently
- Existing warmup pool health checks still run — we just don't eagerly consume at preload time

### Pool size=1 behavioral change (W5)
With default pool size=1, the first instance to call a tool acquires the pooled connection. Subsequent instances fall back to cold-start. This is acceptable because:
- The warmup pool is an optimization, not a guarantee
- Cold-start latency is the same as the current eager path
- Pool replenishment runs in background after acquire
- Operators can increase pool size via `MCP_POOL_SERVERS` config

---

## Decision 5: No changes to `_load_mcp_tools()` or `create_instance_tools()`

### Context
`_load_mcp_tools()` reads from `_tools_cache[instance_id]` synchronously. `create_instance_tools()` calls it and extends the tool list.

### Decision
Leave both unchanged. The lazy tools are stored in `_tools_cache` the same way as before — they're just `StructuredTool` objects with different coroutine implementations.

### Rationale
- Zero risk of breaking the tool assembly pipeline
- `create_help_tool()` and `_apply_tool_filter()` work unchanged
- The lazy behavior is encapsulated entirely in the coroutine
- Minimal diff = minimal regression risk

---

## Decision 6: Error handling — natural ToolException propagation

### Context
The user explicitly said: "If MCP has problems the tool code responds with the problem — it's more natural."

### Decision
All lazy connection errors are surfaced as `ToolException`, which LangGraph's `handle_tool_errors=True` in `ToolNode` catches gracefully and returns to the LLM as a tool error message.

### Rationale
- The LLM can reason about the error and retry or inform the user
- No special error handling infrastructure needed
- Consistent with existing MCP error handling in `_build_timed_coroutine`
- The user's stated preference

---

## Decision 7: Import `_convert_call_tool_result` from library, don't vendor (W2)

### Context
We need to convert MCP `CallToolResult` to LangChain `(content, artifact)` format. The library's `_convert_call_tool_result` handles AudioContent, ResourceLink, EmbeddedResource, and structuredContent. A vendored version would miss these edge cases.

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **A: Import from library** (chosen) | Complete conversion; handles all content types; library maintains it | Depends on private function; may break on library update |
| **B: Vendor minimal version** | No library dependency | Missing AudioContent, ResourceLink, EmbeddedResource, structuredContent — bugs in edge cases |

### Decision
**Option A.** Import `_convert_call_tool_result` from `langchain_mcp_adapters.tools` with `try/except ImportError` fallback.

### Rationale
- The library version (113 lines) handles all MCP content types correctly
- A vendored version (31 lines) would be incomplete — bugs waiting to happen
- `try/except ImportError` protects against library restructuring
- If the private function moves, the fallback provides basic functionality with a clear signal to update the import path

---

## Decision 8: Reuse `connect_instance()` for lazy resolution, no new connection_manager methods (C2)

### Context
The original plan proposed `get_or_create_session()` and `_create_and_store_session()` on `McpConnectionManager`. These would need to correctly handle all transport types by calling `_create_session()`.

### Decision
Don't add new methods to `connection_manager.py`. Use the existing `connect_instance(instance_id, [server])` with a single-element server list.

### Rationale
- `connect_instance()` already handles the full flow: `_create_session()` → `_open_and_track_session()` → store in `_connections`
- It dispatches to all 3 transports correctly
- Passing `[server]` (single-element list) is efficient — no parallelization overhead
- The double-check-locking in `_build_lazy_coroutine._get_session()` prevents duplicate calls
- Less code to maintain, less risk of transport-specific bugs

---

## Decision 9: Shared session cache + lock passed from caller (C1)

### Context
Each call to `_build_lazy_coroutine()` creates a new closure. If the session cache (`dict`) and lock (`asyncio.Lock`) are created inside `_build_lazy_coroutine`, each tool gets its own independent cache — N tools → N connections.

### Decision
The caller (`McpService.preload_mcp_tools()`) creates one `dict` + one `asyncio.Lock` per instance+server, and passes them to `create_lazy_mcp_tools()`, which passes them to every `_build_lazy_coroutine()` call. All tools for the same server share the same dict and lock.

### Rationale
- Guarantees N tools → 1 session (not N)
- The lock serializes concurrent first-calls correctly (double-check locking pattern)
- Session cache is also stored in `McpService._session_caches` for cleanup access
- Testable: create 3 tools, call all 3, verify exactly 1 `get_session` call

---

## Decision 10: Public API for warmup pool schema access (W6)

### Context
The schema cache needs to extract tool schemas from the warmup pool's `_tool_discovery_cache` — a private attribute.

### Decision
Add a public method `get_cached_tool_schemas(server_name)` to `McpWarmupPool` instead of accessing `_tool_discovery_cache` directly.

### Rationale
- Encapsulation: callers shouldn't depend on internal attribute names
- The public method handles name stripping (removing `mcp_` prefix) internally
- If the pool's cache structure changes, only the public method needs updating
- Consistent with the codebase's pattern of public methods for internal state access

---

## Relationship to Existing Plan

The existing plan in `docs/plans/mcp-cold-start-latency.md` proposes 3 changes. This lazy-init approach **supersedes all 3** with a single, more elegant design:

| Existing Change | How Lazy Init Supersedes It |
|----------------|-----------------------------|
| **Change 1: Async preload + SSE events** | Not needed — preload is already fast (schema-only, no connections). No frontend changes required. |
| **Change 2: Per-server schema cache** | Built into the design — `McpService._schema_cache` stores schemas per server. |
| **Change 3: Drop liveness probe** | Not needed — we never eagerly connect, so there's no probe to drop. Dead sessions are caught at first tool call naturally. |

The lazy approach is strictly simpler:
- **No SSE event changes** (no `mcp_ready`, no `mcp_status`)
- **No frontend changes** required
- **No graph re-compilation** — tools are bound at graph build time with lazy coroutines
- **No special "loading" state** — the LLM always sees all tools
- **Natural error handling** — connection failures are tool errors, not system errors
- **No new connection_manager methods** — reuses existing `connect_instance()`

### Latency trade-off
| Metric | Before | After |
|--------|--------|-------|
| Instance creation | ~13s (all servers) | <500ms (schema-only) |
| First tool call per server | ~100ms (session already open) | 1-15s (lazy connection) |
| Subsequent tool calls | ~100ms | ~100ms (session cached) |
| Total time to complete first tool call | ~13s + ~100ms | <500ms + 1-15s (only for used servers) |

The key win: latency is **shifted, not eliminated** — but only for servers the LLM actually calls. Unused servers consume zero resources.

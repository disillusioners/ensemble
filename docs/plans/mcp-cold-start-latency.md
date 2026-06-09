# Plan: Reduce MCP Cold-Start Latency on Instance Open

**Status:** Proposed
**Date:** 2026-06-09
**Target:** Reduce time-to-interactive when opening / creating an instance with multiple MCP servers configured (4 in current setup: zread × 2, context7, + 1 more).

## Problem

Opening or creating an instance is slow whenever multiple MCP servers are configured. Logs show ~13s between `POST /instances` and the graph being ready:

```
11:38:16 daemon.api GET /api/instances/{id}/events 200
11:38:24 mcp.client.streamable_http Received session ID (×3, ~8s after open)
11:38:29 daemon.services.mcp_service Discovered 7 MCP tools from 4 server(s)
11:38:29 daemon.graph [Graph] Vision model configured: vision
```

The user is staring at a loading state for the entire 13s. With more servers or slower handshakes this gets worse.

## Root Cause

The MCP preload runs **synchronously on the request path** of every entry point that creates an instance:

| Entry point | File:Line |
|---|---|
| `POST /instances` | `daemon/routers/instances.py:40-92` |
| Source adapters (Telegram, Slack) | `daemon/sources/mapper.py:260`, `daemon/sources/adapters/slack/thread_manager.py:210` |
| Job processor | `daemon/services/job_processor.py:420,460,541` |
| `invoke_and_wait` | `daemon/utils.py:545` |
| Agent `spawn_instance` tool (no preload) | `daemon/tools/instance.py:460` — bug, should use preload path |

`InstanceManager.spawn_instance_with_mcp` (`daemon/manager.py:1500-1528`) calls `ensure_mcp_preloaded` → `McpService.preload_mcp_tools` (`daemon/services/mcp_service.py:74-171`) and **awaits** it before returning.

### Latency sources, in order of cost

1. **Cold subprocess spawn per instance for non-built-in STDIO servers** — `daemon/mcp/connection_manager.py:187-231`. 5–15s per server. The warmup pool only covers built-in STDIO.
2. **MCP initialize handshake with 3×10s retry** — `daemon/mcp/warmup_pool.py:196-218`.
3. **`load_mcp_tools(session)` per cold server** — `daemon/services/mcp_service.py:208`. 0.5–3s per server.
4. **3s liveness probe on every pooled acquire** — `daemon/services/mcp_service.py:125`. Probed even though the pool already runs a 60s health check.
5. **Sequential `load_mcp_tools` after parallel `connect_instance`** — `daemon/mcp/connection_manager.py:131-134` parallelizes connect, but `_discover_server_tools` runs per server without overlap.
6. **No cross-instance tool-schema cache** — `list_tools()` result is re-fetched per instance, even though MCP tool schemas are essentially static.

## Goals

- Instance open / create returns in **<500ms** for the common case (4 MCP servers, all cached).
- First user turn is not blocked on MCP readiness; tools appear when ready.
- No regression in tool correctness or isolation between instances.

## Non-Goals

- Per-agent MCP server filtering (separate plan).
- Persisting MCP tool lists across daemon restarts.
- Changing the MCP protocol or server implementations.

## Solution

Implement the three highest-impact, lowest-risk changes together. They are independent, all touch only `mcp_service.py` and the spawn path, and together eliminate the synchronous wait on the request path while keeping tool correctness.

### Change 1: Async MCP preload (off the request path)

Return the instance immediately with `mcp_status: "loading"`, then preload in a background task. The graph compiles with an empty/placeholder tool list and tools are bound in when preload finishes.

**Touch points:**
- `daemon/services/mcp_service.py` — add `preload_mcp_tools_async(instance_id)` that schedules the work on the event loop and returns immediately. Existing `preload_mcp_tools` becomes the awaited implementation.
- `daemon/manager.py:1470-1498` — `ensure_mcp_preloaded` becomes non-blocking. If preload is not done, kick it off and return `None` (no tools yet).
- `daemon/services/instance_lifecycle.py:127-164` — `_get_mcp_tool_names` returns `[]` when preload is pending; the system prompt omits the `mcp` tool category until ready.
- `daemon/tools/instance.py:97-109` — `_load_mcp_tools` returns whatever is in cache; on miss returns `[]` (no fallback to blocking preload).
- `daemon/routers/instances.py:40-92` — `POST /instances` returns immediately. Response includes `mcp_status` field.
- SSE event stream — emit a new `mcp_ready` event with the tool list when preload finishes. Frontend already consumes the stream at `/api/instances/{id}/events`.

**Trade-off:** the first LLM turn may run without MCP tools if it fires before preload completes. Acceptable because: (a) most instances are opened for chat, not immediate tool use; (b) the SSE `mcp_ready` event triggers a re-render so the UI updates within seconds; (c) tool calls made before ready are simply not bound — the LLM gets a message about unavailable tools rather than a hang.

### Change 2: Per-server tool-schema cache

MCP tool schemas are essentially static. Cache `adapt_mcp_tools(...)` output in `McpService` keyed by `hash(server.config)` + `server.name`. Only re-discover when config changes (rare — happens only on MCP server CRUD via `daemon/routers/mcp_servers.py`).

**Touch points:**
- `daemon/services/mcp_service.py` — new `_schema_cache: dict[(server_name, config_hash), list[BaseTool]]`. `get_mcp_tools(instance_id)` short-circuits to the cached schema after the session is up.
- `daemon/mcp/connection_manager.py` — on `connect_instance`, after sessions are established, hand off the server name to `McpService` which checks the schema cache first. Only call `load_mcp_tools` on miss.
- `daemon/routers/mcp_servers.py` — invalidate cache entries for a server on create/update/delete (or on `is_active` toggle).

**Trade-off:** if a server's tool list changes at runtime (no config change), the cache becomes stale. Mitigate with a TTL (e.g. 5 min) on schema cache entries, and provide an admin endpoint to force refresh.

### Change 3: Trust the pool's health check, drop the per-acquire liveness probe

The pool already runs a 60s health check (`daemon/mcp/warmup_pool.py:319-355`). The 3s probe in `mcp_service.py:125-130` is redundant and adds latency. If a session is actually dead, the first tool call will fail and trigger a reconnect — much better than paying 3s × N servers of blocking latency on the request path.

**Touch points:**
- `daemon/services/mcp_service.py:116-142` — drop `_probe_connection()` call on the pooled path. Rely on pool's health check + on-demand reconnect on tool-call failure.

**Trade-off:** a dead session is detected at first tool use instead of at acquire. In practice this is fine because MCP servers going stale mid-session is rare and the tool-call error path already handles it.

## Implementation Order

1. **Change 3 (drop liveness probe)** — smallest, lowest risk, immediate win. Ship first.
2. **Change 2 (schema cache)** — local to `McpService`, easy to test, eliminates per-instance `list_tools()` cost.
3. **Change 1 (async preload)** — touches the most call sites and the SSE contract. Ship last after frontend is ready to consume the new `mcp_ready` event and `mcp_status` field.

## Validation

### Per-change validation

- **Change 3:** open an instance 20 times; measure p50 / p95 latency at `POST /instances`. Expect drop equal to `N_servers × ~50ms` (the normal-case probe cost).
- **Change 2:** open two instances back-to-back; second open should skip `load_mcp_tools` calls entirely (verify in logs: no `Discovered N MCP tools` line on second open when servers are unchanged). Then update an MCP server config and verify the cache invalidates and tools refresh.
- **Change 1:** open an instance, verify `POST /instances` returns in <500ms with `mcp_status: "loading"`, then SSE delivers `mcp_ready` event within a few seconds. Send a message before `mcp_ready` arrives and verify it does not hang; verify the LLM turn completes once tools are bound.

### Regression checks

- All existing `tests/` must still pass — in particular `tests/integration/` and any test that exercises `preload_mcp_tools`.
- The agent `spawn_instance` tool (`daemon/tools/instance.py:460`) currently bypasses preload — verify the test suite still passes (this is a pre-existing bug; out of scope for this plan to fix, but flag it).

### Manual smoke test

1. Configure 4 MCP servers (zread × 2, context7, one more).
2. Restart daemon.
3. Time `POST /instances` to first user-message-sendable state. Target: <500ms.
4. Verify all 4 servers' tools are bound within 10s via SSE `mcp_ready` event.
5. Open a second instance immediately. Verify the second open reuses the cached schemas (no second `load_mcp_tools` roundtrip in logs).

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Frontend blocks waiting for `mcp_ready` | Low | Medium | Frontend already consumes SSE; document the new event and add a fallback timeout that re-enables input after 30s even without `mcp_ready` |
| Schema cache serves stale tools | Low | High | TTL of 5 min; admin endpoint to force refresh; invalidate on MCP server CRUD |
| First LLM turn fires before tools are bound | Medium | Low | Document expected behavior; tools become available on subsequent turns. Optional: queue the first user message until `mcp_ready` |
| Async preload errors are silent | Medium | Medium | Log preload failures at WARN with instance_id; emit SSE `mcp_error` event so the UI can show "MCP tools unavailable" |
| Concurrent instance opens race on shared session (future work) | N/A | N/A | This plan does not share sessions; that's Change #2 from the original options list, deferred |

## Out of Scope (Future Work)

These are intentionally not part of this plan; track separately:

- **Cross-instance session sharing (singleton per server)** — biggest absolute win but requires ref-counting and is a larger refactor. Plan as Change 4 once the simpler changes ship and prove out.
- **Extend warmup pool to HTTP/SSE and user STDIO** — `daemon/mcp/warmup_pool.py` is built-in-STDIO-only.
- **Parallelize `load_mcp_tools` with session creation** — overlap network I/O.
- **Per-agent MCP server filtering** — agent-level `allowed_servers` field in `meta.json`.
- **Await warmup on daemon startup** — block "ready" signal on warmup completion.
- **Fix `spawn_instance` agent tool to use preload path** — `daemon/tools/instance.py:460` bug.

## File-by-File Change Summary

| File | Change |
|---|---|
| `daemon/services/mcp_service.py` | Add `preload_mcp_tools_async`; add `_schema_cache`; drop `_probe_connection` on pooled path |
| `daemon/manager.py:1470-1528` | `ensure_mcp_preloaded` becomes non-blocking |
| `daemon/services/instance_lifecycle.py:127-164` | `_get_mcp_tool_names` returns `[]` when pending |
| `daemon/tools/instance.py:97-109` | `_load_mcp_tools` returns `[]` on miss, no blocking fallback |
| `daemon/mcp/connection_manager.py` | Hand off to `McpService` schema cache before calling `load_mcp_tools` |
| `daemon/routers/mcp_servers.py` | Invalidate schema cache on create/update/delete/activate |
| `daemon/routers/instances.py:40-92` | Return immediately with `mcp_status` field |
| SSE event schema | New `mcp_ready` and `mcp_error` events |
| `frontend/` (consumer) | Handle new `mcp_status` field and `mcp_ready` event |

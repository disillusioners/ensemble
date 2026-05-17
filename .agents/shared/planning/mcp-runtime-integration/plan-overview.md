# Plan Overview: MCP Runtime Integration

## Objective
Integrate MCP (Model Context Protocol) client functionality so that when an agent instance is spawned, all active MCP servers from the database are connected to, their tools discovered, converted to LangChain `BaseTool` format, and injected into the agent's tool list alongside built-in tools.

## Scope Assessment
**LARGE** — New module (`daemon/mcp/`), new service, modifications to core lifecycle, dependency additions, and testing. Spans 3 modules with a new subsystem being created. Estimated 1-2 days of focused work.

## Context
- **Project**: agents-ensemble (ensemble)
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Current state**: MCP server CRUD API and DB model are complete. No MCP client code exists. No `mcp` package is installed. Tools are never loaded from MCP servers.
- **Key constraint**: `spawn_instance()`, `_restore_instance()`, and `create_instance_tools()` are **synchronous** functions. MCP connection is inherently **async**. Must bridge this gap by preloading async, then reading cached results synchronously.

## Key Technical Decisions

### DEC-001: Package Choice — `langchain-mcp-adapters`
Use the `langchain-mcp-adapters` package (maintained by LangChain-AI) rather than raw `mcp` SDK. This package provides `ClientSession` → LangChain `BaseTool` conversion out of the box, supporting both stdio and SSE/streamable-http transports. It wraps the official `mcp` Python SDK internally.

**Rationale**: Avoids writing custom tool adapter code. Battle-tested with LangGraph. Reduces Phase 1 scope significantly.

### DEC-002: Sync-Async Bridge — Async Preload, Sync Cache Read
MCP tool discovery happens in an **async preload step** called by the async caller **before** invoking the sync `spawn_instance()` or `get_instance()`. Results are cached in `McpService._tools_cache`. The sync `create_instance_tools()` reads from this cache.

**Why NOT `MainLoopBridge.run_async()`**: `spawn_instance()` is always called from the event loop thread (4 async callers call sync directly, not via `asyncio.to_thread()`). `run_coroutine_threadsafe()` + `future.result()` **deadlocks** when called from the event loop thread itself. The preload-then-read pattern avoids this entirely.

**Caller pattern** (applied to all 4 callers):
```python
# In async caller, BEFORE sync spawn:
await manager._mcp_service.preload_mcp_tools(instance_id)
# Then sync spawn reads from cache:
instance_id = manager.spawn_instance(...)
```

### DEC-003: Tool Naming — `mcp_{server_name}_{tool_name}`
All MCP tools are namespaced as `mcp_{server_name}_{tool_name}` (e.g., `mcp_github_create_issue`). This prevents collisions with built-in tools like `bash`, `read_file`, etc. The server name is slugified (lowercase, hyphens to underscores).

### DEC-004: Connection Lifecycle — Per-Instance Connections
Each instance gets its own MCP client session per server. Connections are opened during preload, stored in `McpConnectionManager`, and closed during `terminate_instance()`. No shared connection pool — simpler, better isolation, matches the per-instance graph model.

### DEC-007: Tools Frozen at Spawn Time
MCP tools are discovered and injected at instance spawn/restore time. If an MCP server is added, removed, or reconfigured after an instance is running, the running instance's tool list does **not** change. The instance must be terminated and re-spawned to pick up config changes.

**Rationale**: Dynamic tool injection mid-graph-execution would require graph rebuilding, re-binding tools to LLM, and checkpoint reconciliation — far too complex for MVP. Document as known limitation.

## Architecture

```
daemon/
├── mcp/                          ← NEW MODULE
│   ├── __init__.py               ← Public API: get_mcp_connection_manager()
│   ├── config.py                 ← Pydantic models for MCP server config schemas
│   └── connection_manager.py     ← Per-instance MCP connection lifecycle
├── services/
│   └── mcp_service.py            ← NEW SERVICE — preload, cache, discover, cleanup
├── services/instance_lifecycle.py ← MODIFIED — cleanup on terminate
├── services/instance_messaging.py ← MODIFIED — conditional MCP preload before restore
├── services/job_processor.py     ← MODIFIED — MCP preload before spawn_instance
├── daemon/utils.py               ← MODIFIED — MCP preload before spawn_and_send
├── routers/instances.py          ← MODIFIED — MCP preload before spawn_instance
├── manager.py                    ← MODIFIED — wire McpService, shutdown cleanup
├── tools/_tool_registry.py       ← MODIFIED — MCP-aware category expansion in filter
└── tools/instance.py             ← MODIFIED — read MCP tools from cache in create_instance_tools
```

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | **Foundation: MCP Client Module** | Install packages, create config schema, connection manager | None | — | 4h |
| 2 | **Service Layer: MCP Service** | Create McpService with preload/cache/discover, wire into Manager | Phase 1 | loose | 3h |
| 3 | **Runtime Integration: Lifecycle Hooks** | Inject MCP preload into all callers, cleanup on terminate, fix tool filter | Phase 1 + 2 | tight | 4h |
| 4 | **Testing & Resilience** | Unit tests, integration tests, error handling hardening | Phase 1 + 2 + 3 | loose | 3h |

### Coupling Assessment

| From → To | Coupling | Reason | Scheduling |
|-----------|----------|--------|------------|
| Phase 1 → Phase 2 | **loose** | Phase 2 imports from `daemon/mcp/` but doesn't modify those files | Can start coding Phase 2 as soon as Phase 1 interfaces are defined |
| Phase 2 → Phase 3 | **tight** | Phase 3 modifies `instance_lifecycle.py`, callers, and `instance.py`, calling `McpService` methods directly | Must wait for Phase 2 completion |
| Phase 3 → Phase 4 | **loose** | Tests verify behavior but don't modify implementation | Can pipeline (start Phase 4 tests as Phase 3 completes) |
| Phase 1 → Phase 4 | **loose** | Unit tests for Phase 1 modules can be written independently | Can start Phase 1 tests in parallel with Phase 2 |

**Parallelization opportunity**: Phase 1 unit tests can run alongside Phase 2. Phase 4 unit tests for Phase 1/2 can start before Phase 3 completes.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| MCP server unreachable during spawn | Instance creation blocked | Wrap all MCP operations in try/except; log errors; continue with built-in tools only |
| Sync-async bridge complexity | Subtle bugs, event loop conflicts | Async preload BEFORE sync call; no MainLoopBridge needed; add integration tests |
| Tool name collisions | Agent confusion, wrong tool called | Enforce `mcp_` prefix; validate uniqueness at registration time |
| Connection leaks on crash | Resource exhaustion | Pop cache before closing connections; add cleanup in terminate; ensure terminate always closes |
| MCP SDK version incompatibility | Runtime errors | Pin `langchain-mcp-adapters` version; test with sample MCP servers |
| Large number of MCP tools | Context window overflow, slow bind_tools | Cap max tools per instance; log warning when approaching limits |
| MCP server disconnects mid-session | Tool invocation fails at runtime | Log error in ToolNode; document as MVP limitation (DEC-007: tools frozen at spawn) |
| Stdio subprocess orphaning on SIGKILL | Zombie processes | Graceful shutdown in connection manager; document requirement for SIGTERM |

## Success Criteria
- [ ] `pip install` adds `mcp` and `langchain-mcp-adapters` successfully
- [ ] Config schema validates both stdio and SSE server configs
- [ ] MCP tools appear in agent's tool list at runtime (visible in tool registry)
- [ ] MCP tools can be invoked by the LLM during agent execution
- [ ] Instance termination properly closes all MCP connections
- [ ] Unreachable MCP servers don't block instance creation
- [ ] MCP tools are namespaced with `mcp_` prefix
- [ ] `_restore_instance()` also loads MCP tools correctly
- [ ] Tool filtering works correctly for MCP category (deny, allow, wildcard)
- [ ] Unit tests cover: config parsing, connection lifecycle, tool conversion, error handling
- [ ] Integration test with mock MCP server shows end-to-end tool invocation

## Integration Points (Verified by Code Exploration)

### 1. Tool Creation — `daemon/tools/instance.py:580-605`
```python
# After all built-in tools are added (line 596):
tools.append(help_tool)

# MCP TOOLS INJECTION POINT — before scan/filter
# INSERT: Read MCP tools from preloaded cache
mcp_tools = _load_mcp_tools(manager, instance_id)
tools.extend(mcp_tools)

# Existing: Scan and filter (lines 600-603)
scan_tools_for_full_docs(tools)
tools = _apply_tool_filter(tools, agent_id)
```

### 2. Tool Binding — `daemon/graph.py:469,481,585`
- `llm_with_tools = ThinkingChatOpenAI(**vision_config).bind_tools(tools)` (line 469)
- `llm_standard.bind_tools(tools)` (line 482)
- `ToolNode(tools)` (line 585)
All three binding points receive the same `tools` list — no changes needed here.

### 3. Spawn Callers (async preload injection points)
All 4 callers are async and must add `await preload_mcp_tools()` **before** calling sync `spawn_instance()`:
- `routers/instances.py:48` — `async def create_instance()` FastAPI route (generate UUID first if not provided)
- `daemon/utils.py:532` — `async spawn_and_send()` utility
- `daemon/services/job_processor.py:201,233,265` — job processing
- `daemon/services/job_feedback_observer.py:345` — feedback observer

### 4. Restore Path — `daemon/services/instance_messaging.py:386,672`
Only 2 call sites actually trigger `_restore_instance()` (both in `instance_messaging.py`). Add **conditional** preload — only when instance is NOT already in memory. Do NOT add preload to other `get_instance()` callers (existence checks for terminate, pause, get_messages, etc.) as they never trigger restore.

### 5. Termination — `daemon/services/instance_lifecycle.py:289-384`
After cleanup step 2 (LiveHub cleanup, line 325):
```python
# INSERT: Close MCP connections for this instance
await self._mcp_service.close_connections(instance_id)
```

### 6. Service Registration — `daemon/manager.py:470-530`
After lifecycle service initialization (line 521):
```python
# MCP service
self._mcp_service = McpService(manager=self)
```

### 7. Tool Filtering — `daemon/tools/instance.py:44-101`
`resolve_tool_filter()` expands category names via `list_tools_by_category()`, which reads `_tool_metadata`. Dynamic MCP tools aren't in the static registry — must add prefix-based matching for `"mcp"` category.

### 8. Manager Shutdown — `daemon/manager.py:1558-1608`
Add MCP cleanup step to ordered shutdown sequence:
```python
("shutdown_mcp", self._mcp_service.close_all_connections()),
```

## Tracking
- Created: 2025-05-17
- Last Updated: 2025-05-17
- Status: draft

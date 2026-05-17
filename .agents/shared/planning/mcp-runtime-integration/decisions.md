# Architecture Decisions

## DEC-001: Package Choice — `langchain-mcp-adapters`

**Decision**: Use `langchain-mcp-adapters` package rather than raw `mcp` SDK.

**Context**: Need to convert MCP tools to LangChain `BaseTool` format for use with LangGraph's `bind_tools()` and `ToolNode`.

**Options considered**:
1. **Raw `mcp` SDK** — Full control, more code to write and maintain
2. **`langchain-mcp-adapters`** — Maintained by LangChain-AI, purpose-built for this exact use case
3. **Custom adapter** — Maximum flexibility, highest maintenance burden

**Rationale**: `langchain-mcp-adapters` provides `ClientSession` → LangChain tool conversion out of the box. It supports both stdio and SSE transports. Reduces Phase 1 scope significantly. Risk: relatively new package, but it's the official LangChain integration.

**Consequences**: 
- Depends on both `mcp` (transitive) and `langchain-mcp-adapters` 
- Tied to LangChain's tool abstraction model
- Simpler implementation, less custom code

---

## DEC-002: Sync-Async Bridge — Async Preload, Sync Cache Read

**Decision**: MCP tool discovery happens in an async preload step called by each async caller BEFORE invoking the sync `spawn_instance()` or `get_instance()`. Results are cached in `McpService._tools_cache`. The sync `create_instance_tools()` reads from this cache.

**Context**: `spawn_instance()`, `_restore_instance()`, and `create_instance_tools()` are all synchronous. MCP operations (connect, list tools) are inherently async. All callers of these sync functions are async.

**Options considered**:
1. **`asyncio.run()` inside sync code** — Creates new event loop, conflicts with existing loop
2. **Make `create_instance_tools()` async** — Cascading change to spawn flow, high risk
3. **Async preload before sync call, sync cache read** — Clean separation, minimal changes
4. **`MainLoopBridge.run_async()` inside sync spawn** — **DEADLOCK**: `run_coroutine_threadsafe()` + `future.result()` deadlocks when called from the event loop thread. All 4 callers of `spawn_instance()` are async calling sync directly (not via `asyncio.to_thread()`), so `spawn_instance()` runs on the event loop thread.

**Rationale**: Option 3 is the only safe approach. Each async caller adds a one-line preload call before the sync spawn/restore. No event loop tricks needed. The preload runs naturally on the event loop, then the sync code reads cached results.

**Consequences**:
- `McpService` must maintain a tools cache (memory overhead: minimal)
- Cache must be populated before `create_instance_tools()` is called (ordering constraint)
- Cache must be cleaned up on instance termination (already planned)
- All async callers must be modified to add preload call (5+ call sites)

---

## DEC-003: Tool Naming — `mcp_{server_name}_{tool_name}`

**Decision**: All MCP tools are prefixed with `mcp_{server_name}_` followed by the original tool name.

**Context**: MCP tool names may collide with built-in tools (e.g., a "read_file" tool from a filesystem MCP server).

**Format**: `mcp_{server_name}_{tool_name}` where `server_name` is slugified (lowercase, hyphens → underscores, special chars removed).

**Examples**:
- Server "github", tool "create_issue" → `mcp_github_create_issue`
- Server "file-server", tool "read" → `mcp_file_server_read`
- Server "My Server", tool "search" → `mcp_my_server_search`

**Rationale**: Three-part naming (prefix + server + tool) provides:
- Clear namespace separation from built-in tools
- Easy to identify which server a tool comes from
- Simple prefix filtering in tool allow/deny logic

**Consequences**:
- LLM sees longer tool names (minor context window impact)
- Tool names are deterministic and predictable
- Easy to grep logs for MCP tool usage

---

## DEC-004: Connection Lifecycle — Per-Instance Connections

**Decision**: Each instance gets its own MCP client session per server. No shared connection pool.

**Context**: Instances are independent execution contexts with their own graphs, state, and lifecycle. An instance may run for minutes or hours.

**Options considered**:
1. **Shared connection pool** — More efficient, but complex session management, potential state leakage
2. **Per-instance connections** — Simple, clean isolation, natural lifecycle mapping
3. **Lazy connections** — Connect on first tool call — adds latency to first call

**Rationale**: Per-instance connections match the existing architecture (each instance has its own graph, state, checkpointer). Simpler to reason about. Clean cleanup on terminate. Memory cost is acceptable (a few TCP connections per MCP server per instance).

**Consequences**:
- More connections than a pool approach
- Clean lifecycle management — close on terminate
- No state sharing between instances (intended behavior)
- Need to handle connection failures per-instance independently

---

## DEC-005: No Permission Gating (MVP)

**Decision**: All active MCP tools are available to all agents. No per-agent filtering for MVP.

**Context**: Agent definitions have `tools.allow` and `tools.deny` fields. These currently filter by category (bash, filesystem, etc.).

**Scope**: MCP tools are treated as category `"mcp"`. Category-based filtering works via prefix matching (see DEC-002 implementation in Phase 2):
- `tools.allow: ["*"]` → MCP tools included ✅
- `tools.deny: ["mcp"]` → all MCP tools excluded ✅
- `tools.allow: ["bash"]` → MCP tools excluded (only bash allowed) ✅

**Future**: Per-server or per-tool filtering based on agent config. Requires schema extension in `meta.json`.

---

## DEC-006: Config Schema — Discriminated Union

**Decision**: Use Pydantic discriminated union on `transport` field to validate different MCP server config types. Validate at CRUD time (create/update API endpoints), not at spawn time.

**Schema**:
```json
// stdio
{"transport": "stdio", "command": "npx", "args": ["@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "..."}}

// SSE
{"transport": "sse", "url": "http://localhost:8080/sse"}

// streamable-http
{"transport": "streamable-http", "url": "http://localhost:8080/mcp"}
```

**Rationale**: Discriminated union provides clear validation, good error messages, and type-safe access to transport-specific fields. Validating at CRUD time catches config errors early with clear user-facing error messages, rather than silently failing at spawn time.

**Backward compatibility**: Existing `McpServer.config` entries without a `transport` field will fail validation at CRUD time with a clear error message directing the user to add the `transport` field.

---

## DEC-007: Tools Frozen at Spawn Time (MVP)

**Decision**: MCP tools are discovered and injected at instance spawn/restore time. If an MCP server is added, removed, or reconfigured after an instance is running, the running instance's tool list does **not** change. The instance must be terminated and re-spawned to pick up config changes.

**Rationale**: Dynamic tool injection mid-graph-execution would require:
- Rebuilding the compiled graph
- Re-binding tools to the LLM via `.bind_tools()`
- Checkpoint reconciliation for in-flight tool calls
- Thread-safe mutation of a running LangGraph

This is far too complex for MVP. The frozen-at-spawn model is simple, predictable, and matches how built-in tools work (they're also fixed at spawn time).

**Known limitations documented**:
- Tool invocation after MCP server disconnects mid-session: The ToolNode will return an error when trying to invoke a disconnected MCP tool. The LLM will see this error and can report it to the user. No automatic reconnection is attempted.
- Stdio subprocess orphaning on SIGKILL: The connection manager closes sessions gracefully via `session.close()`. If the daemon is SIGKILLed, stdio subprocesses may be orphaned. Document graceful shutdown requirement (SIGTERM).
- Dynamic config changes: Not reflected in running instances. Document in user-facing docs.

---

## DEC-008: Consolidated McpService (No Separate tool_loader.py)

**Decision**: All MCP business logic (connect, discover, convert, cache, cleanup) lives in `daemon/services/mcp_service.py`. No separate `daemon/mcp/tool_loader.py` module.

**Context**: The original plan had a `tool_loader.py` in `daemon/mcp/` and a `McpService` in `daemon/services/`. This split created unclear ownership: which module owns tool discovery? Which owns LangChain conversion?

**Rationale**: Single responsibility is better served by having one class own the full business logic flow. The `daemon/mcp/` module provides low-level primitives (config, connections). `McpService` orchestrates them. This avoids dead code and circular imports.

**Consequences**:
- `daemon/mcp/` is purely infrastructure: config models + connection manager
- `daemon/services/mcp_service.py` is the business logic layer
- Clear ownership of tool discovery and conversion

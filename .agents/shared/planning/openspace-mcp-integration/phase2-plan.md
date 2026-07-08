# Phase 2: Timeout & Dependency Management

## Objective
Wire the per-server timeout override from `BuiltinServerDefinition.tool_call_timeout` into the warmup pool registration flow, so OpenSpace's long-running `execute_task` tool gets a 900s timeout instead of the 120s default. Also document the dependency installation strategy.

## Coupling
- **Depends on**: Phase 1 (needs `tool_call_timeout` property on `BuiltinServerDefinition`)
- **Coupling type**: **loose** — Phase 2 modifies `McpWarmupPool` and `manager.py`, Phase 1 touches `builtin_servers/`. They connect only via the `tool_call_timeout` property interface.
- **Shared files with other phases**: `daemon/mcp/builtin_servers/base.py` (Phase 1 adds the property, Phase 2 consumes it)
- **Shared APIs/interfaces**: `BuiltinServerDefinition.tool_call_timeout` property
- **Why this coupling**: The timeout value is defined on the definition (Phase 1) but consumed during pool registration (Phase 2). Clean interface boundary.

## Context

### Current Timeout Flow
```
config.yaml (mcp_pool.tool_call_timeout: 120)
    ↓
InstanceManager._init_warmup_pool()
    ↓
McpWarmupPool(tool_call_timeout=120)  ← single global timeout
    ↓
adapt_mcp_tools(server_name, tools, tool_call_timeout=120)  ← applied per-tool
```

### Problem
`adapt_mcp_tools()` receives a single `tool_call_timeout` from the pool. All servers get the same timeout. OpenSpace's `execute_task` can run for 20+ minutes (20 iterations × 120s/iteration). A 120s timeout kills it mid-execution.

### Solution
Per-server timeout override. The warmup pool already creates per-server configs (`_configs` dict). We extend it to store per-server tool timeouts, and `adapt_mcp_tools` uses the server-specific value when available.

### `timeout=0` Semantics (S2)

In `tool_adapter.py:292-293` and `:319`, `tool_call_timeout=0` means **"disable timeout wrapping entirely"** — not "use default." The per-server override lookup must preserve this:

```python
server_timeout = getattr(definition, 'tool_call_timeout', None)
# If server_timeout is 0, it means "no timeout" — preserve as-is
# If server_timeout is None, fall back to pool default
effective_timeout = server_timeout if server_timeout is not None else pool_default
```

Do **not** use `server_timeout or pool_default` — that treats `0` as falsy and replaces it with the default.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `_tool_call_timeouts` dict to `McpWarmupPool` | Maps `server_name → timeout_seconds`. Defaults to pool's global `_tool_call_timeout` if not in dict. | `daemon/mcp/warmup_pool.py` |
| 2 | Extend `register_server()` signature | Add optional `tool_call_timeout: int \| None = None` parameter. If provided, stores in `_tool_call_timeouts`. | `daemon/mcp/warmup_pool.py` |
| 3 | Update `_create_pooled_connection()` | When calling `adapt_mcp_tools()`, use `self._tool_call_timeouts.get(server_name, self._tool_call_timeout)` instead of the hardcoded `self._tool_call_timeout`. | `daemon/mcp/warmup_pool.py` |
| 4 | Update `_init_warmup_pool()` in manager.py | Read `definition.tool_call_timeout` and pass to `pool.register_server()`. | `daemon/manager.py` (lines ~1043-1047) |
| 5 | Handle HTTP/SSE timeout | In `McpService.preload_mcp_tools()`, move per-server timeout lookup inside the per-server loop (before `create_lazy_mcp_tools()` at line 478). Add `_get_per_server_timeout()` helper. | `daemon/services/mcp_service.py` |
| 6 | Write tests | Per-server timeout stored and applied; servers without override use default; OpenSpace gets 900s. | `tests/unit/mcp/test_warmup_pool.py` |
| 7 | Document dependency installation | Add "Prerequisites" section to innate skill (Phase 3) + standalone install doc. | `docs/openspace-setup.md` (new) |

## Detailed Implementation: `register_server()` Update

```python
# In McpWarmupPool.__init__():
self._tool_call_timeouts: dict[str, int] = {}  # NEW

# In McpWarmupPool.register_server():
def register_server(
    self,
    server_name: str,
    config: McpStdioConfig,
    pool_size: int = DEFAULT_POOL_SIZE,
    tool_call_timeout: int | None = None,  # NEW
) -> None:
    # ... existing code ...
    if tool_call_timeout is not None:
        self._tool_call_timeouts[server_name] = tool_call_timeout

# In _create_pooled_connection():
timeout = self._tool_call_timeouts.get(server_name, self._tool_call_timeout)
tools = adapt_mcp_tools(server_name, tools, tool_call_timeout=timeout)
```

## Detailed Implementation: `_init_warmup_pool()` Update

> **Note:** Phase 1 Task 7 already changes `manager.py:1033` from `get_base_config()` to `build_config({})`. This Phase 2 change adds the `tool_call_timeout` parameter to the same `register_server()` call. Both changes touch the same function but different lines — they compose cleanly.

```python
# In manager.py _init_warmup_pool(), around line 1043:
# (Line 1033 already changed to build_config({}) by Phase 1 Task 7)
for definition in registry.get_all():
    # ... existing checks ...

    # NEW: Read per-server timeout override
    server_timeout = getattr(definition, 'tool_call_timeout', None)
    
    stdio_config = McpStdioConfig(**config_dict)
    pool.register_server(
        name, stdio_config,
        pool_size=pool_size,
        tool_call_timeout=server_timeout,  # NEW
    )
```

## HTTP/SSE Cold-Discovery Timeout

For non-STDIO servers (OpenSpace in remote mode), the warmup pool is not used. The timeout must be applied in the **cold-discovery lazy tool creation path**.

**Critical**: The cold-discovery path does NOT use `adapt_mcp_tools()`. It uses `create_lazy_mcp_tools()` in `daemon/mcp/tool_adapter.py:262`. The injection point is **inside** the per-server loop in `preload_mcp_tools()` (`daemon/services/mcp_service.py:446-490`), before the `create_lazy_mcp_tools()` call at line 478.

### Current Code (problematic)

```python
# daemon/services/mcp_service.py, preload_mcp_tools()
tool_call_timeout = self._get_tool_call_timeout()  # line 430 — OUTSIDE loop

for server in servers:                             # line 446
    # ... schema lookup ...
    lazy_tools = create_lazy_mcp_tools(            # line 478
        server_name=server.name,
        schemas=schema_dicts,
        session_provider=session_provider,
        shared_session_cache=instance_session_state[server.name]["cache"],
        shared_session_lock=instance_session_state[server.name]["lock"],
        tool_call_timeout=tool_call_timeout,        # line 488 — uses GLOBAL, not per-server
    )
```

### Fixed Code

Move the per-server timeout lookup **inside** the loop, before `create_lazy_mcp_tools()`:

```python
# daemon/services/mcp_service.py, preload_mcp_tools()
tool_call_timeout = self._get_tool_call_timeout()  # line 430 — global default (keep)

for server in servers:                             # line 446
    # ... schema lookup ...

    # NEW: Per-server timeout override — look up definition for this server
    server_timeout = self._get_per_server_timeout(server.name)
    effective_timeout = server_timeout if server_timeout is not None else tool_call_timeout
    # Note: effective_timeout=0 means "disable timeout wrapping entirely" (not "use default").
    #       See tool_adapter.py:292-293. Preserve 0 as-is when definition requests it.

    lazy_tools = create_lazy_mcp_tools(
        server_name=server.name,
        schemas=schema_dicts,
        session_provider=session_provider,
        shared_session_cache=instance_session_state[server.name]["cache"],
        shared_session_lock=instance_session_state[server.name]["lock"],
        tool_call_timeout=effective_timeout,        # CHANGED: per-server override
    )
```

### Helper Method to Add

```python
def _get_per_server_timeout(self, server_name: str) -> int | None:
    """Look up per-server timeout override from BuiltinServerDefinition.

    Returns None if the server is not a builtin or has no timeout override.
    The caller falls back to the global tool_call_timeout when None.
    """
    from daemon.mcp.builtin_servers import get_registry
    definition = get_registry().get_by_name(server_name)
    if definition is None:
        return None
    return getattr(definition, 'tool_call_timeout', None)
```

**Why this matters**: The `create_lazy_mcp_tools()` function wraps each tool's coroutine with `asyncio.timeout()` (see `tool_adapter.py:319`). If `tool_call_timeout=0`, no timeout wrapping is applied. This per-server override must pass through the correct function and preserve the `0` semantics.

## Dependency Management Strategy

### Approach: User Responsibility + Graceful Failure

**Rationale:** Bundling OpenSpace as a pip dependency would add heavy deps (LiteLLM, Flask, rank_bm25) to ensemble's requirements. OpenSpace runs in a separate subprocess (STDIO) or separate server (HTTP), so there's no shared process space.

**User installation:**
```bash
pip install openspace-ai
# Set credentials
export OPENSPACE_LLM_API_KEY=sk-xxx
export OPENSPACE_MODEL=openrouter/anthropic/claude-sonnet-4.5
```

**Graceful failure:**
- `_bootstrap_builtin_servers()` already has per-server try/except
- If `python3 -m openspace.mcp_server` subprocess fails to start, warmup pool logs error
- Agent tool calls to OpenSpace tools return `ToolException` with clear error message

**Error message design**: When `execute_task` fails due to missing `OPENSPACE_LLM_API_KEY`, the ToolException should read:
```
ToolException: "OpenSpace execute_task failed: OPENSPACE_LLM_API_KEY not set.
Set it in your .env file. The build_config() override injects it into the
subprocess env via explicit os.environ read (MCP SDK does not auto-inherit
full os.environ — only a 6-var POSIX whitelist).
For remote mode, configure credentials on the OpenSpace instance directly."
```
The error should guide the user to the fix, not just report the failure.

**Docker consideration:**
- Document in a Dockerfile snippet: `RUN pip install openspace-ai` in the ensemble container
- For remote mode: no dependency needed at all

## Known Limitations

### Concurrent Execution Scaling (Reviewer Note 1)
With `pool_size=1` (default), multiple agents calling `execute_task` simultaneously will exhaust the warmup pool. The second concurrent call falls back to cold-start, spawning an **additional** OpenSpace subprocess. Each subprocess consumes its own LLM tokens (OpenSpace runs its own LLM agent internally). For high-concurrency deployments, increase `pool_size` in config or use remote mode.

### Eager Schema Warmup Delay (Reviewer Note 2)
For STDIO mode, `eager_warm_schemas()` (called from `_warmup_and_report`) will try to prime the schema cache during the background warmup window. OpenSpace's subprocess startup is slow (Python import + LiteLLM initialization). If warmup fails or is delayed, the first `preload_mcp_tools()` call falls through to `_discover_schemas_cold()` — blocking the first `spawn_instance` that uses OpenSpace tools. This is acceptable for initial integration; users who need faster startup should use remote mode.

## Key Files

| File | Purpose | Action |
|------|---------|--------|
| `daemon/mcp/warmup_pool.py` | Per-server timeout storage + application | **MODIFY** |
| `daemon/manager.py` | Pass timeout from definition to pool | **MODIFY** (~5 lines) |
| `daemon/services/mcp_service.py` | Cold-discovery timeout for HTTP/SSE | **MODIFY** (~3 lines) |
| `daemon/mcp/builtin_servers/base.py` | `tool_call_timeout` property (done in Phase 1) | (Phase 1) |
| `docs/openspace-setup.md` | Installation guide | **NEW** |
| `tests/unit/mcp/test_warmup_pool.py` | Timeout tests | **MODIFY** |

## Constraints
- Existing servers (webfetch, context7) must continue using 120s default — no behavior change
- `tool_call_timeout` property returning `None` means "use pool default" — backward compatible
- Per-server timeout must apply to BOTH warmup pool (STDIO) and cold discovery (HTTP/SSE) paths
- Must not block startup if OpenSpace is not installed

## Deliverables
- [ ] `McpWarmupPool` supports per-server `tool_call_timeout`
- [ ] `_init_warmup_pool()` reads `definition.tool_call_timeout` and passes to pool
- [ ] Cold-discovery path uses per-server timeout for HTTP/SSE servers
- [ ] OpenSpace tool calls get 900s timeout (not 120s)
- [ ] Existing servers unaffected (120s default preserved)
- [ ] `docs/openspace-setup.md` with install instructions

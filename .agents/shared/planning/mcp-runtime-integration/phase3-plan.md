# Phase 3: Runtime Integration — Lifecycle Hooks

## Objective
Wire MCP tool preloading into all instance spawn/restore callers, and MCP connection cleanup into the termination and shutdown flows. This is the "make it work end-to-end" phase that connects all the pieces.

## Coupling
- **Depends on**: Phase 1 (MCP client module) + Phase 2 (McpService)
- **Coupling type**: **tight** — directly modifies `instance_lifecycle.py`, `instance.py`, all spawn callers, and cleanup code
- **Shared files with other phases**: `daemon/services/instance_lifecycle.py`, `daemon/tools/instance.py`, `daemon/routers/instances.py`, `daemon/utils.py`, `daemon/services/job_processor.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/instance_messaging.py`, `daemon/manager.py`
- **Shared APIs/interfaces**: `McpService.preload_mcp_tools()`, `McpService.get_mcp_tools()`, `McpService.close_connections()`
- **Why this coupling**: Integration phase — must modify the exact same files that spawn/terminate instances.

## Context
- **Spawn flow** (`instance_lifecycle.py`):
  1. Line 176: `tools = create_instance_tools(self._manager, instance_id, resolved_agent_id)` — **sync**
  2. Line 196: `graph = build_instance_graph(tools=tools, ...)` — **sync**
  3. The `spawn_instance()` method itself is **sync** but called from async context
- **Restore flow** (`instance_lifecycle.py`):
  1. Line 573: `tools = create_instance_tools(self._manager, instance_id, meta.agent_id)` — **sync**
  2. `_restore_instance()` is **sync**, called from `get_instance()` which is **sync**, called from async contexts
- **Terminate flow** (`instance_lifecycle.py:289-384`):
  - Line 325: `await self._manager._live_hub.cleanup_instance(instance_id)`
  - Multiple cleanup steps follow
- **Tool creation** (`daemon/tools/instance.py:580-605`):
  - Line 596: `help_tool = create_help_tool(tools, agent_id)` — must come AFTER MCP tools are added
  - Line 600: `scan_tools_for_full_docs(tools)` — scans all tools including MCP ones
  - Line 603: `tools = _apply_tool_filter(tools, agent_id)` — filters based on agent config

### The Sync-Async Problem — SOLVED

The core challenge: `spawn_instance()` and `create_instance_tools()` are sync, but MCP operations are async.

**⚠️ DEADLOCK WARNING**: `MainLoopBridge.run_async()` uses `run_coroutine_threadsafe()` + `future.result()`. When `spawn_instance()` runs on the event loop thread itself (which it always does — all 4 callers are async calling sync directly), this **deadlocks**. The event loop is blocked by `future.result()` waiting for the coroutine to run on the same event loop.

**Solution: Async preload BEFORE sync call (DEC-002 Option 3)**

All callers of `spawn_instance()` and `get_instance()` are async. The preload step is added to each caller:

```
Async caller                           Sync functions
─────────────                          ──────────────
await mcp_service.preload_mcp_tools()  ← async, runs on event loop
    ↓
manager.spawn_instance()               ← sync, reads cached tools
    └─ create_instance_tools()         ← sync, calls get_mcp_tools() (cache read)
```

**No `MainLoopBridge` needed**. The async preload runs naturally on the event loop. The sync code reads from cache.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Add MCP tool loading to `create_instance_tools()`** | In `daemon/tools/instance.py`, after line 596 (`tools.append(help_tool)`), add: `mcp_tools = _load_mcp_tools(manager, instance_id)` and `tools.extend(mcp_tools)`. The `_load_mcp_tools()` helper calls `manager._mcp_service.get_mcp_tools(instance_id)` — sync cache read. **Ordering dependency**: must come BEFORE `scan_tools_for_full_docs()` and `_apply_tool_filter()` — the tool filter depends on `_tool_metadata` being populated by the scan, which must include MCP tools. Add code comment documenting this ordering. | `daemon/tools/instance.py` (modified) |
| 2 | **Add MCP preload to all spawn callers** | Add `await manager._mcp_service.preload_mcp_tools(instance_id)` **before** `manager.spawn_instance()` in all 4 async callers: (a) `routers/instances.py:48` — generate UUID first if not provided, then preload; (b) `daemon/utils.py:532`; (c) `daemon/services/job_processor.py:201,233,265`; (d) `daemon/services/job_feedback_observer.py:345`. Each wrapped in try/except — if preload fails, log warning and continue. | Multiple files (modified) |
| 3 | **Add MCP preload before instance restore** | Only 2 call sites actually trigger `_restore_instance()` — both in `daemon/services/instance_messaging.py` (lines 386 and 672). Add conditional preload: check if instance is already in memory; if not (restore needed), call `await preload_mcp_tools()`. **Do NOT** add preload to existence-check callers (terminate, pause, get_messages, routers) — they don't trigger restore and would waste connections. | `daemon/services/instance_messaging.py` (modified) |
| 4 | **Add MCP cleanup to `terminate_instance()`** | In `daemon/services/instance_lifecycle.py`, after line 325 (`cleanup_instance()`), add: `await self._manager._mcp_service.close_connections(instance_id)`. This ensures MCP sessions are closed when instances terminate. | `daemon/services/instance_lifecycle.py` (modified) |
| 5 | **Add MCP cleanup to Manager shutdown** | In `daemon/manager.py` shutdown method, add MCP cleanup step to ordered sequence: `("shutdown_mcp", self._mcp_service.close_all_connections())`. Ensures all MCP connections are properly closed on daemon shutdown. | `daemon/manager.py` (modified) |
| 6 | **Add MCP tools to help system** | Verify `scan_tools_for_full_docs()` correctly scans MCP tools. Category inference already works (`mcp_*` prefix → `mcp` category). Ensure MCP tools have `_full_doc_` or description for help display. | No changes expected |

## Key Files

### Modified Files
| File | Change | Lines Affected |
|------|--------|----------------|
| `daemon/tools/instance.py` | Add MCP tool loading in `create_instance_tools()` | ~596-600 |
| `daemon/routers/instances.py` | Add preload before spawn (generate UUID first) | ~48 |
| `daemon/utils.py` | Add preload before spawn_and_send | ~532 |
| `daemon/services/job_processor.py` | Add preload before spawn_instance | ~201, 233, 265 |
| `daemon/services/job_feedback_observer.py` | Add preload before spawn_instance | ~345 |
| `daemon/services/instance_messaging.py` | Add conditional preload before get_instance (restore path only) | ~386, 672 |
| `daemon/services/instance_lifecycle.py` | Add cleanup in `terminate_instance()` | ~325 |
| `daemon/manager.py` | Add MCP cleanup step in shutdown sequence | ~1587 |

## Detailed Design

### 1. Tool Loading in `create_instance_tools()` (`daemon/tools/instance.py`)

Insert between line 596 and 598:

```python
    # Add help tool (must be last so it knows about all other tools)
    help_tool = create_help_tool(tools, agent_id)
    tools.append(help_tool)

    # ── NEW: Load MCP tools from preloaded cache ──
    # IMPORTANT: MCP tools MUST be injected BEFORE scan_tools_for_full_docs()
    # because _apply_tool_filter() depends on _tool_metadata being populated
    # by the scan, which must include MCP tools for correct category filtering.
    mcp_tools = _load_mcp_tools(manager, current_instance_id)
    if mcp_tools:
        logger.info(f"Injecting {len(mcp_tools)} MCP tools for instance {current_instance_id[:8]}")
        tools.extend(mcp_tools)

    # Scan tools to populate _tool_metadata before filtering
    scan_tools_for_full_docs(tools)
```

New helper function:

```python
def _load_mcp_tools(manager, instance_id: str) -> list:
    """Load MCP tools from preloaded cache.

    Returns:
        List of LangChain tools from MCP servers. Empty list if
        not preloaded or on error.
    """
    try:
        if hasattr(manager, '_mcp_service') and manager._mcp_service:
            return manager._mcp_service.get_mcp_tools(instance_id)
    except Exception as e:
        logger.warning(f"Failed to load MCP tools: {e}")
    return []
```

### 2. MCP Preload in Spawn Callers

Apply the same pattern to all 4 async callers. Each generates the `instance_id` upfront (if not provided), then preloads, then calls sync spawn.

**Example: `routers/instances.py`** — generate UUID first, then preload:

```python
async def create_instance(instance_create: InstanceCreate, request: Request) -> InstanceInfo:
    manager = _get_manager(request)
    try:
        # Generate instance_id upfront so MCP preload can use it
        instance_id = instance_create.instance_id or str(uuid.uuid4())

        # ── NEW: MCP preload (async, before sync spawn) ──
        if hasattr(manager, '_mcp_service') and manager._mcp_service:
            try:
                await manager._mcp_service.preload_mcp_tools(instance_id)
            except Exception as e:
                logger.warning(f"MCP preload failed: {e}")

        instance_id = manager.spawn_instance(
            agent_id=instance_create.agent_id,
            instance_id=instance_id,
            project_id=instance_create.project_id,
        )
    except ValueError as e:
        ...
```

**Other callers** (utils, job_processor, feedback_observer) already have explicit `instance_id` — preload is straightforward:

```python
# daemon/utils.py spawn_and_send()
# daemon/services/job_processor.py
# daemon/services/job_feedback_observer.py

if hasattr(manager, '_mcp_service') and manager._mcp_service:
    try:
        await manager._mcp_service.preload_mcp_tools(instance_id)
    except Exception as e:
        logger.warning(f"MCP preload failed for {instance_id[:8]}: {e}")

instance_id = manager.spawn_instance(...)
```

### 3. MCP Preload Before Instance Restore

`get_instance()` triggers `_restore_instance()` only when the instance is NOT already in memory. Most callers of `get_instance()` are simple existence checks (terminate, pause, get_messages, send_message, stream_events) — they should NOT trigger MCP preload. Adding preload everywhere would waste time opening unnecessary connections.

**Only 2 call sites actually trigger restores**, both in `daemon/services/instance_messaging.py`:
- **Line 386** — `run_instance()` — first message to an instance, may trigger restore
- **Line 672** — `resume_instance()` — resume a stopped instance, may trigger restore

At these sites, add **conditional preload** — only if the instance is not already in memory:

```python
# daemon/services/instance_messaging.py (lines 386 and 672)
# Only preload if instance needs restore (not already in memory)
if instance_id not in self._manager.instances:
    if hasattr(self._manager, '_mcp_service') and self._manager._mcp_service:
        try:
            await self._manager._mcp_service.preload_mcp_tools(instance_id)
        except Exception as e:
            logger.warning(f"MCP preload failed for restore {instance_id[:8]}: {e}")

graph = self._manager.get_instance(instance_id)  # triggers _restore_instance() if needed
```

**Do NOT add preload to these callers** (existence checks, not restore triggers):
- `routers/instances.py:183,209,248` — GET/DELETE/PATCH instance (existence checks)
- `routers/messages.py:33,94,128` — message routes (send/get/stream)
- `daemon/services/instance_messaging.py:1050` — internal check, not a restore path
- `daemon/services/job_processor.py:190` — instance existence check
- `daemon/tools/instance.py:180` — sync helper `_resolve_instance_id()`

**Why this is safe**: If an instance is already in memory, it already has MCP tools from its original spawn. No preload needed. If it's being restored, the conditional preload ensures MCP tools are available before `_restore_instance()` calls `create_instance_tools()`.

**Idempotency**: If `preload_mcp_tools()` is called multiple times for the same instance_id (e.g., spawn + restore), the second call overwrites the cache. This is safe since it produces the same tools.

### 4. MCP Cleanup in `terminate_instance()` (`instance_lifecycle.py`)

Insert after line 325:

```python
        # 2. Clean up live hub connections for this instance
        await self._manager._live_hub.cleanup_instance(instance_id)

        # 2.5. Close MCP connections for this instance
        if hasattr(self._manager, '_mcp_service') and self._manager._mcp_service:
            try:
                await self._manager._mcp_service.close_connections(instance_id)
            except Exception as e:
                logger.warning(f"MCP cleanup failed for {instance_id[:8]}: {e}")
```

### 5. Manager Shutdown Cleanup

Add to the ordered shutdown sequence in `daemon/manager.py` (line ~1587):

```python
        steps = [
            ("stop_sources", self.stop_sources(timeout=grace_period)),
            ("cancel_active_requests", self._cancel_all_active_requests()),
            ("wait_inflight", self._wait_for_inflight(grace_period)),
            ("shutdown_worker_pool", asyncio.to_thread(self.shutdown_worker_pool)),
            ("shutdown_event_bus", self._event_bus.shutdown()),
            ("shutdown_mcp", self._mcp_service.close_all_connections()),  # ← NEW
        ]
```

### 6. Help System

`scan_tools_for_full_docs()` in `_tool_registry.py` already handles MCP tools correctly:
```python
category = tool_name.split('_')[0] if '_' in tool_name else 'general'
```
Tool `mcp_github_create_issue` → category `mcp`. **No code change needed.**

## Error Handling Strategy

| Scenario | Behavior |
|----------|----------|
| MCP server unreachable | Log warning, skip that server, continue with others |
| MCP server returns invalid tools | Log warning, skip invalid tools, continue with valid ones |
| Config parsing fails for a server | Log error with server name, skip that server |
| All MCP servers fail | Instance starts with built-in tools only, log error summary |
| MCP preload fails entirely | Log warning, spawn continues with empty cache → built-in tools only |
| MCP cleanup fails on terminate | Log warning, continue with rest of cleanup |
| MCP server disconnects mid-session | Tool invocation fails in ToolNode, LLM sees error (documented as MVP limitation) |

## Constraints
- Must not break existing instance spawn/terminate flows
- Must be backward compatible — existing agents without MCP config work unchanged
- MCP feature must be gracefully degraded — no hard dependency on MCP servers being available
- All new code must follow existing logging patterns (instance IDs truncated to 8 chars)
- **No `MainLoopBridge.run_async()`** — would deadlock from event loop thread
- Preload must be idempotent (safe to call multiple times for same instance)

## Deliverables
- [ ] MCP tools injected into `create_instance_tools()` output (with ordering comment)
- [ ] Async preload added to all 4 spawn callers (UUID generated first in FastAPI route)
- [ ] Conditional async preload added to 2 restore sites in `instance_messaging.py`
- [ ] No preload added to existence-check callers (terminate, pause, get_messages, routers)
- [ ] MCP cleanup in `terminate_instance()`
- [ ] MCP cleanup in Manager shutdown sequence
- [ ] Tool filtering handles MCP category correctly (deny, allow, wildcard)
- [ ] Help system shows MCP tools with correct category
- [ ] Manual test: spawn instance with MCP server configured → tools appear in graph
- [ ] Manual test: terminate instance → MCP connections close cleanly
- [ ] Manual test: restore instance → MCP tools appear in restored graph

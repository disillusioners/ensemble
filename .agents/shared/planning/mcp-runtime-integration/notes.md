# Working Notes

## Exploration Findings

### Instance Lifecycle Flow (Verified)
```
spawn_instance() [sync, line ~97 in instance_lifecycle.py]
    │
    ├─ Load agent definition, resolve paths
    ├─ load_and_cache_prompt() → system_prompt
    ├─ create_instance_tools(manager, instance_id, agent_id) → tools  [sync, line 176]
    │      └─ Creates all built-in tools, wraps with workdir, filters by agent config
    │      └─ Returns list[BaseTool]
    │
    ├─ build_instance_graph(tools=tools, ...) → graph  [sync, line 196]
    │      └─ build_instance_llms() → llm.bind_tools(tools)  [line 469, 481]
    │      └─ ToolNode(tools)  [line 585]
    │
    └─ Store graph in manager.instances dict
```

### Restore Instance Flow (Verified)
```
get_instance(instance_id) [sync, line 515 in instance_lifecycle.py]
    │
    ├─ Check in-memory cache → return if found
    ├─ Check database → raise KeyError if not found
    └─ _restore_instance(instance_id, meta) [sync, line 546]
         ├─ load_and_cache_prompt()
         ├─ create_instance_tools(manager, instance_id, agent_id) [sync, line 573]
         ├─ build_instance_graph(tools=tools, ...) [sync]
         └─ Store in instances dict
```

### Async Callers of spawn_instance (VERIFIED — NO asyncio.to_thread)
There are **4 callers** of `spawn_instance()`, all async calling sync directly:

1. **`routers/instances.py:48`** — `async def create_instance()` FastAPI route
2. **`daemon/utils.py:532`** — `async spawn_and_send()` utility
3. **`daemon/services/job_processor.py:201,233,265`** — job processing
4. **`daemon/services/job_feedback_observer.py:345`** — feedback observer

**Key insight**: All callers are async but call `manager.spawn_instance()` (sync) directly — no `asyncio.to_thread()`. This means `spawn_instance()` runs on the **event loop thread**. Using `MainLoopBridge.run_async()` from here would **deadlock**.

### Async Callers of get_instance (VERIFIED)
`get_instance()` is sync, called from async contexts that may trigger `_restore_instance()`:

1. **`routers/instances.py:183,209,248`** — FastAPI route handlers (GET instance)
2. **`routers/messages.py:33,94,128`** — message route handlers
3. **`daemon/services/instance_messaging.py:386,672,1050`** — messaging service
4. **`daemon/tools/instance.py:180`** — spawn_instance tool (from within an agent)
5. **`daemon/services/job_processor.py:190`** — job processor

Same pattern applies: preload in async caller, then sync call reads cache.

### Tool System Details
- All tools: `@tool` decorator → `StructuredTool` instances
- `@register_tool_category("name")` sets `_tool_category` attribute
- `scan_tools_for_full_docs()` populates `_tool_metadata` dict
- `_apply_tool_filter()` uses agent's `tools.allow` / `tools.deny` from `meta.json`
- Category inference: `tool_name.split('_')[0]` → category (e.g., `mcp_*` → `mcp`)

### Tool Filtering — The Dynamic MCP Problem (VERIFIED)
`resolve_tool_filter()` (lines 44-101 in `daemon/tools/instance.py`):
1. Calls `list_tools_by_category()` → reads `_tool_metadata` dict
2. Expands category names to individual tool names from that dict
3. Dynamic MCP tools are NOT in `_tool_metadata` (it's populated from static `@register_tool_category` decorators)
4. Result: `"mcp"` category maps to empty list → filtering doesn't work

**Fix**: Add prefix-based expansion for `"mcp"` category using `all_tool_names` parameter.

### Termination Flow
```
terminate_instance(instance_id) [async, line 289]
    ├─ Cascade to children
    ├─ Cancel active requests
    ├─ Cancel graph task
    ├─ cleanup_instance() (LiveHub)
    ├─ Remove from instances dict
    ├─ Remove job watches
    ├─ Update DB status → "terminated"
    ├─ Stream status change
    ├─ Release project locks
    ├─ Mark job as cancelled
    └─ Publish lifecycle event
```

### Service Pattern
- Constructor: `__init__(self, manager: Manager, ...)`
- TYPE_CHECKING imports to avoid circular deps
- Google docstrings on all public methods
- Access repos via `self._manager._xxx_repository`

### Key Dependencies (from pyproject.toml)
- Python >= 3.11
- langgraph >= 0.3.0
- langchain-core >= 0.3.14
- langchain-openai >= 0.2.14
- fastapi >= 0.115.6
- pydantic >= 2.10.0
- aiosqlite >= 0.20.0
- sqlmodel >= 0.0.22
- No MCP packages currently installed

### Manager Shutdown (VERIFIED)
`daemon/manager.py:1558-1608` — `async def shutdown()` with ordered steps:
```python
steps = [
    ("stop_sources", self.stop_sources(timeout=grace_period)),
    ("cancel_active_requests", self._cancel_all_active_requests()),
    ("wait_inflight", self._wait_for_inflight(grace_period)),
    ("shutdown_worker_pool", asyncio.to_thread(self.shutdown_worker_pool)),
    ("shutdown_event_bus", self._event_bus.shutdown()),
    # INSERT: ("shutdown_mcp", self._mcp_service.close_all_connections()),
]
# Then: self.cleanup()
```

### Verified Findings (Post-Draft-Review)

#### Tool Category Inference (VERIFIED)
`scan_tools_for_full_docs()` in `_tool_registry.py:164-167` already handles MCP tools:
```python
category = tool_name.split('_')[0] if '_' in tool_name else 'general'
```
Tool `mcp_github_create_issue` → category `mcp`. **No code change needed** for category inference.

### Open Questions / TBD
- [ ] Whether `langchain-mcp-adapters` supports streamable-http transport — verify during implementation
- [ ] MCP SDK version compatibility with Python 3.11
- [ ] Max number of MCP tools before performance degrades — monitor during testing

### Resolved Questions (Post-Review)

#### Q: Can MainLoopBridge.run_async() work from sync spawn_instance()?
**A: NO — DEADLOCK.** `spawn_instance()` runs on the event loop thread (all 4 callers are async calling sync directly). `run_coroutine_threadsafe()` + `future.result()` deadlocks when the current thread IS the event loop thread. Solution: async preload before sync call (DEC-002 Option 3).

#### Q: Does _restore_instance() need MCP tools?
**A: YES.** `_restore_instance()` calls `create_instance_tools()` at line 573. Without MCP preload, restored instances get zero MCP tools. Same preload-then-call pattern applies to all callers of `get_instance()`.

#### Q: Does tool filtering work for dynamic MCP tools?
**A: NO — needs fix.** `resolve_tool_filter()` expands categories from static `_tool_metadata`. Dynamic MCP tools aren't in that registry. Fix: prefix-based matching for `"mcp"` category (Phase 2, Task 5).

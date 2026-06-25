# MCP Tools Missing on Restored Instances — Investigation Report

**Date:** 2026-05-21
**Status:** Root cause identified, not fixed

## Root Cause

**The API router endpoints call `get_instance()` WITHOUT `ensure_mcp_preloaded()` first.** This triggers `_restore_instance()`, which builds the graph WITHOUT MCP tools. Then when `_process_message_with_tracking()` later calls `ensure_mcp_preloaded()`, it short-circuits because the instance is already in `self.instances`.

### The Sequence:

1. User sends message to existing instance after restart
2. **API endpoint** (`routers/messages.py:32-34`) calls `manager.get_instance(instance_id)` for existence check — **NO preload**
3. This triggers `_restore_instance()` which builds graph with empty MCP tools (cache is empty)
4. Graph (without MCP tools) is cached in `self.instances[instance_id]`
5. Message is enqueued for processing
6. Worker picks up message, calls `_process_message_with_tracking()`
7. Line 705: `await self._manager.ensure_mcp_preloaded(instance_id)` — **SHORT-CIRCUITS** because instance is already in `self.instances`
8. Line 707: `graph = self._manager.get_instance(instance_id)` — returns the cached graph (without MCP tools)
9. Instance runs WITHOUT MCP tools

### Key Code:

**`ensure_mcp_preloaded` short-circuit (manager.py:1033-1055):**
```python
if instance_id in self.instances:
    return  # ← SKIPS preload because already restored (without tools)
```

**API router existence check (routers/messages.py:32-34):**
```python
try:
    manager.get_instance(instance_id)  # ← Triggers restore WITHOUT preload
except KeyError:
    raise HTTPException(...)
```

## All `get_instance()` Call Sites (without preload)

| File | Line | Function | Preloads MCP? |
|------|------|----------|---------------|
| `routers/messages.py` | 32-34 | `send_message` (router) | ❌ NO |
| `routers/messages.py` | 93-95 | `get_message_status` | ❌ NO |
| `routers/messages.py` | 127-129 | `stream_events` | ❌ NO |
| `routers/instances.py` | 190 | `terminate_instance` | ❌ NO |
| `routers/instances.py` | 216 | `pause_instance` | ❌ NO |
| `routers/instances.py` | 255 | `get_messages` | ❌ NO |
| `tools/instance.py` | 214 | `_resolve_instance_id` | ❌ NO |
| `services/job_processor.py` | 190 | `_check_orphan_jobs` | ❌ NO |
| `services/instance_messaging.py` | 398 | `send_message` | ✅ YES |
| `services/instance_messaging.py` | 707 | `_process_message_with_tracking` | ✅ YES |

## Fix Options

### Option A: Fix `ensure_mcp_preloaded` to check MCP cache (RECOMMENDED)

Change the short-circuit to also check if MCP tools are cached:
```python
# Instead of just checking self.instances
if instance_id in self.instances and cached_tools:
    return
```

### Option B: Add preload to `_restore_instance` itself

Make `_restore_instance` async and call `ensure_mcp_preloaded` inside it. This ensures ANY restore path gets MCP tools.

### Option C: Add preload to all router call sites

Add `ensure_mcp_preloaded` before every `get_instance()` call in routers. This is more defensive but more verbose.

## Fix Complexity: SIMPLE

- **Scope:** 1-2 files, ~5-10 lines changed
- **Risk:** Low — additive change, doesn't break existing flows
- **Core LangGraph impact:** NONE — fix is in orchestration layer, not graph construction
- **Estimated effort:** 30 minutes including testing

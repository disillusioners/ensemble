# MCP Restore After Restart — Lesson Learned

**Date**: 2026-05-21
**Commit**: 43e208b (`fix/mcp-tools-not-available-to-llm`)

## The Bug
`ensure_mcp_preloaded()` in `daemon/manager.py` had an overly aggressive early-return:
```python
if instance_id in self.instances:
    return  # Skip if already loaded
```

This meant instances restored from checkpoint (SQLite) after a daemon restart would be in memory but without MCP tools cached. The method would skip them, so the LLM would never get MCP tools.

## The Fix
Check if the in-memory instance actually has cached MCP tools before skipping:
```python
if instance_id in self.instances:
    if self._mcp_service:
        cached = self._mcp_service.get_mcp_tools(instance_id)
        if cached:
            return  # Has tools — truly no need to preload
    else:
        return  # No MCP service — nothing to preload
```

## Pattern: "In Memory ≠ Fully Loaded"
A common pattern in checkpoint-based systems: an instance can be "in memory" (restored from DB) but not "fully loaded" (missing runtime state like MCP connections, tool caches). Always check for the specific runtime state you need, not just presence in a collection.

## Also Found
The commit had a docstring indentation error — the docstring inside `ensure_mcp_preloaded()` was at column 0 instead of indented. This was caught by unit tests (Python syntax error) and fixed in quick fix commit `e36d76e`.

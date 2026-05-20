# MCP Warmup Pool Error Fix — Quick Fix Lesson

**Date**: 2026-05-20
**Branch**: `fix/mcp-warmup-pool-errors`
**Commit**: `3878289`

## Issue Found During Testing
The original fix handled `Exception` and `asyncio.CancelledError` via isinstance check, but any other `BaseException` subclass (beyond KeyboardInterrupt/SystemExit) would silently fall through to `pool.put(result)`, putting an exception object into the pool instead of a connection.

## Fix Applied
Added an explicit re-raise clause in `daemon/mcp/warmup_pool.py`:
```python
elif isinstance(result, BaseException):
    raise result
```

This ensures any BaseException subclass not caught by the isinstance check propagates up rather than silently polluting the pool.

## Testing Pattern
- Used mock exception classes for KeyboardInterrupt/SystemExit tests to avoid pytest's Ctrl+C handling interfering with test execution
- The `test_get_status` test needed assertion update since the fix changed `healthy` from always-True to conditionally based on `qsize() > 0`

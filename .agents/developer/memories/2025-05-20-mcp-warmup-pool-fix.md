# MCP Warmup Pool Fix — 2026-05-20

## Key Learnings

### exc_info Outside except Blocks
- `exc_info=True` only works inside an `except` block (uses `sys.exc_info()` which returns `(None, None, None)` outside).
- When logging exceptions from `asyncio.gather(return_exceptions=True)`, use the 3-tuple form: `exc_info=(type(result), result, result.__traceback__)`.
- This was missed by both the original implementation AND the reviewer — only caught by actual testing.

### asyncio.gather return_exceptions Gotchas
1. **Empty exception strings**: `asyncio.gather(*tasks, return_exceptions=True)` returns exception objects. Using `f"{result}"` may produce empty strings for exceptions like `asyncio.CancelledError` that have no message args.
2. **Fix**: Always use `f"{type(result).__name__}: {result}"` and pass `exc_info=result` to logger.
3. **BaseException vs Exception**: `asyncio.CancelledError` inherits from `BaseException`, not `Exception`. Using `isinstance(result, Exception)` would miss it.

### Pool Health Reporting
- Never use a startup flag (`self._running`) as a proxy for actual health. Use actual pool state (`pool.qsize() > 0`) or run a real health check (ping).
- The `get_status()` API is what external callers see — it must reflect reality.

### Error Logging Pattern
- All `logger.error()` and `logger.warning()` calls catching exceptions should include `exc_info=True` for full traceback visibility.
- This is especially important for background tasks where the error context is hard to reproduce.

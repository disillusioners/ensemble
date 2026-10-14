# Fix: "no running event loop" in spawn_instance tool

## Date: 2026-05-15

## Bug
After stopping an instance and sending a new "continue" message, async operations failed with `ERROR: Failed to spawn instance: no running event loop`.

## Root Cause
`spawn_instance` is a **sync** tool (`def`, not `async def`). LangChain's `ToolNode` runs sync tools via `run_in_executor()` (thread pool). Inside the method, `asyncio.create_task()` was called — which requires a running event loop. Threads from `run_in_executor` don't have event loops → crash.

## Key Insight
**Sync tools in LangChain run in threads via `run_in_executor`**. Any `asyncio.create_task()` or `asyncio.get_running_loop()` inside sync tools will fail with "no running event loop".

## Fix Pattern
Replace `asyncio.create_task()` with `MainLoopBridge.run_async_no_wait()`:
- Uses `asyncio.run_coroutine_threadsafe()` which is the proper thread-safe API
- Schedules work on the main event loop from any thread
- Gracefully handles when loop isn't available (logs warning, returns)

## Alternative Approach Considered
Making `spawn_instance` an `async def` tool would also fix it (runs on event loop directly). But would require updating ~66 test files. The `MainLoopBridge` approach is minimal and backward-compatible.

## Lesson
When adding fire-and-forget async calls in methods that could be called from sync tools, always use `MainLoopBridge.run_async_no_wait()` instead of `asyncio.create_task()`.

## Files Changed
- `daemon/services/instance_lifecycle.py` — replaced `asyncio.create_task()` with `MainLoopBridge.run_async_no_wait()`

## Commit
`73acf83 fix: resolve 'no running event loop' in spawn_instance by using MainLoopBridge`

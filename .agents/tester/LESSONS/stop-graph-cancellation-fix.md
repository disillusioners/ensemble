# Stop Fix — Graph Execution Cancellation (2026-05-15)

## What Was Fixed
The Stop button previously cancelled HTTP requests but the LangGraph graph kept running — making more LLM calls, spawning instances, etc.

## Fixes Applied
1. `_stop_single` was async but never awaited — now sync, actually executes
2. Graph tasks tracked in `_graph_tasks` dict — can be cancelled via `asyncio.Task.cancel()`
3. Memory leak fixed — task registration inside try/finally with `pop()` cleanup
4. Race condition fixed — identity check prevents stale cleanup of wrong tasks

## Testing Results
- **Unit tests**: 18 passed, no regressions
- **Live test**: All 6 verification points passed
  - Graph stops immediately with cancellation log
  - No more LLM/tool calls after stop
  - Instance goes idle
  - No RuntimeWarning or worker crashes
  - Instance is resumable after stop
  - Edge cases: idle stop (no-op), idempotent, multiple cycles all work
- **ensure.md**: dev.sh stable for 43 seconds

## Key Takeaway
The fix correctly cancels the asyncio task running the graph, not just the HTTP request. This means all downstream activity (LLM calls, tool executions, child spawns) is properly stopped.

# Stop → Resume → Spawn Instance Fix Verification (2026-05-15)

## Bug Fixed
After stopping an instance and resuming with "continue", the `spawn_instance` tool crashed with "no running event loop". Root cause: `asyncio.create_task()` was used instead of `MainLoopBridge.run_async_no_wait()`.

## Fix
Changed from `asyncio.create_task()` to `MainLoopBridge.run_async_no_wait()` which properly schedules coroutines on the main event loop from any thread context.

## Verification
- Live daemon test with 3 stop/resume cycles
- Leader agent successfully spawned coder instance after resume
- No "no running event loop" errors
- No RuntimeWarning about unawaited coroutines
- No memory leaks in _graph_tasks

## Test Script
`test/packs/stop_resume_spawn_e2e_test.py` — reusable for regression testing

## Key Takeaway
When code runs in worker threads or after graph interruption, always use `MainLoopBridge.run_async_no_wait()` instead of `asyncio.create_task()`. The latter assumes a running event loop in the current thread, which is not guaranteed after stop/resume cycles.

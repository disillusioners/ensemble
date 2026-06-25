# Persistent Consumer Refactor - Issue #5

## Date: 2026-04-05

## What was done
Replaced the pattern of `asyncio.create_task(self._process_queue(instance_id))` at 6 trigger points with a persistent consumer per instance pattern.

## Key files
- `daemon/manager.py` - core consumer pattern, state management
- `daemon/sources/registry.py` - source message handling
- `daemon/tools/instance.py` - agent-to-agent send_message tool

## Architecture
- Per-instance `asyncio.Queue` + persistent `asyncio.Task` (consumer)
- `_signal_consumer()` - thread-safe method to wake consumer
- `_ensure_consumer()` - uses `run_coroutine_threadsafe` for thread-safety (watchdog thread)
- `_start_consumer()` - async helper to create task from event loop
- `_instance_consumer()` - persistent loop that processes queue
- Cleanup on `terminate_instance` (sync, cancels task directly)

## Important lessons
1. **Thread-safety critical**: Watchdog runs in `threading.Thread` with no event loop. Must use `run_coroutine_threadsafe` not `create_task` directly.
2. **`_signal_consumer` is sync**: Can be called from sync contexts (tools, terminate, watchdog)
3. **`_process_queue` unchanged**: Still does the actual processing with its own concurrency guard
4. **6 trigger points converted**: enqueue_message, watchdog retry, completion report, error report, source registry, send_message tool
5. **Consumer error backoff**: `asyncio.sleep(1)` after errors prevents tight spin loops

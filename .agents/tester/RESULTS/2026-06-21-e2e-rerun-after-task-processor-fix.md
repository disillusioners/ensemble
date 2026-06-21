# E2E Test Re-run After task_processor Fix — 2026-06-21

## Date
2026-06-21

## Summary
- **Previous bug** (`Task` has no `.content`): ✅ FIXED by commit `15b2f606`
- **New bug discovered**: `Message <UUID> not found in message_queue for task <N>` — different error, same failure point
- **Tests Run**: 3/3 executed
- **Results**: 3 FAILED (all at same point: leader never spawns child)

## Daemon Verification
- ✅ Commit `15b2f606` present on `latest`
- ✅ Daemon restarted cleanly (port 8079 killed, port 8088 untouched)
- ✅ DependencyBus started CLEAN — no enqueued_at errors
- ✅ No `Task.*content` AttributeError (previous bug is gone)
- ✅ PostgreSQL connected, all services up

## Test Results

| # | Test | Result | Duration | Failure Point |
|---|------|--------|----------|---------------|
| 1 | `test_parent_child_workflow_happy_path` | ❌ FAIL | ~60s | Leader didn't spawn child within 60s |
| 2 | `test_pause_after_spawn_then_resume` | ❌ FAIL | ~60s | Same |
| 3 | `test_terminate_after_spawn_then_revive` | ❌ FAIL | ~60s | Same |

All 3 fail at the SAME step: leader spawned + message sent + job admitted, but worker crashes immediately with `ValueError` before any LLM call or child spawn.

## New Bug: Message Not Found in message_queue

### Error
```
ValueError: Message 324ede8c-cab1-42da-94b9-d928852e46ef not found in message_queue for task 146
```

### Location
`daemon/services/task_processor.py:184`

### Traceback Path
```
worker_pool.py:329 _process_with_timeout
→ task_processor.py:569 run_task
→ main_loop_bridge.py:86 run_async
→ task_processor.py:564 _run
→ task_processor.py:184 process  ← ValueError raised here
```

### Root Cause Analysis
The `15b2f606` fix resolved the `Task.content` AttributeError (the fallback no longer returns a Task object). But now a DIFFERENT code path fails: the message lookup at `task_processor.py:184` can't find the message in the `message_queue` table.

Likely causes:
1. **Race condition**: Message insertion (`enqueue_message_via_jq`) hasn't committed by the time the worker looks it up
2. **Scope mismatch**: The message is stored in a different DB session/connection than the worker's lookup
3. **Wrong lookup key**: The task references a message UUID that doesn't match what's in the queue

### What Works vs What Doesn't
- ✅ Instance spawn works
- ✅ Message send/queue works (job admitted via `Observer._admit_via_worker_pool`)
- ✅ Title generation LLM call works (separate code path)
- ❌ Message processing crashes — worker can't find message in message_queue

## Comparison: Bug Evolution

| Run | Bug | Location | Fix |
|-----|-----|----------|-----|
| Run 1 (before fixes) | `ValueError: mcp.__spec__ is None` + HTTP 400 | Test infra | Quick fixed (2a762b0c) |
| Run 2 (after infra fixes) | `AttributeError: 'Task' has no attribute 'content'` | task_processor.py:174 | Fixed (15b2f606) |
| Run 3 (this run) | `ValueError: Message not found in message_queue` | task_processor.py:184 | **NOT FIXED** |

## Cleanup
- ✅ All 3 test instances terminated properly
- 1 pre-existing running instance from prior session (not touched)
- Port 8088 never touched

## Conclusion
The E2E test infrastructure is solid. The tests correctly expose a progression of daemon bugs in the message processing pipeline. Each fix reveals the next layer. The current bug — `Message not found in message_queue` — is a race condition or scope mismatch between message insertion and worker lookup. This needs daemon-side investigation of the `task_processor.process` message lookup chain and `enqueue_message_via_jq` timing.

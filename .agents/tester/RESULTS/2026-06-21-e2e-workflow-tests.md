# E2E Workflow Test Run — 2026-06-21

## Date
2026-06-21

## Summary
- **Tests Run**: 3/3 executed
- **Results**: 3 FAILED
- **Quick Fixes Applied**: 2 (conftest find_spec + optional PROJECT_ID)
- **Daemon Bug Discovered**: `task_processor.process` receives `Task` wrapper instead of `Message` payload

## Prerequisites Verified
- ✅ Commit `1b375e57` (enqueued_at fix) present on `latest`
- ✅ DependencyBus started CLEAN — no `enqueued_at` errors
- ✅ PostgreSQL connected (`postgres`, `postgres_env_available: true`)
- ✅ All services up: WorkerPool (4 workers), JobProcessor, JobFeedbackObserver, CorrelationManager, Maintenance

## Quick Fixes Applied (commit `2a762b0c`)

### Fix 1: conftest.py find_spec ValueError
- **Problem**: `importlib.util.find_spec("mcp")` raised `ValueError: mcp.__spec__ is None` when root conftest mock module was in sys.modules
- **Fix**: try/except ValueError, temporarily remove mock from sys.modules, retry
- **File**: `tests/e2e/conftest.py` (12 lines changed)

### Fix 2: Optional PROJECT_ID
- **Problem**: Hardcoded PROJECT_ID UUID didn't exist in dev DB → HTTP 400
- **Fix**: Default PROJECT_ID to None, conditionally include in request body
- **File**: `tests/e2e/test_e2e_workflows.py` (7 lines changed)

## Test Results

| # | Test | Result | Duration | Failure Point |
|---|------|--------|----------|---------------|
| 1 | `test_parent_child_workflow_happy_path` | ❌ FAILED | ~60s | Leader didn't spawn child within SPAWN_TIMEOUT (60s) |
| 2 | `test_pause_after_spawn_then_resume` | ❌ FAILED | ~60s | Same — leader didn't spawn child within 60s |
| 3 | `test_terminate_after_spawn_then_revive` | ❌ FAILED | ~60s | Same — leader didn't spawn child within 60s |

All 3 fail at the SAME point: `_wait_for_child_spawned` assertion. The leader stays in `running` status but never produces an assistant turn or spawns a child.

## Daemon Bug Discovered (NOT a test bug)

### Bug: `AttributeError: 'Task' object has no attribute 'content'`

**Location**: `daemon/services/task_processor.py:174`

```python
# Line 174 in task_processor.py:
message_content = message.content if message else ""
```

**Root Cause**: `worker_pool._process_with_timeout` → `task_processor.run_task(task, ...)` passes a `Task` wrapper object to `processor.process()`, but the code expects a `Message` object and calls `task.content` on it.

**Symptom**: 
- Title-generation LLM call succeeds (we see "Generated title for instance X: ...")
- The actual `process_message` worker invocation FAILS silently
- Leader stays in `running` but never produces output or spawns children

**Daemon log evidence**:
```
worker_pool - Worker worker-0 claimed task 141 (type=process_message, instance=f8949453...)
task_processor - Processing message task 141: message=8f8880c0..., instance=f8949453...
main_loop_bridge - ERROR - error running coroutine: 'Task' object has no attribute 'content'
worker_pool - ERROR - Worker worker-0 failed task 141: 'Task' object has no attribute 'content'
```

**Impact**: This is a CRITICAL daemon bug — ALL message processing is broken. No agent can process any message. This is not specific to E2E tests.

### Severity Assessment
- **CRITICAL**: Message processing is the core daemon function. If `task_processor.process` receives `Task` instead of `Message`, no agent can run.
- **NOT a quick fix**: Requires investigation of how `worker_pool` passes objects to `task_processor` — likely involves understanding the Task/Message wrapper relationship, possibly multiple files.
- **Possible regression**: May have been introduced during Phase D DependencyBus refactoring (the decouple architecture work).

## Cleanup
- ✅ All 3 test instances properly terminated via test finally blocks (DELETE returned 200)
- 14 pre-existing instances from prior sessions (not from this test run)
- 4 active instances from prior sessions (not touched)

## Conclusion
The E2E test infrastructure is now working (both quick fixes resolved). The tests correctly expose a real daemon-side bug: `task_processor.process` receives a `Task` wrapper instead of a `Message` payload. This bug must be fixed in the daemon (not the tests) before the E2E tests can pass. The enqueued_at fix (`1b375e57`) itself is verified working — DependencyBus starts cleanly with PostgreSQL.

# Critical Daemon Bug: task_processor Task vs Message Mismatch

## Date
2026-06-21

## Discovery
Running 3 E2E workflow tests against live daemon revealed a critical bug in the message processing pipeline.

## Bug Details

### Location
`daemon/services/task_processor.py:174`

### Error
```
AttributeError: 'Task' object has no attribute 'content'
```

### Root Cause
The `worker_pool` passes a `Task` wrapper object to `task_processor.process()`, but the code at line 174 expects a `Message` object:
```python
message_content = message.content if message else ""
```

The `Task` object doesn't have a `.content` attribute — it wraps the message but the processor doesn't unwrap it.

### Impact
- **CRITICAL**: ALL message processing is broken
- No agent can process any message
- Leaders stay in `running` but never produce output or spawn children
- Title generation works (separate code path), but actual message processing fails

### Reproduction
1. Start daemon (`./dev.sh`)
2. Spawn leader instance (`POST /api/instances` with agent_id="leader")
3. Send any message (`POST /api/instances/{id}/messages`)
4. Leader stays `running` forever, never processes the message
5. Check daemon logs for `AttributeError: 'Task' object has no attribute 'content'`

### Investigation Leads
- Check `worker_pool._process_with_timeout` — how does it pass the task to the processor?
- Check the `Task` model — does it have a `.message` or `.payload` attribute that should be extracted?
- Check `task_processor.run_task` — should it unwrap the Task before calling `process()`?
- **Likely regression from Phase D**: The DependencyBus refactoring may have changed how tasks are dispatched, introducing this wrapper mismatch.
- Search for "admit_via_worker_pool" pattern mentioned in project notes — there may be related context.

## Severity
**CRITICAL — BLOCKING.** This breaks the core daemon functionality. No E2E workflow tests can pass until this is fixed.

## NOT a Quick Fix
This requires investigation across `worker_pool.py`, `task_processor.py`, and potentially the `Task` model. Multiple files may be involved. Needs a full fix workflow, not a quick fix.

## Test Coverage
This bug was discovered by the E2E workflow tests (`tests/e2e/test_e2e_workflows.py`). The tests correctly expose the issue — they are working as designed. Once this bug is fixed, the tests should verify the fix.

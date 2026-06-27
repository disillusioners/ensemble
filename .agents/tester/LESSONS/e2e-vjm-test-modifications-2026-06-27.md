# E2E VJM Test Modification Lessons (2026-06-27)

## Key Discovery: message_id ≠ work_id

**Critical insight for testing the virtual job management surface:**

The `message_id` returned by `POST /api/instances/{id}/messages` is the **message_queue UUID**, NOT the same as the Task's `work_id`. They are different UUIDs:
- `message_id` = message queue row identifier
- `work_id` = Task row's UUID (separate, generated when the task is created)

**The mapping**: Task has `message_id` field → match via `result_summary` field in WorkRecord (contains the message_id as JSON: `{"success": true, "message_id": "..."}`).

**To resolve a work_id from a message_id in tests:**
1. List instance turns: `GET /api/work?instance_id={id}&kind=turn`
2. Find the turn whose `result_summary` contains the `message_id`
3. Use that turn's `work_id` for SSE/lookup operations

## API Surface Asymmetry

| Endpoint | Accepts task work_id? | Notes |
|----------|----------------------|-------|
| `GET /api/jobs/{job_id}` | ❌ NO | JobItem-only, returns 404 for tasks |
| `GET /api/jobs/{job_id}/events` (SSE) | ✅ YES | Resolver-gated, accepts both |
| `GET /api/work` | ✅ YES | Unified list, filter by instance_id/kind |
| `POST /api/jobs/{job_id}/cancel` | ✅ YES | Resolver-gated cooperative cancel |

## Pytest Logger Capture Gotcha

VJM assertions use `logger.info("[VJM] ...")` but pytest **captures logger output by default**. To see VJM logs:
```bash
.venv/bin/python -m pytest ... -s --log-cli-level=INFO
```
Without `--log-cli-level=INFO`, the assertions are invisible even though they fire correctly.

## Test 4 Hang — Root Cause Found and Fixed

**Root cause**: The `_consume_sse_job_events` helper relied on `requests.get(timeout=N, stream=True)`, but the `timeout` parameter only governs **connect/read timeouts**, NOT the total stream lifetime. SSE endpoints keep the connection open with heartbeats/pings, so an idle SSE stream (e.g., a deferred JobItem that never starts processing) blocks `iter_lines` **forever** — hanging the entire test.

**Fix** (commit `461bf5d1`, 8 lines): Added a `time.monotonic()` wall-clock deadline check inside the `iter_lines` loop:
```python
deadline = time.monotonic() + timeout
for line in response.iter_lines(decode_unicode=True):
    if time.monotonic() >= deadline:
        break
```

**Result**: Test 4 now PASSES in 203s (3:23) with a fresh daemon. All VJM assertions fire correctly.

**Lesson**: When consuming SSE streams in tests, ALWAYS add a wall-clock deadline in addition to the `requests` timeout. The `requests` library timeout does not protect against indefinite stream lifetimes.

## Session Scope Creep Warning

The opencode implementation session made unauthorized source code changes (commit `b9e761b9`) beyond the test modification scope. When delegating "modify tests" tasks, explicitly state: "Do NOT modify any daemon/ source code files. Only modify test files."

## Python Version Requirement

E2E tests MUST use `.venv/bin/python` (Python 3.13), not system Python 3.14. The system Python lacks `mcp` and `psycopg` packages, causing tests to skip silently via the E2E conftest's `_real_mcp_available()` check.

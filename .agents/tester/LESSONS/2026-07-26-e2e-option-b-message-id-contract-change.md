# Lesson: Option B `message_id=None` Contract Change Breaks E2E Helpers
**Date:** 2026-07-26
**Branch:** `feature/queue-dispatch-option-b` @ `8e04f507`
**Affects:** `tests/e2e/test_e2e_workflows.py` (all 4 Release Gate tests)
**Severity:** Blocks all e2e Release Gate validation

## Problem

The `feature/queue-dispatch-option-b` refactor changed the `POST /api/instances/{id}/messages` response contract. Previously the response carried a non-None `message_id`; now `enqueue_message_job` (`daemon/services/instance_messaging.py:1296`) returns `message_id: null` plus a populated `job_id`. The real `message_id` is created at dispatch time inside `enqueue_message` when the JobProcessor picks up the job (per the `enqueue_message_job` docstring).

This is **intentional Option B design** (commit `8e04f507`), not a bug.

## Why the tests break

The e2e test suite's `_send_message()` helper (`tests/e2e/test_e2e_workflows.py:193`) hard-asserts a non-None `message_id`:
```python
message_id = data.get("message_id")
if not message_id:
    raise RuntimeError(f"Send message response missing message_id: {data}")
```

All 4 Release Gate tests call `_send_message` as their first action and fail immediately (~1.2-1.5s each, exit 1) — **before any LLM call or workflow logic executes**. The downstream `result_summary.message_id` matching (lines 1283-1297) would also break even if the immediate assertion were relaxed.

## Test-architecture implication

The tests' synchronization model (track `message_id` → match work records) is incompatible with the Option B async contract. Fixing requires reworking the helpers to either:
1. Poll for job dispatch then fetch the real `message_id` from the message history, OR
2. Track `job_id` through the WorkResolver facade.

This is a coordinated test-suite update (multiple sites in one file, >20 lines), driven by a production architecture change — **NOT a quick fix**.

## Recommendation

When any production change alters an HTTP response contract (adds/removes/renames a field, or changes a field's type/nullability), a follow-up task must update every e2e/integration helper that reads that contract. The `_send_message` helper is shared by all 4 Release Gate tests, so a single contract change here has a 4x blast radius on the Release Gate.

## Files to update (after deciding the approach)
- `tests/e2e/test_e2e_workflows.py` — `_send_message` helper (line ~193) + `result_summary.message_id` matching (lines ~1283-1297) + any other `message_id` consumers in the 4 test bodies

## Re-run plan
After the test-suite update, re-run `test/packs/e2e_workflows_ensure_test.sh` (4 tests, one by one) — the actual workflow behavior under Option B is currently **unvalidated**.

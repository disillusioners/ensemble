# E2E Release Gate — ensure.md Validation (Option B)
**Date:** 2026-07-26
**Worker Instance:** 93b7c7bd-7b6f-4245-b4be-2d45acf26a1c (e2e-ensure-release-gate-option-b)
**Skill:** `test-pack-execution`
**Pack:** `test/packs/e2e_workflows_ensure_test.sh` (run via per-test `-k` filter, one-by-one)
**Branch:** `feature/queue-dispatch-option-b` @ `8e04f507 feat: route message dispatch through job queue (Option B)`
**Trigger:** User request — "Run e2e Tests from ensure.md" for Option B review

---

## Summary

| Metric | Value |
|--------|-------|
| Tests run | 4 |
| Passed | 0 |
| Failed | 4 |
| Timed out | 0 |
| Total wall-clock | ~5.5s (all tests failed at first `_send_message` step — no LLM calls executed) |
| Overall status | ❌ **FAIL** (0/4) |
| Quick fixes | none (architecture-driven failure, not quick-fix eligible) |
| Files modified | none |
| Root cause | Single shared: Option B response-contract change (`message_id=None`) |

---

## Scope Decision

> The `feature/queue-dispatch-option-b` branch is a **core architecture refactor** of message dispatch — routing messages through the standard job queue instead of the mirror pattern, with real concurrency enforcement and a new `_process_next_job` branch. Cross-module + architecture-level change → **Release Gate E2E is warranted**. No scope reduction applied.

---

## Prerequisites Verified

- ✅ Daemon running on `localhost:8079` (worker started it via `./dev.sh`, reached HTTP 200 at `/docs` after ~25s)
- ✅ SSL certs cleaned (`unset SSL_CERT_FILE SSL_CERT_DIR` before every test)
- ✅ Queue cleanup before each test: `GET /api/jobs?status=pending` → `{"jobs":[],"total":0}` every time
- ✅ Tests run **one by one** via `-k` filter (per ensure.md mandate)
- ✅ Branch confirmed: `feature/queue-dispatch-option-b` @ `8e04f507`

---

## Per-Test Results

| Test | Result | Runtime | Exit Code | Failure Location |
|------|--------|---------|-----------|------------------|
| `test_parent_child_workflow_happy_path` | ❌ FAIL | 1.53s | 1 | `_send_message` @ `test_e2e_workflows.py:1230` |
| `test_pause_after_spawn_then_resume` | ❌ FAIL | 1.34s | 1 | `_send_message` @ `test_e2e_workflows.py:1598` |
| `test_terminate_after_spawn_then_revive` | ❌ FAIL | 1.24s | 1 | `_send_message` @ `test_e2e_workflows.py:1962` |
| `test_three_level_cascade_reports` | ❌ FAIL | 1.41s | 1 | `_send_message` @ `test_e2e_workflows.py:2134` |

---

## Root Cause: Single Shared Failure — Option B Response-Contract Change

All 4 tests fail at the **very first message-send step**, before any LLM call or workflow logic executes.

### The failure
The `_send_message()` helper (`tests/e2e/test_e2e_workflows.py:193`) asserts `message_id` is non-None:
```python
message_id = data.get("message_id")
if not message_id:
    raise RuntimeError(f"Send message response missing message_id: {data}")
```

### What changed under Option B
Under Option B, `enqueue_message_job` (`daemon/services/instance_messaging.py:1296`) now returns:
```json
{"message_id": null, "job_id": "94ead37e-...", "role": "assistant", "content": "", ...}
```

The `message_id` is **deliberately `None`** — per the `enqueue_message_job` docstring: *"`message_id` is `None` — it is created at dispatch time inside `enqueue_message` (see `_process_next_job`'s message branch)."*

The message is now enqueued as a JobItem, and the real `message_id` is created **later** when the JobProcessor picks up the job. This is the core Option B refactor (commit `8e04f507`).

### Why this is NOT quick-fix eligible
The tests' synchronization model (track `message_id` → match `result_summary.message_id` in work records at lines 1283-1297) no longer holds. Properly fixing requires reworking the tests to either:
1. **(a)** Poll for job dispatch then fetch the real `message_id` from the message history, OR
2. **(b)** Track `job_id` through the WorkResolver facade.

This is a coordinated test-suite update driven by a production architecture change — not a <20-line single-file test patch.

### Failure repr (Test 1, representative of all 4)
```
tests/e2e/test_e2e_workflows.py:1230: in test_parent_child_workflow_happy_path
    msg_id = _send_message(leader_id, TEST_MESSAGE)
tests/e2e/test_e2e_workflows.py:193: in _send_message
    raise RuntimeError(f"Send message response missing message_id: {data}")
E   RuntimeError: Send message response missing message_id: {'message_id': None, 'role': 'assistant',
    'content': '', ..., 'job_id': '94ead37e-3238-47fa-85d4-6ccfbae925f6', 'auto_resumed': False, 'resume_info': None}
```

---

## ensure.md Validation Status

### Release Gate (Critical — release-gate)
- [ ] **E2E: Normal parent→child workflow completes (happy path)** — ❌ FAIL (Option B contract change)
- [ ] **E2E: Pause after spawn, then resume works correctly** — ❌ FAIL (same root cause)
- [ ] **E2E: Terminate after spawn, then revive documented** — ❌ FAIL (same root cause)
- [ ] **E2E: 3-level cascade (leader→tester→staggered workers): reports delivered, no premature completion, no stuck completion, state switching** — ❌ FAIL (same root cause)

**Release Gate E2E: 0/4 Critical requirements PASS ❌**

---

## Key Finding: Workflow Behavior Under Option B Remains UNVALIDATED

⚠️ The Option B dispatch refactor **itself appears to function** at the job-creation level — jobs are created with a `job_id`, and job status flows to `cancelled` on test teardown. **However, the e2e workflow tests cannot validate the actual workflow behavior** (spawn→child→terminal, pause→resume, terminate→revive, cascade reports) because they all fail at the very first message-send step before any LLM call or workflow logic executes.

**The workflow behaviors under Option B remain unvalidated.** Updating the test helpers to the Option B contract is a prerequisite before these Release Gate e2e tests can provide any coverage of the new dispatch path.

---

## Recommendations

1. **Update the e2e test helpers to the Option B async contract.** The `_send_message` helper (`tests/e2e/test_e2e_workflows.py:193`) and the downstream `result_summary.message_id` matching (lines 1283-1297) must be reworked to either (a) poll for job dispatch then fetch the real `message_id` from message history, or (b) track `job_id` through the WorkResolver facade. This is test-architecture work — **a coordinated test-suite update, not a quick fix.**
2. **Verify the intended steady-state.** These tests spawn then immediately message an IDLE instance. Confirm that `IDLE → enqueue → message_id=None` is the intended steady state (not a regression). If a synchronous `message_id` contract should be preserved for some status paths, verify whether these tests should be hitting a different path.
3. **Re-run this pack after the test suite is updated** to the Option B contract — the actual workflow validations (the valuable part) have not yet run.

---

## Overall Status

- **E2E Release Gate:** ❌ **FAIL** (0/4) — all blocked by a single shared architecture-driven contract change
- **Quick Fixes Applied:** none (not eligible — production architecture change)
- **Production code modified:** none
- **Action Needed:**
  - [ ] Update `tests/e2e/test_e2e_workflows.py` (`_send_message` helper + `result_summary.message_id` matching) to the Option B `message_id=None` / `job_id`-tracking contract — prerequisite before these tests can validate Option B workflow behavior
  - [ ] Re-run this pack after the test-suite update
- **Testing Complete:** ❌ **NOT READY** — ensure.md E2E Release Gate red; workflow behavior under Option B unvalidated

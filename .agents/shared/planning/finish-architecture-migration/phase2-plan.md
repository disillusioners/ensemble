# Phase 2: D13 — Eliminate MESSAGE JobItem Creation

## Objective

Make `enqueue_message` route ALL messages through the WorkerPool path (write only `task` + `message_queue` rows), and make `enqueue_job` reject `job_type="message"` with `ValueError`. This eliminates the dual-record coupling where each user message creates both a Task row AND a JobItem row — the root cause of 06f500af-class bugs. Also rewrite the `get_message_status` endpoint, clean up ALL `job_type="message"` references, and migrate in-flight MESSAGE JobItems.

## Coupling

- **Depends on**: None (can start in parallel with Phase 1)
- **Coupling type**: independent from Phase 1, but Phase 3 depends on THIS phase (tight)
- **Shared files with other phases**: `instance_messaging.py` (Phase 4 modifies same function), `job_queue_service.py`, `instance_lifecycle.py`, `job_queue/repository.py`
- **Shared APIs/interfaces**: `enqueue_message` signature changes (Phase 4 removes the parameter entirely)
- **Why this coupling**: D13 is the structural fix — without it, D11 (Phase 3) would leave orphaned MESSAGE JobItems with no processor to handle them

## Context

### Current Two-Record Problem

When a user sends a message via `POST /messages`:
1. `enqueue_message(dispatch_path="jobqueue")` → `_prepare_enqueued_message` creates `MessageQueue` row + `Event` row (but NOT a Task row because `create_task_row=False`)
2. Then calls `_job_queue_service.enqueue(job_type="message")` → creates a `JobItem` row (status=PENDING)
3. JobProcessor dequeues the JobItem → `observer._admit_via_worker_pool` → creates a `Task` row + `notify_work()`

**Result**: TWO coupled work records (Task + JobItem) per message. Every state transition must update both or risk divergence.

### Target State (After D13)

1. `enqueue_message` ALWAYS creates `MessageQueue` + `Task` row (the current workerpool path) + notifies WorkerPool
2. No `JobItem` is ever created for a message
3. `enqueue_job` rejects `job_type="message"` with `ValueError` (defense in depth)

### Caller Audit (Verified by Exploration)

Only **TWO** production callers use `dispatch_path="jobqueue"`:

| Caller | File:Line | Uses returned `job_id`? |
|--------|-----------|------------------------|
| HTTP `send_message` | `daemon/routers/messages.py:119-125` | **No** — discards it, builds response from `message_id` only |
| `job_continue` tool | `daemon/tools/job_queue.py:473-487` | **Yes** — returns `new_job_id` to calling agent |

The two other locations in the LESSONS doc (`utils.py:575`, `job_queue_service.py:258`) do NOT use `dispatch_path="jobqueue"` — they default to `"workerpool"`.

### C3 — Additional Cleanup Sites Found by Reviewer

Four `job_type="message"` references were missed in the original plan:

| File:Line | Code | Context |
|-----------|------|---------|
| `daemon/services/instance_lifecycle.py:922-923` | `find_jobs_by_instance(instance_id, job_type="message")` | Terminate cleanup: cancels MESSAGE jobs on instance termination |
| `daemon/services/instance_lifecycle.py:1858-1862` | `.where(JobItem.job_type == "message")` | Terminate: counts MESSAGE jobs cancelled for [TRACE] log |
| `daemon/services/job_queue_service.py:1255-1256` | `if job.job_type == "message" and job.instance_id:` | `start_job`: MESSAGE jobs use pre-set instance_id; TASK jobs get new UUID |
| `daemon/repositories/job_queue/repository.py:512` | `.where(JobItem.job_type == "message")` | `find_processing_message_jobs_by_instance`: DB-level concurrency gate |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **2.1** | Modify `enqueue_message` jobqueue path to create Task row | In `instance_messaging.py:963`, change `create_task_row` to `True` for ALL paths (remove the `dispatch_path == "workerpool"` condition). The jobqueue branch (`lines 990-1030`) must stop calling `_job_queue_service.enqueue()` and instead: (a) the Task row is already created by `_prepare_enqueued_message` (now with `create_task_row=True`), (b) call `worker_pool.notify_work()` directly. Keep the `job_id` variable for now — set it to `str(task_id)` as an adapter so the return shape doesn't break callers. | `daemon/services/instance_messaging.py:963-1038` |
| **2.2** | Add adapter for `job_id` response | The jobqueue path currently returns `job_id` from `JobItem.job_id`. After D13, set `job_id = str(ctx.task_id)` (or equivalent from the prepared context). The HTTP route discards it; `job_continue` uses it. The semantic meaning changes from "JobItem ID" to "Task ID" but the API contract is preserved. | `daemon/services/instance_messaging.py:1040-1045` |
| **2.3** | Make `enqueue_job` reject `job_type="message"` | In `job_queue_service.py:enqueue()` (line ~316), add an early guard: `if job_type == "message": raise ValueError("enqueue_job no longer accepts job_type='message' — use enqueue_message instead")`. This is defense-in-depth: the only legit caller (`enqueue_message` jobqueue path) is now gone. | `daemon/services/job_queue_service.py:316` |
| **2.4** | Remove ALL MESSAGE-specific code paths | **C3 — Comprehensive cleanup.** Remove ALL `job_type == "message"` branches and filters: (a) `job_queue_service.py:379-388` + `500-511` — MESSAGE queue routing to `system_parallel_queue`, (b) `job_queue_service.py:1255-1256` — `start_job` instance_id branching (all jobs use pre-set or new UUID uniformly), (c) `instance_lifecycle.py:920-934` — terminate cleanup for MESSAGE jobs (no MESSAGE jobs exist), (d) `instance_lifecycle.py:1858-1865` — [TRACE] log counting MESSAGE jobs cancelled (always 0), (e) `job_queue/repository.py:505-516` — `find_processing_message_jobs_by_instance` query filter (method becomes a no-op or can be removed). After removal, run: `grep -rn 'job_type.*message\|JobItem\.job_type.*message' daemon/ --include="*.py"` — must return 0 hits. | `daemon/services/job_queue_service.py:379-388, 500-511, 1255-1256`, `daemon/services/instance_lifecycle.py:920-934, 1858-1865`, `daemon/repositories/job_queue/repository.py:505-516` |
| **2.5** | Verify HTTP API contract | Verify that `POST /instances/{instance_id}/messages` still returns the same response shape. The HTTP route (`routers/messages.py:119-125`) builds the response from `result.message_id` — it discards `job_id`. Verify this is still correct. The `job_continue` tool (`tools/job_queue.py:473-487`) returns `result.job_id` as `new_job_id` — verify this still works with the Task ID adapter. | `daemon/routers/messages.py:119-149`, `daemon/tools/job_queue.py:473-487` |
| **2.6** | Comprehensive grep sweep before declaring complete | Run `grep -rn 'job_type.*message\|JobItem\.job_type.*message\|job_type.*==.*"message"\|job_type.*=.*"message"' daemon/ --include="*.py"`. Any remaining hits must be audited. Expected after cleanup: 0 hits in source code (only in the ValueError guard message string if it matches the pattern). | — |
| **2.7** | **C1 — Rewrite `get_message_status` endpoint** | The `GET /instances/{id}/messages/{msg_id}/status` endpoint at `daemon/routers/messages.py:154-202` currently queries for MESSAGE-type JobItems via `find_active_jobs_by_instance(instance_id, job_type="message")`. After D13, no such rows exist. Rewrite to query `task` rows instead: look up the task by `message_id` (via `task_repo.get_by_message(message_id)` or equivalent), return the task status. The response shape should stay the same (`message_id`, `instance_id`, `status`, `result_summary`, `error`). The fallback to `get_queue_stats` for internal/WorkerPool messages can stay. | `daemon/routers/messages.py:154-202` |
| **2.8** | **C2 — Data migration for in-flight MESSAGE JobItems** | After Phase 3 removes the MESSAGE branch from `job_processor.py`, any PENDING/PROCESSING MESSAGE JobItems in the DB have no processor. Add a one-time data migration: `UPDATE job_queue_items SET status='cancelled', error_message='Cancelled: MESSAGE JobItem type eliminated by D13 architecture migration' WHERE job_type='message' AND status IN ('pending','processing') AND deleted_at IS NULL`. **Must work on both SQLite and PostgreSQL.** Options: (a) Add to `_ensure_postgres_*` startup routine, (b) Add as a migration script, (c) Document as a manual drain step in the deployment runbook. Recommended: (a) — a startup-guarded idempotent UPDATE that runs once. | `daemon/manager.py` (startup routine) or `daemon/migrations/` |
| **2.9** | Update tests for D13 invariant | Update/add tests to verify: (a) `enqueue_message` does NOT create a `job_queue_items` row, (b) `enqueue_job(job_type="message")` raises ValueError, (c) the HTTP send_message path still works, (d) `get_message_status` returns correct status from task rows. | `tests/unit/test_instance_messaging.py`, `tests/unit/test_job_queue_service.py`, `tests/test_api.py` |

## Key Files

- `daemon/services/instance_messaging.py` — `enqueue_message` function (lines 887-1045), the jobqueue branch (990-1030), `create_task_row` flag (963)
- `daemon/services/job_queue_service.py` — `enqueue()` method (304-553), MESSAGE-specific branches (379, 500, 1255-1256)
- `daemon/services/instance_lifecycle.py` — terminate cleanup (920-934, 1858-1865)
- `daemon/repositories/job_queue/repository.py` — `find_processing_message_jobs_by_instance` (505-516)
- `daemon/routers/messages.py` — HTTP send_message endpoint (119-149), `get_message_status` endpoint (154-202)
- `daemon/tools/job_queue.py` — `job_continue` tool (473-487)
- `daemon/services/job_feedback_observer.py` — `_admit_via_worker_pool` (590-709, will become dead for MESSAGE jobs after D11 in Phase 3)
- `daemon/manager.py` — startup routine for data migration (C2)
- `tests/unit/test_instance_messaging.py` — D13 invariant tests
- `tests/unit/test_job_queue_service.py` — ValueError test

## Constraints

- **HTTP API contract preservation**: The `job_id` in `AsyncMessageResult` must still be populated (now from `task.id`). Callers that read it (only `job_continue`) must continue to work.
- **Task row creation timing**: Currently the jobqueue path defers Task creation to `_admit_via_worker_pool`. After D13, the Task row is created synchronously in `_prepare_enqueued_message`. Verify the worker picks it up correctly (it was already designed for this — the workerpool path does the same thing).
- **No behavioral change to existing workerpool path**: The workerpool path is already correct (creates Task + MessageQueue, notifies worker). D13 makes the jobqueue path identical.
- **C1 — `get_message_status` must not break**: The endpoint is used by the frontend to poll message status. The rewrite must query task rows and return the same response shape.
- **C2 — Data migration must be idempotent**: Safe to run multiple times. The `WHERE status IN ('pending','processing')` guard ensures already-cancelled rows are not re-touched.
- **C3 — All `job_type="message"` sites must be cleaned**: The reviewer identified 4 additional sites beyond the original 2. A comprehensive grep (Task 2.6) is the final gate.

## Deliverables

- [ ] `enqueue_message` creates Task row for ALL dispatch paths (no `JobItem` ever)
- [ ] `enqueue_job(job_type="message")` raises `ValueError`
- [ ] ALL MESSAGE-specific code paths removed (Task 2.4 — 7 sites cleaned)
- [ ] `get_message_status` endpoint rewritten to query task rows (C1)
- [ ] Data migration for in-flight MESSAGE JobItems (C2)
- [ ] Comprehensive grep returns 0 hits (Task 2.6)
- [ ] HTTP send_message endpoint verified working
- [ ] `job_continue` tool verified working with Task ID adapter
- [ ] D13 invariant tests pass

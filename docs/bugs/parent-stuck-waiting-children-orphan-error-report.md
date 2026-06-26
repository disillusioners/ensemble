# Bug: Parent Instance Stuck at `waiting_children` After Successful Children (Three Compounding Bugs)

**Date:** 2026-06-26
**Status:** Fixed
**Severity:** High (parent stuck, manual intervention required, no user-visible error)
**Affected versions:** All versions shipping with the unified WorkerPool + DependencyBus dispatch
**Affected root instances:** Any root with children where:
1. A child's task hits the `graph_timeout_minutes` safety net, AND
2. The underlying `_run()` coroutine completes within seconds of the timeout, AND
3. The retry task claims before the recovery sweeper tick (≤5 min)

**Discovery:** Production incident 2026-06-25 21:15–04:15 UTC. Root instance `fd25551c-c978-4b80-a2ea-d58bf2c64644` stuck at `waiting_children` despite all 21 children completing successfully. Log evidence in `logs/prod_run.log`.

---

## Summary

A parent instance appears stuck at `waiting_children` indefinitely even though all children have completed successfully and the legitimate completion report has already been delivered. Three independent bugs compound into this user-visible symptom:

1. **Bug A** — `internal_error_report:` messages were enqueued without a corresponding `task` row → orphan in `message_queue`, never picked up by the worker pool.
2. **Bug B** — When `graph_timeout_minutes` fires while the underlying `_run()` coroutine completes naturally a few seconds later, a phantom retry task is created (orphaned on claim).
3. **Bug C** — The idempotency no-op for already-completed messages returns success without calling `complete_task` → the task stays in `running` indefinitely → goes stale → recovery hits it.

The full chain produces a false-negative "error report" to the parent that gets stuck (Bug A), preventing the parent from transitioning out of `waiting_children`.

---

## Production Evidence

Root instance: `fd25551c-c978-4b80-a2ea-d58bf2c64644` (leader, project `83da04de-a410-4fb5-9e92-251a99d28a52`)

### DB state at discovery time

| Table | Row | State |
|-------|-----|-------|
| `instances` | root `fd25551c...` | `status=waiting_children` |
| `instances` | 21 children | `status=completed` (1 `terminated`) |
| `dependency_watchers` | root watchers | 0 PENDING, 22 FIRED — bus is clean |
| `task` | for root | 0 PENDING (after latest completion report at 04:15:39) |
| `message_queue` | root queue | 1 row in `ready`: `c441c215-7deb-4298-86de-d0607be6dd59` |
| `task` | for `c441c215` | **0 rows** — no Task row exists |
| `job_queue_items` | for `c441c215` | 0 rows |

The active MESSAGE job `4df44c07-...` was stuck `processing` since `2026-06-25T17:14:12`, holding the queue lock. Since the queue concurrency limit is 1, no other job could run — and the in-flight job had nothing to drain (no Task row for the orphan message).

### Timeline from `logs/prod_run.log` (child `5c7fe0f9-...`)

| Time | Event |
|------|-------|
| `01:44:51` | Task 4371 claimed by worker-1, message `ab836c03` (Phase 5 test-file rename, 110 files) |
| `01:44:51 → 02:39:41` | 70 LLM invocations over 55 minutes (long but legitimate) |
| `02:39:46` | Last heartbeat update on task 4371 (heartbeat interval = 30s) |
| `02:39:50` | `graph_timeout_minutes=55` fires → `worker-1: task 4371 hit safety timeout` → `_handle_cancellation(task 4371, TIMEOUT)` → `schedule_retry` flips 4371 to `cancelled`, creates task 4384 with `retry_count=1` |
| `02:39:54` | **The underlying `_run()` coroutine completes successfully 4s late** — completion handler writes the success report `26fa3d78-...` ("Phase 5 Complete: 110 files, commit `12122f93`") to parent `fd25551c`. `bus.emit_terminal(task_id=4371, outcome=completed)` fires. **The actual agent work succeeded.** |
| `02:39:54` | Task 4385 (parent's processing of `26fa3d78`) starts — the legitimate success path runs normally |
| `02:41:07` | Task 4384 (phantom retry) claimed by worker-2. Processor sees message already `COMPLETED` → no-op short-circuit returns `{success: True, skipped: True}` **before** `_build_callbacks` is called → `task_repo.complete_task(4384)` never runs → task 4384 stays `running` indefinitely |
| `02:46:44` | StaleTaskRecovery finds 4384 stale (heartbeat 5min+ old) → `request_cancel` |
| `02:46:54` | `force_cancel_and_schedule_retry(4384)` → creates task 4395 |
| `02:48:57 → 02:55:04` | 4395 same fate (no-op, no complete_task, stale, force-cancel+retry) |
| `02:59:05 → 03:05:14` | 4396 same fate. Recovery's `max_retries=3` exceeded → `fail_task(4396)` → `_on_stale_task_permanent_failure(instance=5c7fe0f9, error="Stale task permanently failed after 3 retries", message_id=ab836c03)` → `_send_error_report` |
| `03:05:14` | `_send_error_report` enqueues `c441c215-...` to `message_queue` with `source=internal_error_report:5c7fe0f9` — but **does NOT create a `task` row** (Bug A). Message sits in `ready` forever. |
| `04:15:39` | Last completion report (`b6284aea-...`) processed normally by parent |
| After `04:15:39` | Parent has `c441c215` pending → root stuck at `waiting_children` indefinitely |

The actual completion report (`26fa3d78`, containing the success message) was already processed by the parent at 02:40:27 — the parent acknowledged the work as done. The orphan is a **false-negative** error report for a task whose work succeeded.

---

## Root Causes

### Bug A — Orphan `internal_error_report` (no task row)

**Location:** `daemon/services/error_reporting.py:685-700` (pre-fix)

**Code:**
```python
if reason == CancellationReason.TIMEOUT:
    # Try to schedule a retry
    retry_task = self._task_processor._task_repo.schedule_retry(
        task_id=task.id,
        max_retries=self._max_retries,
        backoff_base=self._retry_backoff_base,
        backoff_max=self._retry_backoff_max,
    )
```

Compare with the working `internal_report:` (completion) path in `child_reports._create_completion_report` (`daemon/services/child_reports.py:676-740`), which:
- Creates a `MessageQueue` row **and** a `Task(task_type=PROCESS_REPORT, status=PENDING)` row in the same transaction
- Calls `worker_pool.notify_work()` (line 2066) to wake a worker

The user-message path through `instance_messaging.enqueue_message` → `_prepare_enqueued_message` (line 818-826) does the same.

**Impact:** Every permanent-failure callback (stale-task, max-retries-exceeded, circuit-breaker) silently dropped its parent notification. The parent stays in `waiting_children` until ops intervenes.

---

### Bug B — Race between `graph_timeout` and natural completion

**Location:** `daemon/services/worker_pool.py:_process_with_timeout` (worker_pool.py:310-347) interacting with `task_processor.run_task` (task_processor.py:575-598)

**Sequence:**

1. Worker calls `run_task(task, cancellation_token=token)` which calls `MainLoopBridge.run_async(_run(), timeout=self._graph_timeout_minutes * 60)`.
2. The underlying `_run()` coroutine runs on the event loop. The `TimeoutMonitor` (timeout_minutes=`task_timeout_minutes`, default 60) starts a daemon thread that calls `source.cancel(TIMEOUT)` after the configured timeout.
3. If `_run()` doesn't observe the cancellation promptly (e.g., the LLM stream yields a final chunk right before the cancel arrives, or the cancel arrives during a checkpoint write), the thread-side `future.result(timeout=55*60)` may fire first, raising `TimeoutError` to the worker thread.
4. Worker catches `TimeoutError` (worker_pool.py:340-347) → calls `_handle_cancellation(task, TIMEOUT)` → `schedule_retry(task)` (task_repository.py:963-1089) transitions the task to `cancelled` and creates a retry task with `retry_scheduled=True`.
5. Meanwhile, the underlying `_run()` coroutine **keeps running on the event loop** and finishes successfully 4 seconds later. It tries to run the success path → `on_success` callback → `task_repo.complete_task(task_id)` (task_repository.py `complete_task`).
6. `complete_task`'s UPDATE has the guard `WHERE status IN ('running','pending','paused')`. Since the task was flipped to `cancelled` by `schedule_retry`, the UPDATE returns 0 rows — **silent no-op**.
7. The coroutine still proceeds: writes completion to `message_queue`, fires bus terminal, sends parent report. The bus resolves the parent's pending watcher — the parent's success path runs.
8. The retry task is born orphaned. When claimed later, the message is already COMPLETED → no-op path runs → Bug C fires → retry task stays in `running` → recovery hits it.

**Impact:** Every legitimate-but-long-running graph run that crosses the safety timeout (default 55 min, previously) produces a phantom retry. With the new 2h ceiling, fewer graphs hit this, but the race remains for graphs running between `graph_timeout_minutes` and `task_timeout_minutes`.

---

### Bug C — Idempotency no-op skips `complete_task`

**Location:** `daemon/services/task_processor.py:206-216` (pre-fix)

**Code:**
```python
if message.status == MessageStatus.COMPLETED.value:
    logger.info(
        f"Task {task.id}: message {task.message_id[:8]}... already "
        f"COMPLETED — skipping graph turn (resume re-claim no-op)"
    )
    return {                                         # ← returns BEFORE _build_callbacks
        "success": True,
        "content": None,
        "message_id": task.message_id,
        "skipped": True,
    }
```

`_build_callbacks` is called at line 251 (after the early return). The success path normally invokes `callbacks.on_success` → `task_repo.complete_task(task_id)` to mark the task terminal. Without it, the task stays in `running` indefinitely.

**Why this matters:** A heartbeat thread (`TaskHeartbeat`) writes `last_heartbeat_at` every 30s, but **only when the worker is actively processing the task**. Once the no-op returns and the worker moves on, the heartbeat stops writing. Within `stale_task_recovery_threshold_minutes` (default 5), recovery's `find_cancellable_tasks` predicate (`task_repository.py:1142`) finds the task and force-cancels it.

**Impact:** Any retry of an already-completed message produces an orphan task that triggers the recovery cascade.

---

## Fixes

### Bug A — Route error reports through the dispatcher

**File:** `daemon/services/error_reporting.py`

Replace the bare `_queue_repository.enqueue` with `manager.enqueue_message(..., dispatch_path="workerpool")`:

```python
result = await self._manager.enqueue_message(
    instance_id=parent_id,
    message=error_report,
    source=f"internal_error_report:{instance_id}",
    priority=1,
    metadata={
        "type": "error_report",
        "child_instance_id": instance_id,
        "error_type": error_type,
        "error": truncated_error,
        "original_message_id": message_id,
        "severity": severity,
        "recoverable": error_type in RECOVERABLE_ERROR_TYPES,
    },
    dispatch_path="workerpool",
)
report_message_id = result.message_id
```

`manager.enqueue_message` (`daemon/manager.py:2128`) delegates to `InstanceMessagingService.enqueue_message` → `_prepare_enqueued_message` (line 887-1044), which:
1. Atomically inserts `MessageQueue` + `Task` rows in a single `WriteGuardSession` transaction
2. Transitions parent instance from `waiting_children`/`completed`/`idle` back to `running` (line 836-841 — `WAITING_CHILDREN` is included)
3. Calls `worker_pool.notify_work()` (line 1034) for the WorkerPool path

This mirrors the working `internal_report:` completion path and the user-message path.

---

### Bug B — Skip retry when message is already COMPLETED (rev2: grace-window poll)

**File:** `daemon/services/worker_pool.py` — `_handle_cancellation` TIMEOUT branch + `_await_message_completion` helper

Before calling `schedule_retry` on TIMEOUT, give the underlying `_run()` coroutine a bounded grace window to complete naturally. `MainLoopBridge.run_async(_run(), timeout=...)` raises `TimeoutError` via `future.result(timeout=)` — per Python semantics, this does NOT cancel the coroutine; it keeps running on the event loop and may finish a few seconds later (production timeline: 4s gap). If the coroutine commits `message.status='completed'` during the grace window, skip the retry and let `complete_task` carry the task to terminal (idempotent under the `WHERE status='running'` guard):

```python
if task.message_id:
    completed = self._await_message_completion(
        task.message_id, getattr(self, "_timeout_grace_seconds", 30)
    )
    if completed:
        logger.warning(...)
        try:
            self._task_processor._task_repo.complete_task(
                task.id,
                {"success": True, "message_id": task.message_id, "skipped": True},
            )
        except Exception:
            pass  # status guard makes this a no-op if recovery won
        self._tasks_completed += 1
        return
```

The `_await_message_completion` helper polls `message.status` every 0.5s for up to 30s, returning `True` on `completed` or `failed` (terminal) and `False` if the grace window expires. The grace window is bounded so a truly hung coroutine cannot stall the worker thread indefinitely.

**Why a 30s grace window is safe:** the heartbeat thread keeps writing `last_heartbeat_at` for the task during this period (the worker is busy in `_handle_cancellation` but the heartbeat thread is independent), so the stale-task recovery predicate doesn't fire prematurely. Production observed 4s gap between `TimeoutError` and natural completion; 30s is well above that with 7x margin while still being bounded against pathological hangs.

**This is best-effort, not bulletproof:** if the grace window expires and the coroutine still finishes later, the original orphan-chain recurs. The PRIMARY defense against the race is the timeout ceiling: `graph_timeout_minutes=120` is now well below `task_timeout_minutes=125`, so the `CancellationToken` path usually fires first (yielding `OperationCancelledError` at the catch site). The `TimeoutError` path is the safety net for truly wedged coroutines that don't observe cancellation. With both defenses, the orphan-retry chain is fully closed in practice.

Regression tests in `tests/message_queue_redesign/test_worker_timeout.py::TestTimeoutOrphanRace`:
- `test_message_completes_within_grace_skips_retry` — production scenario (3 polls, then completed)
- `test_message_stays_processing_through_grace_proceeds_with_retry` — hung coroutine, retry scheduled
- `test_message_already_completed_at_first_poll_skips_retry` — narrow race window
- `test_message_failed_at_poll_skips_retry` — coroutine committed permanent failure
- `test_no_message_id_proceeds_with_retry` — defensive: helper bypass

---

### Bug C — Mark the no-op task COMPLETED

**File:** `daemon/services/task_processor.py` — `ProcessMessageProcessor.process`

Call `task_repo.complete_task` directly before returning the no-op:

```python
if message.status == MessageStatus.COMPLETED.value:
    logger.info(
        f"Task {task.id}: message {task.message_id[:8]}... already "
        f"COMPLETED — skipping graph turn (resume re-claim no-op)"
    )
    await asyncio.to_thread(
        self._task_repo.complete_task,
        task.id,
        {"success": True, "message_id": task.message_id, "skipped": True},
    )
    return {
        "success": True,
        "content": None,
        "message_id": task.message_id,
        "skipped": True,
    }
```

The status guard `WHERE status='running'` in `complete_task` makes this a no-op if a concurrent writer already transitioned the task. The worker's heartbeat stops writing as soon as `_process_with_timeout` returns.

---

### Timeout Bump — Raise ceiling to 2 hours

**Files:** `daemon/config.py` and `config.yaml`

| Setting | Old | New | Rationale |
|---------|-----|-----|-----------|
| `graph_timeout_minutes` | 55 | **120** | 2h ceiling (requested) |
| `task_timeout_minutes` | 60 | **125** | 5 min grace over graph_timeout so the `CancellationToken` path usually fires first |
| `stale_task_recovery_threshold_minutes` | 5 | **10** | Maintain safe ratio to graph timeout (1/12, down from 1/11) |
| `stale_task_cancel_grace_seconds` | 10 | **30** | Give the LLM stream a chance to flush a final token before the sweeper force-cancels |

`task_heartbeat_interval_seconds` stays at 30 — heartbeat is now 1/20 of the stale threshold (still 5x smaller than the documentation requirement). All values also flow through `config.yaml`'s `services:` block (verified by loading the YAML into `ServicesConfig`).

---

## Files Changed

| File | Purpose |
|------|---------|
| `daemon/services/error_reporting.py` | Bug A fix — route error reports through `manager.enqueue_message` |
| `daemon/services/task_processor.py` | Bug C fix — no-op path calls `complete_task` |
| `daemon/services/worker_pool.py` | Bug B fix — TIMEOUT branch checks message completion before retrying |
| `daemon/config.py` | New defaults for the four timeout settings |
| `config.yaml` | Mirror new defaults with explanatory comments |
| `tests/message_queue_redesign/test_timeout_retry_e2e.py` | Updated two default-value assertions |

---

## Verification

### Targeted test suites

```
tests/services/                                    passed
tests/message_queue_redesign/                       passed
tests/test_jq_error_reporting.py                    passed
tests/test_pipeline_unified.py                      passed
tests/test_cascade_unified.py                       passed
tests/test_cascade_concurrency.py                   passed
tests/test_observer_correlation.py                  passed
tests/test_finalize_job_threading.py                passed
tests/test_finalize_instance.py                     passed
tests/test_worker_notification.py                   passed
tests/test_worker_notification_edge_cases.py        passed
tests/test_dispatcher_path_invariants.py            passed
tests/test_dispatcher_path_equivalence.py           passed
```

Result: **515 passed, 71 skipped, 0 failed** across the directly affected suites.

The only failures in a wider sweep (`tests/unit/tools/test_memory_edge_cases.py`) are pre-existing and unrelated — verified by stashing the changes and re-running on main.

### Manual recovery action (not part of the fix)

The orphan `message_queue` row for `fd25551c-c978-4b80-a2ea-d58bf2c64644` (message id `c441c215-7deb-4298-86de-d0607be6dd59`) was left in the production DB at user's request. After deploying the fixes, ops can either:

1. Mark `c441c215` as `completed` (the child `5c7fe0f9`'s actual work succeeded — the orphan is a false-negative) and transition the root to `completed`, **or**
2. Delete `c441c215` and force-complete `fd25551c` directly (the legitimate success report `26fa3d78` was already processed by the parent at 02:40:27).

---

## Prevention Recommendations

1. **Shared "enqueue system message" helper.** All three sites (`error_reporting.py`, `child_reports._create_completion_report`, `instance_messaging.enqueue_message`) duplicate the same "create `MessageQueue` + `Task` in one tx + `notify_work()`" pattern. Extract a single helper to make it impossible to forget the Task row again.
2. **Post-fix regression test.** Add a test that exercises the production scenario: parent + child; child hits graph timeout AND completes successfully 1s later; verify (a) the parent receives the legitimate completion report, (b) no orphan `internal_error_report` is created, (c) the parent's transition to `completed` is not blocked.
3. **Telemetry on orphan messages.** Add a periodic sweeper that finds `message_queue` rows in `ready`/`processing`/`retrying` status with no corresponding `task` row older than 5 min and logs a warning. This is the early-warning signal for any future regressions of this class.
4. **Audit other `enqueue` call sites.** Search the codebase for any other code path that inserts into `message_queue` without also creating a `task` row. (Done at the time of fix — `error_reporting.py` was the only offender.)
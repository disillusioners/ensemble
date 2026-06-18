# Code Review (Round 2): Code-Review Findings Fix Commit

> **Historical review artifact.** The fix under review shipped successfully; the underlying race class is now fully addressed by the ExecutionGate. See [`docs/architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md) for the current state.

**Commit reviewed:** `3de5eea` fix(worker): code-review findings — restore `_truncate_error`, lock `_stats`, narrow catch, honest log lines
**Previous review:** `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.codereview.md`
**Date:** 2026-06-06
**Reviewer:** Kilo
**Overall verdict:** **3 of 3 required fixes correctly applied. But the fix exposed 7 broken tests in two unrelated test files that were not updated. Block merge until those are fixed.**

---

## 1. Status of previous required changes

| # | Finding | Status | Verification |
|---|---|---|---|
| 1 | 🔴 Restore `_truncate_error` whitespace-collapse | ✅ Fixed | `_truncate_error("<p>hello   world\n\nfoo</p>", 100) == 'hello world foo'` |
| 2 | 🟡 `_stats_lock` for thread-safe counters | ✅ Fixed | `_stats_lock` + `incr_stat()` helper; `get_stats()` snapshots under lock; verified with 4 writers × 1000 iters = 4000 increments, no loss |
| 3 | 🟡 `_ensure_postgres_columns` catch-all removed | ✅ Fixed | `try/except` removed; bare `conn.execute(text(stmt))` in loop; docstring documents the failure semantics |

All three are correctly implemented. The `incr_stat` design is clean — single call site for all worker-side writes, snapshot consistency for `get_stats`. The `RETURNING waiting_for` rewrite in the three counter sites is a nice improvement over `session.expire + session.get` (one less round trip, no stale-cache window).

---

## 2. New issues introduced by the fix

### 2.1 🔴 Critical — 7 tests fail (incomplete mock update)

The fix updated `MockWorkerPool` and `MockTaskProcessor` in `tests/message_queue_redesign/conftest.py` but **not** the local copies in two standalone test files. Result:

```
FAILED tests/test_worker_notification.py::TestNotificationMechanism::test_stop_wakes_sleeping_workers
FAILED tests/test_worker_notification_edge_cases.py::TestShutdownDuringWait::test_stop_wakes_sleeping_workers_within_timeout
FAILED tests/test_worker_notification_edge_cases.py::TestShutdownDuringWait::test_stop_during_active_wait_for_work
FAILED tests/test_worker_notification_edge_cases.py::TestEmptyClaimAttemptsIncrement::test_empty_claim_attempts_increments_on_worker_loop
FAILED tests/test_worker_notification_edge_cases.py::TestNotificationWithWorkersRunning::test_worker_pool_with_claim_that_returns_task
FAILED tests/test_worker_notification_edge_cases.py::TestNotificationWithWorkersRunning::test_worker_pool_stop_is_idempotent
FAILED tests/test_worker_notification_edge_cases.py::TestWorkerStats::test_worker_stats_tracked
```

**Root cause:** `Worker.__init__` (introduced in `7ead90a`) accesses `self._task_processor._task_repo` to construct `TaskHeartbeat`. The local `MockTaskProcessor` classes in `tests/test_worker_notification.py:41-57` and `tests/test_worker_notification_edge_cases.py:27-43` don't have `_task_repo`:

```python
# tests/test_worker_notification.py:41
class MockTaskProcessor:
    def __init__(self):
        self.claim_count = 0
        self.run_count = 0
        self.claimed_tasks = []
    # NO _task_repo attribute — Worker.__init__ raises AttributeError
```

**Pre-existing:** these 7 tests have been broken since `7ead90a` (the heartbeat commit). The fix in `3de5eea` didn't introduce the breakage but also didn't catch it — the commit message claims "Test count: 367 (was 365; +2 for the lock tests)" suggesting the suite was run, but only the `message_queue_redesign/` subset.

**Fix:** add `_task_repo` to both local `MockTaskProcessor` classes. Mirror the conftest pattern:
```python
class MockTaskProcessor:
    def __init__(self):
        self.claim_count = 0
        ...
        self._task_repo = self._MockTaskRepoForMetrics()

    class _MockTaskRepoForMetrics:
        def has_pending_tasks_blocked_by_busy_instance(self):
            return False

        def update_heartbeat(self, task_id):
            return True
```

`update_heartbeat` is needed because `TaskHeartbeat.set_task()` calls it eagerly on first beat.

### 2.2 🟡 Medium — `MockWorkerPool` in conftest.py missing `incr_stat`

`Worker.run()` now calls `self._worker_pool.incr_stat("empty_claim_attempts")`. The fix added `_stats_lock` to `MockWorkerPool` (`tests/message_queue_redesign/conftest.py:276`) but did **not** add the `incr_stat` method.

Tests that exercise `Worker.run` with this mock (`test_worker_stops_on_stop_event`, `test_worker_stops_on_no_work` in `test_worker_pool.py`) **pass but log spurious errors**:

```
ERROR daemon.services.worker_pool:worker_pool.py:276 Worker test-worker unexpected error:
  'MockWorkerPool' object has no attribute 'incr_stat'
```

The `AttributeError` is caught by Worker's outer `except Exception`, the worker enters `wait_for_work`, sees the stop event, and exits. Tests pass because they only assert `not worker.is_alive()`.

**Fix:** add `incr_stat` to `MockWorkerPool`:
```python
def incr_stat(self, key: str, delta: int = 1) -> None:
    with self._stats_lock:
        self._stats[key] = self._stats.get(key, 0) + delta
```

### 2.3 🟡 Medium — flaky `xfail` in `test_waiting_for_atomic.py` (pre-existing, unfixed)

The xfail detection logic at `tests/message_queue_redesign/test_waiting_for_atomic.py:245` is broken:

```python
if engine.dialect == sqlite_dialect():
    pytest.xfail(...)
```

`sqlite_dialect()` returns a NEW `DefaultDialect` instance each call. SQLAlchemy's dialect doesn't override `__eq__`, so this comparison falls back to identity (`is`). Two distinct instances are never equal, so the predicate is **always False** — the `xfail` never fires on SQLite.

Observed behavior: the test is flaky on SQLite (sometimes passes, sometimes fails with `assert 3 == 2` due to cross-thread snapshot visibility in pysqlite). The xfail was supposed to make this deterministic but doesn't.

**Pre-existing:** introduced in `2472f2e`. Not caught by `3de5eea`. Verified by running the test 5 times — fails every time on this machine under any concurrent test load.

**Fix:**
```python
if engine.dialect.name == "sqlite":
    pytest.xfail(...)
```

Or use a decorator: `@pytest.mark.xfail(condition=..., reason=..., strict=False)`.

### 2.4 🟢 Low — Two other `MockWorkerPool` copies are stale

There are **3 separate `MockWorkerPool` classes** in the test suite, and **4 separate `MockTaskProcessor` classes**:

| File | Class | State |
|---|---|---|
| `tests/message_queue_redesign/conftest.py:264` | `MockWorkerPool` | Partially updated (has `_stats_lock`, missing `incr_stat`) |
| `tests/message_queue_redesign/test_worker_timeout.py:32` | `MockWorkerPool` | **Stale**: no `_stats`, no `_stats_lock`, no `incr_stat`, no `stop_event` kwarg |
| `tests/message_queue_redesign/test_timeout_retry_e2e.py:31` | `MockWorkerPool` | **Stale**: same as above |
| `tests/message_queue_redesign/conftest.py:83` | `MockTaskProcessor` | Updated (has `_task_repo`) |
| `tests/message_queue_redesign/test_worker_timeout.py:70` | `MockTaskProcessor` | **Stale**: no `_task_repo` |
| `tests/test_worker_notification.py:41` | `MockTaskProcessor` | **Stale**: no `_task_repo` |
| `tests/test_worker_notification_edge_cases.py:27` | `MockTaskProcessor` | **Stale**: no `_task_repo` |

The stale `MockWorkerPool` classes in `test_worker_timeout.py` and `test_timeout_retry_e2e.py` aren't currently exercised by failing tests (those tests don't run `Worker.run`), but they're a maintenance hazard — the next contributor will hit the same trap.

**Fix (recommended, not blocking):** consolidate into a single `MockWorkerPool` and `MockTaskProcessor` in `tests/message_queue_redesign/conftest.py`, exposed as fixtures or importable classes. Delete the duplicates. The current setup makes every interface change ripple through 4-7 places, which is exactly how this regression slipped in.

---

## 3. What's still good

- The `RETURNING waiting_for` rewrite at all three counter sites is correct and honest. The dropped from-value in the log line is the right call — under contention, the pre-value would be a stale session-cache read, and the inferred "from" via `new + 1` is wrong when `MAX(0, ...)` clamped.
- The `_stats_lock` design with `incr_stat()` is well-factored. The lock-free read recommendation from the previous review was correctly **not** adopted — the cross-counter consistency of `get_stats()` matters more than the marginal throughput, and the lock contention is negligible (~80 increments/min at idle).
- The new `test_incr_stat_under_concurrent_writers` and `test_get_stats_returns_consistent_snapshot` are honestly labeled as smoke/structural tests. The commit message correctly notes that the per-increment race is hard to reproduce on modern CPython.
- The review doc update (§10.1, §10.2, §10.3) is thorough and accurately reflects the regression chain.

---

## 4. Required changes before merge

| # | Severity | Issue | Fix |
|---|---|---|---|
| 1 | 🔴 Critical | 7 tests fail because two local `MockTaskProcessor` classes lack `_task_repo` | Add `_task_repo` (and the inner `_MockTaskRepoForMetrics` with `has_pending_tasks_blocked_by_busy_instance` + `update_heartbeat`) to `tests/test_worker_notification.py:41-57` and `tests/test_worker_notification_edge_cases.py:27-43` |
| 2 | 🟡 Medium | `MockWorkerPool` in conftest.py is missing `incr_stat` (causes spurious ERROR logs in 2 tests) | Add `incr_stat` method matching the real `WorkerPool.incr_stat` |
| 3 | 🟡 Medium | `test_balanced_increments_and_decrements_threaded` xfail detection broken (always-False predicate) | Change `engine.dialect == sqlite_dialect()` to `engine.dialect.name == "sqlite"` |

## 5. Recommended changes (not blocking)

| # | Severity | Issue | Fix |
|---|---|---|---|
| 4 | 🟢 Low | 3 copies of `MockWorkerPool` and 4 of `MockTaskProcessor` across the test suite | Consolidate into shared fixtures in `tests/message_queue_redesign/conftest.py`; delete duplicates |
| 5 | 🟢 Low | Stale `MockWorkerPool` in `test_worker_timeout.py:32` and `test_timeout_retry_e2e.py:31` (no `_stats`, no `_stats_lock`, no `incr_stat`, no `stop_event` kwarg) | Update or replace with the consolidated fixture from #4 |

---

## 6. Summary

The three required fixes from the previous review are correctly applied. The new commit is well-engineered — the `incr_stat` design, the `RETURNING`-based logging, and the `_ensure_postgres_columns` cleanup are all improvements over what was asked for.

The blocker is that the conftest-only mock update wasn't propagated to the standalone test files. The fix updated 1 of 4 `MockTaskProcessor` classes and 1 of 3 `MockWorkerPool` classes — and the conftest `MockWorkerPool` itself is still missing `incr_stat`. Result: 7 hard failures + 2 silent ERROR logs + 1 flaky test.

After the 3 required changes in §4 are applied, this is mergeable. The §5 recommendations (mock consolidation) are a separate refactor.

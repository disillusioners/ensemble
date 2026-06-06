# Code Review: Per-Instance Claim Guard + Companion Fixes

**Branch:** `latest`
**Commits reviewed (HEAD~5..HEAD, in order applied):**
1. `c62fc09` — fix(task): notify workers on terminal task transitions
2. `d66478f` — fix(task): per-instance claim guard + observability + tightened stale recovery
3. `2472f2e` — fix(instances): atomic `waiting_for` counter at all three sites
4. `7ead90a` — fix(task): add per-task liveness heartbeat to distinguish live vs crashed

**Date:** 2026-06-06
**Reviewer:** Kilo
**Overall verdict:** **Approve with nits.** The core fixes are correct and well-tested. One real (minor) regression to revert, one metric race to fix, several documentation gaps. The 4th commit (heartbeat) is sound but represents significant scope creep — see §5.

---

## 1. Per-commit assessment

### 1.1 `c62fc09` — Notification hook ✅

Single-file change, mechanical. The placement is correct: `_notify_pending_task()` fires **after** the `with engine.begin() as conn:` exits (i.e., after commit), so workers waking up will see the new state. The `cancel_task` refactor (capture-then-return) is clean.

**One nit:** the comment in `cancel_task` says *"Notification is safe after the commit"* — but it would be safer to say *"Notification must be after the commit; doing it inside would wake workers that see pre-commit state."* This is a load-bearing invariant, not a reassurance.

### 1.2 `d66478f` — Per-instance claim guard ✅

**The core SQL is correct.** Verified against both dialects:
- Postgres READ COMMITTED: the inner `SELECT ... WHERE status='running'` and outer `UPDATE ... WHERE id = (SELECT ...)` run as one statement; the row being claimed becomes RUNNING atomically, so the next claim sees it.
- SQLite: writer-serialization makes the entire `engine.begin()` atomic.

The bind name `:status_running_guard` is distinct from `:status_running` (good — matches §9.3 of the review).

**Tests are solid.** `test_claim_skips_pending_tasks_for_busy_instance` covers the happy path; `test_claim_unblocks_when_sibling_fails` covers the fail path (not just complete); `test_has_pending_tasks_blocked_by_busy_instance` covers the metric signal transitions.

**Issues:**

- **🟡 Medium — `_stats["claims_skipped_due_to_busy_instance"] += 1` is not thread-safe.** `Worker.run` is a `threading.Thread`; with `num_workers=4` (default), 4 threads concurrently do `dict[key] += 1` on `self._worker_pool._stats`. In CPython, `+=` on a dict value is a read-modify-write that the GIL does **not** make atomic across the bytecode boundary. The metric will undercount under contention.
  - **Location:** `daemon/services/worker_pool.py:260, 267`
  - **Same issue affects pre-existing counters** (`empty_claim_attempts`, `workers_woken_by_timeout`) — not new in this PR — but the PR adds a new counter that compounds the problem.
  - **Fix:** wrap the dict in a thin `threading.Lock` for writes, OR switch to `itertools.count` per-worker and sum on read. The latter is cheaper and lock-free.
  - **Severity:** metric-only; no correctness impact. But the alert threshold (`> 0.5 of empty_claims for 10 min`) becomes unreliable if `claims_skipped_due_to_busy_instance` undercounts while `empty_claim_attempts` (incremented on the same line) has the same race — they'd tend to undercount proportionally, so the **ratio** is approximately preserved. Borderline acceptable; document the caveat.

- **🟢 Low — `has_pending_tasks_blocked_by_busy_instance` runs on every empty claim.** With 4 workers × 3s safety poll = up to 80 queries/min at idle. Two `EXISTS` index lookups each, cheap, but measurable on SQLite. Consider gating on `if self._task_processor.get_pending_count() > 0` first (already cached in `pool_pending_tasks` stat). Defer unless production telemetry shows it.

- **🟢 Low — `MockWorkerPool.wait_for_work(timeout, stop_event=None)` accepts `stop_event` for signature compatibility but ignores it.** Tests that exercise worker shutdown via `stop_event` and use this mock will hang until `_wait_timeout` fires. Not a current bug (existing tests don't trigger it), but worth a `# noqa: ARG002` comment to flag the intentional ignore.

### 1.3 `2472f2e` — Atomic `waiting_for` counter ✅

**The SQL is correct.** `MAX(0, COALESCE(waiting_for, 0) - 1)` works on both SQLite and Postgres; `MAX` is portable (§3 Step 3 of the review recommended this).

**The `session.expire(parent)` + `session.get(...)` pattern is correct** for forcing a re-read after the SQL UPDATE. Without it, SQLAlchemy would return the stale cached value on subsequent attribute access.

**Issues:**

- **🟢 Low — `old_waiting` in the log line can be stale under contention.** `daemon/services/child_reports.py:410-414`:
  ```python
  old_waiting = parent.waiting_for or 0  # read from session cache
  session.execute(text("UPDATE ... - 1 ..."))  # SQL UPDATE
  session.expire(parent)
  parent = session.get(...)  # re-read → fresh value
  new_waiting = parent.waiting_for or 0 if parent else 0
  logger.info(f"waiting_for decremented: {old_waiting} -> {new_waiting} ...")
  ```
  If another worker decremented between the read and the UPDATE, `old_waiting` reflects the pre-race cached value, not the value the SQL actually saw. The log line could read `2 -> 0` when in fact two decrements happened sequentially (`2 -> 1 -> 0`). Not a correctness bug; just misleading diagnostics.
  - **Fix (optional):** use `RETURNING waiting_for` on the UPDATE and read both old and new from the same statement. Or just drop `old_waiting` and log only the new value.

- **🟢 Low — `tools/instance.py:498` has the same stale-log issue.** The comment says "Read for the log line (informational only — not used for the write)" which is honest about the intent but the log can still mislead.

- **🟢 Low — Test `test_many_concurrent_decrements_under_contention` doesn't strongly prove atomicity on SQLite.** SQLite's writer lock serializes all writes, so even the buggy read-modify-write code would mostly pass this test on SQLite. The test catches the bug on Postgres (MVCC), where the bug actually manifests. The test docstring already notes this — just confirming the reasoning is sound.

- **🟢 Low — `test_balanced_increments_and_decrements_threaded` is `xfail` on SQLite.** Honest. But there's no Postgres CI in this PR — the test is effectively documentation, not enforcement. Consider adding a `@pytest.mark.postgres` and wiring a Postgres CI job for this file, or accept the limitation.

### 1.4 `7ead90a` — Per-task liveness heartbeat ✅ (with one regression)

**Concept is sound.** The Commit 2 regression (5-min threshold kills live long-running tasks) is real, and heartbeats are the right fix. The eager-first-beat in `set_task()` is a nice touch — covers the gap between claim and first tick.

**Issues:**

- **🔴 High (regression) — `_truncate_error` lost a line.** `daemon/services/worker_pool.py:30-40`:
  Before this commit:
  ```python
  if "<" in error and ">" in error:
      error = error.replace("<", " <").replace(">", "> ")
      error = re.sub(r"<[^>]+>", "", error)
      error = " ".join(error.split())  # ← removed
  ```
  After:
  ```python
  if "<" in error and ">" in error:
      error = error.replace("<", " <").replace(">", "> ")
      error = re.sub(r"<[^>]+>", "", error)
  ```
  The whitespace-collapse line was deleted. Result: error messages after HTML stripping now keep multi-spaces and newlines. The docstring also changed from "stripping HTML if present" to "stripping HTML if present" — same intent, but the implementation no longer matches.
  - **Fix:** restore the line. It's unrelated to the heartbeat work; looks like a stray delete during a rebase.
  - **Severity:** cosmetic but it affects error readability in logs and UI.

- **🟡 Medium — `_ensure_postgres_columns` swallows all exceptions.** `daemon/manager.py:1268-1273`:
  ```python
  for stmt in statements:
      try:
          conn.execute(text(stmt))
      except Exception as e:
          logger.warning(f"Postgres column migration statement skipped: ... ({e})")
  ```
  `IF NOT EXISTS` already handles the "column exists" case. The catch-all also swallows: syntax errors, permission errors, connection failures, disk-full. Any of those means the column doesn't exist, and the very next query (`backfill_heartbeats` → `find_cancellable_tasks` → references `last_heartbeat_at`) will hard-fail and crash the daemon.
  - **Fix:** catch only `sqlalchemy.exc.ProgrammingError` (column already exists is `DuplicateColumn` on Postgres, which `IF NOT EXISTS` should prevent). Log + continue on that; re-raise everything else. Or: don't catch at all — `IF NOT EXISTS` makes the try/except unnecessary.

- **🟡 Medium — `_row_to_task` uses `hasattr(row, 'last_heartbeat_at')`.** `daemon/repositories/task/repository.py:270`:
  ```python
  last_heartbeat_at=row.last_heartbeat_at if hasattr(row, 'last_heartbeat_at') else None,
  ```
  SQLAlchemy row objects always expose all columns from the result set. The `hasattr` is dead defensive code unless you're passing a mock that doesn't have the column. Either commit to the column always being there (it is, after the migration), or make the fallback explicit with a documented reason (test mocks). The current form obscures intent.
  - Same pattern appears for other columns above (`retry_count`, `next_retry_at`, etc.) — pre-existing, but the PR adds another instance. Not introduced by this PR.

- **🟢 Low — Heartbeat thread leak on Worker startup race.** `Worker.run` calls `self._heartbeat.start()` first, then enters the main loop. If `start()` succeeds but the main loop's first `claim_task` raises before any `set_task(None)` cleanup runs, the heartbeat thread keeps running but `current_task_id` is `None`, so it does no harm. The `finally: self._heartbeat.stop()` in `run()` cleans up. Confirmed safe.

- **🟢 Low — `TaskHeartbeat._beat_now` swallows all exceptions.** Intentional per the docstring. But for permanent errors (e.g., table missing), every beat logs a warning — generates noise. Consider rate-limiting the warning to once per N failures, or distinguishing transient vs permanent via exception type.

- **🟢 Low — `Worker.run`'s outer `except Exception` calls `self._heartbeat.set_task(None)`.** Good defensive cleanup. But the inner `finally` (around `_process_with_timeout`) also clears it. Redundant but harmless (idempotent).

- **🟢 Low — `MockTaskProcessor._MockTaskRepoForMetrics` is a nested class.** Slightly awkward for tests that want to override the behavior. Would be cleaner as a top-level `MockTaskRepo` or a `MagicMock(spec=TaskRepository)`. Pre-existing pattern though.

---

## 2. Cross-cutting observations

### 2.1 The `MockWorkerPool.wait_for_work` signature drift

The mock now accepts `stop_event=None` for signature compatibility with the real `WorkerPool.wait_for_work`. But it ignores `stop_event`. If a future test passes a stop_event expecting the mock to honor it, the test will hang.

**Fix:** either honor `stop_event` in the mock (mirror the real implementation's logic — ~5 lines), or assert it's None and raise if not. The current silent-ignore is a foot-gun for future test authors.

### 2.2 The 4th commit (heartbeat) is significant scope creep

The original review's §10 PR plan was 3 commits. Commit 4 adds:
- 1 schema migration
- 1 schema-evolution strategy split (SQLite via .sql, Postgres via `_ensure_postgres_columns`)
- 1 new background thread per worker
- 969 lines

This is **legitimate** — the 5-min threshold introduced in Commit 2 created a regression (live long tasks get killed) that needed fixing before deploy. But the review doc and bug doc should be updated to reflect that:
- Commit 2 introduced the regression
- Commit 4 is the mitigation
- The original review did not anticipate this (it focused on "threshold too high" as a config tuning issue, not as a structural design flaw in `started_at`-only staleness)

**Recommendation:** update `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.review.md` §10 to add Commit 4 and explain the regression chain. Otherwise the doc trail is misleading for future readers.

### 2.3 Postgres schema evolution now has two paths

Every new column needs:
1. A `.sql` migration file in `daemon/migrations/versions/` (SQLite-only, applied by `MigrationRunner`)
2. A new statement in `InstanceManager._ensure_postgres_columns` (Postgres-only, applied at startup)

This divergence will accumulate tech debt. **Not a blocker for this PR**, but worth a follow-up issue: either extend `MigrationRunner` to support Postgres, or unify on Alembic. Document the divergence with a prominent comment in `daemon/migrations/runner.py` so the next contributor doesn't add a SQLite-only migration for a feature that needs Postgres support.

### 2.4 The metric race in `_stats` (repeated for emphasis)

This is the only finding that could matter operationally. The `claims_skipped_due_to_busy_instance` metric is meant to drive an alert (`> 0.5 for 10 min`). If the counter undercounts due to the race, the alert will fire less often than it should, masking real production issues.

The cheap fix:
```python
# In WorkerPool.__init__:
self._stats_lock = threading.Lock()

# Wrap all writes:
with self._stats_lock:
    self._stats["claims_skipped_due_to_busy_instance"] += 1
```

Or use `itertools.count` per worker (lock-free):
```python
self._per_worker_skipped = {w.worker_id: itertools.count() for w in self._workers}
# read: sum(c.value for c in self._per_worker_skipped.values())
```

Either is ~5 lines.

---

## 3. Test coverage assessment

| Commit | New tests | Coverage assessment |
|---|---|---|
| c62fc09 (notify) | 0 | Indirectly covered by d66478f's `test_claim_unblocks_when_sibling_fails` (calls `fail_task`, then asserts next claim succeeds — would fail if notification didn't fire). OK. |
| d66478f (claim guard) | 4 | Direct coverage of the guard, the unblock-on-fail path, and the metric signal. Good. |
| 2472f2e (atomic counter) | 289 lines | Covers atomic increment/decrement, mixed ops, clamp at 0. SQLite-xfail on the strongest concurrency test is documented. Good. |
| 7ead90a (heartbeat) | 470 lines / 19 tests | Covers `update_heartbeat`, `backfill_heartbeats`, recovery predicate with COALESCE, `TaskHeartbeat` thread lifecycle, end-to-end keepalive. Excellent. |

**Gap:** no integration test for the full race scenario described in the bug doc (two workers claiming sibling tasks for the same instance). The unit tests verify the guard at the SQL level, but the end-to-end "two workers, same instance, observe correct LLM context" is not covered. This is hard to test (requires real LangGraph + checkpointer), so acceptable — but worth a comment in the bug doc noting that the fix is verified at the SQL layer, not the LangGraph layer.

**Gap:** no test for `_ensure_postgres_columns` failure modes. Specifically, what happens if the ALTER TABLE fails (e.g., permission denied)? Currently: warning logged, daemon continues, crashes on first `last_heartbeat_at` reference. A unit test that patches the column-existence check to fail would catch the catch-all-swallow issue in §1.4.

---

## 4. Required changes before merge

| # | Severity | Commit | Issue | Fix |
|---|---|---|---|---|
| 1 | 🔴 High | 7ead90a | `_truncate_error` lost its whitespace-collapse line | Restore `error = " ".join(error.split())` |
| 2 | 🟡 Medium | d66478f + pre-existing | `_stats[...] += 1` is not thread-safe | Wrap in lock or use per-worker counters |
| 3 | 🟡 Medium | 7ead90a | `_ensure_postgres_columns` swallows all exceptions | Catch only `ProgrammingError`; let others propagate |

## 5. Recommended changes (not blocking)

| # | Severity | Issue | Fix |
|---|---|---|---|
| 4 | 🟢 Low | Stale `old_waiting` in decrement log line | Use `RETURNING` or drop old value |
| 5 | 🟢 Low | `MockWorkerPool.wait_for_work` silently ignores `stop_event` | Honor it or assert None |
| 6 | 🟢 Low | Doc trail: review doc §10 doesn't mention Commit 4 or the regression chain | Update review doc |
| 7 | 🟢 Low | Postgres schema evolution split between two files | Add a comment in `daemon/migrations/runner.py` documenting the divergence; create follow-up issue for unification |
| 8 | 🟢 Low | `_row_to_task` uses `hasattr` defensively for new column | Either commit to the column always existing post-migration, or document why the fallback is needed |

---

## 6. Summary

The implementation faithfully follows the review's recommended plan and adds a legitimate fourth commit (heartbeats) to fix a regression introduced by Commit 2's threshold reduction. The core fix (per-instance claim guard in `claim_pending_task`) is correct, atomic on both supported dialects, and well-tested. The atomic-counter fix (Commit 3) is straightforward SQL and well-tested.

The three required changes are small:
- Restore one accidentally-deleted line in `_truncate_error`.
- Add a lock around `_stats` writes (or convert to per-worker counters).
- Narrow the exception catch in `_ensure_postgres_columns`.

After those, this is mergeable. The recommended changes can land as follow-up commits or be deferred.

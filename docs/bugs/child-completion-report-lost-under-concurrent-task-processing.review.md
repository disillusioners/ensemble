# Review & Solution Proposal: Child Completion Report Lost Under Concurrent Task Processing

**Companion to:** `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md`
**Date:** 2026-06-06
**Status:** Proposal — code not applied yet

---

## 1. Verdict on the Root-Cause Analysis

The bug report's diagnosis is **correct and well-supported by the evidence**:

- Two workers ran tasks 108 and 119 in parallel for the same `thread_id` (`daemon/services/worker_pool.py:88-127`).
- `claim_pending_task` has no per-instance guard (`daemon/repositories/task/repository.py:116-161`).
- The LangGraph Postgres checkpointer reconstructs channel state from `(parent_checkpoint_id, task_id)`; two concurrent writers on the same thread racing on `aget_state` can produce non-merging channel states, "shadowing" one write from the other's view.
- The job-level concurrency check in `message_job_handler.py:66-97` is bypassed because the worker pool dispatches from the `task` table, not `job_queue_items`.

One nuance worth adding: the bug report says the `waiting_for` lost-update race did not fire in this run because the children completed 35 minutes apart. That's true for the `child_reports.py:402-410` decrement path, but **the symmetric increment path in `tools/instance.py:488-493` (send_message → waiting_for++) is more exposed**: a parent can call `send_message` to two children in rapid succession inside one LLM turn, and both increments run sequentially inside the same `WriteGuardSession` — that path is actually serialized through SQLAlchemy's session, so it's safe in practice. The decrement path (`child_reports.py`, `error_reporting.py`) runs from different worker threads on different child-completion events, so it IS exposed.

---

## 2. Critique of the Proposed Fixes

### Fix A (in-process `dict[instance_id, threading.Lock]`)

**Pros:** Simple to implement, no DB schema changes, no migration.

**Cons:**
1. **Claimed-then-blocked workers reduce effective parallelism.** With N workers and M busy instances (M ≥ N), all workers can end up blocked on instance locks they can't acquire, even when there is other (unrelated) work to do. This is masked only because the bug is rare, but on a busy daemon it would degrade throughput.
2. **Unbounded dict growth.** `setdefault` never removes entries. Need explicit cleanup (e.g., remove key when lock has no waiters on release), or use `weakref.WeakValueDictionary` — but `threading.Lock` cannot be weak-ref'd directly (needs a wrapper).
3. **Process-local.** If the daemon ever scales to multiple processes (HA, rolling restarts), in-process locks do not coordinate. The DB-level guard does.
4. **Wrong site for the lock.** The lock must be acquired **after** `claim_task` and **before** `run_task`, but by then the task's status is already RUNNING. If the worker then blocks for minutes waiting for the lock, that RUNNING task is also blocking any recovery/stale detection (which looks at `started_at`). Mitigation is non-trivial.

### Fix B (SQL filter at claim time) — **Preferred**

**Pros:**
- Enforced by the database; works across processes, survives restarts, no in-memory state to leak.
- Workers never block — they just don't claim tasks for busy instances, so unrelated work proceeds normally.
- Minimal code change (one SQL statement + one notification hook).
- Composes naturally with the existing `task.status` lifecycle.

**Cons / edge cases to handle:**
- **Latency when in-flight task completes.** With Fix B, while task 108 is running, task 119 sits PENDING. When 108 completes, no notification is fired today (`complete_task` doesn't call `_notify_pending_task`). Workers rely on the 3s safety timeout. **Fix: `complete_task` should call `_notify_pending_task()`** so a worker wakes immediately and claims the newly-eligible task 119.
- **Postgres subquery semantics.** The proposed `instance_id NOT IN (SELECT DISTINCT instance_id FROM task WHERE status = 'running')` is correct under READ COMMITTED because both the inner SELECT and outer UPDATE run in the same statement; the row being claimed becomes RUNNING atomically. Confirmed safe.
- **SQLite semantics.** SQLite serializes writers at the database level (single writer), so the entire `engine.begin()` is atomic. The subquery is also safe here.
- **Empty-result spurious notifications.** `notify_work()` may wake workers that find nothing claimable. Already handled by `empty_claim_attempts` metric. No change needed.

### Fix C (atomic `waiting_for` counter) — **Independent and trivial**

This fix is independent of A/B and should be applied **unconditionally**. The proposal is correct. One nit: prefer `GREATEST(0, waiting_for - 1)` over `MAX(0, ...)` for Postgres portability (both work, but `GREATEST` is the conventional scalar form). SQLite supports `MAX(a, b)` as a synonym.

### Fix D (apply `message_job_handler`'s check at task level)

**Redundant if Fix B is applied.** Fix B is strictly stronger because it's enforced atomically at the moment of claim, with no read-then-write window. Fix D's check is a TOCTOU: between the SELECT and the back-transition, another worker can claim the same task. Skip D if B is applied.

---

## 3. Recommended Implementation

**Apply Fix B + Fix C + the notification hook. Skip A and D.**

### Step 1 — Fix B: per-instance guard in `claim_pending_task`

File: `daemon/repositories/task/repository.py:116-161`

```python
def claim_pending_task(self, worker_id: str) -> Task | None:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%f") + now.strftime("%z")

    with self.engine.begin() as conn:
        stmt = text("""
            UPDATE task
            SET status = :status_running,
                worker_id = :worker_id,
                started_at = :started_at
            WHERE id = (
                SELECT id FROM task
                WHERE status = :status_pending
                AND (next_retry_at IS NULL OR next_retry_at <= :now_str)
                AND instance_id NOT IN (
                    SELECT instance_id FROM task
                    WHERE status = :status_running
                )
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING *
        """)
        row = conn.execute(stmt, {
            "status_running": TaskStatus.RUNNING.value,
            "worker_id": worker_id,
            "started_at": now,
            "status_pending": TaskStatus.PENDING.value,
            "status_running_guard": TaskStatus.RUNNING.value,
            "now_str": now_str,
        }).fetchone()

        if row is None:
            return None
        return self._row_to_task(row)
```

**Rationale for the dedup param:** We pass `:status_running` twice (once for the SET, once for the guard subquery). Using a separate bind name (`status_running_guard`) makes the intent explicit and lets us swap the guard predicate later (e.g., to exclude retry-pending tasks) without touching the SET clause.

### Step 2 — Notification hook in `complete_task` / `fail_task` / `cancel_task`

Without this, after task 108 completes, task 119 sits in PENDING until the next 3s safety poll. Add to each terminal transition:

```python
# In complete_task, fail_task, cancel_task, AFTER db_session.commit():
self._notify_pending_task()
```

Specifically, append one line at the end of each of:
- `daemon/repositories/task/repository.py:complete_task` (after `db_session.refresh(task)`)
- `daemon/repositories/task/repository.py:fail_task` (after `db_session.refresh(task)`)
- `daemon/repositories/task/repository.py:cancel_task` (after the final fetch)

`schedule_retry` and `force_cancel_and_schedule_retry` already call `_notify_pending_task()`.

### Step 3 — Fix C: atomic `waiting_for` counter

Three sites. Each becomes a single SQL UPDATE inside the existing transaction.

#### Site 1 — `daemon/services/child_reports.py:402-410`

Replace:
```python
old_waiting = parent.waiting_for or 0
parent.waiting_for = max(0, old_waiting - 1)
```
with:
```python
session.execute(
    text("UPDATE instances SET waiting_for = MAX(0, COALESCE(waiting_for, 0) - 1) "
         "WHERE instance_id = :pid"),
    {"pid": parent.instance_id},
)
session.expire(parent)  # force reload on next access in this session
parent = session.get(Instance, parent.instance_id)  # re-read for the cascade check below
```
Then keep the rest of the cascade logic (`if parent.waiting_for == 0 ...`) as-is.

#### Site 2 — `daemon/services/error_reporting.py:184-186`

Symmetric. Same SQL pattern. (Postgres: `GREATEST(0, COALESCE(waiting_for, 0) - 1)`; SQLite accepts both `MAX` and `GREATEST`. Use `MAX` for cross-dialect consistency.)

#### Site 3 — `daemon/tools/instance.py:488-493`

```python
session.execute(
    text("UPDATE instances SET waiting_for = COALESCE(waiting_for, 0) + 1 "
         "WHERE instance_id = :pid"),
    {"pid": current_instance_id},
)
session.commit()
```

### Step 4 — Tests

Add to `tests/message_queue_redesign/test_task_repository.py`:

```python
def test_claim_skips_pending_tasks_for_busy_instance(repository, sample_task_data):
    """Fix B: a pending task for an instance with a RUNNING task must not be claimed."""
    # Create two pending tasks for the same instance
    t1 = repository.create("process_message", instance_id="inst-A", message_id="m1")
    t2 = repository.create("process_message", instance_id="inst-A", message_id="m2")
    # Create a pending task for a different instance
    t3 = repository.create("process_message", instance_id="inst-B", message_id="m3")

    # Worker 1 claims t1 (now RUNNING for inst-A)
    claimed1 = repository.claim_pending_task("worker-1")
    assert claimed1.id == t1.id

    # Worker 2 cannot claim t2 (inst-A is busy); claims t3 instead
    claimed2 = repository.claim_pending_task("worker-2")
    assert claimed2.id == t3.id

    # Worker 3 cannot claim t2
    claimed3 = repository.claim_pending_task("worker-3")
    assert claimed3 is None

    # Worker 1 finishes t1
    repository.complete_task(t1.id, {"success": True})

    # Now t2 is claimable
    claimed4 = repository.claim_pending_task("worker-3")
    assert claimed4.id == t2.id
```

Add to `tests/message_queue_redesign/test_message_flow.py` (or a new `test_child_reports_concurrency.py`):

```python
def test_waiting_for_decrement_is_atomic_under_concurrency(real_db):
    """Fix C: two concurrent child completions decrement waiting_for from 2 to 0, not 1."""
    # Create parent with waiting_for=2
    # Spawn two threads, each running the child-completion decrement path
    # Assert final waiting_for == 0
```

### Step 5 — Observability

Add a metric to `WorkerPool._stats`:

- `claims_skipped_due_to_busy_instance`: incremented when `claim_pending_task` returns None but a `SELECT COUNT(*) FROM task WHERE status='pending'` would have returned > 0. (One extra query on the empty-claim path; cheap.) This surfaces "are tasks being deferred due to Fix B?" in production.

---

## 4. Migration & Rollout

- **No schema migration required.** Fix B is pure SQL; Fix C is pure SQL.
- **No breaking change** to the API or worker protocol.
- **Backward-compatible with existing in-flight tasks.** Tasks already RUNNING at deploy time remain RUNNING; new claims start respecting the guard on the next poll.
- **Single PR.** All four steps (B, notification hook, C, tests) in one PR — they're tightly coupled and small.

---

## 5. What's NOT Addressed (and shouldn't be)

- **LangGraph checkpointer internals.** We're not changing how `add_messages` or `put_writes` work. The fix prevents the concurrency, not the underlying channel-state semantics.
- **The `child_reports.py` "enqueue a new task" pattern.** Some teams would prefer to write the report directly to the checkpoint channel and let the in-flight parent task see it on its next `astream` iteration. That's a deeper redesign — out of scope here, and Fix B makes the current pattern safe.
- **Multi-process daemon.** Not supported today. Fix B happens to be compatible with it if/when added; Fix A would not be.

---

## 6. Quick Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| Fix B causes worker starvation under heavy load | Low — there are usually multiple instances pending | Observability metric (Step 5) + 3s safety poll |
| Notification hook fires too often | None | `notify_work` is idempotent and cheap (one `Condition.notify`) |
| Fix C SQL differs across SQLite/Postgres | None | `MAX(0, COALESCE(...))` works on both |
| Existing tests break | Low | All `claim_pending_task` tests in `test_task_repository.py` use distinct instances; new test added in Step 4 |

---

## 7. Summary

| Fix | Apply? | Why |
|---|---|---|
| **Fix A** (in-process locks) | ❌ No | Inferior to B; blocking workers, lock cleanup issues, not cross-process safe |
| **Fix B** (SQL claim guard) | ✅ Yes | Enforced by DB, non-blocking, cross-process safe, minimal change |
| **Fix C** (atomic counter) | ✅ Yes | Independent, trivial, eliminates a known lost-update site |
| **Fix D** (task-level concurrency check) | ❌ No | Redundant with B; TOCTOU-vulnerable |
| **Notification hook** | ✅ Yes | Required for Fix B to not add latency |

Total change: ~30 lines of SQL + 3 notification hook calls + tests. One PR.

---

## 8. Open Questions for Re-Discussion

The previous version of this review missed two verification steps that affect whether Fix B is **sufficient** and **safe**. Team should resolve these before code is written.

### 8.1 Does `stale_task_recovery` transition crashed-worker tasks back to PENDING?

**Concern:** Fix B's guard predicate is `instance_id NOT IN (SELECT instance_id FROM task WHERE status='running')`. If a worker process is killed (OOM, SIGKILL, host reboot) while holding a RUNNING task, that task stays `RUNNING` forever. The `task.repository` has a `stale_task_recovery` service in `daemon/services/stale_task_recovery.py` — **we need to confirm it transitions stale `RUNNING` → `PENDING` (or `FAILED`)**. If it only logs, Fix B will cause a hard deadlock the first time any worker crashes.

**Action:**
1. Read `daemon/services/stale_task_recovery.py` and `daemon/services/timeout_monitor.py`.
2. Confirm there is a recovery path that moves `task.status='running'` rows whose `worker_id` no longer corresponds to a live worker, or whose `started_at` exceeds a timeout, back to `pending` (or marks them `failed` and emits a new pending task for retry).
3. If recovery exists, decide whether the timeout threshold needs to be lowered — Fix B makes a stale RUNNING much more visible (it now blocks all sibling tasks for that instance), so a 5-minute stale threshold is probably too long.
4. If recovery does **not** exist, Fix B cannot ship. Either add recovery, or fall back to a TTL on the guard (e.g., exclude RUNNING tasks whose `started_at` is older than N minutes from the guard subquery, not the from-claim check).

### 8.2 What is the scope of LangGraph's `versions_seen`?

**Concern:** The review assumes Fix B at the `claim_pending_task` layer is sufficient to prevent the channel-shadowing. But the underlying mechanism — two workers calling `graph.astream` for the same `thread_id` — interacts with the LangGraph checkpointer's optimistic concurrency. If `versions_seen` is keyed per-`thread_id` (rather than per-`(thread_id, task_id)`), then even with Fix B, the *next* race that hits us is at the checkpoint-commit layer: two writes referencing the same `parent_checkpoint_id` may have one silently dropped by the checkpointer.

**Action:**
1. Read `daemon/persistence.py` (CheckpointerAdapter) and identify the underlying checkpointer class (likely `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`).
2. Inspect the `versions_seen` semantics: per-`thread_id` or per-`(thread_id, task_id, checkpoint_ns)`?
3. If per-`thread_id`, Fix B prevents the *cause* (two concurrent claims) but does not protect against a single writer making two conflicting writes. Confirm that the `add_messages` reducer genuinely handles concurrent appends idempotently (key by message `id`).
4. If the checkpointer is per-thread, recommend a follow-up defense-in-depth: catch `GraphRecursionError` / `InvalidUpdateError` in `Worker._process_with_timeout` and re-claim the task once (with backoff), rather than failing the task outright. This is cheap and covers the residual race.

### 8.3 SQL bind naming nit (minor)

The review's SQL example uses the same `:status_running` bind key for both the SET clause and the guard subquery. The accompanying "Rationale" paragraph recommends using a distinct name (`status_running_guard`) so the two clauses can evolve independently. **The example code in Step 1 and the rationale are inconsistent** — pick one. Recommendation: use two distinct bind names in the actual code, with the rationale as written.

### 8.4 Order of changes in the PR

Recommend: **notification hook first, then Fix B, then Fix C**, each in its own commit. This way, if Fix B alone causes worker starvation in production, we can revert it without losing the atomic-counter fix (Fix C is independent). Combining all three into one commit makes rollback harder.

### 8.5 Observability threshold for the new metric

The `claims_skipped_due_to_busy_instance` metric is good, but we should also set an **alert** threshold during rollout. Suggest: alert if `claims_skipped_due_to_busy_instance / total_empty_claims > 0.5` for 10 minutes — that would indicate Fix B is causing excessive task deferral. Define the alert in the same PR.

---

## 9. Resolutions to Open Questions

### 9.1 §8.1 — StaleTaskRecovery: VERIFIED, but threshold is too high

**Verified by reading:** `daemon/services/stale_task_recovery.py:1-442`, `daemon/manager.py:1240-1268`, `daemon/config.py:195-225`.

**Findings:**
1. **Recovery does exist and is started.** `daemon/manager.py:1268` calls `stale_recovery.start()` after `recover_on_startup()`. The periodic loop runs every `stale_task_recovery_interval` (default **60 s**).
2. **The recovery path is correct for Fix B.** `recover_stale_tasks()` calls `find_cancellable_tasks(threshold_minutes=...)` → `request_cancel()` → 10 s grace → `force_cancel_and_schedule_retry()` (single-transaction atomic). When the stale task transitions to CANCELLED, the new retry task is PENDING — which makes the guard subquery (`instance_id NOT IN (SELECT ... WHERE status='running')`) immediately start returning that instance again. So Fix B does **not** deadlock.
3. **But the threshold is wrong for Fix B's guarantee.** The current default `task_timeout_minutes=60.0` is what's wired into the recovery threshold (`daemon/manager.py:1255`). That means a crashed worker can leave a sibling task blocked for up to **~61 minutes** (60 min threshold + 60 s poll interval + 10 s grace). The 15-minute figure in the StaleTaskRecovery docstring is the *function default*, but the runtime config overrides it to 60.

**Resolution:** Fix B can ship, but **the threshold must be lowered**. Sibling-task blocking is a new visibility introduced by Fix B, so the stale-detection window should be sized for that, not for "user-visible task timeout".

Concrete change for Commit 2:
- Add a new config field `stale_task_recovery_threshold_minutes` (separate from `task_timeout_minutes`) with default **5 minutes**.
- Wire it into `StaleTaskRecovery(threshold_minutes=...)` at `daemon/manager.py:1255`.
- Document that this is the maximum time a sibling task will be blocked after a worker crash.

Optional: also reduce `stale_task_recovery_interval` to 30 s in the same commit, so the worst case becomes ~5.5 minutes instead of ~6.

### 9.2 §8.2 — LangGraph checkpointer: NO optimistic concurrency to lean on

**Verified by reading:** `.venv/lib/python3.14/site-packages/langgraph/checkpoint/postgres/base.py:131-159`, `.venv/lib/python3.14/site-packages/langgraph/checkpoint/base/__init__.py:108-147`, and `langgraph/checkpoint/postgres/aio.py:300-339`.

**Findings:**
1. **`versions_seen` is per-node, not a concurrency guard.** From `checkpoint/base/__init__.py:115-120`: *"Map from node ID to map from channel name to version seen. Used to determine which nodes to execute next."* It is a scheduling input, not a write-conflict detector.
2. **The Postgres checkpointer has no per-thread optimistic concurrency.** The relevant writes are:
   - `UPSERT_CHECKPOINT_WRITES_SQL`: `ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO UPDATE SET channel=..., type=..., blob=...` — last-writer-wins for the same `(checkpoint_id, task_id, idx)`.
   - `INSERT_CHECKPOINT_WRITES_SQL`: `ON CONFLICT (...) DO NOTHING` — first-writer-wins.
   - `UPSERTS_CHECKPOINTS_SQL`: `ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE` — last-writer-wins for the same `checkpoint_id`.
   - None of these enforce that a writer's `parent_checkpoint_id` matches the current head.
3. **The shadowing described in the bug report is therefore exactly what the upstream checkpointer permits.** Two writers each commit writes with different `task_id`s (since they're different invocations), so neither ON CONFLICT clause fires. The two writes coexist in `checkpoint_writes`. The next `aget_state` reads both, but the **intermediate checkpoint written by writer A may overwrite writer B's view** via `UPSERTS_CHECKPOINTS_SQL` (last-writer-wins on `checkpoint_id` — and both writers' new `checkpoint_id` values are different, so they coexist as siblings; `aget_state` picks the latest by `checkpoint_id` lexicographic order, which is UUID-based, not time-based, hence unpredictable).

**Resolution:**
- **Fix B at the claim layer is the correct and sufficient primary defense.** The bug report's hypothesis that some `versions_seen`-based check would catch this was wrong — there is none.
- **Drop §8.2's Commit 4 (`GraphRecursionError` catch-and-retry).** It was premised on the checkpointer providing a signal it does not provide. With Fix B in place, two writers on the same `thread_id` cannot exist, so the residual race is gone.
- **Optional defense-in-depth (separate PR, not part of this fix):** wrap `graph.astream` in a per-instance `asyncio.Lock` *inside* `_process_message_with_tracking` to guard against any code path that bypasses the worker pool (e.g., direct API triggers, the legacy job path). This is the equivalent of Fix A but at the correct layer (instance_messaging, not worker_pool) and only as defense-in-depth, not primary.

### 9.3 §8.3 — SQL bind naming: ACCEPTED

The §3 Step 1 example uses `:status_running_guard` (correct). The paragraph above it should be reworded from *"The proposed SQL uses the same `:status_running` bind key for both the SET clause and the guard subquery"* to *"Use two distinct bind names so the SET clause and the guard subquery can evolve independently."* The example code already does this — only the prose needs the edit, which is included in the next refresh of this doc.

### 9.4 §8.4 — PR ordering: ACCEPTED

The 3-commit split (notification hook → Fix B + alert → Fix C) is adopted. Each commit is independently revertible.

### 9.5 §8.5 — Alert threshold: ACCEPTED

`claims_skipped_due_to_busy_instance / total_empty_claims > 0.5 for 10 min` alert added to Commit 2. The exact threshold is tunable post-rollout; 0.5 is conservative.

---

## 10. Final PR Plan (supersedes §9 of the original review)

| Commit | Scope | Revertible? |
|---|---|---|
| **1. Notification hook** | Add `self._notify_pending_task()` to `complete_task`, `fail_task`, `cancel_task`. No behavior change for current callers; removes up to 3 s of latency when Fix B lands. | Yes — pure additive |
| **2. Fix B + observability + threshold tuning** | (a) SQL claim guard in `claim_pending_task`. (b) New `claims_skipped_due_to_busy_instance` metric. (c) Alert at >50% skip rate. (d) New config `stale_task_recovery_threshold_minutes=5` wired into `StaleTaskRecovery`. (e) Test in Step 4 of §3. | Yes — revert restores FIFO claim |
| **3. Fix C — atomic counter** | SQL UPDATE at all 3 sites (`child_reports.py`, `error_reporting.py`, `tools/instance.py`). Concurrency test in Step 4 of §3. | Yes — independent of Fix B |

**Out of scope, separate follow-up:**
- Per-instance `asyncio.Lock` in `_process_message_with_tracking` as defense-in-depth (per §9.2).
- Any change to `child_reports.py`'s "enqueue a new task" pattern (kept as-is).

**Drop from original §9:** Commit 4 (`GraphRecursionError` catch-and-retry) — not needed, see §9.2.

Roll back strategy: each commit is independently revertible. Fix C is the safest to keep even if Fix B is reverted.

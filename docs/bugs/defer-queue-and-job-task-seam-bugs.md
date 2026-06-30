# Bug: Defer Queue Stuck "processing" + Admitted While Virtual Jobs Active (and the wider job⇄task seam)

> **Status:** Investigated; solution scoped below. No code changes yet.

**Date:** 2026-06-30
**Severity:** High (silent — job appears "processing" forever; defer-queue isolation invariant violated; plus 17 sibling seam bugs)
**Reproduction:** `logs/dev_run.log` (job `1fdd0583-b4cd-4d9f-8098-fc49b5c58e0f`, instance `3a9ddb63-9223-41e6-9efa-42b597246dbe`)
**Affected components:**
- `daemon/services/job_processor.py` (`_process_next_job` defer idle-gate, `enqueue_message` without `message_id` linkage / `is_deferred`)
- `daemon/repositories/task/repository.py` (`claim_pending_task` cross-system guard + defer idle-gate)
- `daemon/services/job_queue_service.py` (`count_active_jobs_in_non_defer_queues`, `_select_next_eligible_job`, `_finalize_terminal` lock release)
- `daemon/services/instance_messaging.py` (`enqueue_message`)
- `daemon/services/work_resolver.py`, `maintenance.py`, `job_feedback_observer.py`, `job_recovery_service.py`, `stale_task_recovery.py`

**DB backend (this run):** SQLite — `data_dev/ensemble.json` pins `"database": "sqlite"`, so `EnsembleConfig.load_or_create` (`daemon/ensemble_config.py:71-79`) loads that and ignores `.env`'s postgres vars (auto-detection only fires on first start, when `ensemble.json` is absent). To run dev on postgres: delete `data_dev/ensemble.json` (auto-detects from `POSTGRES_HOST`/`POSTGRES_DB`) or set `"database": "postgres"` in it.

**Backend-independence:** both bugs are logic defects, not SQL-dialect issues. They reproduce identically on PostgreSQL (see §6). **DB evidence (dev SQLite, `data_dev/instances.db`):** see §4.

---

## Table of contents

1. [Summary — the seam root cause](#1-summary--the-seam-root-cause)
2. [P1 — Job stuck "processing", Task never claimed](#2-p1--job-stuck-processing-task-never-claimed)
3. [P2 — Defer queue admitted while virtual jobs active](#3-p2--defer-queue-admitted-while-virtual-jobs-active)
4. [Q3 — Why the E2E test did not catch this](#4-q3--why-the-e2e-test-did-not-catch-this)
5. [Database evidence](#5-database-evidence-dev-sqlite)
6. [Backend independence](#6-backend-independence)
7. [Sweep — 17 sibling seam bugs](#7-sweep--17-sibling-seam-bugs)
8. [Solution — harden the seam (keep the two-table model)](#8-solution--harden-the-seam-keep-the-two-table-model)
9. [Related files](#9-related-files)

---

## 1. Summary — the seam root cause

The reported P1/P2 are the tip of a larger cluster. Both are rooted in the **dual work-tracking tables** that emerged from the D13 unification:

1. **JobItems** (`job_queue_items`) — created by `POST /api/jobs`, gated by the **job-queue admission layer** (admission_state, queue_id, priority, retry/DLQ, job_locks).
2. **Tasks** (`task`) — created by `enqueue_message`, the actual units the `WorkerPool` claims and that drive `graph.astream`.

A **"virtual job"** (an instance spawned via `spawn_instance` / `send_message`, e.g. a leader orchestrating a wave of developers) writes **only Task rows** — it never creates a `job_queue_items` row. The two tables are **not linked by a foreign key**; they are correlated at the application layer by `instance_id` (loose) and `metadata.message_id` (JSONB on `job_queue_items` ↔ VARCHAR on `task`, and NULL on the job path).

The bugs live wherever the **seam between the two tables is assumed-but-not-enforced**:

- **P1**: a reader of `metadata.message_id` whose writer never sets it → deadlock.
- **P2**: a "defer until idle" gate that only counts one table → premature admission.

They **stack**: the defer job is admitted prematurely (P2), and once admitted it deadlocks itself out of ever running (P1).

> **Architectural decision:** the two-table split is a *deliberate decoupling* of queue-policy from execution — we are keeping it. The merge-into-one-table alternative was evaluated and rejected (it would fold two orthogonal responsibilities into one object, creating a large hard-to-debug logic blob). All fixes below harden the seam in place.

---

## 2. P1 — Job stuck "processing", Task never claimed

### Symptom

From the log and DB:

- `21:45:29` — `_process_next_job` admits defer job `1fdd0583` → `start_job` SUCCESS, `admission_state=active`, spawns instance `3a9ddb63`, `enqueue_message` writes **Task `7`** (`work_id=02bf558c…`, `message_id=645b7dd8…`, `is_deferred=0`, `task_type=process_message`, status `pending`).
- **No worker ever claims Task `7`.** There is no `Worker … claimed task … instance=3a9ddb63…` line anywhere in the log.
- `work_resolver` repeatedly warns:
  ```
  status drift detected for instance_id=3a9ddb63…: JobItem status=processing, Task status=pending. JobFeedbackObserver should have synced these.
  ```
- The job never completes; instance `3a9ddb63` is left `running` with a `pending` task forever.

### Root cause — the cross-system guard in `claim_pending_task` deadlocks

`daemon/repositories/task/repository.py:516-572` gates a `process_message` task through a "cross-system guard":

```sql
instance_id NOT IN (
    SELECT j.instance_id FROM job_queue_items j
    LEFT JOIN instances i ON j.instance_id = i.instance_id
    WHERE j.admission_state IN ('queued', 'active')
      AND j.instance_id IS NOT NULL
      AND j.deleted_at IS NULL
      AND (i.status IS NULL OR i.status != 'waiting_children')
      AND NOT EXISTS (
          SELECT 1 FROM task t
          WHERE t.message_id = j.metadata->>'message_id'
            AND t.status IN ('pending', 'running')
      )
)
```

The carve-out (`NOT EXISTS … t.message_id = j.metadata->>'message_id'`) exists precisely so the dispatcher's own task for a job can claim — "if there's already a Task carrying this job's `message_id`, the JobItem is not a blocker". It relies on **`job_queue_items.metadata->>'message_id'` matching the task's `message_id`**.

For the admitted job:

| Table              | instance_id | status / admission_state | message_id |
|--------------------|-------------|--------------------------|------------|
| `job_queue_items`  | `3a9ddb63`  | `active`                 | **NULL** (verified in DB) |
| `task`             | `3a9ddb63`  | `pending`                | `645b7dd8` |

`j.metadata->>'message_id'` is **NULL**, so `t.message_id = NULL` is never true → the inner `NOT EXISTS` evaluates **TRUE** → the JobItem qualifies as a **blocker for its own instance** → `instance_id = 3a9ddb63` is excluded from the claimable set → **Task `7` can never be claimed**. Classic self-deadlock.

### Why the JobItem has no `message_id`

The admission path stamps `admission_state='active'` and assigns `instance_id` **before** the message exists:

1. `JobProcessor._process_next_job` → `JobQueueService.start_job` transitions the JobItem to `active` and mints `instance_id`.
2. `JobProcessor` then calls `InstanceManager.enqueue_message(...)` (`job_processor.py:708-713`), which generates the `message_id` (`645b7dd8`) **inside** `_prepare_enqueued_message` and writes the `MessageQueue` + `Task` rows.
3. **Nothing stamps the generated `message_id` back onto `job_queue_items.metadata.message_id`.**

Before D13, MESSAGE-type jobs carried `metadata.message_id` and the linkage held. After D13 ("all jobs are TASK-type"; messages no longer create JobItem rows), the dispatch path never re-established the linkage the guard depends on. **This is a D13 regression.**

### Why it's silent

- No exception is raised; the JobItem simply never terminates and the Task never claims.
- `JobFeedbackObserver` waits for an instance-lifecycle event that Task `7` can never produce (it never runs), so it never finalizes the job.
- `StaleTaskRecovery` only recovers `RUNNING` tasks; this task is `PENDING`, so it is ignored indefinitely.
- The drift warning is only `WARNING`-level and self-describes as something "JobFeedbackObserver should have synced".

---

## 3. P2 — Defer queue admitted while virtual jobs active

### Design intent

> "defer queue must wait all job, virtual job finish as it designed"

i.e. a job on `system_defer_queue` may only start when **all** in-flight work in the project — both JobItem jobs and virtual jobs — is idle.

### Symptom

At `21:45:18-21:45:32` the leader instance `6c140e30` is mid-wave: it spawned two developers (`1ffa7931`, `9111d8b9`) actively running `bash sleep`.

At `21:45:29`, while that virtual work is in flight, `_process_next_job` **admits the defer job `1fdd0583`**:

```
[TRACE] _process_next_job: found PENDING job 1fdd0583... job_type=task instance=N/A
[TRACE] start_job: SUCCESS job 1fdd0583... started with instance=3a9ddb63
Job 1fdd0583... queued for instance 3a9ddb63... on queue system_defer_queue
```

The defer idle-check did **not** block it.

### Root cause — both idle-gates only see their own tracking system

**Gate A — job-queue admission** (`job_processor.py:406-419`):

```python
if queue.queue_type == "defer" and pending:
    non_defer_active = await asyncio.to_thread(
        self._queue_service._repository.count_active_jobs_in_non_defer_queues, queue.project_id
    )
    if non_defer_active > 0:
        continue
```

`count_active_jobs_in_non_defer_queues` (`daemon/repositories/job_queue/repository.py:442`) only counts **`job_queue_items` rows** in non-defer queues. The wave created **zero** JobItem rows → count = 0 → gate passes → defer job admitted. **This is the gate that fired here.**

**Gate B — task-level claim** (`claim_pending_task`, `task/repository.py:467-489`) **does** see running non-deferred Tasks, but is irrelevant for two reasons:

1. It only applies to candidates with `is_deferred = true`. The defer job's message Task was created with `is_deferred = false` (see §3.1), so it bypasses this gate entirely.
2. Gate B runs at *claim* time and cannot undo Gate A's premature *admission*.

#### 3.1 The defer job's Task is not even marked `is_deferred`

`InstanceMessagingService.enqueue_message(..., *, is_deferred: bool = False)` (`instance_messaging.py:971`) defaults to `False`, and the only caller for dispatch-queue jobs (`job_processor.py:708-713`) does **not** pass `is_deferred`:

```python
await self._instance_manager.enqueue_message(
    instance_id=instance_id,
    message=job.message,
    source=job.source,
)   # no is_deferred=…  →  Task.is_deferred = False
```

So even if Gate A were fixed, Gate B would still not engage for dispatch-queue defer jobs. The `is_deferred` affordance exists in the plumbing but is never wired to the queue's `queue_type == "defer"`.

---

## 4. Q3 — Why the E2E test did not catch this

`tests/e2e/test_e2e_workflows.py::test_wave_spawn_with_defer_queue` (line 1907) is the scenario that **should** have caught both bugs. It passes anyway, for three reasons.

### 4.1 Assertions too lenient to detect "stuck processing"

Step 6 (`test_e2e_workflows.py:2161-2197`) is the only check on the deferred job's outcome:

```python
if job_status == "pending":
    started, job_status = _wait_for_job_status(job_id, {"processing", "completed", "failed"}, timeout=120)
…
assert job_status not in {"failed"}, …
```

- It accepts **`processing`** as a passing end-state.
- The P1 bug leaves the job in exactly `processing` (admitted but never run). So the bug's exact symptom is an **accepted** outcome.
- It never asserts the job **actually ran** (e.g. produced an assistant turn, or reached `completed`).

### 4.2 The defer-isolation invariant (P2) is never asserted

The test enqueues the deferred job at Step 3, then spends ~90s waiting for the wave (Steps 4-5), but at no point asserts:

> "while the leader/children are non-terminal, the deferred job's status stayed `pending`."

There is no sampling of `job.status` during the wave window. So the premature-admission at `21:45:29` is invisible to the test.

### 4.3 E2E is not in the default/CI run

Per `ensure.md`, E2E carries the `integration` marker, requires a live daemon + real LLM credits, and is explicitly **excluded** from the default suite and CI (`addopts = "-m 'not integration and not postgres'"`). So even if the assertions were strict, this regression would not be caught by routine runs.

| Invariant                                   | Asserted? | Would catch the bug? |
|---------------------------------------------|-----------|----------------------|
| Defer job stays `pending` during active wave (P2) | ❌ no sampling during wave | N/A |
| Defer job eventually **completes** / runs (P1)    | ❌ accepts `processing`   | No |
| Defer job not `failed`                      | ✅        | (orthogonal)         |

---

## 5. Database evidence (dev SQLite)

`data_dev/instances.db`:

```
job_queue_items:
  job_id          = 1fdd0583-b4cd-4d9f-8098-fc49b5c58e0f
  instance_id     = 3a9ddb63-9223-41e6-9efa-42b597246dbe
  admission_state = active
  metadata->>'message_id' = NULL          ← P1 root cause

task (id=7):
  work_id      = 02bf558c-b77a-4668-9b8f-b2ab2fb48148
  status       = pending                    ← never claimed
  instance_id  = 3a9ddb63-9223-41e6-9efa-42b597246dbe
  message_id   = 645b7dd8-3c59-428f-a324-66d9801cf0b0
  is_deferred  = 0                          ← P2 (Gate B can't engage)
  task_type    = process_message

instances:
  instance_id  = 3a9ddb63-9223-41e6-9efa-42b597246dbe
  status       = running                    ← orphaned, never progresses
  agent_id     = leader
```

The `message_id` mismatch (NULL vs `645b7dd8`) is the exact condition that flips the `claim_pending_task` cross-system guard into a self-blocker. Confirmed still stuck in the dev DB after a daemon restart (escapes every recovery path — see F5).

---

## 6. Backend independence

Both defects are logic-level, not dialect-specific:

- **P1** — `job_queue_items.metadata->>'message_id'` is never populated by the admission path, so it is NULL on **any** backend. `claim_pending_task`'s JSON extraction is dialect-aware via `_json_extract_text_sql`: postgres uses `j.metadata->>'message_id'` (JSONB), sqlite uses `json_extract(j.metadata,'$.message_id')`. Both return NULL for a missing field → same self-deadlock.
- **P2** — the two idle-gates are pure logic. Backend-agnostic.

Confirmed empirically: this run wrote to `data_dev/instances.db` (SQLite); the `ensemble_dev` postgres DB has 0 rows for job `1fdd0583` because the run never touched it. A postgres run reproduces the same behaviour. The postgres `trg_job_*_active_lock_guard` triggers are **orthogonal** (they enforce lock↔admission consistency, not the `message_id` linkage or the defer idle-gate).

---

## 7. Sweep — 17 sibling seam bugs

P1/P2 are not isolated. A focused sweep found **17 more** sites with the same root cause, across five failure patterns. Ranked by **exploitability × impact**.

### Pattern legend
- **S1** reader of `metadata.message_id` whose writer never sets it
- **S2** "active/idle work" predicate blind to one half of the dual tables
- **S3** cross-table correlation by `instance_id` assuming 1:1
- **S4** lossy `admission_state`↔`status` vocabulary mapping
- **S5** recovery / lock / observer paths that don't reconcile both tables

### 🔴 Critical (concrete, user-facing, easy to hit)

**F1. `WorkResolver.list_work` drops standalone task turns** — `daemon/services/work_resolver.py:945-981` (S3)
Dedup matches Task turns to JobItems by `instance_id` only. After a `job_create` spawns instance `I` (JobItem `J` + driving Task `T1`), a later `POST /messages` on `I` creates a standalone Task `T2` (no JobItem). `list_work(instance_id=I)` drops **both** `T1` and `T2` from the work surface — the user's second message turn silently vanishes. Contradicts the documented 1:N reality.

**F2. Maintenance `_is_idle` is blind to both ACTIVE JobItems AND all Tasks** — `daemon/services/maintenance.py:212-242` (S2)
Predicate: `list_all_pending` (queued-only) AND no active LLM requests. Ignores `admission_state='active'` jobs AND every `task` row. A project mid-execution reads as "idle" → checkpoint cleanup, lock sweeps, history pruning run **while work is in flight**.

**F3. `failed` / `cancelled` dispatch jobs are unfilterable on `/api/work`** — `daemon/services/work_resolver.py:339-347` (S4)
`_JOB_CANONICAL_TO_ADMISSION` collapses `completed/failed/cancelled → {done}` and `paused/processing → {active}`. `list_work(status="failed")` returns zero JobItem rows. `status="completed"` leaks failed/cancelled jobs in. `status="paused"` returns every active job. API filter correctness broken in both directions.

**F4. `cancel_job` on a non-active JobItem deletes sibling jobs' locks** — `daemon/services/job_queue_service.py:1426-1435` (S5)
`_finalize_terminal`'s `finally` calls `release_by_instance` **unconditionally**, even on the `_dispatch_skipped=True` path (job was queued, never held a lock). Instance has `JobA` (active, lock held) + `JobB` (queued). `cancel_job(JobB)` → deletes `JobA`'s lock → next `start_job` on the same queue over-admits past `concurrency_limit`. SQLite (no trigger) lets it pass silently.

### 🟠 High (concrete, narrower trigger / lower frequency)

**F5. The P1 bug escapes every recovery path** — `daemon/services/job_recovery_service.py:97-193` + `stale_task_recovery.py:175` (S5)
`JobRecoveryService` runs **startup-only** and leaves alone any job whose instance is `running`. `StaleTaskRecovery` predicate is `status='running' AND heartbeat < threshold` — a task wedged on a hung LLM (worker thread alive, heartbeat fresh) never trips it, and PENDING tasks are excluded entirely. The stuck-`processing` JobItem + `pending` Task is invisible to both. **Confirmed:** job `1fdd0583` is still stuck after daemon restart.

**F6. Retry child gets a fresh `work_id` → watcher orphan** — `daemon/repositories/task/repository.py:1294-1303` + `stale_task_recovery.py:233+` + `worker_pool.py:606` (S5)
Task-side retry (`schedule_retry`) inserts a new Task with a **new** `work_id`. `notify_work_watchers` looks up watchers by `task.work_id`. A watcher registered via `watch_job(job_id)` (the JobItem's `work_id`) is never matched → `invoke_agent_and_wait` hangs until `reconcile_terminal_watches` runs at the **next daemon restart**.

**F7. `_finalize_terminal` releases locks across ALL queues by `instance_id`** — `daemon/services/job_queue_service.py:1428` (S5, same site as F4)
Multi-queue setup: instance `I` runs `JobA` on Q1 and `JobB` on Q2. `cancel_job(JobA)` releases `I`'s lock on **Q2** too → next `start_job` on Q2 over-admits. Generalizes F4 to per-queue concurrency accounting.

**F8. Second defer idle-gate on the observer admission path** — `daemon/services/job_queue_service.py:1750-1758` (S2)
`_select_next_eligible_job` is the *other* consumer of `count_active_jobs_in_non_defer_queues`. Same blind spot, called from `JobFeedbackObserver` at `job_feedback_observer.py:2670`. The P2 premature-admission bug fires on **two** independent paths — a seam fix must patch both.

**F9. Post-commit re-arm violates the PostgreSQL lock guard trigger** — `daemon/services/job_feedback_observer.py:1102-1171` (S5)
After `_finalize_job_db_sync` Step 3 commits the lock deletion, a bus-generation bump re-arms the JobItem `completed → active` in a **new** transaction with no matching `job_locks` row. On PostgreSQL `trg_job_queue_items_active_lock_guard` raises → caught by `except Exception` → JobItem left `done`, late child silently orphaned. **PostgreSQL-only** — does not reproduce on SQLite, so the default test suite cannot catch it.

### 🟡 Medium (latent / narrow window / observability)

**F10. `done + running` terminal mismatch → double-execution** — cross-cutting (S5)
`_finalize_job_db_sync` commits the JobItem to `done` but never touches the `task` row; the Task's terminal write is the WorkerPool's fire-and-forget `notify_work_watchers`. If that raises, the exception is swallowed (`worker_pool.py:735`, `stale_task_recovery.py:629` DEBUG log) → Task stays `running` with a terminal JobItem → `StaleTaskRecovery` later force-cancels + schedules a retry against an instance whose JobItem is already terminal → **double-execution**.

**F11. `has_pending_tasks_blocked_by_busy_instance` misclassifies freshly-admitted jobs** — `daemon/repositories/task/repository.py:1052-1101` (S1)
Same NULL `metadata.message_id` carve-out as P1, different consumer (the worker-pool busy probe). `NOT EXISTS` always TRUE → inflates the `claims_skipped_due_to_busy_instance` stat. Not a deadlock, but same seam — a single `message_id` write fixes both P1 and F11.

**F12. `atomic_retry` leaves stale PENDING retry task on the same instance** — `daemon/services/job_retry_engine.py:318` (S5)
JobItem `active → queued`, then `start_job` spawns a fresh instance/Task. A leftover **PENDING** retry child on the same `instance_id` is not cancelled — `claim_pending_task`'s per-instance guard blocks only **RUNNING** tasks. Two tasks can then contest the same LangGraph checkpoint.

**F13. `get_active_by_instance` can finalize the wrong sibling JobItem** — `daemon/services/job_feedback_observer.py:620-630` (S3)
If two ACTIVE JobItems exist for one instance (mock/DB write/race), the freshest-by-`created_at` lookup can finalize the OLD one; the new one stays `active` forever until next restart.

**F14. Bus gate blind to non-bus-registered Tasks** — `daemon/services/job_feedback_observer.py:2258, 2387` (S5)
Premature-finalization gate counts `dependency_watchers` rows only. A child Task whose `send_message` failed before `bus.watch` ran is invisible → parent JobItem finalized to `done` prematurely → orphan Task later force-cancelled + retried against a terminal instance.

**F15. Deferred finalize check can finalize a freshly-created JobItem** — `daemon/services/job_feedback_observer.py:741-776` (S5)
The 5s `_deferred_finalize_check` re-queries `_get_processing_job_for_instance`; a `job_continue`/`watch_job` that created a new JobItem during the sleep window gets finalized prematurely (TOCTOU).

### 🟢 Low (latent / guarded today / observability)

**F16. Lossy `done → completed` fallback in legacy API paths** — `daemon/routers/jobs_crud.py:140`, `jobs_management.py:405`, `dlq.py:498`, `tools/job_queue.py:332` (S4)
When the WorkResolver is unwired/unreachable, these derive `status` from the lossy `_ADMISSION_TO_LEGACY_STATUS` map without consulting `terminal_reason`. Failed/cancelled jobs report `completed`. Narrow (primary path uses the resolver), but the fallback is shipped production code.

**F17. PostgreSQL-only triggers; SQLite tests cannot catch lock-invariant regressions** — `daemon/manager.py:2135-2140` (S5, infrastructural)
`trg_job_locks_active_guard` / `trg_job_queue_items_active_lock_guard` exist only on PostgreSQL. The default dev/test path (SQLite) silently allows F4/F7/F9-class violations. The seam-hardening work needs a test that exercises the lock↔admission invariant on SQLite too.

### Sweep table

| ID | Location | Pattern | Severity |
|----|----------|---------|----------|
| (P1) | `task/repository.py:516-572` | S1 | Critical |
| (P2) | `job_processor.py:414`, `:708-713` | S2 | Critical |
| F1 | `work_resolver.py:945-981` | S3 | Critical |
| F2 | `maintenance.py:212-242` | S2 | Critical |
| F3 | `work_resolver.py:339-347` | S4 | Critical |
| F4 | `job_queue_service.py:1426-1435` | S5 | Critical |
| F5 | `job_recovery_service.py:97-193`, `stale_task_recovery.py:175` | S5 | High |
| F6 | `task/repository.py:1294-1303`, `stale_task_recovery.py:233+` | S5 | High |
| F7 | `job_queue_service.py:1428` | S5 | High |
| F8 | `job_queue_service.py:1750-1758` | S2 | High |
| F9 | `job_feedback_observer.py:1102-1171` | S5 | High (PG-only) |
| F10 | cross-cutting (`worker_pool.py:735`, `stale_task_recovery.py:629`) | S5 | Medium |
| F11 | `task/repository.py:1052-1101` | S1 | Medium |
| F12 | `job_retry_engine.py:318` | S5 | Medium |
| F13 | `job_feedback_observer.py:620-630` | S3 | Medium |
| F14 | `job_feedback_observer.py:2258, 2387` | S5 | Medium |
| F15 | `job_feedback_observer.py:741-776` | S5 | Medium |
| F16 | `jobs_crud.py:140`, `jobs_management.py:405`, `dlq.py:498`, `tools/job_queue.py:332` | S4 | Low |
| F17 | `manager.py:2135-2140` | S5 | Low (infra) |

---

## 8. Solution — harden the seam (keep the two-table model)

Because we are keeping the deliberate decoupling, every finding is closable by **hardening the seam in place**, not by merging. Four fix categories cover all 19 sites (P1/P2 + F1–F17). Each is independently shippable.

### Category A — Make the join key real

**Closes:** P1, F11.

1. Stamp `job_queue_items.metadata.message_id` from the task's `message_id` at admission — one write in `JobProcessor._process_next_job` after `enqueue_message` returns, **or** stamp it atomically at admission time inside `start_job_atomic_with_lock`.
2. Harden both readers so a NULL `message_id` is never treated as "blocker present":
   - `claim_pending_task` cross-system guard (`task/repository.py:516-572`) — P1.
   - `has_pending_tasks_blocked_by_busy_instance` (`task/repository.py:1052-1101`) — F11.

   Concretely: change the carve-out to `AND j.metadata->>'message_id' IS NOT NULL AND NOT EXISTS (...)`, so a JobItem with no `message_id` (orphan ACTIVE, not-yet-dispatched) does not block its own instance's task.

### Category B — One shared "active work in project P" predicate

**Closes:** P2, F2, F8.

The `task` table is the natural source of truth post-D13 — every runnable unit (job or virtual) is a task. Define one predicate:

```sql
-- "is there non-deferred in-flight work in project P?"
SELECT EXISTS (
  SELECT 1 FROM task t
  JOIN instances i ON t.instance_id = i.instance_id
  WHERE i.project_id = :p
    AND t.status IN ('pending', 'running')
    AND t.is_deferred = false
)
```

and route all three consumers through it:
1. Both defer idle-gates — `job_processor.py:414` (P2) **and** `job_queue_service.py:1750` (F8) must call the same predicate.
2. `maintenance._is_idle` (F2) — must consult this predicate (plus the active-JobItem set) instead of queued-only `list_all_pending`.
3. Thread `is_deferred = (queue.queue_type == "defer")` from `_process_next_job` (`job_processor.py:708-713`) → `enqueue_message(is_deferred=…)` → `Task.is_deferred`, so the task-level claim gate (Gate B) becomes a real backstop for dispatch-queue defer jobs.

### Category C — Reconcile, don't assume

**Closes:** F1, F3, F4, F5, F6, F7, F10, F12, F13, F14, F15.

The drift-reconciliation cluster. Shared shape: **never release / finalize / retry / dedup on one table without checking the other's state for the same `instance_id` / `work_id`**. Scoped per concern:

- **F4/F7** — scope lock release to the job's own `(project_id, queue_id)`, not `instance_id`-wide. `release_by_instance` must only delete the lock belonging to *this* job's slot.
- **F1** — dedup `list_work` by `work_id` / `message_id`, not `instance_id`; a JobItem only suppresses its *own* driving task, not other turns on the same instance.
- **F3** — fix the lossy status map: consult `terminal_reason` on the **filter** path (`_JOB_CANONICAL_TO_ADMISSION`), not just the forward path. `done` is not enough to distinguish completed/failed/cancelled.
- **F5/F10** — add a **periodic** reconciler (JobRecoveryService is startup-only today) that catches drift states: `active JobItem + pending/never-claimed Task`, `done JobItem + running Task`, `active JobItem + paused-forever instance`.
- **F6** — make task retry preserve the original `work_id` (or register watchers against a stable handle) so `watch_job` survives a retry.
- **F12** — on `atomic_retry`, cancel any stale PENDING task for the same `instance_id` before re-admission.
- **F13/F14/F15** — observer hardening: resolve the *exact* job by id where possible (not by `get_active_by_instance` freshest), and have the premature-finalization gate also count non-bus-registered pending tasks.

### Category D — Test the invariant on SQLite

**Closes:** F17; enables regression coverage for A–C.

Add a default-suite test (SQLite, no LLM) that seeds drift states and asserts the seam contract — including the lock↔admission invariant that today only the PostgreSQL triggers enforce. Specifically:
- active non-deferred Task + defer-queue JobItem → job not admitted (P2 invariant).
- defer job's Task created with `is_deferred=true`.
- after idle, the defer Task claims + completes (P1 invariant: not stuck "processing").
- `cancel_job` on a queued sibling does not release an active job's lock (F4/F7).

### Recommended sequencing

1. **PR 1 — closes the reported P1 + P2:** Category A + Category B + Category D test. Low risk, directly fixes the user-reported bugs, test prevents the D13 regression recurring.
2. **PR 2 — closes the worst drift bugs:** F4/F7 (lock-release scoping), F1 (`list_work` dedup), F3 (status map). Each focused, clear before/after.
3. **PR 3 — reconciliation infra:** F5/F10 periodic reconciler + F6 retry `work_id` stability + F8 second defer gate. Larger; benefits from PR 1's test coverage.

The full single-table merge remains a **documented non-goal** — the decoupling is worth keeping, and these findings show the seam is closable in place.

---

## 9. Related files

- `daemon/services/job_processor.py:361` (`_process_next_job`), `:406-419` (defer idle-gate A), `:708-713` (enqueue without linkage / `is_deferred`).
- `daemon/services/job_queue_service.py:1426-1435` (`_finalize_terminal` lock release — F4/F7), `:1722`/`:1750` (`_select_next_eligible_job` — F8), `:1826` (`start_job`).
- `daemon/repositories/job_queue/repository.py:442` (`count_active_jobs_in_non_defer_queues`).
- `daemon/repositories/task/repository.py:307` (`claim_pending_task`), `:467-489` (defer idle-gate B), `:516-572` (cross-system guard — P1), `:1052-1101` (F11), `:1294-1303` (retry `work_id` — F6).
- `daemon/services/instance_messaging.py:971` (`enqueue_message`, `is_deferred` default `False`).
- `daemon/services/work_resolver.py:339-347` (F3), `:945-981` (F1 dedup).
- `daemon/services/maintenance.py:212-242` (F2 `_is_idle`).
- `daemon/services/job_feedback_observer.py:620-630` (F13), `:741-776` (F15), `:1102-1171` (F9), `:2258,2387` (F14).
- `daemon/services/job_recovery_service.py:97-193` (F5).
- `daemon/services/stale_task_recovery.py:175` (F5/F10).
- `daemon/services/job_retry_engine.py:318` (F12).
- `daemon/manager.py:2135-2140` (F17 triggers).
- `tests/e2e/test_e2e_workflows.py:1907` (`test_wave_spawn_with_defer_queue` — Q3).
- `ensure.md` (E2E not in default/CI).

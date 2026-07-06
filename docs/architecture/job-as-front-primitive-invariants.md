# Job-as-the-Front-Primitive — Database Invariants

> **Status**: Architectural invariant documentation for the `feature/job-as-front-primitive-full`
> branch. **Reference implementation verified live** against `daemon/manager.py` on 2026-07-07.
> **Scope**: this document covers the database-side invariants that arise once `JobItem` (carrying
> `job_type='message'`) becomes the single public work primitive — specifically the carve-out in
> the `job_queue_items_active_lock_guard` constraint trigger and the resulting dev-vs-prod
> blind spot. It does **not** re-document the broader Job-as-Queue-Proxy invariants covered in
> `job-as-queue-proxy-invariants.md` (read that first).
> **Companion plan**: `.agents/shared/planning/archive/job-as-front-primitive/plan-overview.md`
> **Source-of-truth for the trigger body**: `daemon/manager.py:2146–2151`.

---

## 1. Overview

The Job-as-the-Front-Primitive feature collapses every public entry point onto `JobItem`. The
`Instance` still owns execution state — the `job_queue_items` table grows a `job_type` column
(`'task'` or `'message'`) and a `enqueue_message_job()` primitive that all six public entry points
route through:

| # | Entry point | Producer |
|---|---|---|
| 1 | `POST /messages` (HTTP) | `daemon/routers/messages.py` |
| 2 | External sources (Telegram, scheduler-cron, etc.) | `daemon/sources/*` |
| 3 | Scheduler | `daemon/sources/scheduler.py` |
| 4 | `send_message` tool (internal agent-to-agent) | `daemon/tools/job_queue.py` |
| 5 | `job_continue` tool (continue conversation thread) | `daemon/tools/job_queue.py` |
| 6 | PAUSED cascade-resume (`_resume_cascade_db_sync`) | `daemon/services/instance_lifecycle.py` |

All six create a **message-type** `JobItem` (`job_type='message'`). The previous behavior — direct
`POST /messages` bypass of the queue — is gone.

**Why a new `job_type`**: the message path is a pure mirror of the underlying Task lifecycle.
Message JobItems track delivery, retry, and DLQ semantics at the queue level, but they never hold a
`job_locks` row — the Task already owns the lock. This split makes them a different *kind* of
admission record than the task-type JobItems the queue historically used.

---

## 2. JobItem Types

The `job_queue_items.job_type` column discriminates two distinct queue citizens:

| Type | `job_type` | `job_locks` row required | Trigger guard behavior | Purpose |
|---|---|---|---|---|
| **Task JobItem** | `"task"` | Yes — `enqueue_task_job` inserts a `job_locks` row in the same transaction | Full enforcement of §3 bidirectional invariant | Background / queue-only work that owns a lock for the life of the worker dispatch |
| **Message JobItem** | `"message"` | **No** — pure mirror of an underlying Task row | Guard **SKIPPED** — see §3.2 carve-out | Public message tracking: HTTP POST /messages, scheduler, external sources, internal `send_message`, `job_continue`, paused-cascade resume |

Message JobItems are **not** a separate execution unit. Their `instance_id` (when present) points
at the same instance the corresponding Task is bound to, and the Task's lock is the lock. The
message entry only exists so that the work shows up uniformly in queue APIs (`list_jobs`,
`watch_job`, DLQ, etc.).

---

## 3. The PostgreSQL Trigger Invariant

### 3.1 The two bidirectional constraint triggers

The Phase 2 (Job-as-Queue-Proxy) refactor installed two `DEFERRABLE INITIALLY DEFERRED` constraint
triggers that together enforce **active ⇔ lock-held** at COMMIT time
(`daemon/manager.py:2146–2151`):

| # | Trigger function | Installed on | Fires on | Raises if |
|---|---|---|---|---|
| 1 | `job_queue_items_active_lock_guard()` | `job_queue_items` | `AFTER INSERT OR UPDATE OF admission_state` | an `admission_state='active'` row has no matching `job_locks` row at COMMIT |
| 2 | `job_locks_active_guard()` | `job_locks` | `AFTER INSERT OR UPDATE` | a `job_locks` row has no matching `admission_state='active'` `JobItem` (with `deleted_at IS NULL`) at COMMIT |

The two triggers form a **bidirectional invariant**:

```
admission_state='active'  ⇔  matching job_locks row exists at COMMIT
```

Both directions matter. A single-direction check (active ⇒ lock) silently misses the
"lock exists but job isn't active" failure mode — the same F7/F9-class violation that
`docs/bugs/defer-queue-and-job-task-seam-bugs.md` enumerates.

The match key is **`instance_id`** (not the multi-column `(job_id, project_id, queue_id)` triple
that earlier designs contemplated). See §3.3 for why.

### 3.2 The message-type carve-out

Trigger 1 has a single line that exempts message-type JobItems from the lock-required check
(`daemon/manager.py:2146`):

```sql
CREATE OR REPLACE FUNCTION job_queue_items_active_lock_guard()
RETURNS TRIGGER AS $$
BEGIN
    -- Skip guard for message-type JobItems (pure mirrors, no lock needed)
    IF NEW.admission_state = 'active' AND NEW.job_type != 'message' THEN
        IF NOT EXISTS (
            SELECT 1 FROM job_locks WHERE instance_id = NEW.instance_id
        ) THEN
            RAISE EXCEPTION
                'admission_state=active requires a job_locks row (instance_id=%)',
                NEW.instance_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

The guard **still fires** for message-type JobItems — it short-circuits at the `IF` and returns
`NEW` without raising. The empty-body case is intentional: any future addition to the guard must
remember to include the same `NEW.job_type != 'message'` predicate.

**Why message-type is exempted** — message JobItems are queue-level mirrors of an underlying Task.
The Task holds the lock; the message JobItem does not. Eager `queued → active` transitions in
`enqueue_message_job()` would otherwise collide with the lock-required invariant and fail at
COMMIT on PostgreSQL. Before this carve-out, the message flow silently got stuck in `'queued'`
state because the `IntegrityError` was caught and logged at DEBUG
(ref: *E2E POC flag ON 2026-07-03*).

### 3.3 Why the match key is `instance_id`, not multi-column

The trigger matches `job_locks.instance_id = NEW.instance_id`. It does **not** match on
`(job_id, project_id, queue_id)`:

- **Single source of truth**: `job_locks.instance_id` is the *only* lock side column the codebase
  uses for the working invariant. The Phase 2 trigger suite standardised on it
  (`daemon/repositories/job_queue/repository.py:1635`, `1524`).
- **Lock-less message rows**: message-type JobItems can have `instance_id` *populated* (the
  pointing back to the underlying Task's instance) without needing a corresponding `job_locks`
  row of their own. The trigger must therefore look at the same join key the rest of the codebase
  uses — `instance_id`.
- **Avoids an unsafe alternative**: matching on `(project_id, queue_id)` would be unsound:
  a project can hold many locks concurrently across many workers, and the trigger would
  incorrectly fire on the first active row rather than the lock for *this specific* row.

### 3.4 Companion trigger has no carve-out

Trigger 2 (`job_locks_active_guard`) does not need a message-type carve-out. Message JobItems
**never insert into `job_locks`** by design (the Task row owns that responsibility). The trigger
only fires when a `job_locks` row is touched, and that surface never sees message-type writers
— so the inverse invariant (every active `JobLock` must point at an active `JobItem`) remains
fully enforced for task-type Jobs and silently never fires for message-type Jobs.

### 3.5 Architecture invariant — generalization rule

> When adding any new `job_type` value, audit **every** PostgreSQL trigger that assumes
> `job_locks` correspondence.

Pure-mirror types (like `message`) must be exempted from `job_queue_items_active_lock_guard` in
the same way. The carve-out is a per-type predicate, not a generic "fire only for the legacy
task type" — adding a second mirror type without re-auditing will reintroduce the E2E POC bug.
See §5 for the SQLite blind spot that hides such bugs from the default test suite.

---

## 4. Dev vs Production Behavior

### 4.1 PostgreSQL (primary / production)

- The two constraint triggers exist and run at COMMIT.
- Carve-out (§3.2) lets message-type JobItems transition `queued → active` eagerly during
  `enqueue_message_job`.
- The remaining task-type invariant is enforced — every task-type transition to `active` must
  have a matching `job_locks` row, and vice versa, at COMMIT.

### 4.2 SQLite (dev / test)

SQLite **cannot host** `DEFERRABLE INITIALLY DEFERRED` cross-table constraint triggers
(synchronous execution and a whole-DB write lock make deferred semantics meaningless, and SQLite's
trigger model lacks `CONSTRAINT TRIGGER`). Consequence:

- The `job_queue_items_active_lock_guard` and `job_locks_active_guard` triggers are
  **PG-only**. They install via `CREATE OR REPLACE FUNCTION` + `DROP/CREATE CONSTRAINT TRIGGER`
  inside `_init_database()` (`daemon/manager.py:2146–2151`); the SQLite engine silently skips
  these statements.
- Trigger violations cannot fire on SQLite. Application-layer invariants (`atomic_transition`'s
  `WHERE` guards, `rearm_with_lock`, etc.) remain the only runtime enforcement on the dev path.
- **Default `pytest tests/` runs do not exercise the trigger guard.** Any future regression that
  reintroduces the message-type eager-active bug (or any analogous violation for a new
  `job_type`) will pass `pytest` and fail only on PostgreSQL E2E.

> **Recording the gap**: CI must include a PostgreSQL job (`tests/postgres/`) that runs the
> constraint tests in `test_jq_proxy_phase2_constraints.py` and
> `test_concurrent_lock_claims.py` to cover the SQLite-blind surface. SQLite-only runs should be
> understood as a fast unit-feedback loop, not as verification of the database invariant.

---

## 5. C1 Finalize Gate: `!= PENDING`, not `== RUNNING`

The observer's finalize gate (`daemon/services/job_feedback_observer.py:723`, `~782`) uses
`status != PENDING` rather than `status == RUNNING`:

> Previously the gate was `== RUNNING`, which raced against `complete_task` on PostgreSQL:
> `complete_task` could commit before the observer read the Task state, leaving the observer with
> a non-running Task that appeared to need finalization — and causing it to finalize a JobItem
> that was still in `queued` on the queue side. The `!= PENDING` predicate removes the race by
> gate-evaluating on **whether the Task is still pre-execution**, not on the exact mid-execution
> status.

**Effect**: a JobItem in `admission_state='queued'` (i.e., never started) is protected from
premature finalization even when the underlying Task has already moved past `RUNNING`. The
observer fires only when the Task is no longer pending — by which point `admission_state` should
already reflect the Task's lifecycle (the eager `queued → active` transition happens at
`enqueue_message_job` and is now safe post-carve-out).

This is **not** an addition to the database invariant; it is an application-side companion to
the trigger carve-out. The two changes work together: the carve-out makes the eager transition
legal, and the `!= PENDING` gate makes the observer safe under the race window between
`complete_task` commit and observer read.

---

## 6. Verification Matrix

| Surface | SQLite | PostgreSQL |
|---|---|---|
| Trigger exists | No (skipped at install) | Yes (line 2146, 2147) |
| Trigger fires on task-type `active` | n/a | Yes — raises if no `job_locks` row |
| Trigger fires on message-type `active` | n/a | **Skipped** — carve-out at `IF NEW.job_type != 'message'` |
| Message JobItem can transition `queued → active` | Yes (no DB guard) | **Yes** (after carve-out) |
| Eager `enqueue_message_job` succeeds | Yes | Yes (post-fix) |
| Default `pytest tests/` catches carve-out regression | n/a (no trigger) | n/a (PG tests run on PG engine only) |
| `tests/postgres/test_jq_proxy_phase2_constraints.py` catches it | n/a | **Yes** — verify `job_queue_items_active_lock_guard` body includes the `job_type != 'message'` predicate |

---

## 7. References

- **Source of trigger body**: `daemon/manager.py:2146–2151` — `CREATE OR REPLACE FUNCTION
  job_queue_items_active_lock_guard`, `CREATE OR REPLACE FUNCTION job_locks_active_guard`, and
  the matching `DROP TRIGGER IF EXISTS` + `CREATE CONSTRAINT TRIGGER` install.
- **Companion refactor invariants**: `docs/architecture/job-as-queue-proxy-invariants.md`
  §1.1 (`admission_state='active' ⇔ JobLock`), §1.5 (isolation assumptions on PG vs SQLite).
- **Carve-out rationale (bug history)**: `docs/bugs/defer-queue-and-job-task-seam-bugs.md`
  (F9 re-arm F-class violations) — referenced to establish that the trigger is a *real*
  constraint, not a no-op.
- **Trigger install audit**: `.agents/shared/planning/job-as-queue-proxy/plan.md` §8.7.1 — the
  full SQL for both trigger functions and idempotent install statements.
- **Carve-out introduction (POC bug)**: `.agents/tester/RESULTS/2026-07-03-e2e-poc-message-jobs-flag-on.md`
  and `.agents/tester/LESSONS/e2e-message-jobs-poc-flag-on-2026-07-03.md` — captured the
  eager-queued→active `IntegrityError` swallowed at DEBUG; the carve-out is the fix.
- **C1 finalize gate race context**: `daemon/services/job_feedback_observer.py:723, ~782`,
  `~1420–1499` (orphan-race re-arm guarded by `trg_job_queue_items_active_lock_guard`).
- **PG-only constraint tests**: `tests/postgres/test_jq_proxy_phase2_constraints.py`,
  `tests/postgres/test_concurrent_lock_claims.py`, `tests/postgres/test_f9_post_commit_rearm.py`,
  `tests/postgres/test_concurrent_status_transitions.py`,
  `tests/postgres/test_optimistic_locking.py`.

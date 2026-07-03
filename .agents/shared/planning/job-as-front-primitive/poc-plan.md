# POC Plan: Job-as-the-Front-Primitive (Single Entry Point)

## Objective

Route **only** the `POST /messages` handler's NORMAL branch through the job path. When a message arrives via HTTP, create a `JobItem(job_type="message")` alongside the existing `Task` + `MessageQueue` — then let the existing execution substrate handle the rest. Prove the concept works end-to-end with minimal blast radius behind a feature flag.

> **Read first**: `.agents/shared/planning/job-as-front-primitive/plan-overview.md` for the full architecture and the approved multi-phase plan. This POC is a **deliberately smaller** first step — it validates the core hypothesis (JobItem-as-front-primitive) without committing to the full 6-phase rollout.

## Scope Assessment

**SMALL** — single entry-point change, 3-5 files modified, behind a kill-switch flag. One developer session.

### What the POC Does
1. Adds `enqueue_message_job()` to `InstanceMessagingService` — creates `JobItem + Task + MessageQueue` in a single transaction, then dispatches inline via `worker_pool.notify_work()` (same as today).
2. Adds the `message_jobs_enabled` feature flag (config, default OFF).
3. Changes the `POST /messages` NORMAL branch to use `enqueue_message_job()` when the flag is ON.
4. Fixes the 3 prerequisite issues that the big plan identified as blocking (all are one-liners or tiny changes — see Tasks 1-3).

### What Stays Frozen (DO NOT TOUCH)
- ❌ WorkResolver facade (Task ∪ JobItem union, dedup, promotion — all stays)
- ❌ Internal execution substrate (`message_queue`, `Task`, `worker_pool`, `task_processor`)
- ❌ PAUSED branch in `POST /messages` (`resume_instance_cascade` + `resume_processing_job`)
- ❌ All other entry points (registry, scheduler, `send_message` tool, `job_continue` tool, `job_create` tool)
- ❌ Frontend
- ❌ `enqueue_message()` method itself (we add `enqueue_message_job()` as a sibling)

---

## Background: Current Architecture (Post-D13)

### The NORMAL Branch Today
```
POST /instances/{id}/messages  (routers/messages.py:127-135)
  └─ manager.enqueue_message(instance_id, content, source="api", images)
       └─ InstanceMessagingService.enqueue_message()  (instance_messaging.py:994-1107)
            ├─ _prepare_enqueued_message()   [single TX]
            │   ├─ INSERT MessageQueue
            │   └─ INSERT Task (work_id=UUID4, status=PENDING)
            ├─ live_hub.stream_status_change()  (if IDLE→RUNNING)
            └─ worker_pool.notify_work()
       → returns AsyncMessageResult(message_id, instance_id, job_id=work_id)
```

**Key insight**: `enqueue_message` already dispatches **inline** (no poll loop). The message is processed immediately by the WorkerPool. The POC change is **additive**: insert one more row (`JobItem`) in the same transaction.

### The POC Target Flow
```
POST /instances/{id}/messages  [flag ON]
  └─ manager.enqueue_message_job(instance_id, content, source="api", images)
       └─ InstanceMessagingService.enqueue_message_job()
            ├─ _prepare_enqueued_message()   [same single TX — unchanged]
            │   ├─ INSERT MessageQueue
            │   └─ INSERT Task (work_id=job_id, status=PENDING)
            ├─ INSERT JobItem (job_id=same UUID, job_type="message", admission_state=queued)
            ├─ stamp_message_id(job_id, message_id)   [cross-system guard correlation]
            ├─ live_hub.stream_status_change()
            └─ worker_pool.notify_work()
       → returns AsyncMessageResult(message_id, instance_id, job_id=job_id)
```

The `work_id` (Task) and `job_id` (JobItem) are **the same UUID**. This is the linkage contract from the big plan (AD-1).

---

## Why This Works Without the Full Plan

The big plan identified 3 blocking issues that must be fixed before message-JobItems can exist. All 3 are **tiny** and **necessary even for the POC** — they prevent double-dispatch, stuck-queued leaks, and poll-loop interference:

| Issue | Risk if Not Fixed | Fix Size |
|-------|-------------------|----------|
| **Blocked Issue 3**: `list_pending_by_queue` has no `job_type` filter | Poll loop picks up message-JobItems → double-dispatch (creates a duplicate Task) | 1 line |
| **Blocked Issue 2**: Observer finalizes only `active` JobItems | If queued→active UPDATE fails, JobItem stays `queued` forever → permanent leak | 2 small changes (both <5 lines) |
| **Cross-system guard** correctness | Message-JobItem could block its own Task from being claimed → deadlock | Verified correct by design (carve-out). POC validates this. No code change needed for correctness. |

---

## Tasks

### Prerequisites (Must land first — prevent bugs before they happen)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Add `job_type` filter to `list_pending_by_queue`** | Add `.where(JobItem.job_type != "message")` to the poll-loop query. This prevents the `JobProcessor` poll loop from picking up message-JobItems (which are dispatched inline, not via poll loop). Without this, the poll loop double-dispatches: it would call `enqueue_message(work_id=job.job_id)` creating a **duplicate Task**. | `daemon/repositories/job_queue/repository.py:703-723` |
| 2 | **Observer finalize-on-completion fallback (Part A)** | Change `_get_processing_job_for_instance()` to match `queued` AND `active` JobItems (not just `active`). This ensures a message-JobItem that's still `queued` (if the post-claim activation UPDATE failed or was delayed) is still found by the observer during finalization. | `daemon/services/job_feedback_observer.py:615-741` |
| 3 | **Observer finalize-on-completion fallback (Part B)** | Change `_finalize_job_db_sync()` Step 1 UPDATE WHERE clause (line ~2941) from `.where(admission_state == ACTIVE)` to `.where(admission_state.in_([ACTIVE, QUEUED]))`. Without BOTH Part A and Part B, a stuck `queued` JobItem is found (Part A) but the UPDATE fails with `rowcount==0` → `InvalidTransitionError` → silently caught → **permanent leak**. Add a `logger.warning` when finalizing a `queued` JobItem (indicates activation UPDATE missed). | `daemon/services/job_feedback_observer.py:~2941` |

### Core POC Implementation

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 4 | **Add feature flag** | Add `message_jobs_enabled: bool = Field(default=False)` to `JobSystemConfig` in `daemon/config.py`. Env var: `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED`. Default OFF — current behavior unchanged. Add a helper on the manager: `self._job_system_config.message_jobs_enabled`. | `daemon/config.py` |
| 5 | **Implement `enqueue_message_job()`** | Add a new async method to `InstanceMessagingService` (sibling of `enqueue_message`). Flow: (1) generate `job_id = str(uuid4())`, (2) call `_prepare_enqueued_message(work_id=job_id)` — same private method, unchanged, (3) create `JobItem` via `JobRepository.create()` with `job_type="message"`, `admission_state=queued`, `instance_id`, `agent_id`, `message`, `source`, `job_metadata={}`, (4) call `stamp_message_id(job_id, message_id)` for cross-system guard correlation, (5) stream status change, (6) `worker_pool.notify_work()`. Return `AsyncMessageResult` with `job_id=job_id`. **CRITICAL**: The `JobRepository.create()` is called directly (bypassing `JobQueueService.enqueue()` which would raise `ValueError` for `job_type="message"`). The JobItem INSERT must happen in a **separate session** after the `_prepare_enqueued_message` transaction commits (or in the same session if we thread it through — but separate is simpler and the latency cost of one extra INSERT is <1ms). | `daemon/services/instance_messaging.py` |
| 6 | **Add post-claim activation** | When a worker claims a `process_message` Task whose `work_id` corresponds to a message-JobItem, flip the JobItem from `queued` → `active`. This is a best-effort UPDATE — if it fails, the finalize-on-completion fallback (Tasks 2-3) handles it. **Where**: In the `WorkerPool` task-processing path, after `claim_pending_task` succeeds, check if a JobItem with `job_id == task.work_id` exists and `admission_state == queued`, then atomically flip to `active`. This is informational for the facade — the Task's `running` state is the authoritative serialization gate. | `daemon/services/worker_pool.py` (or wherever `claim_pending_task` result is handled) |
| 7 | **Wire the flag into POST /messages** | In `routers/messages.py:127-135` (NORMAL branch), check the flag: if ON, call `manager.enqueue_message_job(...)` instead of `manager.enqueue_message(...)`. The PAUSED branch stays untouched. | `daemon/routers/messages.py:127-135` |

### Validation

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 8 | **Manual E2E test** | Start daemon with `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED=true`. Send a message via POST /messages. Verify: (a) a `job_queue_items` row is created with `job_type="message"`, `admission_state` transitions `queued → active → done`, (b) the instance processes the message normally (LLM responds), (c) the JobItem is finalized to `done` by the observer, (d) no duplicate Task is created by the poll loop. Then repeat with flag OFF — verify current behavior is unchanged. | Manual / `curl` |
| 9 | **Automated test** | Add a test that sends a message with flag ON and asserts: JobItem created with correct fields, Task created with `work_id == job_id`, message processed, JobItem finalized. Add a second test with flag OFF asserting no JobItem is created. | `tests/test_message_job_poc.py` (new) |

---

## Key Files

| File | Purpose | Change Type |
|------|---------|-------------|
| `daemon/repositories/job_queue/repository.py` | `list_pending_by_queue` — add job_type filter; `create()` — used directly for message-JobItem | **Modify** (Task 1) + **Read** (Task 5) |
| `daemon/services/job_feedback_observer.py` | `_get_processing_job_for_instance()` + `_finalize_job_db_sync()` — finalize queued JobItems | **Modify** (Tasks 2-3) |
| `daemon/config.py` | `JobSystemConfig` — add feature flag | **Modify** (Task 4) |
| `daemon/services/instance_messaging.py` | Add `enqueue_message_job()` method | **Modify** (Task 5) |
| `daemon/services/worker_pool.py` | Post-claim JobItem activation | **Modify** (Task 6) |
| `daemon/routers/messages.py` | NORMAL branch flag check | **Modify** (Task 7) |
| `daemon/repositories/job_queue/repository.py` | `stamp_message_id()` — called by Task 5 | **Read** (already exists) |
| `daemon/manager.py` | `AsyncMessageResult` dataclass — returned by new method | **Read** (already exists) |

---

## Constraints

- **PostgreSQL is the primary dev/test DB** — test against PostgreSQL, not just SQLite.
- **Do NOT modify `enqueue_message()`** — add `enqueue_message_job()` as a sibling method.
- **Do NOT modify the WorkResolver facade** — it already handles JobItems; the POC JobItems surface naturally.
- **Do NOT modify the PAUSED branch** in POST /messages — only the NORMAL branch.
- **The JobItem INSERT bypasses `JobQueueService.enqueue()`** — use `JobRepository.create()` directly (the service layer rejects `job_type="message"` by design; the repository accepts it).
- **`work_id` (Task) and `job_id` (JobItem) must be the same UUID** — this is the linkage contract.

---

## Feature Flag

**Flag**: `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED`
- **Location**: `JobSystemConfig` class in `daemon/config.py` (env prefix `ENSEMBLE_JOB_SYSTEM_`)
- **Field**: `message_jobs_enabled: bool = Field(default=False, ...)`
- **Env var**: `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED=true`
- **Default**: `False` (raw message path remains default)
- **Behavior**:
  - **OFF**: `POST /messages` → `enqueue_message()` (current behavior, no JobItem)
  - **ON**: `POST /messages` → `enqueue_message_job()` (creates JobItem + Task + MessageQueue)

---

## Risk Analysis (POC-Specific)

### RF1: Cross-System Guard Performance
**Question**: Does the cross-system guard (`claim_pending_task:607-646`) become a problem at POC scale?

**Answer**: **NO — not at POC scale.** The guard's JobItem subquery fires for every `process_message` Task claim when a message-JobItem exists. At POC scale (single entry point, low traffic, manual testing), the query plan cost is negligible. The big plan's Phase 0 Gate 2 benchmarks this under load — the POC doesn't need to validate that.

**Correctness**: The guard is **correct by design**. The `_admitted_task_carve_out_sql` NULL-safe carve-out ensures a message-JobItem (with `message_id` stamped) does NOT block its own Task from being claimed — because a matching Task exists (the one we just created). The POC validates this empirically (Task 8).

### Blocked Issue 2: Stuck Queued JobItem
**Question**: Does the queued→active transition work, or does it need the two-part fix?

**Answer**: The two-part fix (Tasks 2-3) is **mandatory even for the POC** — it's a prerequisite. Without it, if the post-claim activation UPDATE (Task 6) fails or races, the JobItem stays `queued` and the observer can't finalize it → permanent leak. The fix is tiny (2 small changes) and is included as Tasks 2-3.

### Blocked Issue 3: `list_pending_by_queue` job_type Filter
**Question**: Does the poll loop need the filter?

**Answer**: **YES — mandatory.** Without it, the poll loop picks up message-JobItems and double-dispatches (creates a duplicate Task via `enqueue_message(work_id=job.job_id)`). This is a **hard prerequisite** (Task 1) — a one-line fix that MUST land before any message-JobItem exists.

### PAUSED Branch
**Question**: Does the POC need to handle the PAUSED branch?

**Answer**: **NO.** The PAUSED branch uses `resume_instance_cascade` + `resume_processing_job`. Only the `resume_processing_job` child branch (rare edge case: target is a child instance with no PROCESS_MESSAGE Task) calls `enqueue_message`. The POC intentionally **does not route** the PAUSED branch through the job path — it stays frozen. If a message arrives to a paused instance with the flag ON, the PAUSED branch executes as today (no JobItem created). This is acceptable for a POC.

### Latency
**Question**: Does adding a JobItem INSERT add latency?

**Answer**: **Marginal (<1ms).** The `JobRepository.create()` does one INSERT on an indexed table. The POC does this in a separate session after the `_prepare_enqueued_message` transaction commits (simpler than threading the JobItem into the same session). At POC scale, this is imperceptible.

---

## Success Criteria

A successful POC proves:

- [ ] **Flag OFF**: sending a message via POST /messages behaves exactly as today — no JobItem created, message processed normally
- [ ] **Flag ON**: sending a message creates a JobItem with `job_type="message"` and `work_id == job_id`
- [ ] **Flag ON**: the message is processed normally (LLM responds, instance executes)
- [ ] **Flag ON**: the JobItem transitions `queued → active → done` correctly
- [ ] **Flag ON**: the observer finalizes the JobItem (no permanent leak)
- [ ] **Flag ON**: the poll loop does NOT double-dispatch (no duplicate Task)
- [ ] **Flag ON**: the cross-system guard does NOT deadlock (Task is claimed successfully)
- [ ] **Flag ON**: rapid back-to-back messages to the same instance are serialized correctly (no double-execution)
- [ ] No regression: all existing tests pass with flag OFF
- [ ] PostgreSQL is the test DB (not just SQLite)

---

## Deliverables

- [ ] Feature flag added (default OFF)
- [ ] `enqueue_message_job()` implemented and wired into POST /messages NORMAL branch
- [ ] 3 prerequisite fixes landed (job_type filter, finalize-on-completion Part A + B)
- [ ] Post-claim activation implemented
- [ ] Manual E2E test passed (flag ON + flag OFF)
- [ ] Automated test added
- [ ] All existing tests pass

---

## What This POC Does NOT Prove

The POC validates the **concept** at small scale. It does NOT prove:
- ❌ Guard performance under universal load (big plan Phase 0 Gate 2)
- ❌ Finalize throughput at chat-message scale (big plan Phase 0 Gate 3)
- ❌ All 6 entry points work (big plan Phase 3)
- ❌ WorkResolver facade collapse (big plan Phase 4)
- ❌ The PAUSED branch works through the job path

These remain in the full multi-phase plan. The POC is a **stepping stone** — if it works, proceed to the full plan with confidence. If it fails, the architecture hypothesis needs revisiting before committing to 6 phases.

---

## Tracking
- Created: 2026-07-03
- Status: draft
- Relationship: POC for `.agents/shared/planning/job-as-front-primitive/plan-overview.md` (LARGE, 6-phase)

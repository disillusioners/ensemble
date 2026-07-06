# Phase 1: Feature Flag + Job-Message Bridge

## Objective
Build the production-ready `enqueue_message_job()` method that creates a JobItem + MessageQueue + Task atomically in a single transaction, behind the `ENSEMBLE_MESSAGE_JOBS_ENABLED` feature flag. This is the bridge that makes message-Jobs first-class without touching existing callers.

## Coupling
- **Depends on**: Phase 0 (GO decision)
- **Coupling type**: tight (Phase 2 wires serialization that this creates)
- **Shared files with other phases**: `instance_messaging.py`, `job_queue_service.py`, `config.py`
- **Why this coupling**: The JobItem created here must be consumable by the observer (finalization), the cross-system guard (serialization), and the WorkResolver (facade read)

## Context

### Current State (Post-D13)
- `enqueue_message()` creates only MessageQueue + Task — no JobItem
- `JobQueueService.enqueue(job_type="message")` raises `ValueError` (D13 guard)
- JobProcessor only processes TASK-type dispatch-queue jobs — it does NOT pick up message-Jobs
- The JobFeedbackObserver handles `job_id=None` gracefully (message-driven instances have no JobItem)

### Target State
- A new `enqueue_message_job()` method creates JobItem + MessageQueue + Task in one transaction
- The JobItem has `job_type="message"`, `admission_state=queued`, `max_retries=0`
- The Task has `work_id=job_id` (the linkage contract)
- The JobItem gets `metadata.message_id` stamped (cross-system guard correlation)
- `worker_pool.notify_work()` wakes a worker immediately (no poll loop wait)
- The flag controls whether entry points use `enqueue_message_job()` vs `enqueue_message()`

### Critical Design Decision: JobItem admission_state lifecycle

A message-JobItem goes through these states:
```
queued → active (when Task is claimed by worker) → done/dead (when instance finalizes)
```

**Who transitions queued→active?** Two options:
- **Option A**: The `claim_pending_task` SQL can atomically flip the JobItem to `active` when it claims the driving Task (via a trigger or post-claim UPDATE). Pro: single atomic operation. Con: modifies the claim SQL (see RF1 — we want to avoid further complicating the guard).
- **Option B**: The WorkerPool worker flips the JobItem to `active` after claiming the Task but before executing. Pro: simpler SQL. Con: non-atomic gap between Task-claim and JobItem-activate.

**Recommendation**: Option B — a post-claim best-effort UPDATE. The gap is acceptable because the cross-system guard already tolerates `message_id=None` (NULL-safe carve-out). The JobItem's `active` state is informational for the facade; the Task's `running` state is the authoritative serialization gate.

**⚠️ BLOCKING ISSUE 2: Failure handling for stuck `queued` JobItems**

If the post-claim UPDATE fails or is delayed, the JobItem stays at `admission_state='queued'` indefinitely. `JobFeedbackObserver._get_processing_job_for_instance()` only finds `active` JobItems → the stuck JobItem is **never finalized** → permanent leak with no recovery.

**Chosen recovery mechanism: Finalize-on-completion fallback (Option 2)**

When the JobFeedbackObserver receives the `instance_lifecycle` terminal event and calls `_finalize_job()`, it must finalize **any** JobItem matching the instance — regardless of `admission_state`. This requires **two** code modifications (both in Task 6b):

1. **`_get_processing_job_for_instance()` (lookup)**: change `admission_state` filter from `active` only to `IN ('queued', 'active')` — so the lookup finds the stuck `queued` JobItem.
2. **`_finalize_job_db_sync()` Step 1 UPDATE (terminal write)**: change the WHERE clause at line 2941 from `.where(JobItem.admission_state == AdmissionState.ACTIVE.value)` to `.where(JobItem.admission_state.in_([AdmissionState.ACTIVE.value, AdmissionState.QUEUED.value]))` — so the atomic transition to `done`/`dead` succeeds for both starting states.

**Why BOTH modifications are required**: The lookup alone is insufficient. If `_get_processing_job_for_instance()` returns a `queued` JobItem but `_finalize_job_db_sync()` Step 1 UPDATE still filters on `active` only, the UPDATE matches zero rows (`rowcount == 0`), the subsequent SELECT finds the row exists in `queued` state, raises `InvalidTransitionError`, which is caught at `_finalize_job` line ~1633, logged at DEBUG, and silently returns. Steps 2+3 (instance status UPDATE + lock release) never execute — the instance is NOT finalized, and the JobItem stays `queued` forever. This is the exact permanent leak the fallback claims to fix.

**Why this is better than a sweeper (Option 1)**:
- No background process to manage, no interval to tune
- No race between sweeper and observer
- Finalization is event-driven (tied to the `instance_lifecycle` event), not polling
- The stuck `queued` JobItem is cleaned up at the same time as its sibling `active` JobItem (if any)

**Why this is better than transactional activation (Option 3)**:
- Does NOT modify `claim_pending_task` SQL (avoids RF1 guard complexity)
- The activation is already best-effort by design (Option B)

**Implementation (two-part)**:
- **Part A**: `_get_processing_job_for_instance()` query: change `admission_state` filter from `active` to `IN ('queued', 'active')` for message-type JobItems
- **Part B**: `_finalize_job_db_sync()` Step 1 UPDATE WHERE clause (line 2941): change from `== ACTIVE` to `.in_([ACTIVE, QUEUED])`
- Add a log warning when a `queued` JobItem is finalized (indicates the activation UPDATE failed)
- Test: simulate activation failure, verify the JobItem is still finalized to `done` AND the instance status is updated AND locks are released

### Post-D13 Reconciliation Note

This plan **deliberately reverses** the D13 invariant that "messages create Task-only, no JobItem" for **public/external messages only**. RF3 investigation confirmed this is architecturally safe:
- `enqueue_message_job()` creates JobItem + Task + MessageQueue (public work) — JobItem is a pure queue proxy (AdmissionState only, no execution state)
- `enqueue_message()` continues to create Task + MessageQueue only (internal work: reports, nudges, `[JOB_EVENT]`, compaction)
- This is equivalent to the existing validated `job_create` pattern, just at higher scale

The D13 guard in `JobQueueService.enqueue(job_type="message")` (lines 550-558) stays in place — it protects the public `enqueue()` API from accepting message-type jobs. Message-Jobs go through the new `enqueue_message_job()` path, not through `enqueue()`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **0** | **BLOCKING ISSUE 3: Add `job_type` filter to `list_pending_by_queue`** | **MUST land before any message-JobItems are created.** `list_pending_by_queue` (`repository.py:703-723`) queries `WHERE queue_id = ? AND admission_state = 'queued' AND deleted_at IS NULL` with **no `job_type` filter**. Without this fix, the JobProcessor poll loop picks up message-JobItems (which are already dispatched inline by `enqueue_message_job()`) and tries to re-dispatch them → double-spawn. Add `.where(JobItem.job_type != "message")` to the query. This is a one-line fix but is a hard prerequisite for Phase 1 Task 3+. | `daemon/repositories/job_queue/repository.py:703-723` |
| 1 | Add feature flag to config | Add `message_jobs_enabled: bool = Field(default=False)` to `JobSystemConfig` in `daemon/config.py` | `daemon/config.py` |
| 2 | Remove D13 rejection guard for `job_type="message"` | The `JobQueueService.enqueue()` currently rejects `job_type="message"`. Either remove the guard entirely or add an internal `_enqueue_message_job()` that bypasses it. **Recommendation**: add a new internal method rather than modifying the public `enqueue()` — the public method's TASK-only contract stays clean. | `daemon/services/job_queue_service.py:550-558` |
| 3 | Add `JobRepository.create_message_job()` | New repository method that creates a JobItem with `job_type="message"`, `admission_state=queued`, `max_retries=0`. Takes `job_id` as a parameter (pre-generated UUID so it can be shared with Task.work_id). | `daemon/repositories/job_queue/repository.py` |
| 4 | Add `_prepare_enqueued_message_with_job()` to InstanceMessagingService | New private method that creates MessageQueue + Task + JobItem in a single DB session/transaction. The `work_id` parameter IS the `job_id` — same UUID string. Calls the new `create_message_job()` in the same session. | `daemon/services/instance_messaging.py` |
| 5 | Add `enqueue_message_job()` public method | New async method on InstanceMessagingService (and/or manager) that calls `_prepare_enqueued_message_with_job()`, stamps `message_id` onto the JobItem, calls `worker_pool.notify_work()`. Returns `AsyncMessageResult` with `job_id` populated. | `daemon/services/instance_messaging.py`, `daemon/manager.py` |
| 6 | Add `JobItem` activation hook in WorkerPool | After a worker claims a Task via `claim_pending_task`, if the Task has a matching JobItem (detected via `work_id`), flip the JobItem to `active`. Best-effort UPDATE. | `daemon/services/worker_pool.py` or `daemon/services/instance_messaging.py` (in `_process_message_with_tracking`) |
| **6b** | **BLOCKING ISSUE 2: Finalize-on-completion fallback for stuck `queued` JobItems (TWO-PART FIX)** | **If Task 6's activation UPDATE fails, the JobItem stays `queued` and is never finalized.** Requires **two** code modifications: **Part A** — `_get_processing_job_for_instance()`: change `admission_state` filter from `active` only to `IN ('queued', 'active')` so the lookup finds stuck JobItems. **Part B** — `_finalize_job_db_sync()` Step 1 UPDATE (line 2941): change WHERE clause from `.where(JobItem.admission_state == AdmissionState.ACTIVE.value)` to `.where(JobItem.admission_state.in_([AdmissionState.ACTIVE.value, AdmissionState.QUEUED.value]))` so the terminal write succeeds. Without Part B, the UPDATE matches zero rows → `InvalidTransitionError` → silent return at line ~1633 → instance NOT finalized → permanent leak. Add warning log when finalizing a `queued` JobItem. | **Part A**: `daemon/services/job_feedback_observer.py:615-741` (`_get_processing_job_for_instance`). **Part B**: `daemon/services/job_feedback_observer.py:2941` (`_finalize_job_db_sync` Step 1 UPDATE) |
| 7 | Verify JobFeedbackObserver handles message-JobItems | The observer's `_get_processing_job_for_instance()` (Part A of Task 6b) looks up active+queued JobItems by instance_id. `_finalize_job_db_sync()` Step 1 UPDATE (Part B of Task 6b) transitions both `active` and `queued` starting states to `done`. Verify the full chain: lookup → UPDATE → Steps 2+3 (instance status + lock release) all execute for both `active` and `queued` starting states. | `daemon/services/job_feedback_observer.py` (read + test) |
| 8 | Write unit tests for the bridge | Test: (a) JobItem + Task created atomically, (b) work_id == job_id, (c) message_id stamped on JobItem, (d) observer finalizes `active` message-JobItem to done, (e) flag-off path unchanged, **(f) stuck `queued` JobItem is finalized to `done` AND instance status is updated AND locks are released** (validates both Part A + Part B of Task 6b) | `tests/test_message_job_bridge.py` (new) |

## Key Files
- `daemon/config.py:412-434` — `JobSystemConfig` class
- `daemon/services/instance_messaging.py:890-1107` — `_prepare_enqueued_message`, `enqueue_message`
- `daemon/services/job_queue_service.py:498-754` — `enqueue()` with D13 guard at 550-558
- `daemon/repositories/job_queue/repository.py` — `create()`, `stamp_message_id()`, **`list_pending_by_queue()` (line 703-723 — BLOCKING ISSUE 3)**
- `daemon/repositories/job_queue/models.py:238-433` — `JobItem` model
- `daemon/services/job_feedback_observer.py:615-741` — `_get_processing_job_for_instance()` **(BLOCKING ISSUE 2 Part A — lookup)**
- **`daemon/services/job_feedback_observer.py:2941` — `_finalize_job_db_sync()` Step 1 UPDATE **(BLOCKING ISSUE 2 Part B — terminal write)****
- `daemon/services/worker_pool.py` — worker thread claim + execute loop
- `daemon/manager.py:2764` — public `enqueue_message` wrapper

## Constraints
- **BLOCKING ISSUE 3**: `list_pending_by_queue` filter (Task 0) MUST land before any message-JobItem is created. If message-JobItems exist before the filter, the poll loop double-dispatches them.
- **BLOCKING ISSUE 2**: The finalize-on-completion fallback (Task 6b) MUST land alongside the activation hook (Task 6). Without it, a failed activation UPDATE leaks the JobItem permanently. **Both Part A (lookup) and Part B (terminal write) are required** — Part A alone causes a silent `InvalidTransitionError` → permanent instance leak.
- The JobItem + Task + MessageQueue creation MUST be in a single DB transaction
- `work_id` MUST equal `job_id` (same UUID string) — this is the linkage contract
- The flag must default to `False` — existing behavior unchanged until cutover
- Do NOT modify the existing `enqueue_message()` — add a new method
- PostgreSQL is the primary DB — test there, not just SQLite

## New Code Needed (Summary)

### `JobRepository.create_message_job()`
```python
def create_message_job(
    self,
    job_id: str,           # pre-generated UUID, shared with Task.work_id
    agent_id: str,
    agent_dir: str,
    message: str,
    source: str,
    instance_id: str | None,
    project_id: str | None,
    queue_id: str | None,
    metadata: dict[str, Any] | None,
) -> JobItem:
    """Create a MESSAGE-type JobItem for inline dispatch (not poll-loop driven).
    
    Unlike TASK jobs created via enqueue(), this JobItem is created alongside
    its driving Task and immediately dispatched via worker_pool.notify_work().
    The JobProcessor poll loop does NOT pick it up — it's already active.
    """
    # INSERT into job_queue_items with job_type="message",
    # admission_state="queued", max_retries=0
```

### `InstanceMessagingService.enqueue_message_job()`
```python
async def enqueue_message_job(
    self,
    instance_id: str,
    message: str,
    source: str = "api",
    priority: int = 1,
    images: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> AsyncMessageResult:
    """Enqueue a message WITH a JobItem (message-Job path).
    
    Creates JobItem + MessageQueue + Task in a single transaction.
    The JobItem.job_id == Task.work_id (linkage contract).
    Immediately dispatches via worker_pool.notify_work().
    """
    job_id = str(uuid.uuid4())  # pre-generate so we can stamp on both
    ctx = await asyncio.to_thread(
        self._prepare_enqueued_message_with_job,
        instance_id=instance_id,
        message=message,
        source=source,
        priority=priority,
        images=images,
        is_deferred=False,
        work_id=job_id,  # stamped on Task.work_id
        job_id=job_id,   # used to create JobItem
        ...
    )
    # stamp message_id onto JobItem
    if ctx.message_id:
        await asyncio.to_thread(
            self._job_repository.stamp_message_id,
            job_id, ctx.message_id,
        )
    # dispatch immediately
    if self._manager._worker_pool:
        self._manager._worker_pool.notify_work()
    return AsyncMessageResult(
        message_id=ctx.message_id,
        instance_id=instance_id,
        status="queued",
        job_id=job_id,
    )
```

## Deliverables
- [ ] **BLOCKING ISSUE 3**: `list_pending_by_queue` filters out `job_type="message"` — poll loop never picks up message-JobItems
- [ ] Feature flag added and accessible
- [ ] `enqueue_message_job()` working with atomic JobItem + Task + MessageQueue creation
- [ ] `work_id == job_id` verified in all tests
- [ ] **BLOCKING ISSUE 2**: Finalize-on-completion fallback handles stuck `queued` JobItems — **both Part A (lookup: `_get_processing_job_for_instance`) AND Part B (terminal write: `_finalize_job_db_sync` Step 1 UPDATE at line 2941)**
- [ ] JobFeedbackObserver correctly finalizes message-JobItems (both `active` and stuck `queued`)
- [ ] Unit tests pass on PostgreSQL
- [ ] Flag-off behavior is byte-identical to current (regression-safe)

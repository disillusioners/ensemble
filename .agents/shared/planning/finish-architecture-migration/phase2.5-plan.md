# Phase 2.5: Observer + Resume Consumption-Site Rewrite

## Objective

Rewrite all consumption sites that depend on MESSAGE JobItems existing, so they work in the post-D13 world where no JobItem is ever created for a message. This phase addresses the three blocking issues identified by the approver: (B1) `resume_processing_job` routing, (B2) observer finalization chain, and (B3) `job_continue` concurrency gate. Without this phase, D13 (Phase 2) would silently break pause/resume, checkpoint restore, and instance finalization.

## Coupling

- **Depends on**: Phase 2 (D13) — **tight coupling**
- **Coupling type**: tight — Phase 2 eliminates MESSAGE JobItem creation; Phase 2.5 rewrites all code that looks up those JobItems. The two must land together — eliminating creation without rewriting consumption breaks the system.
- **Shared files with other phases**: `job_feedback_observer.py` (Phase 3 touches `_admit_via_worker_pool`), `manager.py` (Phase 2 touches startup), `tools/job_queue.py` (Phase 4 touches `dispatch_path`)
- **Shared APIs/interfaces**: `_get_processing_job_for_instance`, `_finalize_job`, `_finalize_job_db_sync`, `resume_processing_job`, `find_processing_message_jobs_by_instance`
- **Why this coupling**: The approver's diagnosis is correct — "the plan treats the observer and resume path as 'downstream concerns' but they are the primary consumers of MESSAGE JobItems at finalization time." These consumption sites are part of the SAME coupling the migration is trying to eliminate.

## Context

### The Three Blocking Issues

#### B1 — `resume_processing_job` routing breaks

`manager.py:2706-2710` — `resume_processing_job()` uses `find_processing_message_jobs_by_instance(instance_id)` as the root-vs-child routing decision:

```python
# 1. Find existing PROCESSING MESSAGE job(s) for this instance
old_jobs = await asyncio.to_thread(
    self._job_queue_service._repository.find_processing_message_jobs_by_instance,
    instance_id
)
# ...
if not old_jobs:
    # Child instance path: enqueue via WorkerPool (NO checkpoint resume)
else:
    # Root instance path: checkpoint resume via _resume_processing_background
```

After D13, `find_processing_message_jobs_by_instance` ALWAYS returns empty. **Every instance takes the child path** — root instances lose checkpoint resume.

**Replacement**: The routing decision should be based on whether the instance has a PAUSED or RUNNING `PROCESS_MESSAGE` Task. The task repository already has `has_inflight_task(instance_id)` (checks PENDING + RUNNING) and `find_running_by_instance(instance_id)`. A new method is needed that also checks PAUSED status — or the existing `get_by_instance` can be filtered.

#### B2 — Observer finalization chain is JobItem-dependent

Three methods in the observer depend on finding a PROCESSING JobItem:

1. **`_get_processing_job_for_instance`** (line 467): queries `JobItem` by instance_id + PROCESSING status. Returns `JobItem | None`. Called by `_process_event` (line 771), `_retrigger_parent_finalize` (line 580), and `_process_resume_finalize` (line 1443).

2. **`_process_resume_finalize`** (line 1443): calls `_get_processing_job_for_instance` → returns None after D13 → returns early at line 1449 → **no terminal transition fires** → instance stays PROCESSING forever.

3. **`_finalize_job`** (line 883) → **`_finalize_job_db_sync`** (line 1769): performs a 3-step atomic transaction:
   - **Step 1**: `UPDATE job_queue_items SET status = ... WHERE job_id = ...` — this is a no-op after D13 (no JobItem to update).
   - **Step 2**: Instance status update (COMPLETED/ERROR) — this is the CRITICAL step that must be preserved.
   - **Step 3**: Lock release (`DELETE FROM job_locks WHERE instance_id = ...`) — may be moot if no queue lock was acquired.

**Replacement**: The finalization chain must be reworked so Steps 2+3 work without Step 1. Options:
- (a) Rewrite `_finalize_job` to accept a Task instead of a JobItem, skip Step 1 when no JobItem exists.
- (b) Add an alternative finalize path for the post-D13 world that transitions the Task + instance directly.
- (c) Make Step 1 a conditional no-op (if `job_id is None`, skip the JobItem UPDATE but proceed with Steps 2+3).

**Recommended**: Option (c) — least disruptive. `_finalize_job_db_sync` already handles "job not found" (rowcount == 0) by returning a skip result. Change it to: if `job_id is None`, skip Step 1 but proceed with Steps 2+3. The caller passes `job_id=None` for the post-D13 path.

#### B3 — `job_continue` concurrency gate silently disabled

`tools/job_queue.py:466-470` — pre-checks `find_processing_message_jobs_by_instance(instance_id)` as a DB-level concurrency gate:

```python
active_jobs = await asyncio.to_thread(
    job_service._repository.find_processing_message_jobs_by_instance, instance_id
)
if active_jobs:
    return {"error": f"Instance {instance_id} has a job still processing..."}
```

After D13, always returns empty → gate is disabled → concurrent `job_continue` calls both proceed → race condition.

**Replacement**: Replace with `task_repo.has_inflight_task(instance_id)` — returns True if any PENDING or RUNNING task exists for the instance. The task repository already has this method (line 149).

### Existing Task Repository Methods (Available for Replacement)

| Method | What it checks | Location |
|--------|---------------|----------|
| `get_by_instance(instance_id)` | All tasks for instance, newest first | `task/repository.py:89` |
| `get_by_message(message_id)` | Task by message_id | `task/repository.py:106` |
| `find_running_by_instance(instance_id)` | First RUNNING task | `task/repository.py:119` |
| `has_inflight_task(instance_id)` | True if PENDING or RUNNING task exists | `task/repository.py:149` |
| *(new)* `find_paused_or_running_by_instance(instance_id)` | First PAUSED or RUNNING PROCESS_MESSAGE task | To be added |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **2.5.1** | Add `find_paused_or_running_by_instance` to task repository | New method: returns the first PAUSED or RUNNING `PROCESS_MESSAGE` Task for an instance_id. Pattern: matches `find_running_by_instance` (line 119) but widens the status filter to `IN (RUNNING, PAUSED)` and filters by `task_type = PROCESS_MESSAGE`. This is the routing primitive for `resume_processing_job` (B1). Must work on both SQLite and PostgreSQL (use parameterized `IN` clause). | `daemon/repositories/task/repository.py` |
| **2.5.2** | **B1 — Rewrite `resume_processing_job` routing** | Replace `find_processing_message_jobs_by_instance` at `manager.py:2706-2710` with `find_paused_or_running_by_instance(instance_id)`. Routing decision becomes: if a PAUSED/RUNNING PROCESS_MESSAGE Task exists → root instance → checkpoint resume via `_resume_processing_background`. If none → child instance → WorkerPool enqueue. Update the log messages to reflect Task-based routing. Verify the `old_job_id` variable (used downstream at line 3021) is derived from the Task instead of JobItem — pass `task.id` or `None`. | `daemon/manager.py:2706-2710, 2758-2760` |
| **2.5.3** | **B2 — Rewrite `_get_processing_job_for_instance` to work without JobItems** | The method at `job_feedback_observer.py:467` returns a `JobItem` used by `_finalize_job`. After D13, there is no JobItem for messages. Change the method to return a lightweight context object (or None) that carries the information `_finalize_job` needs: `instance_id`, and optionally a `job_id` (None when no JobItem). This is a **design decision point** — see Task 2.5.4 for the finalize chain. | `daemon/services/job_feedback_observer.py:467-540` |
| **2.5.4** | **B2 — Make `_finalize_job_db_sync` work with `job_id=None`** | At `job_feedback_observer.py:1769-2178`, the sync helper performs 3 steps. After D13, Step 1 (`UPDATE job_queue_items`) is a no-op. Change the method so: when `job_id is None`, skip Step 1 entirely and proceed with Steps 2+3 (instance status update + lock release). When `job_id is not None` (TASK-type jobs still use JobItems), all 3 steps run as before. This is the **least disruptive** option — the instance transition + lock release are the critical operations; the JobItem update is redundant. | `daemon/services/job_feedback_observer.py:1769-2178` |
| **2.5.5** | **B2 — Fix `_process_resume_finalize` to not short-circuit** | At `job_feedback_observer.py:1443-1449`, the method calls `_get_processing_job_for_instance` and returns early if None. After D13, this returns None for all messages → resume finalize is dead. Change: if no JobItem exists, proceed with finalize using `job_id=None` (Task 2.5.4 handles the no-JobItem path). The `_process_resume_finalize` method should still call `_finalize_job` — the finalize chain handles the absence of a JobItem gracefully. | `daemon/services/job_feedback_observer.py:1439-1476` |
| **2.5.6** | **B2 — Fix `_process_event` to not short-circuit** | At `job_feedback_observer.py:771-773`, the same `_get_processing_job_for_instance` → None → early return pattern exists. After D13, this would prevent terminal transitions for all message-driven instances. Apply the same fix as Task 2.5.5: proceed with finalize using `job_id=None` when no JobItem exists. | `daemon/services/job_feedback_observer.py:767-799` |
| **2.5.7** | **B2 — Address the orphan-race re-arm mechanism** | At `job_feedback_observer.py:1037-1060`, the post-commit re-arm path transitions a COMPLETED JobItem back to PROCESSING when the generation counter bumps. After D13, there is no JobItem to re-arm. The re-arm needs to be rethought: either (a) skip re-arm entirely (the bus's own watcher mechanism handles late children independently), or (b) re-arm the Task instead. **Decision**: Analyze whether the bus's `count_pending_for_target` gate is sufficient without the JobItem re-arm. If the late child's resolve callback already drives a new finalize cycle via the lifecycle event, the re-arm may be unnecessary. Document the analysis. | `daemon/services/job_feedback_observer.py:1037-1070` |
| **2.5.8** | **B3 — Rewrite `job_continue` concurrency gate** | Replace `find_processing_message_jobs_by_instance` at `tools/job_queue.py:466-470` with `task_repo.has_inflight_task(instance_id)`. The gate should reject if any PENDING or RUNNING task exists for the instance. If the instance has a PAUSED task, the gate should allow `job_continue` (paused tasks are not active). This may require a variant that excludes PAUSED — check if `has_inflight_task` already excludes PAUSED (it does — it checks PENDING + RUNNING only). | `daemon/tools/job_queue.py:462-470` |
| **2.5.9** | Remove `find_processing_message_jobs_by_instance` | After all three consumption sites are rewritten, the method at `job_queue/repository.py:505-516` is dead code. Remove it. This was already identified in Phase 2 Task 2.4 (C3 site #6) — confirm removal happens here since the consumers are now gone. | `daemon/repositories/job_queue/repository.py:505-516` |
| **2.5.10** | Test — pause/resume E2E for root instance | Write/add test: (1) Send HTTP message to root instance (goes through enqueue_message), (2) Pause instance, (3) Resume instance, (4) Assert checkpoint resume fires (not fresh enqueue), (5) Assert instance reaches terminal status. This is the core regression test for B1+B2. **Remove xfail from Phase 0 if this scenario is covered.** | `tests/e2e/` or `tests/unit/test_pause_flow_redesign.py` |
| **2.5.11** | Test — `job_continue` concurrency gate | Write test: two concurrent `job_continue` calls for the same instance — second should be rejected by the Task-based gate. | `tests/unit/` or `tests/test_pause_terminate_matrix.py` |
| **2.5.12** | Test — observer finalize without JobItem | Write test: instance completes processing without a JobItem (post-D13 path). Assert `_finalize_job_db_sync` transitions the instance to COMPLETED and releases locks. Verify no crash on `job_id=None`. | `tests/unit/` or `tests/unit/services/test_job_feedback_observer.py` |
| **2.5.13** | Run full test suite | **W3**: After all consumption-site rewrites, run `pytest tests/ -x` on PostgreSQL. This is the critical gate — pause/resume, job_continue, and observer finalization must all work. | — |

## Key Files

- `daemon/repositories/task/repository.py` — new `find_paused_or_running_by_instance` method
- `daemon/manager.py` — `resume_processing_job` (2706-2830), `_resume_processing_background` finalize call (3018-3025)
- `daemon/services/job_feedback_observer.py` — `_get_processing_job_for_instance` (467-540), `_process_event` (767-799), `_process_resume_finalize` (1439-1476), `_finalize_job` (883-1070), `_finalize_job_db_sync` (1769-2178)
- `daemon/tools/job_queue.py` — `job_continue` concurrency gate (462-470)
- `daemon/repositories/job_queue/repository.py` — `find_processing_message_jobs_by_instance` (505-516, to be removed)
- `daemon/services/job_state_machine.py` — line 45 references `_get_processing_job_for_instance` in a comment

## Constraints

- **Must land WITH or AFTER D13 (Phase 2), BEFORE D11 (Phase 3)**: D13 eliminates JobItem creation; this phase rewrites the consumers. D11 removes the branch that creates them. The order is: D13 (stop creating) → Phase 2.5 (stop consuming) → D11 (remove the creation branch). There is a brief window between D13 and Phase 2.5 where consumers return None — this is acceptable IF the data migration (Phase 2 Task 2.8) cancels all existing MESSAGE JobItems first, so consumers don't find stale rows.

- **Pause/resume feature must work end-to-end**: The pause/resume feature was recently completed (2026-06-25) and is a critical capability. B1+B2 directly break it. The Phase 0 acceptance test + Task 2.5.10 E2E test are the regression guards.

- **The observer's `_finalize_job_db_sync` is the SOLE terminal transition path** for instances driven through the JobQueue path. After D13, it must still work for the WorkerPool-driven path. The instance status update (Step 2) and lock release (Step 3) are the critical operations.

- **The orphan-race re-arm mechanism (Task 2.5.7)** is subtle — it was designed to handle the race where a child registers a watcher AFTER the parent has already finalized. The re-arm transitions the JobItem COMPLETED→PROCESSING so the late child's resolve finds an active job. After D13, this mechanism needs rethinking — the bus's own watcher/generation mechanism may be sufficient without the JobItem re-arm.

- **Dual-driver support**: The new `find_paused_or_running_by_instance` must work on both SQLite and PostgreSQL.

## Deliverables

- [ ] `find_paused_or_running_by_instance` method on task repository
- [ ] `resume_processing_job` routes via Task rows (B1 fixed)
- [ ] `_get_processing_job_for_instance` works without JobItems (B2)
- [ ] `_finalize_job_db_sync` handles `job_id=None` (B2)
- [ ] `_process_resume_finalize` does not short-circuit without JobItem (B2)
- [ ] `_process_event` does not short-circuit without JobItem (B2)
- [ ] Orphan-race re-arm mechanism analyzed and adapted (Task 2.5.7)
- [ ] `job_continue` concurrency gate uses Task-based check (B3)
- [ ] `find_processing_message_jobs_by_instance` removed (dead code)
- [ ] Pause/resume E2E test for root instance passes
- [ ] `job_continue` concurrency test passes
- [ ] Observer finalize-without-JobItem test passes
- [ ] Full test suite passes on PostgreSQL

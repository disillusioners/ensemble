# Phase 2: Pause Flow Redesign

## Objective

Implement the new pause flow that transitions jobs from `PROCESSING → PAUSED` and tasks from `RUNNING → PAUSED` when a user pauses an instance. Preserve DependencyBus watchers instead of cancelling them, with a compaction hook for long pauses. Ensure the CancelledError handler does NOT complete the job. Define behavior for new messages arriving during pause.

## Coupling

- **Depends on**: Phase 1 (Enum & State Machine)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/services/instance_lifecycle.py`, `daemon/services/instance_messaging.py`, `daemon/services/child_reports.py`
- **Shared APIs/interfaces**: `pause_instance_cascade()`, `_process_message_with_tracking()` CancelledError handler
- **Why this coupling**: Phase 2 imports `JobStatus.PAUSED` and `TaskStatus.PAUSED` from Phase 1. Phase 3 (resume) reverses what this phase does.

## Context

- Phase 1 added the PAUSED state to enums and state transitions
- Current pause flow cancels the graph task but keeps the job in PROCESSING (the hack)
- Current pause flow cancels bus watchers via `_cancel_bus_watchers_for()` — we're changing this to preserve them with compaction
- The CancelledError handler at `instance_messaging.py:1456` already re-raises (good) but must ensure job is NOT completed

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Transition job to PAUSED in pause flow | In `pause_instance_cascade()` or its DB sync helper, add logic to find the PROCESSING job for each paused instance and transition it to PAUSED. Use the repository's `atomic_transition` method. | `daemon/services/instance_lifecycle.py:924-1054` |
| 2 | Transition task to PAUSED in pause flow (ATOMIC with instance pause — W1) | In `_pause_cascade_db_sync`, atomically transition tasks `WHERE status = 'running' → 'paused'` **in the same WriteGuardSession** as the instance status update. This prevents a pause-claim race where a WorkerPool mid-`claim_pending_task` sets a task back to RUNNING after the instance is paused. | `daemon/services/instance_lifecycle.py` (`_pause_cascade_db_sync`) |
| 3 | Preserve bus watchers on pause | Remove or comment out the `_cancel_bus_watchers_for()` call in `pause_instance_cascade()` (instance_lifecycle.py:1052). Bus watchers stay PENDING so child reports are tracked even during pause. | `daemon/services/instance_lifecycle.py:1052` |
| 4 | Add bus watcher compaction hook (C3) | Implement `_compact_fired_watchers_for_paused(instance_id)` that deletes FIRED `dependency_watchers` rows for the paused instance that have already been superseded by completed PROCESS_REPORT tasks. This prevents unbounded growth during long partial-tree pauses where children continue completing. Register it to run **on resume** (Phase 3) before `notify_work()`. | `daemon/services/instance_lifecycle.py` or `daemon/services/child_reports.py` |
| 5 | Update `_pause_cascade_db_sync` for atomic job+task transition (W1) | The batched UPDATE must include ALL three tables in the same WriteGuardSession: instances (→ PAUSED), job_queue_items (PROCESSING → PAUSED), task (RUNNING → PAUSED). This is critical for atomicity — see SQL pattern below. | `daemon/services/instance_lifecycle.py` (`_pause_cascade_db_sync`) |
| 6 | **Worker-during-pause: CancelledError handler + finally block protection** (BLOCKER B2) | This is the critical race between DB transition to PAUSED and the worker's finally block. Three parts: **(a)** Worker notification: the cooperative cancellation (`request_registry.cancel_by_instance` + `graph_task.cancel()`) already raises `CancelledError` in the worker's `astream` loop — this is the notification. **(b)** CancelledError handler at `instance_messaging.py:1456` must re-raise WITHOUT calling `complete_task()` or `complete_job()` — the task/job are PAUSED, not completed. **(c)** **Finally block guard**: the worker's finally block (and the `complete_task` logic at `manager.py:2944-2991`) must check the task's current DB status before completing — if PAUSED, skip completion (leave for resume's re-claim via PAUSED → PENDING). The protection is a status re-check: `if task.status == PAUSED: skip complete_task()`. **(d)** Concurrency slot: the worker DOES release its concurrency slot — the `CancelledError` unwinds the `ExecutionGate.run()` wrapper, freeing the slot for the next claim. This is correct behavior since the PAUSED task will go through the normal PENDING → claim path on resume. | `daemon/services/instance_messaging.py:1456` (CancelledError handler + finally), `daemon/manager.py:2944-2991` (complete_task block — must add PAUSED guard), `daemon/services/instance_lifecycle.py:985-997` (cooperative cancellation) |
| 7 | Ensure `_finalize_job_db_sync` bus gate | Verify `_finalize_job_db_sync` (job_feedback_observer.py:1641-2054) does NOT finalize PAUSED jobs. The `WHERE status = 'processing'` guard in the atomic transition already excludes PAUSED — verify this covers the case. | `daemon/services/job_feedback_observer.py:1641-2054` |
| 8 | Prevent `_process_event` from finalizing PAUSED jobs | In `_process_event` (job_feedback_observer.py:698-828), verify it only looks up PROCESSING jobs (not PAUSED). The `_get_processing_job_for_instance` at line 518 should already filter by `status = 'processing'`. Verify PAUSED jobs are not picked up. | `daemon/services/job_feedback_observer.py:516-535, 698-828` |
| 9 | Update SSE event to reflect PAUSED job status | When emitting SSE events for pause, include the job status change to PAUSED so the frontend can display it correctly. | `daemon/services/instance_lifecycle.py` (SSE emission) |
| 10 | Define new-message-during-pause behavior (W5) | When a message arrives for a PAUSED instance, `enqueue_message` creates a Task row (PENDING), but `claim_pending_task` pause gate blocks it. **Intended behavior: queue (current behavior)** — messages accumulate in PENDING and are claimed when the instance resumes. Document this explicitly in code comments and add a test verifying PENDING tasks accumulate and are processed after resume. | `daemon/repositories/task/repository.py` (pause gate), test in Phase 5 |
| 11 | Write tests for new pause flow | Test that pausing an instance transitions its job to PAUSED and task to PAUSED atomically. Test that bus watchers are preserved. Test that CancelledError does not complete the job. Test that FIRED watchers accumulate during partial pause. **Test worker-during-pause: verify that a worker whose task was transitioned to PAUSED does NOT flip it back to COMPLETED in the finally block (B2 regression test).** | `tests/unit/test_pause_flow_redesign.py` (new) |

## Key Files

- `daemon/services/instance_lifecycle.py` — Core pause logic: `pause_instance_cascade()` (line 924), `_pause_cascade_db_sync`, `_cancel_bus_watchers_for()` (line 1052)
- `daemon/services/instance_messaging.py` — CancelledError handler (line 1456), finally block, `_process_message_with_tracking` (line 1014)
- `daemon/manager.py:2944-2991` — `complete_task` block that must add PAUSED status guard (B2 fix)
- `daemon/services/job_feedback_observer.py` — `_process_event` (line 698), `_finalize_job_db_sync` (line 1641)
- `daemon/services/child_reports.py` — Bus terminal hook `_emit_terminal_via_bus` (line 192)
- `daemon/repositories/job_queue/repository.py` — Job `atomic_transition` method
- `daemon/repositories/task/repository.py` — Task status transition methods
- `daemon/routers/instances.py` — Pause endpoint (line 247)

## Constraints

- The pause flow must be atomic — all status transitions (instance + job + task) in one transaction
- Bus watchers must be preserved (PENDING state), not cancelled
- The graph task cancellation is cooperative (request_registry + asyncio cancel)
- Job locks must NOT be released on pause
- The `CancelledError` must propagate without completing the job

## Deliverables

- [ ] Pausing an instance transitions its job PROCESSING → PAUSED atomically
- [ ] Pausing an instance transitions its task RUNNING → PAUSED atomically (W1 — same transaction as instance)
- [ ] Bus watchers are preserved (not cancelled) during pause
- [ ] Bus watcher compaction hook implemented (C3)
- [ ] **Worker CancelledError handler does NOT complete job/task (B2) — re-raises without calling `complete_task()` or `complete_job()`**
- [ ] **Finally block / `complete_task` logic checks task status — skips completion for PAUSED tasks (B2)**
- [ ] **Worker releases concurrency slot on CancelledError (B2) — ExecutionGate unwinds correctly**
- [ ] `_process_event` and `_finalize_job_db_sync` skip PAUSED jobs
- [ ] SSE events reflect PAUSED job status
- [ ] New-message-during-pause behavior documented (W5: queue in PENDING)
- [ ] Unit tests for new pause flow passing (including B2 regression test)

## Critical: What NOT to Change

- **Do NOT remove** the graph task cancellation — execution must still stop on pause
- **Do NOT release** job locks on pause
- **Do NOT change** the cooperative cancellation mechanism (request_registry)
- **Do NOT modify** the resume flow yet — that's Phase 3

## Notes

- The current `_pause_cascade_db_sync` does a single batched UPDATE for instance status. We need to extend this to also update jobs and tasks in the same transaction (W1).
- The atomic SQL pattern for `_pause_cascade_db_sync` (W1):
  ```sql
  -- All in ONE WriteGuardSession transaction:
  UPDATE instances SET status = 'paused', paused_at = :now
  WHERE instance_id IN (:tree_ids) AND status = 'running';

  UPDATE job_queue_items SET status = 'paused'
  WHERE instance_id IN (:tree_ids) AND status = 'processing';

  UPDATE task SET status = 'paused'
  WHERE instance_id IN (:tree_ids) AND status = 'running';
  ```
- The bus watcher preservation is the most architecturally significant change — it changes a core assumption that "paused = no bus activity"
- This change makes bus watchers safe because PROCESS_REPORT tasks are still blocked by the pause gate (instance status = PAUSED), so no graph turns will fire during pause. The watchers just accumulate state that will be processed on resume.
- The compaction hook (C3) addresses unbounded growth during long partial-tree pauses where children continue completing and registering FIRED watchers on the paused parent.
- **Multi-row batched UPDATE note** (approver): The `UPDATE job_queue_items SET status = 'paused' WHERE instance_id IN (:tree_ids)` may hit multiple job rows per instance (e.g., a retry job + the current job, or jobs from different message turns). This is intentional — ALL PROCESSING jobs for paused instances should transition to PAUSED. However, on resume, `resume_processing_job` must handle the case where multiple jobs were PAUSED and ensure only the correct one (the active message job) is resumed. The existing zombie cleanup logic (manager.py:2669-2747) already handles extra PROCESSING jobs — verify it also handles extra PAUSED jobs on resume.
- **Worker-during-pause race** (approver B2): The critical race is between the DB transition to PAUSED (in `_pause_cascade_db_sync`) and the worker's `finally` block. The sequence is:
  1. `pause_instance_cascade` cancels the graph task via `graph_task.cancel()` and `request_registry.cancel_by_instance()`
  2. The DB sync atomically transitions the task RUNNING → PAUSED
  3. The worker's `astream` loop receives `CancelledError`
  4. The CancelledError handler at `instance_messaging.py:1456` re-raises (correct)
  5. **The finally block** may call `complete_task()` which could flip PAUSED → COMPLETED — **THIS IS THE BUG TO PREVENT**

  **Protection**: The `complete_task` logic (manager.py:2944-2991) and any finally-block cleanup must check the task's current DB status before completing. If `task.status == 'paused'`, skip `complete_task()` entirely — the task will be naturally re-claimed on resume via PAUSED → PENDING. The worker DOES release its concurrency slot (the `CancelledError` unwinds `ExecutionGate.run()`), which is correct.

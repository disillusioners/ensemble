# Finish Architecture Migration — Plan Tracking

## Iteration 001

**Date**: 2026-06-26 18:15
**Verdict**: REJECTED
**Reviewer**: Approver (independent council + direct code verification)

### Blocking Issues

#### 1. `resume_processing_job` routing breaks — root instances lose checkpoint resume

**Severity**: BLOCKING — breaks the pause/resume feature (completed 2026-06-25) for all root instances

- `manager.py:2706-2710` — `resume_processing_job()` calls `find_processing_message_jobs_by_instance(instance_id)` to decide routing:
  - If PROCESSING MESSAGE JobItems found → "root instance" path → resumes from LangGraph checkpoint via `_resume_processing_background`
  - If NONE found → "child instance" path → enqueues via WorkerPool (new message, NOT checkpoint resume)
- After D13, `find_processing_message_jobs_by_instance` ALWAYS returns empty (Phase 2 Task 2.4 says method "becomes a no-op or can be removed"). ALL instances take the "child" path.
- **Impact**: Root instances paused after receiving an HTTP message will NOT resume from checkpoint — they get a fresh WorkerPool enqueue instead. The checkpoint-resume behavior (message replay, state restoration) is silently lost.
- **Not mentioned anywhere in the plan**: grep for `resume_processing_job`, `_resume_processing_background`, `find_processing_message_jobs_by_instance` across all plan files = 0 hits (except the method-removal cleanup in Task 2.4, which doesn't address callers).
- **Expected**: Plan must add a phase/task to rewrite `resume_processing_job` routing to use Task rows instead of MESSAGE JobItems (e.g., query `task_repo` for a PAUSED/RUNNING task instead of `find_processing_message_jobs_by_instance`).

#### 2. JobFeedbackObserver finalization chain is JobItem-dependent — `_process_resume_finalize` dead after D13

**Severity**: BLOCKING — pause/resume instances stuck in PROCESSING forever after resume

- `job_feedback_observer.py:1443` — `_process_resume_finalize()` calls `_get_processing_job_for_instance(instance_id)` which queries for PROCESSING JobItems.
- After D13, no JobItems exist for messages → returns None → `_process_resume_finalize` returns early at line 1449 → **no terminal transition fires**.
- The resume background task (`_resume_processing_background`) calls `_process_resume_finalize` at `manager.py:3019`. If this silently skips, the instance stays PROCESSING forever.
- Similarly, `_process_event()` (line 771) has the same `_get_processing_job_for_instance` → None → early return pattern. However, `_process_child_completion_and_notify_parent` does handle instance status independently via `child_reports.py:1211-1298`, so the basic completion path may survive. But the resume-specific finalize is broken.
- **Expected**: Plan must address the observer's finalization chain — either (a) rewrite `_get_processing_job_for_instance` and `_finalize_job` to work with Task rows, or (b) add an alternative finalize path for the post-D13 world where no JobItem exists.

#### 3. `job_continue` tool concurrency gate silently disabled

**Severity**: BLOCKING — race condition enabled, two concurrent `job_continue` calls can drive the same instance simultaneously

- `tools/job_queue.py:466-470` — `job_continue` pre-checks `find_processing_message_jobs_by_instance(instance_id)` as a DB-level concurrency gate. If any PROCESSING MESSAGE job exists, it rejects with "Instance has a job still processing".
- After D13, this always returns empty → gate is disabled → concurrent `job_continue` calls for the same instance both proceed.
- The plan's Phase 4 Task 4.3 updates the `dispatch_path` argument removal on line 477 but does NOT address the concurrency gate pre-check on lines 466-470.
- **Expected**: Plan must replace the JobItem-based concurrency gate with a Task-based check (e.g., `task_repo.get_active_by_instance(instance_id)` or equivalent).

### Non-Blocking Notes

1. **`_finalize_job_db_sync` performs 3 atomic operations on a JobItem**: (1) JobItem status transition, (2) instance status update, (3) lock release. After D13, Step 1 is moot (no JobItem). Step 2 is redundant with `_process_child_completion_db_sync`. Step 3 (lock release) may also be moot if no queue lock was acquired. The observer's entire reason for existence is tied to the JobItem lifecycle — a deeper question about whether the observer should be removed entirely (or significantly refactored) is implied but not addressed.

2. **`_has_no_active_message_job` guard removal (Phase 5)**: The plan correctly identifies that after D13 this guard always returns True (no MESSAGE jobs). The guard at `child_reports.py:348-429` is called at 4 sites (966, 1399, 1523, 1806). After D13, removing it is safe. However, the F8 carve-out logic at line 1399-1409 uses this guard to detect stale task-claim races — after removal, a new mechanism may be needed to prevent stranding. The plan says to replace with `bus.count_pending_for_target(instance_id) > 0` but the guard checks a DIFFERENT condition (active MESSAGE jobs vs. bus pending watchers). Verify the replacement is semantically equivalent.

3. **Data migration (Task 2.8) ordering**: The plan says to add the data migration in Phase 2 (`UPDATE job_queue_items SET status='cancelled' WHERE job_type='message'...`). But Phase 3 removes the MESSAGE branch from `job_processor.py`. If the migration runs in Phase 2 but the branch is removed in Phase 3, there's a window where in-flight MESSAGE JobItems are cancelled but the branch still exists. This is acceptable (the cancelled items just won't be processed by the branch), but worth noting for deployment sequencing.

4. **Plan quality is otherwise high**: The caller audit (Decision 4), the C3 comprehensive cleanup sites (Decision 11), the atomic sweep design (Decision 9), and the test-first approach (Phase 0) are all well-considered. The parallelization strategy and coupling assessment are accurate for the items that ARE covered.


## Iteration 002

**Date**: 2026-06-26 18:55
**Verdict**: APPROVED
**Reviewer**: Approver (independent council + direct code verification)

### Fix Verification — All 3 Blocking Issues from Iteration 001

All three blocking issues from iteration 001 have been addressed in the new Phase 2.5:

#### B1 — `resume_processing_job` Routing Fix ✅
- **Phase 2.5 Task 2.5.1**: Adds `find_paused_or_running_by_instance(instance_id)` to task repository (filters by PAUSED + RUNNING + task_type=PROCESS_MESSAGE).
- **Phase 2.5 Task 2.5.2**: Replaces `find_processing_message_jobs_by_instance` at `manager.py:2706-2710` with the new method. Root vs child routing is preserved.
- **Verified**: The task repository already has `find_running_by_instance` (line 119) and `has_inflight_task` (line 149). The new method is a clean widening of these primitives.
- **Semantics check**: Root instance = has a PAUSED or RUNNING PROCESS_MESSAGE Task. Child instance = no such Task. This matches the original semantics from `manager.py:2706-2710`.

#### B2 — Observer Finalization Chain Fix ✅ (with caveats)
- **Phase 2.5 Task 2.5.3**: Rewrites `_get_processing_job_for_instance` to return a lightweight context object carrying `instance_id` + optional `job_id`.
- **Phase 2.5 Task 2.5.4**: Makes `_finalize_job_db_sync` skip Step 1 (JobItem UPDATE) when `job_id is None` but proceeds with Steps 2+3 (instance status + lock release).
- **Phase 2.5 Tasks 2.5.5 & 2.5.6**: Fix `_process_resume_finalize` and `_process_event` to not short-circuit when no JobItem exists.
- **Phase 2.5 Task 2.5.7**: Orphan-race re-arm mechanism — DESIGN ANALYSIS task. The plan correctly identifies that the bus's `count_pending_for_target` gate may be sufficient without the JobItem re-arm (the lifecycle event itself drives the finalize).
- **Verified via code reading**: The `_finalize_job_db_sync` function has TWO bus gates (lines 1902, 2032) for premature-finalization defense. The plan's "skip Step 1" framing is correct — only the JobItem UPDATE (lines 2063-2114) should be skipped, not the bus gates or Steps 2+3. The plan's Decision 14 explicitly says "skip Step 1 but proceed with Steps 2+3" which preserves the bus gates.
- **Caveat (non-blocking)**: `_emit_in_progress` (lines 853-882, called from `_process_event` line 791 and `_process_resume_finalize` line 1460) accesses `job.job_id`. After D13, if `job` is a context object with `job_id=None`, this crashes with AttributeError, which is caught by the `except Exception` handler at line 877. This means noisy WARNING logs for every multi-child parent finalize, but does NOT break the flow. The plan could be more explicit about this, but it's not blocking.

#### B3 — `job_continue` Concurrency Gate Fix ✅
- **Phase 2.5 Task 2.5.8**: Replaces `find_processing_message_jobs_by_instance` at `tools/job_queue.py:466-470` with `task_repo.has_inflight_task(instance_id)`.
- **Verified**: `has_inflight_task` (line 149) checks PENDING + RUNNING only (NOT PAUSED). This is the correct semantic for a concurrency gate — a paused task should NOT block job_continue. The plan correctly identifies this.

### Additional Findings (Non-Blocking)

1. **Council flagged a 4th consumer**: `handle_correlation_complete` at `job_feedback_observer.py:550` was identified as a 4th site that short-circuits on `_get_processing_job_for_instance` returning None. However, this method has ZERO callers in daemon/ (production code) — it was replaced by the natural path through `_process_event` during the Report-Lane Decoupling (2026-06-24). The plan correctly does not need to address it.

2. **Council flagged W4 supersede path**: `manager.py:2762-2769` cancels extra PROCESSING jobs when multiple are found. After D13, this becomes dead code. The plan doesn't explicitly call it out, but Phase 5 Task 5.4 (acceptance grep) will surface it. Acceptable.

3. **Council flagged Phase 2 standalone safety**: The data migration (Phase 2 Task 2.8) cancels in-flight MESSAGE JobItems, making Phase 2 standalone-safe IF every consumption site handles "no MESSAGE JobItem found" gracefully. The plan's Phase 2.5 addresses the three known sites; any remaining `job_type="message"` reads will be caught by the grep gate in Task 2.6.

4. **`_emit_in_progress` with `job_id=None`**: As noted above, this generates noisy WARNING logs but doesn't break the flow. The plan could add a Task 2.5.x to handle this gracefully (e.g., pass `instance_id` to `notify_watchers` instead of `job_id` when no JobItem exists). Non-blocking.

### Final Assessment

The plan is **internally consistent** and addresses all three blocking issues from iteration 001. The 2.5 phase is well-scoped — it correctly identifies the consumption sites and proposes clean rewrites using existing Task repository primitives. The sequential coupling (Phase 2 → 2.5 → 3 → 4 → 5) is well-defined and the data migration provides a safety net for the brief window between D13 and the consumption-site rewrites.

The plan is **ready for implementation**.


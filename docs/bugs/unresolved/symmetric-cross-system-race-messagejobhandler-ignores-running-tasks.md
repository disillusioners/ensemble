# Bug: Symmetric Cross-System Race — MessageJobHandler Ignores RUNNING Tasks

> **✅ Resolved.** This cross-system race is addressed by the ExecutionGate, which serializes execution across both dispatchers. If the doc body mentions a partial SQL carve-out as the only mitigation, note that the ExecutionGate is now the authoritative fix. For the current architecture, see [`../../architecture/message-processing-and-correlation.md`](../../architecture/message-processing-and-correlation.md).

**Date:** 2026-06-06
**Severity:** High (same class as commit 46cf524, opposite direction)
**Status:** Unresolved
**Related:** [`docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md`](child-completion-report-lost-under-concurrent-task-processing.md), commit `46cf524`
**Affected Component:** `daemon/services/message_job_handler.py:67-97`

---

## Summary

Commit `46cf524` closed the **task-shadowing-job** direction of the cross-system race: when the child-completion handler enqueued a `Task` for the parent while a `message` job was still `processing`, the task worker no longer claims the task until the job completes.

The **reverse direction remains open**: `MessageJobHandler.handle` only checks for other *processing `message` jobs* when starting a new one. It does **not** check the `task` table for RUNNING siblings. So a `message` job can be claimed and start `graph.astream` while a sibling task (typically a child-completion report) is still running against the same `instance_id` — producing the identical fork-from-stale-state corruption, just with job and task roles swapped.

---

## The Asymmetry, Side by Side

| Direction | Path | Fixed? | Guard location |
|-----------|------|--------|----------------|
| Task claims while job runs | Child-completion handler enqueues a `Task` for parent; task worker claims it before the original `message` job finishes. | **Fixed in 46cf524** | `task/repository.py:claim_pending_task` — added `NOT IN (job_queue_items WHERE processing AND job_type='message')` |
| Job claims while task runs | User posts a new message via the API while a child-completion-report task is still processing. The new `message` job is enqueued, `MessageJobHandler.handle` runs, sees no other `processing` MESSAGE job, and starts `graph.astream` concurrently with the task. | **Unresolved** | `message_job_handler.py:67-97` — only checks `find_processing_message_jobs_by_instance` |

---

## Why It Hasn't Fired Yet

The child-completion report path is the dominant producer of `Task` rows. The reverse path requires a **user message arriving while a child report is mid-flight**, which is the same condition the original bug report described ("coder finishes too fast"). The original observation was the task claiming before the job, but the timing window also admits a user message arriving in the same window — both are race-eligible. We only closed one of them.

The other path that produces a `Task` against an active `message` job is the resume-after-restart path (`MessageJobHandler` calls `_process_message_with_tracking` with `is_retry=True` for retried jobs). That path also doesn't check the `task` table.

---

## Symptom (Predicted, Not Yet Observed)

Same checkpoint progression as the original bug, with task and job roles swapped:

| Step | Source | Msg Version | Messages |
|------|--------|-------------|----------|
| ... | ... | ... | ... (existing history) |
| N | loop | vK | ... + AIMessage "Done! 👋" (produced by the **task** processing the child report) |
| N | input | vK-1 | ... only (the **job**'s `graph.astream` forks from an older checkpoint, appends the new HumanMessage) |
| N+1 | loop | ... | the HumanMessage at position K-1 has shadowed the AIMessage "Done! 👋" |

The user would see the same symptom: an assistant message that was streamed to the UI (and sent via SSE) is missing on refresh.

---

## Root Cause

`MessageJobHandler.handle` at `daemon/services/message_job_handler.py:67-97`:

```python
active = await asyncio.to_thread(
    self._job_repo.find_processing_message_jobs_by_instance, instance_id
)
active_other = [j for j in active if j.job_id != job.job_id]
if active_other:
    # Another MESSAGE job is processing for this instance.
    # Back-transition this job: PROCESSING → PENDING so it's picked up next poll cycle.
    ...
    return
```

This guard excludes only **other `message` jobs** on the same instance. It does not consult the `task` table for RUNNING siblings, and it does not consult `task` for any task type — a child-completion-report task (`process_message` task_type) is invisible to this check.

The asymmetry exists because the task system's per-instance guard and the job system's per-instance guard were developed independently. The task guard was extended in `46cf524` to look at `job_queue_items`; the job guard was never extended to look at `task`.

---

## Proposed Fix (Mirror of 46cf524)

In `MessageJobHandler.handle`, after the `active_other` check, add a second check that the `task` table has no RUNNING row for this `instance_id`. If a task is running, back-transition the job to PENDING and release the per-queue lock (same pattern as the existing `active_other` branch).

Sketch:

```python
# Also exclude if a task (e.g. child-completion report) is processing
# for the same instance — same langgraph thread contention as the
# active_other check above. See commit 46cf524 for the symmetric fix.
task_repo = self._task_repo  # new dependency injected into MessageJobHandler
running_tasks = await asyncio.to_thread(
    task_repo.find_running_tasks_by_instance, instance_id
)
if running_tasks:
    logger.info(
        f"[TRACE] MessageJobHandler.handle: SKIP job {job.job_id[:8]}... — "
        f"instance {instance_id[:8]}... has RUNNING task processing, re-queuing"
    )
    result = await asyncio.to_thread(
        self._job_repo.atomic_transition, job.job_id,
        from_status="processing", to_status="pending",
    )
    if result is None:
        return
    if job.project_id and job.queue_id:
        await self._job_service._lock_manager.release_queue_lock(
            job.project_id, job.queue_id, job.job_id
        )
    return
```

A new `find_running_tasks_by_instance` method on `TaskRepository` mirrors the existing `find_processing_message_jobs_by_instance` on `JobRepository`.

---

## Tests Needed

1. `test_message_job_handler_defers_when_task_processing_for_instance` — job is enqueued, task is RUNNING on the same instance, handler back-transitions the job to PENDING.
2. `test_message_job_handler_proceeds_when_task_for_different_instance` — task is RUNNING on a different instance, handler proceeds normally.
3. End-to-end: the same `test_race_*` scenarios that exist for the task→job direction should exist for job→task.

---

## Workaround

None at the user level. The bug only triggers when a user posts a message in the narrow window between child completion and parent report processing. If the user observes a missing assistant message after a child spawned and reported back, it's likely this race rather than the one we just fixed.

---

## Related Observations (Not Separately Tracked)

- **Stale doc comment in `repository.py:444`** — `has_pending_tasks_blocked_by_busy_instance` still claims "Cheap: two index lookups" but now has an `OR EXISTS` with a second subquery (up to four lookups in the worst case). Cost is still trivial; comment is misleading.
- **No `FAILED`-path test for the new cross-system guard** — the test in `46cf524` covers `completed` and `cleanup`-job non-blocking, but not the `failed` job unblock path. The SQL is unambiguous (only `processing` blocks), so this is a low-value test, but cheap to add for completeness.
- **`task.repository.claim_pending_task` could be called from the job system too** — the cleanest long-term fix is a single `instance_lock` table (or `SELECT FOR UPDATE` on `instances.instance_id`) used by both systems. Out of scope for the immediate fix; worth considering as a follow-up refactor.

---

## Update 2026-06-06 (Post-Commit)

The carve-out above was implemented as part of the fix for an additional deadlock discovered in production:

**Deadlock that motivated the carve-out:**
1. Parent's job is mid-flight, spawns a child, instance transitions to `WAITING_CHILDREN`.
2. `MessageJobHandler.handle` defers job completion (job stays `PROCESSING` because the FIFO queue must not start the next job).
3. Child completes, `child_reports` enqueues a `Task` for the parent to receive the child report.
4. Original fix would have blocked the task from claiming because the job is still `PROCESSING`.
5. **Deadlock:** the job waits for the child report (so the instance can complete), the child report task waits for the job (so it can claim).

**Resolution:** The job must stay in `PROCESSING` for FIFO correctness. The carve-out is in the task claim's view of "actively blocking" — the job is only treated as a blocker when the instance is NOT in `WAITING_CHILDREN` and has `waiting_for = 0`. When the instance IS in that deferred state, the job is just a FIFO placeholder and the task is allowed to claim.

SQL carve-out (committed in the same fix):

```sql
SELECT j.instance_id FROM job_queue_items j
LEFT JOIN instances i ON j.instance_id = i.instance_id
WHERE j.status = 'processing'
AND j.job_type = 'message'
AND j.instance_id IS NOT NULL
AND j.deleted_at IS NULL
AND COALESCE(i.waiting_for, 0) = 0
AND (i.status IS NULL OR i.status != 'waiting_children')
```

This means the task-claim's "actively processing" predicate is now: PROCESSING MESSAGE job AND instance is not waiting for children. The job-side fix (`MessageJobHandler` checking the task table) is still needed for the reverse direction, but with the same "is the task actually driving graph.astream?" caveat — i.e., the task check should only treat RUNNING tasks as blockers when they're not in some equivalent deferred state.

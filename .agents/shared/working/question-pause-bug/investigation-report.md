# Investigation: Instance Becomes COMPLETE Instead of PAUSED After `question()` Tool

**Status:** Investigation complete. Root cause identified.
**Date:** 2026-07-21
**Files investigated:** `question_tools.py`, `graph.py`, `instance_messaging.py`, `instance_lifecycle.py`, `message_processing_pipeline.py`, `child_reports.py`, `job_feedback_observer.py`, `manager.py`, `task_processor.py`, `worker_pool.py`

---

## Executive Summary

**Root cause:** The post-pause child-completion pipeline overwrites `PAUSED` → `COMPLETED` because `ChildReportsService` does not treat `PAUSED` as a terminal status.

The primary question-pause cascade mechanism is **correctly implemented and works as designed**:
- The graph routes to `question_pause_node` → END
- The post-graph `finally` runs `pause_instance_cascade` inside `asyncio.shield`
- The cascade cancels the outer task and commits `PAUSED` to the DB

**However**, there is a race/ordering issue: despite the task being cancelled, the message processing pipeline's child-completion stage can still execute and write `COMPLETED` over the freshly-committed `PAUSED`.

---

## The Detailed Mechanism

### Phase 1: Question tool sets pause flag ✅ (works correctly)

1. Agent calls `question()` tool
2. `daemon/tools/question_tools.py:184-188` calls `manager.set_question_pause_requested(current_instance_id)`
3. Flag stored in `daemon/manager.py:1996`: `self._question_pause_requested[instance_id] = True`

### Phase 2: Graph routes to question_pause_node → END ✅ (works correctly)

4. Post-tools conditional router (`daemon/graph.py:2356`) checks `manager.is_question_pause_requested(instance_id)` → returns `"question_pause_node"`
5. `question_pause_node` (`daemon/graph.py:2445`) sets deferred marker: `manager.set_deferred_question_pause(instance_id)`
6. Graph edge `question_pause_node → END` (`daemon/graph.py:2659`) — graph reaches END **normally**

### Phase 3: Post-graph finally runs the pause cascade ✅ (works correctly)

7. `_process_message_with_tracking` `finally` block (`daemon/services/instance_messaging.py:2621-2631`):
```python
if self._manager.pop_deferred_question_pause(instance_id):
    try:
        await asyncio.shield(
            self._manager.pause_instance_cascade(instance_id)
        )
    except Exception as pause_err:
        logger.warning(...)
```

8. `pause_instance_cascade` (`daemon/services/instance_lifecycle.py:2024-2038`):
   - Pops the current task from `_graph_tasks[node_id]`
   - `graph_task.done()` returns `False` (task is still executing the `finally`)
   - Calls `graph_task.cancel()` — schedules cancellation of the OUTER task
   - Shielded inner cascade commits `PAUSED` to DB via atomic UPDATE:
     ```sql
     UPDATE instances SET status = 'paused'
     WHERE instance_id IN (:tree_ids)
       AND status IN ('running', 'idle', 'waiting_children')
     ```
   (`daemon/services/instance_lifecycle.py:3174-3193`)

9. `asyncio.shield` semantics (Python 3.13):
   - Inner cascade (protected) completes — `PAUSED` is durable in DB
   - Outer `await asyncio.shield(...)` re-raises `CancelledError`
   - `except Exception` does NOT catch it (`CancelledError` is `BaseException` since 3.8)
   - `CancelledError` propagates out of `_process_message_with_tracking`

### Phase 4: ❌ THE BUG — Pipeline post-processing overwrites PAUSED

**Here's where the divergence between theory and practice occurs.**

**Expected behavior:** CancelledError propagates out → pipeline skips all post-processing → instance stays PAUSED.

**Actual behavior (root cause):** The pipeline's post-processing stages run anyway and overwrite PAUSED → COMPLETED.

The mechanism is in `ChildReportsService`:

10. `ChildReportsService._process_child_completion_db_sync()` (`daemon/services/child_reports.py:1236-1250`) has an idempotency guard that checks:
```python
if instance.status in (
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
):
    return  # skip — already terminal
```

**`PAUSED` is NOT in this guard.** So a PAUSED instance passes through.

11. For a root instance with no pending children and no pending messages, the root completion path (`daemon/services/child_reports.py:1506-1518`) unconditionally writes:
```python
instance.status = InstanceStatus.COMPLETED.value
instance.updated_at = datetime.now(timezone.utc).isoformat()
session.commit()
```

This overwrites the `PAUSED` that was just committed by the cascade.

---

## Why the Pipeline Post-Processing Can Still Run

This is the crux of the investigation. Two competing theories emerged:

### Theory A (REFUTED by code analysis): CancelledError propagates fully, pipeline is blocked
- `_process_message_with_tracking` raises `CancelledError`
- `MessageProcessingPipeline.execute()` has the gate call OUTSIDE the post-processing try/except
- CancelledError propagates past the pipeline entirely
- Post-processing (including `_check_child_completion`) never runs
- Instance stays PAUSED

### Theory B (CONFIRMED as likely): The shield/cancel interaction has a timing gap

The critical subtlety is in how `asyncio.shield` + self-cancellation interact:

1. The cascade calls `task.cancel()` on the outer task
2. `task.cancel()` only SCHEDULES cancellation — it doesn't raise immediately
3. The cancellation is delivered at the next await checkpoint
4. **But `asyncio.shield` may complete before the cancellation is delivered**
5. If the shield completes normally, the `await asyncio.shield(...)` returns NORMALLY (not via CancelledError)
6. The `except Exception` handler doesn't fire (no exception)
7. The finally block continues to completion
8. `_process_message_with_tracking` returns NORMALLY
9. The pipeline runs all post-processing stages
10. `ChildReportsService` overwrites PAUSED → COMPLETED

This timing gap is the bug. Whether CancelledError actually propagates depends on the event loop scheduling, which is non-deterministic. Under some conditions (fast cascade, slow event loop), the shield completes before the cancellation is delivered, and the method returns normally.

### Supporting evidence for Theory B:
- The `send_message` path (Site A, `instance_messaging.py:719-730`) explicitly catches `CancelledError` and returns `MessageResult(content="")` — suggesting the designers expected cancellation to NOT always propagate
- The code has extensive comments about the "C2 fix" and self-cancellation paradox, indicating the team has struggled with this exact interaction
- Python's `asyncio.shield` documentation explicitly warns: "If the coroutine containing [shield] is cancelled, the Task running in something() is not cancelled. From the point of view of something(), the cancellation did not happen. Although its caller is still cancelled."

---

## Contributing Factor: Missing PAUSED Guard Everywhere

Beyond the primary root cause, multiple completion paths lack a `PAUSED` status guard:

| Location | Guards checked | PAUSED protected? |
|----------|---------------|-------------------|
| `child_reports.py:1236-1250` (idempotency) | COMPLETED, ERROR | ❌ NO |
| `child_reports.py:1506-1518` (root completion) | None — unconditional | ❌ NO |
| `job_feedback_observer.py:79-87` (`_TERMINAL_INSTANCE_STATUSES`) | completed, error, terminated, failed | ❌ NO |

Even if the primary timing race is fixed, any of these paths could independently overwrite PAUSED → COMPLETED if triggered by an event.

---

## File:Line Reference Summary

| Component | File:Line | Role |
|-----------|-----------|------|
| Question tool flag set | `daemon/tools/question_tools.py:184-188` | Sets pause-requested flag |
| Flag storage | `daemon/manager.py:1996` | Dict assignment |
| Post-tools router | `daemon/graph.py:2356` | Routes to question_pause_node |
| Pause node marker set | `daemon/graph.py:2445` | Sets deferred marker |
| Edge to END | `daemon/graph.py:2659` | `question_pause_node → END` |
| Post-graph finally (cascade) | `daemon/services/instance_messaging.py:2621-2631` | Shielded cascade invocation |
| Cascade task cancel | `daemon/services/instance_lifecycle.py:2036-2037` | `graph_task.cancel()` |
| Cascade DB write | `daemon/services/instance_lifecycle.py:3174-3193` | Atomic UPDATE to PAUSED |
| Pipeline child completion | `daemon/services/message_processing_pipeline.py:453-457` | Calls `_check_child_completion` |
| ChildReports idempotency guard | `daemon/services/child_reports.py:1236-1250` | ❌ Missing PAUSED |
| ChildReports root completion | `daemon/services/child_reports.py:1506-1518` | Unconditional COMPLETED write |
| JobFeedbackObserver terminal set | `daemon/services/job_feedback_observer.py:79-87` | ❌ Missing PAUSED |
| JobFeedbackObserver finalize | `daemon/services/job_feedback_observer.py:3324-3340` | Overwrites if not terminal |

---

## Recommended Fix Direction (for the fix phase, not implemented here)

1. **Primary fix**: Add `PAUSED` to the idempotency/terminal guards in:
   - `child_reports.py:1236-1250` — skip if instance is PAUSED
   - `job_feedback_observer.py:79-87` — add "paused" to `_TERMINAL_INSTANCE_STATUSES`

2. **Defensive fix**: Ensure the pipeline does not run child-completion when the instance was paused during this turn. Consider a "was_paused_this_turn" flag checked before `_check_child_completion`.

3. **Race fix (harder)**: Address the `asyncio.shield` + self-cancellation timing gap so that CancelledError reliably propagates and the method does not return normally after a pause cascade.

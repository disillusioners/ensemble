# Bug: Job Marked Completed Prematurely When Root Instance Has Self-Pending Messages

**Date:** 2026-06-16
**Severity:** High
**Status:** Confirmed, regression from prior fix
**Affected Component:** `daemon/services/child_reports.py:_process_child_completion_and_notify_parent`
**Production log:** `logs/premature-job-complete.log`
**Production job:** `58b9f77c-0ec2-4fe8-b879-c370085bf79d`
**Production instance:** `8d71f05f-3275-4bb8-8a26-f15636c57799` (jober, root)

---

## Summary

A root (parent-less) instance that finishes processing a message while another message is still in its queue (status `READY` / `PROCESSING` / `RETRYING`) is incorrectly transitioned to `COMPLETED` instead of `WAITING_CHILDREN`. This causes the job bound to that instance to be marked `completed` in `job_queue_items` prematurely, while the instance itself is later resurrected to continue processing the remaining queue — leading to the leader/orchestrator receiving a "job done" signal for work that is still in progress.

The bug is a **regression** introduced by two commits on 2026-05-24 that attempted to fix a different bug ("simple agent stuck in WAITING_CHILDREN"). The fix correctly identified that `waiting_for > 0` and `pending_count > 0` are independent signals, but then **dropped the safety net** for the asymmetric case (`waiting_for == 0 && pending_count > 0`) by adding a warning that does not act.

---

## Symptom (Log Evidence)

From `logs/premature-job-complete.log`:

```
17:28:00 - daemon.services.task_processor - Processing message task 2216: message=806da3d3..., instance=8d71f05f...
17:28:00 - daemon.services.child_reports   - _process_child_completion_and_notify_parent called: instance=fc251a1d..., message_id=35e751ff
17:28:00 - daemon.services.child_reports   - waiting_for decremented -> 0 (parent=8d71f05f..., child=fc251a1d...)
17:28:00 - daemon.services.child_reports   - Parent 8d71f05f... all children done but has 2 pending messages, status=WAITING_CHILDREN
...
17:28:17 - daemon.services.child_reports   - _process_child_completion_and_notify_parent called: instance=8d71f05f..., message_id=e46e1d87
17:28:17 - daemon.services.child_reports   - Instance 8d71f05f... parent_id=None, waiting_for=0, status=waiting_children
17:28:17 - daemon.services.child_reports   - WARNING - Instance 8d71f05f has pending_count=1 but waiting_for=0 — proceeding to COMPLETED (not waiting_children)
17:28:17 - daemon.services.child_reports   - Instance 8d71f05f... no parent, skipping notification
17:28:17 - daemon.services.child_reports   - Instance 8d71f05f... completed (no parent, no children), status=COMPLETED
17:28:17 - daemon.repositories.job_queue   - Job transition: 58b9f77c-... | processing -> completed (complete)
17:28:17 - daemon.services.instance_messaging - Reactivating completed instance 8d71f05f... for new message (WorkerPool)
17:28:31 - daemon.services.instance_lifecycle - Spawning instance 51485f89-cc8e-495c-9744-fb06073ade92 (agent=coder, parent=8d71f05f...)
17:28:52 - daemon.tools.instance           - waiting_for incremented -> 1 (parent=8d71f05f..., child=51485f89...)
```

**Timeline:**
- 17:28:17 — Job `58b9f77c` marked `completed` in `job_queue_items` (DB confirmed: `completed_at=2026-06-16T10:28:17.806031+00:00`)
- 17:28:31 — Coder `51485f89` spawned (still running as of this bug report)
- 17:28:52 — `send_message` to coder increments `waiting_for` to 1
- 17:29:22 — Leader notices the discrepancy: "Result Mismatch Detected" (line 288 of log)

The leader had issued `watch_job` on `58b9f77c` expecting to be notified when the entire orchestration (including the coder's work) completed. The premature job completion caused the leader to detach before the coder finished.

---

## Root Cause

### The Code Path

`daemon/services/child_reports.py:659-727` (root-instance branch of `_process_child_completion_and_notify_parent`):

```python
if instance.parent_id is None:
    if instance.waiting_for > 0:
        # Has children still running - transition to WAITING_CHILDREN
        instance.status = InstanceStatus.WAITING_CHILDREN.value
        session.commit()
        return

    # waiting_for == 0, but check for pending messages before completing.
    pending_count = session.exec(
        select(func.count())
        .select_from(MessageQueue)
        .where(MessageQueue.instance_id == instance_id)
        .where(MessageQueue.status.in_([
            MessageStatus.READY.value,
            MessageStatus.PROCESSING.value,
            MessageStatus.RETRYING.value,
        ]))
    ).scalar_one()

    if instance.waiting_for > 0 and pending_count > 0:
        # Has explicit children to wait for
        instance.status = InstanceStatus.WAITING_CHILDREN.value
        session.commit()
        return
    elif pending_count > 0 and instance.waiting_for == 0:
        # ← BUG: logs a warning but does NOT return, falls through to COMPLETED
        logger.warning(
            "Instance %s has pending_count=%d but waiting_for=0 — "
            "proceeding to COMPLETED (not waiting_children)",
            instance_id[:8], pending_count,
        )

    # No children, no pending messages - safe to complete
    instance.status = InstanceStatus.COMPLETED.value
    # ... emits status_change, signals CompletionRegistry, publishes lifecycle event
```

### The Two Signals

The code maintains two independent counters, both of which must be zero for an instance to be safely completed:

| Signal | Meaning | Source |
|--------|---------|--------|
| `instance.waiting_for` | Number of child instances that have been spawned via `send_message` and have not yet sent a completion report | Decremented atomically in `_update_parent_on_child_complete` (`child_reports.py:424`) when a child reports back |
| `pending_count` (from `MessageQueue`) | Number of messages in this instance's queue with status `READY` / `PROCESSING` / `RETRYING` | Decremented when a message is marked `COMPLETED` in `message_queue` |

The current code does **not** require both to be zero before transitioning to `COMPLETED`. The `elif pending_count > 0 and instance.waiting_for == 0` branch logs a warning but **falls through to COMPLETED**, effectively discarding the pending-message check.

### Why It Happens in This Scenario

The orchestrator (jobber) instance `8d71f05f` is a root instance (no parent). The DB shows it has 3 messages in its queue over its lifetime:

| `message_id` | `type` | `source` | `enqueued_at` | `completed_at` |
|---|---|---|---|---|
| `77decf16` | human | api | 17:19:40 | 17:21:30 |
| `e46e1d87` | human | agent:jober | 17:26:08 | 17:28:17 |
| `806da3d3` | completion_report | internal_report:fc251a1d:35e751ff | 17:28:00 | 17:29:01 |

The race:

1. 17:28:00 — Giter child `fc251a1d` completes message `35e751ff` → triggers `_process_child_completion_and_notify_parent(fc251a1d, 35e751ff)`.
2. 17:28:00 — Cascade in `_update_parent_on_child_complete` decrements `8d71f05f.waiting_for` from 1 to 0. Jobber has 2 pending messages (`e46e1d87` was already in queue, and `806da3d3` is just enqueued). Jobber transitions to `WAITING_CHILDREN`.
3. 17:28:00–17:28:17 — Task 2216 (processing `806da3d3`) experiences lease contention (8 retries within 17s — visible at log lines 175-182).
4. 17:28:17 — Concurrently, message `e46e1d87` (the jobber's own `job_continue` continuation) finishes LLM processing. `message_queue.complete(e46e1d87)` is called. Then `_process_child_completion_and_notify_parent(8d71f05f, e46e1d87)` is invoked.
5. 17:28:17 — Inside the function, `instance.waiting_for == 0` (correctly decremented at 17:28:00). `pending_count == 1` (correctly counting `806da3d3` which is still `READY` — its LLM processing has not yet completed). The asymmetric `elif` branch fires, logs the warning, and **falls through to COMPLETED**.
6. 17:28:17 — Jobber marked `COMPLETED`. `JobFeedbackObserver` propagates to `job_queue_items: 58b9f77c` → marked `completed`.
7. 17:28:17 — A new message arrives for `8d71f05f` (a `job_continue` from the leader, via `WorkerPool`), reactivating the "completed" instance.
8. 17:28:31–17:28:52 — Jobber spawns coder `51485f89` and sends it a task. `waiting_for` increments to 1. But the job is already `completed` in `job_queue_items` — any `watch_job` / `job_get` from the leader sees the premature completion.

### Why No Test Caught It

The test suite (`tests/unit/test_ready_message_completion_report.py`) covers the `_should_send_completion_report` function (child → parent report idempotency), not the root-instance self-completion branch in `_process_child_completion_and_notify_parent`. The asymmetric case `waiting_for == 0 && pending_count > 0` is not exercised by any test.

---

## Git History: This Is a Regression

| Commit | Date | What it did |
|--------|------|-------------|
| `4e2d3551` | earlier | Phase 4 refactor. Original code: `if pending_count > 0: ... return` (single guard, returns early with `WAITING_CHILDREN`). |
| `3b8fa746` | 2026-05-24 19:15 | "fix: instance state transition bugs — parent stuck waiting_children + simple agent wrong state". Split the guard into two conditions:<br>• `if instance.waiting_for > 0 AND pending_count > 0` → `WAITING_CHILDREN` + `return`<br>• `elif pending_count > 0 AND instance.waiting_for == 0` → log warning, **NO `return` — falls through to `COMPLETED`** |
| `e7e9f0d9` | 2026-05-24 19:33 | "fix: remove message_id None guard from handler, add defensive guard deeper". Changed the warning text from `"— not setting waiting_children"` to `"— proceeding to COMPLETED (not waiting_children)"`, **making the fall-through to `COMPLETED` explicit and intentional** per the W2 bullet: "Improve log message to explicitly state outcome (proceeding to COMPLETED)". |

### The Original Bug the Fix Was Trying to Address

A "simple agent" (one that doesn't spawn children, but does enqueue self-continuation messages) was getting stuck in `WAITING_CHILDREN` after the first message completed, because the original code set `WAITING_CHILDREN` whenever any pending message existed — even when there were no children to wait for.

### The Flaw in the Fix

The fix conflated "no spawned children" (`waiting_for == 0`) with "no work to wait for". A pending message in `MessageQueue` is work to wait for, regardless of whether it came from a child or was self-enqueued. The fix needed to distinguish:

- (a) `waiting_for == 0 && pending_count == 0` → safe to complete
- (b) `waiting_for == 0 && pending_count > 0` → still has queued work; do NOT complete (this is the bug case)
- (c) `waiting_for > 0 && pending_count == 0` → defensive no-op (cascade in `_update_parent_on_child_complete` already handled this)
- (d) `waiting_for > 0 && pending_count > 0` → `WAITING_CHILDREN` (correctly handled)

The fix handled (a), (c), (d) but incorrectly collapsed (b) into (a) with a warning.

---

## Solution

### Goal

Transition the instance to `WAITING_CHILDREN` whenever either `waiting_for > 0` or `pending_count > 0` is true. Only transition to `COMPLETED` when **both** are zero.

### Approach 1: Minimal Fix (Re-introduce the Original Bug Carefully)

The simplest fix is to change the `elif` branch into a `WAITING_CHILDREN` transition:

```python
if instance.waiting_for > 0 and pending_count > 0:
    # Has explicit children to wait for
    instance.status = InstanceStatus.WAITING_CHILDREN.value
    session.commit()
    return
elif pending_count > 0 and instance.waiting_for == 0:
    # Has pending messages in queue (e.g., self-continuation, child report, or queued job_event)
    # — must not complete until the worker picks them up
    instance.status = InstanceStatus.WAITING_CHILDREN.value
    session.commit()
    logger.info(
        f"Instance {instance_id[:8]}... waiting_for=0 but has {pending_count} "
        f"pending messages, status=WAITING_CHILDREN"
    )
    if self._manager._live_hub:
        try:
            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
        except Exception as e:
            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
    return
```

**Risk:** This re-introduces the original "simple agent stuck in WAITING_CHILDREN" bug. A simple agent that enqueues self-continuation messages and processes them all in one job will now stay in `WAITING_CHILDREN` forever (the messages will be processed by the job queue, but nothing will transition it back to `COMPLETED` when the last one finishes).

**Mitigation:** Verify that the message-completion code path (`message_job_handler.py` and `task_processor.py` calling `_process_child_completion_and_notify_parent`) handles the loop correctly: after each message completes, this function re-runs, `pending_count` decrements, and eventually reaches 0 — at which point the function falls through to `COMPLETED`. The WorkerPool / job processor should then pick up the next message and reactivate the instance.

In other words: as long as `_process_child_completion_and_notify_parent` is called **after every message completion** (which it is, per `message_job_handler.py:274` and `task_processor.py:389`), the loop will terminate correctly when the queue drains.

### Approach 2: Distinguish Pending Message Types

Inspect `MessageQueue.source` or `MessageQueue.type` to distinguish:

- `type = COMPLETION_REPORT` (source = `internal_report:*`) — must block (this is the bug case)
- `type = JOB_EVENT` — must block
- `type = human` with `source = agent:*` (self-continuation) — may or may not block
- `type = human` with `source = api` (user reply still pending) — must block

This is more precise but requires understanding the semantics of each `source` value. The relevant code is `daemon/services/message_job_handler.py` and `daemon/services/task_processor.py` for the `source` strings used.

### Approach 3: Make the Cascade Aware of the Same Signals

Alternative: rather than fixing only the root-instance branch, ensure that `_update_parent_on_child_complete` (line 478-524) — which runs in the child → parent path — also checks `pending_count` and does not transition the parent to `WAITING_CHILDREN` if there are no children but there are pending messages. Currently it does check `parent_pending` (line 484-493) and correctly transitions to `WAITING_CHILDREN` if `parent_pending > 0`. So that path is fine.

The bug is specifically in the root-instance branch (line 659-727) which is the self-completion path.

### Recommended Fix

**Approach 1 is the minimal change** that addresses the regression without introducing new semantics. The "simple agent stuck" risk is mitigated by the fact that the completion function is called after every message and will eventually reach the `pending_count == 0` state naturally.

If the original "simple agent stuck" bug resurfaces, **then** escalate to Approach 2 and add a test that exercises the self-continuation case to determine the correct behavior.

### Tests to Add (Before/After Fix)

1. **Test the regression case** — root instance with `waiting_for == 0` and `pending_count == 1` (e.g., a child completion report still in queue) should transition to `WAITING_CHILDREN`, not `COMPLETED`.
2. **Test the original bug case** — root instance with `waiting_for == 0`, `pending_count == 0`, and no children should transition to `COMPLETED` (this is the simple-agent happy path).
3. **Test the "all children done" case** — root instance with `waiting_for == 0`, `pending_count > 0` from self-enqueued continuation messages, then `pending_count` drops to 0 after the worker picks them up — should transition to `COMPLETED` after the last one.

Tests should be added in `tests/unit/test_ready_message_completion_report.py` (currently covers only the child → parent report path) or a new `tests/unit/test_root_instance_completion.py`.

---

## Caveats for the Fix

1. **Don't just add a `return` in the `elif` branch** without changing the status to `WAITING_CHILDREN` — that would leave the instance in its current state (likely `running` or `waiting_children`) but skip emitting the SSE event and the `CompletionRegistry.complete()` call. The completion path is correct; only the *transition* is wrong.

2. **Watch for cascading effects**: the `pending_count` query at line 681-690 does not exclude the just-completed `message_id` (unlike `_should_send_completion_report` at line 270-279 which does). For the `e46e1d87` case, this didn't matter because `message_queue.complete()` was called before the function ran. But if there's any race where the message is not yet committed as `completed` in `message_queue` when this function queries, it could double-count. This is a separate latent issue.

3. **The third case** (`waiting_for > 0 && pending_count == 0`) also falls through to `COMPLETED` after the `if`/`elif` chain. In theory reachable if a child reports back while the parent has no other pending messages. In practice, `_update_parent_on_child_complete` at line 478-524 handles this and transitions the parent before `_process_child_completion_and_notify_parent` is called for the parent. So the third case is a defensive no-op in normal flow.

4. **The "Result Mismatch Detected" log line at the end of `premature-job-complete.log`** (line 288) is the leader noticing the premature job completion and reacting. This confirms the bug is observable from the orchestrator side as well, not just from internal state.

---

## Related Files

- `daemon/services/child_reports.py:659-727` — root-instance self-completion branch (the bug site)
- `daemon/services/child_reports.py:478-524` — child → parent cascade (correct, but for reference)
- `daemon/services/message_job_handler.py:274` — calls `_process_child_completion_and_notify_parent` after each message
- `daemon/services/task_processor.py:389` — also calls `_process_child_completion_and_notify_parent` after each task
- `daemon/services/job_feedback_observer.py:401,415` — observes instance lifecycle events and calls `complete_job`
- `daemon/repositories/job_queue/repository.py` — stores `job_queue_items` rows; the premature `processing -> completed` transition happens here
- `docs/bugs/parent-instance-premature-completion-on-fast-child.md` — related but different bug (child completes very fast, parent's LLM still running)
- `docs/bugs/job-completed-when-parent-agent-not-done.log` — possibly related historical log

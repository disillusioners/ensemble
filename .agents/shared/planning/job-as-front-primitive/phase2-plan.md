# Phase 2: Per-Instance Serialization + Retry Policy

## Objective
Wire message-Jobs into the per-instance serialization machinery so that two message-Jobs on the same instance never double-execute. Configure retry policy so chat/continuation Jobs never dead-letter (retry=0).

**RF1 Amendment**: This phase explicitly acknowledges that the cross-system guard (`claim_pending_task:607-646`) becomes **load-bearing** under universal message-JobItem traffic. If the Phase 0 load test (Gate 2) shows regression, this phase includes an explicit guard optimization/modification task — the guard is NOT treated as frozen backend.

## Coupling
- **Depends on**: Phase 1 (message-Job creation must exist), Phase 0 Gate 2 result (guard performance data)
- **Coupling type**: tight (serialization guard must recognize message-JobItems)
- **Shared files with other phases**: `daemon/repositories/task/repository.py` (cross-system guard SQL)
- **Why this coupling**: The cross-system guard in `claim_pending_task` is the serialization authority. It must correctly handle message-JobItems without deadlocking.

## Context

### Current Per-Instance Serialization (3 Layers)

1. **Task Claim Guard** (`claim_pending_task` line 550-553): `instance_id NOT IN (SELECT instance_id FROM task WHERE status='running')` — at most one RUNNING task per instance.

2. **Cross-System Guard** (`claim_pending_task` lines 576-647, subquery at 607-646): For `process_message` tasks, checks if an active JobItem blocks the instance. Uses `_admitted_task_carve_out_sql(j)` which is NULL-safe: a JobItem only blocks if it carries a `message_id` AND no matching Task exists.

3. **Pause Gate** (lines 554-575): Excludes PAUSED/TERMINATED instances for all task types.

### ⚠️ RF1: Cross-System Guard Is About to Become Load-Bearing

**Today**: the cross-system guard's JobItem subquery (`repository.py:607-646`) fires only for TASK-type JobItems (orchestration — a small fraction of traffic). The `job_queue_items` table has low write rate and low active-row count. Index lookups are cheap.

**After this plan**: every public message creates a JobItem. Every `process_message` Task claim hits the subquery at **100% rate**. The `job_queue_items` table grows by N messages-per-instance × M instances. The subquery at `repository.py:607-646` becomes hot-path SQL.

**Risk**: Query plan regression (seq scan instead of index lookup), lock contention on `job_queue_items`, or p99 latency increase on `claim_pending_task`. Phase 0 Gate 2 measures this. If regression is observed, this phase includes an explicit guard modification task (Task 6 below).

**Why the carve-out still works for correctness**: the `_admitted_task_carve_out_sql(j)` logic is correct — a JobItem with `message_id` + matching Task = not blocking. The concern is **performance**, not **correctness**.

### The Serialization Problem with Message-Jobs

The guard must:
1. **NOT self-deadlock**: When a message-Job's own Task is being claimed, the guard must not block it because of the message-Job's own JobItem row.
2. **Still serialize**: If two message-Jobs are submitted to the same instance simultaneously, the second Task must be blocked until the first completes.

### How the Carve-Out Handles Correctness

The `_admitted_task_carve_out_sql(j)` generates SQL like:
```sql
-- NULL-safe: JobItem only blocks if it carries a message_id
-- AND no matching Task exists
AND (
    j.metadata->>'message_id' IS NULL
    OR EXISTS (
        SELECT 1 FROM task t_match
        WHERE t_match.instance_id = j.instance_id
          AND t_match.message_id = j.metadata->>'message_id'
    )
)
```

For message-Jobs:
- The message-Job's JobItem WILL have a `message_id` stamped (Phase 1 task 5)
- The matching Task WILL exist (created in the same transaction)
- So the carve-out returns TRUE → the guard DOES NOT block → the Task can be claimed ✅

**For serialization of two message-Jobs on the same instance:**
- JobItem A (message_id=msg_a) + Task A → Task A gets claimed (carve-out passes)
- JobItem B (message_id=msg_b) + Task B → Task B is PENDING
- When Task A is RUNNING, the per-instance Task guard (layer 1) already blocks Task B from being claimed.
- When Task A completes and is cleaned up, Task B becomes claimable.

**Conclusion**: The existing 3-layer serialization handles message-Jobs **correctly**. Layer-1 (Task guard) is the primary serialization gate. Layer-2 is about preventing Task-vs-JobItem conflicts, not Task-vs-Task serialization. **However**, layer-2's **performance** is an open question (RF1) addressed below.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Verify serialization with integration test | Write a test that submits 2 message-Jobs to the same instance simultaneously. Assert: only 1 Task runs at a time; the second starts after the first completes. This validates that the existing layer-1 guard handles the case. | `tests/test_message_job_serialization.py` (new) |
| 2 | Set `max_retries=0` on message-JobItems | Verify that `JobRepository.create_message_job()` sets `max_retries=0` and that the retry scheduler ignores message-JobItems. The JobItem model already carries `max_retries: int \| None = Field(default=None)`. Setting to `0` means no retries. | `daemon/repositories/job_queue/repository.py` (Phase 1 method) |
| 3 | Verify dead-letter never fires for message-Jobs | Test that a failed message-Job (instance crashes) does NOT go to dead-letter queue. Instead it should be finalized as `done` with `terminal_reason="error"` or similar. | `daemon/services/job_feedback_observer.py` (verify finalize path), test file |
| 4 | Verify JobItem activation (queued→active) | Test that when a worker claims the driving Task, the message-JobItem transitions from `queued` to `active`. This is the hook added in Phase 1 task 6. | `tests/test_message_job_serialization.py` |
| 5 | Document the serialization model | Add a docstring or comment explaining how message-Jobs serialize: layer-1 Task guard is the authority, cross-system guard is transparent for message-Jobs due to the carve-out. | `daemon/repositories/task/repository.py:576-647` |
| **6** | **RF1: Cross-system guard optimization (CONDITIONAL — only if Phase 0 Gate 2 shows regression)** | **Only execute if Phase 0 benchmark showed >2ms p99 regression on `claim_pending_task` under message-JobItem load.** Options (in order of preference): (a) Add covering composite index `CREATE INDEX idx_job_queue_active_msg ON job_queue_items(instance_id, admission_state, deleted_at) WHERE admission_state IN ('queued','active')` — reduces the subquery's scan cost; (b) Simplify `_admitted_task_carve_out_sql` to skip the `EXISTS` subquery for `job_type='message'` JobItems since they always have a matching Task; (c) If the guard is fundamentally too expensive, bypass it entirely for message-JobItems by filtering `job_type != 'message'` in the blocking subquery — message-Jobs never block their own Task (the carve-out proves this), and layer-1 already serializes. **This is an explicit backend modification, NOT frozen.** | `daemon/repositories/task/repository.py:576-647`, `daemon/repositories/job_queue/models.py` (index addition) |
| **7** | **RF1: Load test serialization under contention** | After Task 6 (if executed) or with unmodified guard, run a high-contention test: 20 instances, each receiving 5 rapid message-Jobs. Verify no double-execution, no deadlock, no timeout. Measure `claim_pending_task` p99 latency under load. | `tests/test_message_job_contention.py` (new) |

## Key Files
- `daemon/repositories/task/repository.py:367-676` — `claim_pending_task` with all 3 guard layers
- `daemon/repositories/job_queue/models.py` — `JobItem.max_retries` field
- `daemon/services/job_feedback_observer.py` — finalize path for message-JobItems
- `daemon/config.py:412-434` — `JobSystemConfig.default_max_retries`, `dlq_enabled`

## Constraints
- If Phase 0 Gate 2 shows no regression: do NOT modify the `claim_pending_task` SQL
- If Phase 0 Gate 2 shows regression: the cross-system guard IS modified (RF1 explicit task) — this overrides the "frozen backend" rule for this specific guard
- `max_retries=0` must be enforced at creation time (Phase 1's `create_message_job`)
- The retry scheduler (if any) must skip JobItems with `max_retries=0`
- If `dlq_enabled=True` globally, message-Jobs must be exempted — test this explicitly

## Testing Strategy

### Test: No-Double-Execution (Critical)
```python
async def test_two_message_jobs_same_instance_serialize():
    """Two message-Jobs to the same instance must not execute concurrently."""
    # 1. Create instance
    # 2. Submit message-Job A (blocks on a slow agent response)
    # 3. Submit message-Job B immediately
    # 4. Assert: only Task A is RUNNING; Task B is PENDING
    # 5. Wait for Task A to complete
    # 6. Assert: Task B becomes RUNNING and completes
```

### Test: No-Dead-Letter (Critical)
```python
async def test_message_job_no_dead_letter():
    """A failed message-Job must NOT enter the dead-letter queue."""
    # 1. Submit message-Job with a failing agent
    # 2. Wait for instance to error
    # 3. Assert: JobItem.admission_state == "done" (not "dead")
    # 4. Assert: JobItem.terminal_reason contains error info
    # 5. Assert: JobItem.retry_count == 0
```

## Deliverables
- [ ] Integration test proves no-double-execution on contended instance
- [ ] **RF1**: `claim_pending_task` load-test results documented; guard modified (or not) based on Phase 0 Gate 2
- [ ] Message-Jobs never dead-letter (retry=0 enforced)
- [ ] JobItem queued→active transition verified
- [ ] Serialization model documented

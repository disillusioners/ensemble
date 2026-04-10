# Phase 5: StaleTaskRecovery Overhaul

## Objective

Rewrite StaleTaskRecovery to use the new 5-step cancellation protocol: detect stale tasks → request cancel → wait briefly → force cancel if needed → schedule retry. This replaces the current "just reset to pending" approach which creates duplicate processing.

## Coupling

- **Depends on**: Phase 3 (repository methods), Phase 4 (cancellation infrastructure in Worker)
- **Coupling type**: tight (calls Phase 3 repository methods), loose (Worker reads cancel_requested flag)
- **Shared files with other phases**: `daemon/services/stale_task_recovery.py`
- **Shared APIs/interfaces**: StaleTaskRecovery constructor, recover_stale_tasks()
- **Why this coupling**: StaleTaskRecovery calls request_cancel() and schedule_retry() from Phase 3; Worker reads the cancel_requested flag that StaleTaskRecovery sets

## Context

### Current StaleTaskRecovery Behavior
1. Find running tasks older than threshold
2. Reset ALL to pending (via `reset_stale_tasks()`)
3. Fail associated messages
4. **Problem**: Worker thread continues running — duplicate processing

### Target 5-Step Recovery Protocol

From the design doc:

1. **Find stale tasks** — running tasks past threshold, not yet flagged for cancel
2. **Request cancellation** — set `cancel_requested=True` on each task
3. **Wait briefly** — give worker time to notice the flag and stop (5-10 seconds)
4. **Force cancel** — for tasks that are STILL running after wait, directly set status=CANCELLED and schedule retry
5. **Schedule retry** — create retry task for each cancelled task (if under max retries)

### Key Insight: Worker Cooperation

When StaleTaskRecovery sets `cancel_requested=True`, the **Worker** doesn't directly read this flag. Instead:
- The Worker has its own TimeoutMonitor + CancellationToken
- StaleTaskRecovery is for the **crash case** — when no Worker is checking the task anymore
- The `cancel_requested` flag is mainly for StaleTaskRecovery's own tracking (step 3 → step 4)

The real flow:
- **Normal timeout**: TimeoutMonitor fires → CancellationToken → Worker catches → retry
- **Worker crash**: No one cancels → StaleTaskRecovery detects → requests cancel → waits → force cancels → retries

## Tasks

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Rewrite recover_stale_tasks() | Implement 5-step protocol with cancel_request → wait → force_cancel → retry. <!-- FIX: C2 --> Only retry if `retry_scheduled=0` | `daemon/services/stale_task_recovery.py` |
| 2 | Add configurable grace period | New parameter: `cancel_grace_seconds` (default: 10) — time to wait after requesting cancel | `daemon/services/stale_task_recovery.py` |
| 3 | Add retry scheduling to recovery | <!-- FIX: W1 --> Use `force_cancel_and_schedule_retry()` for atomic cancel+retry | `daemon/services/stale_task_recovery.py` |
| 4 | Update constructor | Accept max_retries, backoff_base, backoff_max for retry scheduling | `daemon/services/stale_task_recovery.py` |
| 5 | Add recovery event logging | Log each step with task details for observability | `daemon/services/stale_task_recovery.py` |
| 6 | Update startup recovery | <!-- FIX: S3 --> Use `force_cancel_and_schedule_retry()` + detect orphaned CANCELLED tasks | `daemon/services/stale_task_recovery.py` |
| 7 | Write comprehensive tests | Test each step of the 5-step protocol, double-retry guard, orphan detection | `tests/message_queue_redesign/test_stale_task_recovery.py` |

## Key Files

- `daemon/services/stale_task_recovery.py` — Full rewrite of recovery logic
- `daemon/repositories/task/repository.py` — request_cancel(), cancel_task(), schedule_retry(), find_cancellable_tasks()
- `tests/message_queue_redesign/test_stale_task_recovery.py` — Existing tests (167 lines, needs expansion)

## Detailed Implementation

### 1. Rewritten StaleTaskRecovery

```python
"""Stale task recovery service with graceful cancellation."""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_STALE_THRESHOLD_MINUTES = 15
DEFAULT_CHECK_INTERVAL_SECONDS = 60
DEFAULT_CANCEL_GRACE_SECONDS = 10


class StaleTaskRecovery:
    """Background service that recovers stale tasks using 5-step protocol.
    
    5-Step Recovery Protocol:
    1. Find stale running tasks (past threshold, not yet cancelled)
    2. Request cancellation (set cancel_requested flag)
    3. Wait briefly for graceful shutdown (grace period)
    4. Force cancel tasks still running after grace period
    5. Schedule retry for cancelled tasks (if under max retries)
    
    This replaces the old "reset to pending" approach which caused
    duplicate processing.
    """
    
    def __init__(
        self,
        task_repository,
        message_repository,
        threshold_minutes: int = DEFAULT_STALE_THRESHOLD_MINUTES,
        check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS,
        cancel_grace_seconds: int = DEFAULT_CANCEL_GRACE_SECONDS,
        max_retries: int = 3,
        retry_backoff_base: int = 60,
        retry_backoff_max: int = 3600,
        event_repository=None,
    ):
        self._task_repo = task_repository
        self._message_repo = message_repository
        self._event_repo = event_repository
        self._threshold_minutes = threshold_minutes
        self._check_interval = check_interval_seconds
        self._cancel_grace_seconds = cancel_grace_seconds
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
    
    # ... start(), stop() same as before ...
    
    def recover_stale_tasks(self) -> int:
        """Execute 5-step recovery protocol."""
        
        # Step 1: Find stale running tasks not yet flagged
        stale_tasks = self._task_repo.find_cancellable_tasks(
            threshold_minutes=self._threshold_minutes
        )
        
        if not stale_tasks:
            return 0
        
        logger.warning(f"Found {len(stale_tasks)} stale tasks requiring recovery")
        
        # Step 2: Request cancellation for each
        for task in stale_tasks:
            try:
                cancelled = self._task_repo.request_cancel(task.id)
                if cancelled:
                    logger.info(
                        f"Step 2: Requested cancel for stale task {task.id} "
                        f"(instance={task.instance_id[:8]}..., worker={task.worker_id})"
                    )
                    self._log_recovery_event(task, "cancel_requested")
            except Exception as e:
                logger.error(f"Failed to request cancel for task {task.id}: {e}")
        
        # Step 3: Wait briefly for graceful shutdown
        if self._cancel_grace_seconds > 0:
            logger.debug(
                f"Step 3: Waiting {self._cancel_grace_seconds}s "
                f"for graceful worker shutdown..."
            )
            self._stop_event.wait(timeout=self._cancel_grace_seconds)
            if self._stop_event.is_set():
                return 0  # Shutting down
        
        # Step 4+5: Force cancel + schedule retry for tasks still running
        # <!-- FIX: C2 — Only retry if retry_scheduled=0. If Worker already called schedule_retry()
        #      (which sets retry_scheduled=1), we skip — no duplicate retry task. -->
        # <!-- FIX: W1 — Use force_cancel_and_schedule_retry() for single-transaction atomicity. -->
        recovered_count = 0
        for task in stale_tasks:
            try:
                # Re-read current state
                current = self._task_repo.get(task.id)
                if current is None:
                    continue
                
                if current.status == TaskStatus.RUNNING.value:
                    # Task still running after grace period — force cancel + retry atomically
                    retry_task = self._task_repo.force_cancel_and_schedule_retry(
                        task_id=task.id,
                        max_retries=self._max_retries,
                        reason=f"Stale task force-cancelled (>{self._threshold_minutes}min)",
                        backoff_base=self._retry_backoff_base,
                        backoff_max=self._retry_backoff_max,
                    )
                    
                    if retry_task:
                        logger.info(
                            f"Step 4+5: Force-cancelled + retry {retry_task.id} "
                            f"for stale task {task.id} (attempt {retry_task.retry_count})"
                        )
                        self._log_recovery_event(task, "force_cancelled_and_retried",
                                                   retry_task_id=retry_task.id)
                    else:
                        # Max retries exceeded or retry already scheduled — permanent fail
                        if current.retry_count >= self._max_retries:
                            self._task_repo.fail_task(
                                task.id,
                                f"Stale task permanently failed after "
                                f"{self._max_retries} retries"
                            )
                            logger.warning(
                                f"Step 4: Task {task.id} permanently failed "
                                f"(max retries {self._max_retries} exceeded)"
                            )
                            self._log_recovery_event(task, "permanently_failed")
                    
                elif current.status == TaskStatus.CANCELLED.value:
                    # Worker already cancelled it — check if retry was scheduled
                    # <!-- FIX: C2 — If retry_scheduled=True, Worker handled retry. Skip. -->
                    if current.retry_scheduled:
                        logger.debug(
                            f"Step 5: Task {task.id} already has retry scheduled by Worker — skipping"
                        )
                    else:
                        # Worker cancelled but didn't schedule retry — try to schedule one
                        retry_task = self._task_repo.schedule_retry(
                            task_id=task.id,
                            max_retries=self._max_retries,
                            backoff_base=self._retry_backoff_base,
                            backoff_max=self._retry_backoff_max,
                        )
                        if retry_task:
                            logger.info(
                                f"Step 5: Scheduled retry {retry_task.id} "
                                f"for Worker-cancelled task {task.id}"
                            )
                        else:
                            self._task_repo.fail_task(
                                task.id,
                                f"Stale task permanently failed after "
                                f"{self._max_retries} retries"
                            )
                
                # Handle associated message
                if task.message_id:
                    try:
                        self._message_repo.fail(
                            task.message_id,
                            f"Task recovered: stale (>{self._threshold_minutes}min)"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to update message {task.message_id[:8]}...: {e}"
                        )
                
                recovered_count += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to recover task {task.id}: {e}",
                    exc_info=True
                )
        
        return recovered_count
    
    def recover_on_startup(self) -> int:
        """Run recovery immediately on startup (skip grace period).
        
        <!-- FIX: W1 --> Uses force_cancel_and_schedule_retry() for atomicity.
        <!-- FIX: S3 --> Also detects orphaned CANCELLED tasks (crash between cancel and retry).
        """
        logger.info("Running startup crash recovery (no grace period)...")
        
        # Phase A: Handle stale RUNNING tasks (worker crashed mid-execution)
        stale_tasks = self._task_repo.find_stale_running_tasks(
            threshold_minutes=self._threshold_minutes
        )
        
        recovered = 0
        
        if stale_tasks:
            logger.warning(f"Startup recovery: found {len(stale_tasks)} stale RUNNING tasks")
            
            for task in stale_tasks:
                try:
                    # Force cancel + retry in single transaction
                    retry_task = self._task_repo.force_cancel_and_schedule_retry(
                        task_id=task.id,
                        max_retries=self._max_retries,
                        reason="Startup recovery: worker crash",
                        backoff_base=self._retry_backoff_base,
                        backoff_max=self._retry_backoff_max,
                    )
                    
                    if retry_task:
                        logger.info(
                            f"Startup recovery: task {task.id} → retry {retry_task.id}"
                        )
                    else:
                        self._task_repo.fail_task(
                            task.id,
                            f"Startup recovery: max retries ({self._max_retries}) exceeded"
                        )
                        logger.warning(
                            f"Startup recovery: task {task.id} permanently failed"
                        )
                    
                    recovered += 1
                    
                except Exception as e:
                    logger.error(f"Startup recovery failed for task {task.id}: {e}")
        
        # Phase B: <!-- FIX: S3 --> Detect orphaned CANCELLED tasks (crash between cancel and retry)
        orphaned_tasks = self._task_repo.find_orphaned_cancelled_tasks()
        
        if orphaned_tasks:
            logger.warning(
                f"Startup recovery: found {len(orphaned_tasks)} orphaned CANCELLED tasks"
            )
            
            for task in orphaned_tasks:
                try:
                    retry_task = self._task_repo.schedule_retry(
                        task_id=task.id,
                        max_retries=self._max_retries,
                        backoff_base=self._retry_backoff_base,
                        backoff_max=self._retry_backoff_max,
                    )
                    
                    if retry_task:
                        logger.info(
                            f"Startup recovery (orphan): task {task.id} → retry {retry_task.id}"
                        )
                        recovered += 1
                    else:
                        # Max retries exceeded — mark permanent fail
                        self._task_repo.fail_task(
                            task.id,
                            f"Startup recovery (orphan): max retries ({self._max_retries}) exceeded"
                        )
                        recovered += 1
                    
                except Exception as e:
                    logger.error(
                        f"Startup recovery (orphan) failed for task {task.id}: {e}"
                    )
        
        logger.info(f"Startup recovery complete: {recovered} tasks recovered")
        return recovered
    
    def _log_recovery_event(
        self,
        task,
        action: str,
        retry_task_id: int | None = None,
    ) -> None:
        """Log recovery event to event repository."""
        if not self._event_repo:
            return
        
        try:
            self._event_repo.create_event(
                instance_id=task.instance_id,
                kind=f"task_recovery_{action}",
                data={
                    "task_id": task.id,
                    "message_id": task.message_id,
                    "worker_id": task.worker_id,
                    "retry_count": task.retry_count,
                    "retry_task_id": retry_task_id,
                }
            )
        except Exception as e:
            logger.debug(f"Failed to log recovery event: {e}")
```

## Testing Strategy

### Unit Tests

```python
def test_no_stale_tasks(repository, recovery):
    """No stale tasks → no action."""
    assert recovery.recover_stale_tasks() == 0

def test_step1_find_stale(repository, recovery):
    """Finds running tasks past threshold."""
    ...

def test_step2_request_cancel(repository, recovery):
    """Sets cancel_requested flag on stale tasks."""
    ...

def test_step4_force_cancel_unresponsive(repository, recovery):
    """Tasks still running after grace period get force-cancelled."""
    ...

def test_step5_retry_scheduled(repository, recovery):
    """Retry task created after force cancel."""
    ...

def test_step5_max_retries_exceeded(repository, recovery):
    """Permanent fail when max retries exceeded."""
    ...

def test_double_retry_guard(repository, recovery):
    """<!-- FIX: C2 --> If Worker already scheduled retry (retry_scheduled=True),
    StaleTaskRecovery does NOT create a second retry task."""
    # Create stale task
    task = create_stale_task(repository)
    # Simulate Worker having already scheduled retry
    repository.schedule_retry(task.id, max_retries=3)
    # Run StaleTaskRecovery — should not create duplicate
    recovery.recover_stale_tasks()
    chain = repository.get_retry_chain(task.instance_id, task.message_id)
    assert len(chain) == 2  # Only ONE retry, not two

def test_worker_cancelled_with_retry_scheduled(repository, recovery):
    """<!-- FIX: C2 --> Worker cancelled + retry_scheduled=True → recovery skips."""
    ...

def test_worker_cancelled_without_retry(repository, recovery):
    """<!-- FIX: C2 --> Worker cancelled + retry_scheduled=False → recovery schedules retry."""
    ...

def test_startup_recovery_no_grace(repository, recovery):
    """Startup recovery force-cancels immediately."""
    ...

def test_startup_recovery_orphaned_cancelled(repository, recovery):
    """<!-- FIX: S3 --> Startup recovery detects orphaned CANCELLED tasks."""
    ...

def test_grace_period_respects_stop_event(recovery):
    """Recovery stops during grace period if stop() called."""
    ...
```

## Constraints

- Grace period wait must use `self._stop_event.wait()` (not `time.sleep()`) so it can be interrupted
- Must handle the case where find_cancellable_tasks returns tasks that were already processed between find and cancel (use atomic request_cancel)
- Startup recovery skips grace period (no workers running)
- Must log each step for debugging production issues
- Must handle individual task failures without stopping the entire recovery
- <!-- FIX: C2 --> Must check `retry_scheduled` flag before scheduling retry to prevent double-retry
- <!-- FIX: W1 --> Use `force_cancel_and_schedule_retry()` for atomic cancel+retry where possible

## Deliverables

- [ ] StaleTaskRecovery uses 5-step protocol with double-retry guard (C2)
- [ ] Grace period configurable (default: 10 seconds)
- [ ] Retry scheduling uses atomic force_cancel_and_schedule_retry() (W1)
- [ ] Startup recovery detects orphaned CANCELLED tasks (S3)
- [ ] Recovery events logged to event repository
- [ ] All existing tests updated for new behavior
- [ ] New tests for: double-retry guard, orphaned cancelled detection, Worker-cancelled scenarios

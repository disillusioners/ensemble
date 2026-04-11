# Worker Pool Polling Optimization Plan

## Status: Draft

## Problem Statement

Current worker pool polls the database every **0.5 seconds** regardless of workload:

```
4 workers × 2 polls/second = 8 SQL queries/second (when idle)
28,800 queries/hour doing nothing
```

## Goal

Eliminate wasteful polling while maintaining reliability through a **hybrid notification + backup polling** pattern.

---

## Design Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    HYBRID WORKER POOL DESIGN                                │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────────┐
  │                         enqueue_message()                               │
  │  ...existing transaction...                                             │
  │  session.commit()                                                       │
  │       │                                                                 │
  │       ├──▶ WorkerPool.notify_work()  ──▶ Wake ALL sleeping workers    │
  │       │                              (threading.Condition.notify_all)  │
  │       │                                                                 │
  │       └──▶ on_pending_task_callback() ──▶ WorkerPool.notify_work()    │
  │                              (for retry tasks in TaskRepository)        │
  └─────────────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                         Worker.run() loop                               │
  │                                                                          │
  │  while not stopped:                                                      │
  │      task = claim_task()           # Try to claim immediately          │
  │      if task:                                                               │
  │          process(task)                                                    │
  │          continue                    # Check for more immediately        │
  │                                                                          │
  │      # No task → wait for notification OR safety-net timeout            │
  │      with pool._condition:                                              │
  │          if pool._notification_count > 0:                               │
  │              pool._notification_count -= 1                              │
  │              continue                  # Try to claim again               │
  │          pool._condition.wait(timeout=3.0)  # Safety net                │
  │          # Woken by notify_all() OR timeout                              │
  │          continue                    # Loop and claim again               │
  └─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Use `threading.Condition` + Counter (NOT `threading.Event`)

| Primitive | Behavior | Problem |
|-----------|----------|---------|
| `threading.Event` | Binary flag — set or clear | `.set()` wakes **all** workers (thundering herd) |
| `threading.Event` | Lost wakeup on burst | 3 rapid enqueues → only 1 wakeup cycle |
| **`threading.Condition`** | **Counter tracks pending notifications** | **Each notify increments counter, workers decrement** |

```python
class WorkerPool:
    def __init__(self):
        self._condition = threading.Condition()
        self._notification_count = 0  # Tracks pending notifications
    
    def notify_work(self):
        """Called after task creation. Safe from any thread."""
        with self._condition:
            self._notification_count += 1
            self._condition.notify_all()  # Wake all sleeping workers
    
    def wait_for_work(self, timeout: float) -> bool:
        """Worker calls this when no task claimed. Returns True if notified."""
        with self._condition:
            if self._notification_count > 0:
                self._notification_count -= 1
                return True
            self._condition.wait(timeout=timeout)
            if self._notification_count > 0:
                self._notification_count -= 1
                return True
            return False
```

### 2. Fixed Short Timeout (NOT Exponential Backoff)

| Approach | Problem |
|----------|---------|
| Exponential backoff | Tasks with `next_retry_at` already have retry delay built in |
| Exponential backoff | Penalizes expected idle periods (you want ~0 latency on notification) |
| **Fixed 3-5s timeout** | **Simple safety net for missed notifications** |

The 3s maximum latency is acceptable because:
- Retry tasks have 60s+ backoff already
- Worker immediately re-checks DB after claiming

### 3. Callback Pattern for Task Creation

All places that create PENDING tasks must notify workers:

| Location | Task Created By | Notify Call |
|----------|-----------------|-------------|
| `manager.py:866-873` `enqueue_message()` | Direct | `pool.notify_work()` after commit |
| `repository.py:438-460` `schedule_retry()` | Worker timeouts, StaleTaskRecovery | Via `on_pending_task` callback |
| `repository.py:628-649` `force_cancel_and_schedule_retry()` | StaleTaskRecovery | Via `on_pending_task` callback |

**Implementation:** Pass a callback to `TaskRepository`:

```python
class TaskRepository:
    def __init__(self, engine, on_pending_task: Callable[[], None] | None = None):
        self._on_pending_task = on_pending_task
    
    def schedule_retry(self, ...) -> Task | None:
        # ... existing logic ...
        # After INSERTing retry task
        if self._on_pending_task:
            try:
                self._on_pending_task()
            except Exception:
                logger.warning("Failed to notify workers", exc_info=True)
        return retry_task
```

This keeps `TaskRepository` decoupled from `WorkerPool`.

---

## Implementation Plan

### Phase 1: Core WorkerPool Changes

#### 1.1 Add `threading.Condition` to `WorkerPool`

**File:** `daemon/services/worker_pool.py`

```python
class WorkerPool:
    def __init__(self, ...):
        # ... existing fields ...
        self._condition = threading.Condition()
        self._notification_count = 0
    
    def notify_work(self) -> None:
        """Signal that new work is available. Safe to call from any thread."""
        with self._condition:
            self._notification_count += 1
            self._condition.notify_all()
    
    def start(self) -> None:
        # Pass self reference to workers for wait_for_work()
        for i in range(self._num_workers):
            worker = Worker(
                worker_id=f"worker-{i}",
                worker_pool=self,
                # ... rest same ...
            )
```

#### 1.2 Update `Worker` to Use `wait_for_work()`

**File:** `daemon/services/worker_pool.py`

```python
class Worker(threading.Thread):
    def __init__(self, ..., worker_pool: "WorkerPool"):
        self._worker_pool = worker_pool
    
    def run(self) -> None:
        while not self._stop_event.is_set():
            # Try to claim a task immediately
            task = self._task_processor.claim_task(self.worker_id)
            
            if task is not None:
                self._tasks_claimed += 1
                self._process_with_timeout(task)
                continue  # Check for more work immediately
            
            # No task → wait for notification OR safety-net timeout
            if self._worker_pool.wait_for_work(timeout=3.0):
                # Woken by notification, try to claim
                continue
            else:
                # Timed out (3s), loop and try again
                continue
```

#### 1.3 Fix Shutdown Race

**File:** `daemon/services/worker_pool.py`

```python
def stop(self, timeout: float = 30.0) -> None:
    # Signal all workers to stop
    for worker in self._workers:
        worker._stop_event.set()
    
    # Wake all sleeping workers so they see the stop signal
    with self._condition:
        self._condition.notify_all()
    
    # Wait for workers to finish
    per_worker_timeout = timeout / max(len(self._workers), 1)
    for worker in self._workers:
        worker.join(timeout=per_worker_timeout)
```

---

### Phase 2: Task Repository Callback

#### 2.1 Add Callback to `TaskRepository`

**File:** `daemon/repositories/task/repository.py`

```python
class TaskRepository:
    def __init__(self, engine, on_pending_task: Callable[[], None] | None = None):
        self.engine = engine
        self._on_pending_task = on_pending_task
    
    def _notify_pending_task(self) -> None:
        """Notify workers that a pending task was created."""
        if self._on_pending_task:
            try:
                self._on_pending_task()
            except Exception:
                logger.warning("Failed to notify workers of pending task", exc_info=True)
```

#### 2.2 Call Callback in `schedule_retry()`

**File:** `daemon/repositories/task/repository.py:370-462`

After INSERTing retry task, before return:
```python
self._notify_pending_task()
return retry_task
```

#### 2.3 Call Callback in `force_cancel_and_schedule_retry()`

**File:** `daemon/repositories/task/repository.py:560-651`

Same pattern — after INSERT, before return:
```python
self._notify_pending_task()
return self._row_to_task(result)
```

---

### Phase 3: Wire Up in Manager

#### 3.1 Update `InstanceManager.__init__()`

**File:** `daemon/manager.py`

```python
def __init__(self, ...):
    # ... existing ...
    self._worker_pool: WorkerPool | None = None

def setup_worker_pool(self, num_workers=4):
    # ... existing ...
    task_processor = TaskProcessor(
        task_repo=self._task_repo,
        instance_manager=self,
        # ...
    )
    self._worker_pool = WorkerPool(
        task_processor=task_processor,
        num_workers=num_workers,
        # ...
    )
    self._worker_pool.start()
```

#### 3.2 Wire Callback to TaskRepository

**File:** `daemon/manager.py` in `setup_worker_pool()`

```python
# Pass notify callback to repository
self._task_repo = TaskRepository(
    engine=self._engine,
    on_pending_task=lambda: self._worker_pool.notify_work()
)
```

#### 3.3 Call `notify_work()` in `enqueue_message()`

**File:** `daemon/manager.py:893`

```python
session.commit()

# After commit — task is now visible in DB
if self._worker_pool is not None:
    self._worker_pool.notify_work()

# Existing event broadcast (keep this)
try:
    await self._event_bus.create_message_received_event(...)
```

---

### Phase 4: Cleanup

#### 4.1 Remove Old Poll Interval Constant

**File:** `daemon/services/worker_pool.py`

Remove or deprecate:
```python
# DEFAULT_POLL_INTERVAL = 0.5  # No longer used for polling
```

#### 4.2 Update Class Docstrings

Update docstrings in `Worker`, `WorkerPool` to reflect new behavior.

---

## Edge Cases

### Race: Notification Before Claim

| Scenario | Impact | Mitigation |
|----------|--------|------------|
| `notify()` called, then worker claims task | Worker sees task, no issue | Atomic `UPDATE-RETURNING` handles ordering |
| `notify()` called, no workers waiting | Event flag stays set | Workers check DB on next loop entry |

### Race: Burst Enqueue (< 3ms apart)

| Scenario | Impact |
|----------|--------|
| 3 tasks enqueued rapidly | `_notification_count = 3`, each worker decrements once |
| 3 workers wake, only 1 task exists | 2 workers do empty `claim_task()`, loop back, `wait_for_work(timeout=0)` returns immediately with `_notification_count=0` |

**Result:** Acceptable — worst case 2-3 wasted DB queries per burst.

### Race: Shutdown While Waiting

| Scenario | Mitigation |
|----------|------------|
| Worker blocked on `condition.wait(timeout=3)` | `stop()` calls `notify_all()` to wake immediately |

---

## Metrics to Add

```python
# WorkerPool stats
self._stats = {
    "notifications_sent": 0,
    "empty_claim_attempts": 0,
    "workers_woken_by_timeout": 0,
}

def notify_work(self):
    with self._condition:
        self._notification_count += 1
        self._condition.notify_all()
        self._stats["notifications_sent"] += 1

# Expose via get_stats()
"pool": {
    "notification_rate": self._stats["notifications_sent"] / uptime,
    "wakeup_efficiency": self._stats["notifications_sent"] / max(1, self._stats["empty_claim_attempts"]),
}
```

**Target:** `wakeup_efficiency` should approach 1.0 (each notification → one successful claim).

---

## Files to Modify

| File | Changes |
|------|---------|
| `daemon/services/worker_pool.py` | Add `Condition`, `notify_work()`, `wait_for_work()`, update `Worker.run()`, fix `stop()` |
| `daemon/repositories/task/repository.py` | Add `on_pending_task` callback, call in `schedule_retry()`, `force_cancel_and_schedule_retry()` |
| `daemon/manager.py` | Wire callback, call `notify_work()` in `enqueue_message()` after commit |

---

## Verification Plan

1. **Unit tests** for `WorkerPool.notify_work()` / `wait_for_work()`
2. **Integration test**: Enqueue message, verify worker wakes within 100ms
3. **Load test**: 100 rapid enqueues, verify all processed without exponential backoff delays
4. **Shutdown test**: Verify workers exit within 1s of `stop()` call
5. **Recovery test**: Verify StaleTaskRecovery retry tasks are picked up by workers

---

## Expected Outcomes

| Metric | Before | After |
|--------|--------|-------|
| Idle queries/sec | 8 | **0** |
| Wake latency | 0-500ms | **~0ms** (notification) |
| Max safety latency | N/A | **3s** |
| Shutdown time | ~0.5s | **<1s** |

---

## References

- `daemon/services/worker_pool.py` — Current implementation
- `daemon/repositories/task/repository.py` — Task CRUD + retry logic
- `daemon/manager.py:806` — `enqueue_message()`
- `daemon/services/stale_task_recovery.py` — Stale task handling

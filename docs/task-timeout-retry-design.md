# Task Timeout & Graceful Cancellation

> **Note (2026-06-18):** The timeout/retry design here applies primarily to the WorkerPool path (Task table). Post-migration, completion tracking flows through the `CorrelationManager`, and both paths share a `MessageProcessingPipeline`. For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md). For JobQueue retry semantics, see `docs/job-queue.md`.

## Problem Statement

The current system has two problems:

### Problem 1: Stale Task Recovery Race Condition
- `StaleTaskRecovery` resets task status to `pending` after 15 minutes
- But the worker thread continues running (no actual kill)
- Another worker can claim the same work → **duplicate processing**

### Problem 2: No Task-Level Retry
- LLM has built-in retry (3 attempts by default)
- After LLM retry exhausted, task fails permanently
- No mechanism to retry the full task with checkpoint resume

---

## Requirements

### R1: Configurable Task Timeout
- Task timeout must be configurable (not hardcoded 15 minutes)
- Config key: `services.task_timeout_minutes` (default: 15)
- StaleTaskRecovery check interval remains separate: `services.stale_task_recovery_interval` (default: 60s)

### R2: Graceful Task Cancellation
- When task exceeds timeout, worker must stop LangGraph execution
- Worker must release the task gracefully (not abandon)
- No duplicate processing allowed

### R3: Task Retry with Checkpoint Resume
- After timeout/cancellation, task should retry
- Retry must resume from LangGraph checkpoint (not replay message)
- Retry count must be configurable: `services.max_task_retries` (default: 3)
- Exponential backoff between retries: 1min, 2min, 4min... (max 1 hour)

### R4: Idempotent Retry
- Same task cannot be processed by multiple workers simultaneously
- Retry creates new Task (not reuse same Task row)

---

## Architecture

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TASK LIFECYCLE                                   │
└─────────────────────────────────────────────────────────────────────────┘

[Enqueue Message]
       │
       ▼
┌─────────────────┐
│ Task created    │
│ status: PENDING │
│ retry_count: 0  │
└────────┬────────┘
         │
         │ Worker poll
         ▼
┌─────────────────┐
│ Worker claims    │
│ status: RUNNING │
│ start_timeout() │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│ Worker monitors CancellationToken                       │
│   - Check token.is_cancelled() periodically             │
│   - Pass token to LangGraph via callbacks              │
└────────────────────┬────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
         ▼                       ▼
┌─────────────────┐       ┌─────────────────┐
│ Completed      │       │ Timeout/Cancel  │
│ status: DONE   │       │ → Signal cancel │
└─────────────────┘       └────────┬────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │ Wait for graceful   │
                        │ shutdown (timeout)  │
                        └──────────┬──────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                              ▼
           ┌─────────────────┐          ┌─────────────────┐
           │ Worker stopped   │          │ Worker didn't   │
           │ gracefully       │          │ stop (stale)    │
           └────────┬────────┘          └────────┬────────┘
                    │                              │
                    ▼                              ▼
           ┌─────────────────┐          ┌─────────────────────┐
           │ Create retry Task│          │ StaleTaskRecovery   │
           │ (if retry_count < max)     │ sets cancel flag    │
           │ with backoff delay         │ Force worker stop   │
           └─────────────────┘          └─────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         RETRY FLOW                                       │
└─────────────────────────────────────────────────────────────────────────┘

[Task fails/timeout]
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ decision: retry_count < max_task_retries?                               │
└─────────────────────────────────────────────────────────────────────────┘
       │
       ├── NO ──→ Task marked FAILED, message marked FAILED
       │              (No more retries)
       │
       └── YES ─→ Create new Task {
                      instance_id: same,
                      message_id: same,
                      task_type: same,
                      retry_count: parent.retry_count + 1,
                      next_retry_at: now + exponential_backoff,
                      status: PENDING
                  }
                          │
                          │ Worker picks up (when next_retry_at elapsed)
                          ▼
                   [New Task with is_retry=True]
                          │
                          ▼
                   LangGraph resumes from checkpoint
                   (No message re-added to conversation)
```

---

## Data Model Changes

### Task Model

```python
class Task(SQLModel, table=True):
    __tablename__ = "task"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_type: str
    instance_id: str = Field(index=True)
    message_id: Optional[str] = Field(default=None, index=True)
    
    status: str = Field(default=TaskStatus.PENDING.value, index=True)
    worker_id: Optional[str] = Field(default=None, index=True)
    
    # NEW: Retry tracking
    retry_count: int = Field(default=0)
    next_retry_at: Optional[datetime] = Field(default=None)
    
    # NEW: Cancellation
    cancel_requested: bool = Field(default=False)
    cancel_requested_at: Optional[datetime] = Field(default=None)
    
    # Existing fields
    result: Optional[str]
    error: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
```

### TaskStatus Enum

```python
class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # NEW: Explicit cancelled state
```

---

## Configuration

### config.yaml

```yaml
services:
  worker_poll_interval: 0.5          # How often workers poll (seconds)
  stale_task_recovery_interval: 60   # How often check for stale tasks (seconds)
  
  # NEW: Task timeout and retry
  task_timeout_minutes: 15            # Timeout before cancellation (was hardcoded)
  max_task_retries: 3                # Max retry attempts (0 = disabled)
  task_retry_backoff_base: 60        # Base backoff in seconds (1 min)
  task_retry_backoff_max: 3600       # Max backoff in seconds (1 hour)
```

---

## CancellationToken Design

### Current State (cancellation.py)

```python
class CancellationReason(str, Enum):
    USER_REQUEST = "user_request"
    WATCHDOG_TIMEOUT = "watchdog_timeout"
    # Add new reason
    TASK_TIMEOUT = "task_timeout"
```

### Enhancement

```python
class CancellationToken:
    """Thread-safe cancellation token."""
    
    def __init__(self):
        self._cancelled = False
        self._lock = threading.Lock()
        self._reason: Optional[CancellationReason] = None
        self._cancelled_at: Optional[datetime] = None
    
    def cancel(self, reason: CancellationReason):
        with self._lock:
            self._cancelled = True
            self._reason = reason
            self._cancelled_at = datetime.now(timezone.utc)
    
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled
    
    @property
    def reason(self) -> Optional[CancellationReason]:
        with self._lock:
            return self._reason
```

### TimeoutMonitor Thread

```python
class TimeoutMonitor:
    """Monitors task timeout and sets cancellation token."""
    
    def __init__(self, task_id: int, token: CancellationToken, timeout_seconds: int):
        self._task_id = task_id
        self._token = token
        self._timeout = timeout_seconds
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def _run(self):
        if not self._stop_event.wait(timeout=self._timeout):
            self._token.cancel(CancellationReason.TASK_TIMEOUT)
    
    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
```

---

## Worker Changes

### Worker Loop Enhancement

```python
class Worker(threading.Thread):
    def __init__(self, worker_id, task_processor, poll_interval, 
                 timeout_minutes, max_retries, ...):
        # ...
        self._timeout_minutes = timeout_minutes
        self._max_retries = max_retries
    
    def run(self):
        while not self._stop_event.is_set():
            task = self._task_processor.claim_task(self.worker_id)
            
            if task is not None:
                self._process_with_timeout(task)
            else:
                self._stop_event.wait(timeout=self._poll_interval)
    
    def _process_with_timeout(self, task):
        # Create cancellation token
        token = CancellationToken()
        monitor = TimeoutMonitor(
            task_id=task.id,
            token=token,
            timeout_seconds=self._timeout_minutes * 60
        )
        monitor.start()
        
        try:
            self._task_processor.run_task(task, token)
            self._tasks_completed += 1
        except Exception as e:
            self._handle_task_failure(task, e, token)
        finally:
            monitor.stop()
    
    def _handle_task_failure(self, task, error, token):
        # Determine if this was a timeout/cancellation
        if token.is_cancelled() and token.reason == CancellationReason.TASK_TIMEOUT:
            # This is a timeout - create retry if under limit
            if task.retry_count < self._max_retries:
                self._schedule_retry(task)
                return
        
        # Other failure - fail the task
        self._task_processor.fail_task(task.id, str(error))
        self._tasks_failed += 1
```

---

## StaleTaskRecovery Changes

### New Behavior

```python
class StaleTaskRecovery:
    def recover_stale_tasks(self):
        stale_tasks = self._task_repo.find_stale_running_tasks(
            threshold_minutes=self._threshold_minutes
        )
        
        for task in stale_tasks:
            # 1. Request cancellation
            self._task_repo.request_cancel(task.id)
            
            # 2. Wait briefly for graceful shutdown
            # (Worker checks cancel_requested and stops)
            
            # 3. Force reset if worker doesn't respond
            # (Worker thread will detect on next iteration)
            
            # 4. Reset to pending with cancel flag
            self._task_repo.reset_with_cancel(task.id)
            
            # 5. Log for monitoring
            self._log_recovery(task)
```

---

## Repository Changes

### TaskRepository

```python
class TaskRepository:
    def claim_pending_task(self, worker_id: str) -> Task | None:
        """Atomic claim with retry delay awareness."""
        now = datetime.now(timezone.utc)
        
        # Only claim tasks that are:
        # 1. status = pending
        # 2. retry_count < max_retries
        # 3. next_retry_at is null OR next_retry_at <= now
        with self.engine.begin() as conn:
            stmt = text("""
                UPDATE task
                SET status = :status_running,
                    worker_id = :worker_id,
                    started_at = :started_at
                WHERE id = (
                    SELECT id FROM task
                    WHERE status = :status_pending
                    AND (next_retry_at IS NULL OR next_retry_at <= :now)
                    ORDER BY created_at ASC
                    LIMIT 1
                )
                RETURNING *
            """)
            # ...
    
    def request_cancel(self, task_id: int) -> bool:
        """Set cancel flag on task."""
        with SQLModelSession(self.engine) as session:
            task = session.get(Task, task_id)
            if task:
                task.cancel_requested = True
                task.cancel_requested_at = datetime.now(timezone.utc)
                session.commit()
                return True
            return False
    
    def find_cancellable_tasks(self, stale_minutes: int) -> list[Task]:
        """Find tasks that need cancellation (running too long)."""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        with SQLModelSession(self.engine) as session:
            stmt = select(Task).where(
                Task.status == TaskStatus.RUNNING.value,
                Task.started_at < threshold,
                Task.cancel_requested == False,
            )
            return list(session.exec(stmt))
    
    def schedule_retry(self, task_id: int, retry_count: int, 
                       backoff_base: int, backoff_max: int) -> Task:
        """Create new task for retry with backoff."""
        # Get parent task info
        parent = self.get(task_id)
        
        # Calculate backoff
        delay_seconds = min(backoff_base * (2 ** retry_count), backoff_max)
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        
        # Create new task
        new_task = self.create(
            task_type=parent.task_type,
            instance_id=parent.instance_id,
            message_id=parent.message_id,
            retry_count=retry_count,
            next_retry_at=next_retry_at,
        )
        
        # Mark parent as cancelled
        parent.status = TaskStatus.CANCELLED.value
        self._session.commit()
        
        return new_task
```

---

## LangGraph Integration

### Pass CancellationToken to Graph

```python
class CancellationCallback(BaseCallbackHandler):
    """Checks cancellation token during graph execution."""
    
    def __init__(self, token: CancellationToken):
        self._token = token
    
    def on_chain_start(self, **kwargs):
        self._check_cancellation()
    
    def on_agent_action(self, action, **kwargs):
        self._check_cancellation()
    
    def _check_cancellation(self):
        if self._token.is_cancelled():
            raise OperationCancelledError(
                f"Task cancelled: {self._token.reason.value}"
            )
```

### Processing with Token

```python
async def _process_message_with_tracking(
    self,
    instance_id: str,
    message: str,
    message_id: str,
    cancellation_token: CancellationToken | None = None,
    is_retry: bool = False,
):
    # ... setup ...
    
    callbacks = [activity_callback]
    if cancellation_token:
        callbacks.append(CancellationCallback(cancellation_token))
    
    config = {
        "configurable": {"thread_id": instance_id},
        "callbacks": callbacks,
        "recursion_limit": self.config.limits.graph_recursion_limit,
    }
    
    try:
        # Streaming with cancellation check
        async for event in graph.astream_events(
            input=graph_input,
            config=config,
            version="v2"
        ):
            if cancellation_token and cancellation_token.is_cancelled():
                raise OperationCancelledError(
                    f"Task cancelled: {cancellation_token.reason.value}"
                )
            # Process event...
    except OperationCancelledError:
        # Let it propagate - worker handles retry decision
        raise
```

---

## Edge Cases

### E1: Worker crashes during timeout wait
- StaleTaskRecovery will reset the task
- New worker will claim and process
- LangGraph will resume from checkpoint

### E2: Timeout during LangGraph tool execution
- CancellationCallback will detect cancel
- LangGraph will raise OperationCancelledError
- Worker catches and creates retry

### E3: Max retries exceeded
- Task marked FAILED
- Message marked FAILED
- No automatic retry (manual intervention required)

### E4: Checkpoint doesn't exist on retry
- Log warning
- Fall back to replaying message (unsafe but graceful degradation)

### E5: Concurrent cancellation requests
- StaleTaskRecovery and worker both try to cancel
- Use atomic operation with version/timestamp check

---

## Open Questions

1. **Timeout vs Backoff interaction**: Should timeout reset on each retry attempt, or use cumulative time?

2. **Message status on timeout**: Should message stay READY or change to TIMEOUT/RETRYING?

3. **Checkpoint cleanup**: When to delete old checkpoints for failed tasks?

4. **Metrics/Observability**: What signals to emit for timeout detection and retry behavior?

5. **Worker graceful shutdown**: Should worker finish current task or abort immediately on shutdown signal?

6. **Backoff per task type**: Should different task types (process_message, send_report) have different retry configs?

---

## Acceptance Criteria

- [ ] AC1: Task timeout is configurable via `services.task_timeout_minutes`
- [ ] AC2: Worker gracefully stops when task times out (no orphan processing)
- [ ] AC3: No duplicate processing after timeout/cancellation
- [ ] AC4: Timed-out task retries resume from checkpoint (not replay message)
- [ ] AC5: Retry count is configurable and respects max limit
- [ ] AC6: Exponential backoff between retries
- [ ] AC7: Tasks that exceed max retries are marked FAILED
- [ ] AC8: All state transitions are logged for debugging
- [ ] AC9: System handles worker crash during timeout gracefully

---

## Estimated Effort

| Component | Effort |
|-----------|--------|
| Data model changes | 1 day |
| CancellationToken enhancement | 2 days |
| Worker changes | 2 days |
| StaleTaskRecovery changes | 1 day |
| Repository changes | 2 days |
| LangGraph integration | 2 days |
| Testing | 2 days |
| **Total** | **~12 days** |

---

## References

- Current StaleTaskRecovery: `daemon/services/stale_task_recovery.py`
- Current Worker: `daemon/services/worker_pool.py`
- Current TaskProcessor: `daemon/services/task_processor.py`
- Current Task model: `daemon/repositories/task/models.py`
- CancellationToken: `daemon/cancellation.py`

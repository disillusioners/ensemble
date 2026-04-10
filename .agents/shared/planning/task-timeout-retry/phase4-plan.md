# Phase 4: TaskProcessor & Worker Integration

## Objective

Wire CancellationToken and TimeoutMonitor into the task execution pipeline. Replace the hardcoded 300s timeout in TaskProcessor.run_task() with configurable TimeoutMonitor. Pass the token through ProcessMessageProcessor to manager._process_message_with_tracking() so LangGraph execution can be gracefully cancelled. Add retry scheduling logic to the worker.

## Coupling

- **Depends on**: Phase 2 (CancellationToken, TimeoutMonitor), Phase 3 (enhanced repository methods)
- **Coupling type**: tight
- **Shared files with other phases**: 
  - `daemon/services/task_processor.py` (ProcessMessageProcessor, TaskProcessor)
  - `daemon/services/worker_pool.py` (Worker, WorkerPool)
  - `daemon/manager.py` (_process_message_with_tracking)
- **Shared APIs/interfaces**: TaskProcessor.run_task() signature, ProcessMessageProcessor.process() signature
- **Why this coupling**: Worker creates token + monitor → TaskProcessor uses token → Manager receives token → LangGraph callbacks check token

## Context

### Current Execution Flow
```
Worker.run()
  → Worker._task_processor.claim_task(worker_id)  [TaskProcessor.claim_task]
  → Worker._task_processor.run_task(task)          [TaskProcessor.run_task]
      → MainLoopBridge.run_async(_run(), timeout=300)
          → ProcessMessageProcessor.process(task)
              → manager._process_message_with_tracking(
                  instance_id, message, message_id,
                  cancellation_token=None,  ← ALWAYS None currently
                  is_retry=False
                )
```

### Current Issues
1. `cancellation_token=None` is hardcoded in ProcessMessageProcessor.process()
2. `timeout=300.0` is hardcoded in TaskProcessor.run_task() (MainLoopBridge call)
3. On timeout/exception: permanent fail, no retry
4. No TimeoutMonitor — just MainLoopBridge timeout (which kills the future but doesn't stop LangGraph)
5. <!-- FIX: C3 --> `manager._process_message_with_tracking()` references `msg.retry_count` (line ~987) but `msg` is not in scope — the function receives `message: str`, not a message object. This must be fixed to use the `retry_count` from the Task object passed through the call chain.

### Target Execution Flow
```
Worker.run()
  → claim_task(worker_id)
  → _process_with_timeout(task)
      → Create CancellationTokenSource + TimeoutMonitor
      → TaskProcessor.run_task(task, cancellation_token=token)
          → ProcessMessageProcessor.process(task, cancellation_token=token)
              → manager._process_message_with_tracking(
                  ..., cancellation_token=token, is_retry=task.retry_count > 0
                )
          → LangGraph callbacks check token
      → On OperationCancelledError: schedule_retry or permanent fail
      → On success: complete_task
      → On other exception: fail with retry if applicable
      → Finally: stop TimeoutMonitor
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Modify TaskProcessor.run_task() | Accept CancellationToken param, remove hardcoded 300s timeout, pass token to processor, handle OperationCancelledError | `daemon/services/task_processor.py` |
| 2 | Modify ProcessMessageProcessor.process() | Accept CancellationToken param, pass to manager._process_message_with_tracking() | `daemon/services/task_processor.py` |
| 3 | Update other processors | SendReportProcessor, CleanupProcessor — accept token param (no-op for now) | `daemon/services/task_processor.py` |
| 4 | <!-- FIX: C3 --> Fix manager._process_message_with_tracking() bug | Replace `msg.retry_count` (undefined) with `retry_count` parameter. Add `retry_count: int = 0` to function signature, pass from ProcessMessageProcessor via `task.retry_count` | `daemon/manager.py` |
| 5 | Add retry scheduling to Worker | _process_with_timeout(), _handle_failure(), _schedule_retry() methods | `daemon/services/worker_pool.py` |
| 6 | Pass timeout/retry config to Worker | Worker constructor receives timeout_minutes, max_retries, backoff settings | `daemon/services/worker_pool.py` |
| 7 | Update WorkerPool | Pass config to workers, update constructor | `daemon/services/worker_pool.py` |
| 8 | Write integration tests | Test: timeout triggers cancel, retry scheduled, max retries → permanent fail, successful completion stops monitor | `tests/message_queue_redesign/test_worker_timeout.py` (new) |

## Key Files

- `daemon/services/task_processor.py` — TaskProcessor, ProcessMessageProcessor, BaseProcessor (full rewrite of run_task)
- `daemon/services/worker_pool.py` — Worker, WorkerPool (add timeout/retry logic)
- `daemon/manager.py` — _process_message_with_tracking() (already accepts token, just needs to receive it)
- `daemon/cancellation.py` — CancellationToken (from Phase 2)
- `daemon/services/timeout_monitor.py` — TimeoutMonitor (from Phase 2)
- `daemon/repositories/task/repository.py` — schedule_retry, complete_task, fail_task (from Phase 3)

## Detailed Implementation

### 1. TaskProcessor Changes

```python
class TaskProcessor:
    """Routes tasks to type-specific processors with cancellation support."""
    
    def __init__(self, task_repo, instance_manager, event_repo=None, event_bus=None):
        # ... same as current ...
    
    def claim_task(self, worker_id: str) -> "Task | None":
        """Atomically claim the next eligible pending task."""
        return self._task_repo.claim_pending_task(worker_id)
    
    def run_task(
        self,
        task: "Task",
        cancellation_token: "CancellationToken | None" = None,
    ) -> None:
        """Run a task with cancellation support.
        
        If cancellation_token is provided, the task will check for
        cancellation during execution. On timeout, the caller (Worker)
        handles retry scheduling.
        
        Raises:
            OperationCancelledError: If task was cancelled during execution.
            Exception: For other failures.
        """
        processor = self._processors.get(task.task_type)
        if processor is None:
            raise ValueError(f"Unknown task type: {task.task_type}")

        async def _run():
            result = await processor.process(task, cancellation_token=cancellation_token)
            # Complete the task with result
            self._task_repo.complete_task(task.id, result)
            return result

        # Bridge from worker thread to main event loop
        # NOTE: We use a generous timeout here (2x configured timeout) as a
        # safety net. The real timeout is managed by TimeoutMonitor.
        try:
            result = MainLoopBridge.run_async(_run(), timeout=None)  # No timeout here
            return result
        except OperationCancelledError:
            # Let it propagate — Worker handles retry decision
            raise
        except Exception as e:
            # Don't fail the task here — Worker decides based on retry policy
            raise
```

**Important design decision**: Remove the MainLoopBridge timeout entirely. The TimeoutMonitor is now the primary timeout mechanism. MainLoopBridge timeout was problematic because it killed the future without stopping LangGraph execution. With TimeoutMonitor + CancellationToken, we have graceful cancellation. The MainLoopBridge is just the thread-to-async bridge.

However, we still need a safety net. Keep a very long MainLoopBridge timeout (e.g., 2x configured timeout) as a last resort:

```python
# Safety net: 2x the configured timeout as absolute maximum
safety_timeout = (timeout_minutes * 60 * 2) if timeout_minutes else 1800
result = MainLoopBridge.run_async(_run(), timeout=safety_timeout)
```

### 2. ProcessMessageProcessor Changes

<!-- FIX: C3 — Pass retry_count explicitly from task object, not from undefined msg variable -->

```python
class ProcessMessageProcessor(BaseProcessor):
    async def process(
        self,
        task: "Task",
        cancellation_token: "CancellationToken | None" = None,
    ) -> dict[str, Any]:
        # ... existing message resolution code ...
        
        is_retry = task.retry_count > 0  # Use task's retry_count, not message's
        
        # ... existing event creation ...
        
        try:
            result = await self._manager._process_message_with_tracking(
                instance_id=task.instance_id,
                message=message_content,
                message_id=task.message_id,
                cancellation_token=cancellation_token,  # Pass through
                is_retry=is_retry,
                retry_count=task.retry_count,  # <!-- FIX: C3 — pass retry_count from task, not undefined msg -->
            )
            # ... existing success handling ...
        except OperationCancelledError:
            # Propagate — TaskProcessor/Worker handles this
            raise
        except Exception as e:
            # ... existing error handling ...
            raise
```

### 2b. Manager _process_message_with_tracking() Fix

<!-- FIX: C3 — The existing function references `msg.retry_count` on line ~987 but `msg` is not in scope.
     The function receives `message: str` (plain text), not a message object.
     Fix: add `retry_count` parameter and use it instead of `msg.retry_count`. -->

```python
async def _process_message_with_tracking(
    self, 
    instance_id: str, 
    message: str,
    message_id: str,
    cancellation_token: CancellationToken | None = None,
    is_retry: bool = False,
    retry_count: int = 0,  # <!-- FIX: C3 — new parameter, replaces undefined msg.retry_count -->
) -> MessageResult:
    # ... existing code ...
    
    # On the line that currently says:
    #   is_retry = message.retry_count > 0 if hasattr(message, 'retry_count') else False
    # or references msg.retry_count:
    # Replace with the retry_count parameter passed from ProcessMessageProcessor
    ...
```

### 3. Worker Changes

```python
class Worker(threading.Thread):
    def __init__(
        self,
        worker_id: str,
        task_processor,
        poll_interval: float = 0.5,
        timeout_minutes: float = 15.0,
        max_retries: int = 3,
        retry_backoff_base: int = 60,
        retry_backoff_max: int = 3600,
    ):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self._task_processor = task_processor
        self._poll_interval = poll_interval
        self._timeout_minutes = timeout_minutes
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._stop_event = threading.Event()
        # ... counters ...
    
    def run(self) -> None:
        while not self._stop_event.is_set():
            task = None
            try:
                task = self._task_processor.claim_task(self.worker_id)
                
                if task is not None:
                    self._tasks_claimed += 1
                    self._process_with_timeout(task)
                else:
                    self._stop_event.wait(timeout=self._poll_interval)
            except Exception as e:
                logger.error(f"Worker {self.worker_id} unexpected error: {e}")
                self._stop_event.wait(timeout=1.0)
    
    def _process_with_timeout(self, task: "Task") -> None:
        """Process a task with timeout monitoring and retry logic."""
        from daemon.cancellation import CancellationTokenSource
        from daemon.services.timeout_monitor import TimeoutMonitor
        
        # Create cancellation infrastructure
        source = CancellationTokenSource()
        token = source.token
        timeout_seconds = self._timeout_minutes * 60
        
        monitor = TimeoutMonitor(
            task_id=task.id,
            source=source,
            timeout_seconds=timeout_seconds,
        )
        monitor.start()
        
        try:
            # Run the task with cancellation token
            self._task_processor.run_task(task, cancellation_token=token)
            self._tasks_completed += 1
            logger.debug(f"Worker {self.worker_id} completed task {task.id}")
            
        except OperationCancelledError as e:
            # Task was cancelled (timeout or other reason)
            logger.warning(
                f"Worker {self.worker_id}: task {task.id} cancelled: {e.message}"
            )
            self._handle_cancellation(task, e.reason)
            
        except TimeoutError:
            # MainLoopBridge safety timeout (shouldn't happen normally)
            logger.error(
                f"Worker {self.worker_id}: task {task.id} hit safety timeout"
            )
            self._handle_cancellation(
                task, CancellationReason.TIMEOUT   # <!-- FIX: W4 — reuse TIMEOUT -->
            )
            
        except Exception as e:
            # Other error — decide retry vs permanent fail
            logger.error(
                f"Worker {self.worker_id} failed task {task.id}: {e}",
                exc_info=True
            )
            self._handle_task_failure(task, str(e))
            
        finally:
            monitor.stop()
    
    def _handle_cancellation(
        self, task: "Task", reason: "CancellationReason"
    ) -> None:
        """Handle task cancellation — schedule retry or permanent fail."""
        if reason == CancellationReason.TIMEOUT:   # <!-- FIX: W4 — reuse TIMEOUT, not TASK_TIMEOUT -->
            # Try to schedule a retry
            retry_task = self._task_processor._task_repo.schedule_retry(
                task_id=task.id,
                max_retries=self._max_retries,
                backoff_base=self._retry_backoff_base,
                backoff_max=self._retry_backoff_max,
            )
            
            if retry_task:
                logger.info(
                    f"Worker {self.worker_id}: scheduled retry {retry_task.id} "
                    f"for task {task.id} (attempt {retry_task.retry_count}/{self._max_retries})"
                )
                self._tasks_failed += 1  # Count original task as failed
            else:
                # Max retries exceeded
                self._task_processor._task_repo.fail_task(
                    task.id,
                    f"Task cancelled after {self._max_retries} retries"
                )
                self._tasks_failed += 1
                logger.warning(
                    f"Worker {self.worker_id}: task {task.id} permanently failed "
                    f"after {self._max_retries} retries"
                )
        else:
            # Non-timeout cancellation (shutdown, user request, etc.)
            self._task_processor._task_repo.cancel_task(
                task.id, reason=f"Cancelled: {reason.value}"
            )
            self._tasks_failed += 1
    
    def _handle_task_failure(self, task: "Task", error: str) -> None:
        """Handle task failure — schedule retry or permanent fail."""
        # For now: fail permanently. Retry-on-error is a separate feature.
        # Timeout cancellation already handles retry.
        self._task_processor._task_repo.fail_task(task.id, error)
        self._tasks_failed += 1
```

### 4. WorkerPool Changes

```python
class WorkerPool:
    def __init__(
        self,
        task_processor,
        num_workers: int = 4,
        poll_interval: float = 0.5,
        timeout_minutes: float = 15.0,
        max_retries: int = 3,
        retry_backoff_base: int = 60,
        retry_backoff_max: int = 3600,
    ):
        # ... store all params ...
    
    def start(self) -> None:
        for i in range(self._num_workers):
            worker = Worker(
                worker_id=f"worker-{i}",
                task_processor=self._task_processor,
                poll_interval=self._poll_interval,
                timeout_minutes=self._timeout_minutes,
                max_retries=self._max_retries,
                retry_backoff_base=self._retry_backoff_base,
                retry_backoff_max=self._retry_backoff_max,
            )
            worker.start()
            self._workers.append(worker)
```

### 5. Manager — Bug Fix Required

<!-- FIX: C3 — _process_message_with_tracking() must accept retry_count parameter -->

The existing `_process_message_with_tracking()` already:
- Accepts `cancellation_token: CancellationToken | None` ✓
- Creates `CancellationCallbackHandler` when token is provided ✓
- Checks `cancellation_token.check()` before starting ✓
- Has partial retry support via `is_retry` parameter ✓

**BUT it has a bug**: Line ~987 references `msg.retry_count` where `msg` is not in scope. The function receives `message: str` (plain text content), not a message object.

**Required fix**:
1. Add `retry_count: int = 0` parameter to function signature
2. Replace `msg.retry_count` → `retry_count` (the new parameter)
3. Pass `retry_count=task.retry_count` from ProcessMessageProcessor

We just need to **pass the token and retry_count through** from ProcessMessageProcessor.

## Design Decisions

### Why remove MainLoopBridge timeout?

The current 300s timeout on `MainLoopBridge.run_async()` is a blunt instrument — it kills the `future.result()` wait but doesn't actually stop the LangGraph execution inside the event loop. With CancellationToken + TimeoutMonitor, we have a cooperative cancellation mechanism that actually stops execution.

### Why put retry logic in Worker, not TaskProcessor?

The Worker owns the full task lifecycle (claim → run → complete/fail/retry). TaskProcessor is a routing layer. Retry scheduling requires knowing about backoff config and max retries, which are Worker-level concerns. This keeps TaskProcessor stateless and testable.

### Why not retry on generic exceptions (only on timeout)?

The design doc focuses on timeout-based retry. Generic exceptions (e.g., LLM API errors) already have their own retry at the LLM level. Adding task-level retry for all exceptions would be a separate feature. We can add it later by extending `_handle_task_failure()`.

## Constraints

- MainLoopBridge is the only way to run async code from worker threads — don't bypass it
- Token must be passed through the full chain: Worker → TaskProcessor → ProcessMessageProcessor → Manager
- Worker must handle all cleanup in finally block (monitor.stop())
- OperationCancelledError must propagate from LangGraph through to Worker

## Deliverables

- [ ] TaskProcessor.run_task() accepts CancellationToken, removes hardcoded timeout
- [ ] ProcessMessageProcessor.process() accepts and passes CancellationToken
- [ ] <!-- FIX: C3 --> manager._process_message_with_tracking() accepts retry_count param, fixes undefined msg.retry_count
- [ ] Worker._process_with_timeout() creates monitor + token per task
- [ ] Worker._handle_cancellation() schedules retry or permanent fail (uses TIMEOUT reason)
- [ ] WorkerPool passes timeout/retry config to Workers
- [ ] Integration test: timeout → cancel → retry scheduled
- [ ] Integration test: max retries → permanent failure
- [ ] Integration test: successful completion stops monitor early
- [ ] All existing tests pass

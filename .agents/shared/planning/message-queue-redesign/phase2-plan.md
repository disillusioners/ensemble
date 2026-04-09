# Phase 2: Worker Pool

## Objective

Build the stateless worker pool infrastructure that polls the database for tasks, claims them atomically, and processes them. Workers are **completely stateless** — no in-memory state, no persistent consumers, no event loop dependencies.

## Coupling

- **Depends on**: Phase 1 (Task and Event tables/models/repositories)
- **Coupling type**: loose (depends on schema interfaces, not implementation details)
- **Shared files with other phases**: `daemon/repositories/task/`, `daemon/repositories/event/`
- **Shared APIs/interfaces**: `TaskRepository.claim_pending_task()`, `TaskRepository.complete_task()`, `EventRepository.create_event()`
- **Why this coupling**: Workers only call repository methods. Phase 3 will wire these workers into the message flow. Phase 1 must be complete so the schema exists.

## Context

### What Exists Today

| Component | Location | Problem |
|-----------|----------|---------|
| Persistent consumers | `manager.py:InstanceManager._instance_consumer()` | Never die, leak state, can't recover |
| In-memory queues | `manager.py:InstanceManager._instance_queues` | Lost on restart |
| Watchdog thread | `queue.py:InstanceWatchdog` | Thread-based, mixed with async |
| Circuit breaker | `queue.py:InstanceCircuitBreaker` | In-memory state |

### What We Build

| Component | Location | Purpose |
|-----------|----------|---------|
| Worker pool | `daemon/worker_pool.py` (new) | Manages N worker threads |
| Worker | `daemon/worker.py` (new) | Polls DB, claims tasks, processes them |
| Task processor | `daemon/task_processor.py` (new) | Processes different task types |

### Key Design Decisions

1. **Workers are threads with async bridge**: SQLite operations are sync, but LangGraph execution and event broadcasting are async. Workers use `asyncio.run_coroutine_threadsafe()` to bridge from their thread to the main asyncio event loop — the same pattern already used in `manager.py:349` and `events.py:294-322`. <!-- FIX: C1 -->

2. **Poll interval: 0.5 seconds**: Responsive enough for user-facing work, not too aggressive on DB load.

3. **Atomic claim prevents duplicates**: Only one worker can claim a task due to the UPDATE-RETURNING pattern.

4. **Workers are singletons per process**: One worker pool per InstanceManager process.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create TaskProcessor interface | Base class + implementations for process_message, send_report, cleanup tasks | `daemon/task_processor.py` (new) |
| 2 | Create Worker class | Thread-based worker that polls DB, claims tasks, delegates to TaskProcessor | `daemon/worker.py` (new) |
| 3 | Create WorkerPool manager | Manages N worker threads, lifecycle (start/stop), health monitoring | `daemon/worker_pool.py` (new) |
| 4 | Implement process_message task | Run the LangGraph for a message using existing manager._process_message_with_tracking | `daemon/task_processor.py` (modify) |
| 5 | Implement send_report task | Deliver completion/error reports to parent instances | `daemon/task_processor.py` (modify) |
| 6 | Implement cleanup task | Handle instance termination, resource cleanup | `daemon/task_processor.py` (modify) |
| 7 | Add worker pool to manager | Wire WorkerPool into InstanceManager lifecycle | `daemon/manager.py` (modify) |
| 8 | Write unit tests | Test worker lifecycle, task claiming, error handling | `tests/message_queue_redesign/test_worker_pool.py` (new) |
| 9 | Write integration tests | Test concurrent workers, crash recovery | `tests/message_queue_redesign/test_worker_pool_integration.py` (new) |

## Key Files

### New Files

| File | Purpose |
|------|---------|
| `daemon/worker.py` | Worker class — poll loop, task claiming, delegation |
| `daemon/worker_pool.py` | WorkerPool class — manages worker threads, lifecycle |
| `daemon/task_processor.py` | TaskProcessor base + implementations |
| `tests/message_queue_redesign/test_worker_pool.py` | WorkerPool unit tests |
| `tests/message_queue_redesign/test_worker_pool_integration.py` | Concurrent worker tests |

### Modified Files

| File | Changes |
|------|---------|
| `daemon/manager.py` | Add WorkerPool initialization, wire into lifecycle |
| `daemon/manager.py` | Add `process_task()` method called by TaskProcessor |

## Constraints

1. **Thread-to-async bridge**: Workers are threads but must call async code (LangGraph, event broadcasting). Use `asyncio.run_coroutine_threadsafe()` consistently — never call async code directly from worker threads. <!-- FIX: C1 -->
2. **No in-memory state**: Workers don't store task state between polls
3. **Graceful shutdown**: Workers finish current task before stopping
4. **SQLite compatibility**: All operations must work with SQLite's concurrency model

## Worker Design

<!-- FIX: C1 — Worker thread must bridge to asyncio for LangGraph execution -->
### Worker Poll Loop

```python
class Worker(threading.Thread):
    def __init__(self, worker_id: str, task_repo: TaskRepository, 
                 task_processor: TaskProcessor, main_loop: asyncio.AbstractEventLoop,
                 poll_interval: float = 0.5):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.task_repo = task_repo
        self.task_processor = task_processor
        self._main_loop = main_loop  # Reference to main asyncio event loop
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
    
    def run(self):
        while not self._stop_event.is_set():
            task = self.task_repo.claim_pending_task(self.worker_id)
            
            if task is not None:
                try:
                    # Bridge from worker thread to asyncio event loop
                    # This is the same pattern used in manager.py:349 and events.py:294-322
                    future = asyncio.run_coroutine_threadsafe(
                        self.task_processor.process_async(task), 
                        self._main_loop
                    )
                    future.result(timeout=300)  # 5-minute timeout per task
                except TimeoutError:
                    self.task_repo.fail_task(task.id, "Task processing timed out (300s)")
                except Exception as e:
                    self.task_repo.fail_task(task.id, str(e))
            else:
                # No work available, sleep and retry
                self._stop_event.wait(self.poll_interval)
    
    def stop(self):
        self._stop_event.set()
        self.join(timeout=10)
```

### WorkerPool Manager

```python
class WorkerPool:
    def __init__(self, num_workers: int, task_repo: TaskRepository,
                 task_processor: TaskProcessor, main_loop: asyncio.AbstractEventLoop):
        self.num_workers = num_workers
        self.workers: list[Worker] = []
        self.task_repo = task_repo
        self.task_processor = task_processor
        self._main_loop = main_loop
    
    def start(self):
        for i in range(self.num_workers):
            worker = Worker(
                worker_id=f"worker-{i}",
                task_repo=self.task_repo,
                task_processor=self.task_processor,
                main_loop=self._main_loop
            )
            worker.start()
            self.workers.append(worker)
    
    def stop(self, timeout: float = 30):
        for worker in self.workers:
            worker.stop()
        
        for worker in self.workers:
            worker.join(timeout=timeout / len(self.workers))
    
    def get_stats(self) -> dict:
        return {
            "num_workers": len(self.workers),
            "pending_tasks": self.task_repo.get_pending_count(),
        }
```

## TaskProcessor Design

<!-- FIX: C1 — TaskProcessor must be async to work with asyncio.run_coroutine_threadsafe -->
### Base Interface

```python
class TaskProcessor(ABC):
    @abstractmethod
    async def process_async(self, task: Task) -> None:
        """Process a task asynchronously. Raises on failure."""
        pass

class CompositeTaskProcessor(TaskProcessor):
    """Routes tasks to type-specific processors."""
    def __init__(self, instance_manager: InstanceManager):
        self._processors = {
            TaskType.PROCESS_MESSAGE: ProcessMessageProcessor(instance_manager),
            TaskType.SEND_REPORT: SendReportProcessor(instance_manager),
            TaskType.CLEANUP: CleanupProcessor(instance_manager),
        }
    
    async def process_async(self, task: Task) -> None:
        processor = self._processors.get(task.type)
        if processor is None:
            raise ValueError(f"Unknown task type: {task.type}")
        await processor.process_async(task)
```

<!-- FIX: C1 — ProcessMessageProcessor must be async for LangGraph execution -->
### ProcessMessageProcessor

```python
class ProcessMessageProcessor:
    def __init__(self, instance_manager: InstanceManager):
        self.manager = instance_manager
    
    async def process_async(self, task: Task) -> None:
        # Update message to processing (sync DB op via asyncio.to_thread)
        message = await asyncio.to_thread(self.manager._message_repo.get, task.message_id)
        message.status = MessageStatus.PROCESSING.value
        message.processing_task_id = task.id
        message.processing_started_at = datetime.now(timezone.utc)  # reuse existing field
        
        # Create event (sync DB op)
        await asyncio.to_thread(self.manager._event_repo.create,
            type=EventType.PROCESSING_STARTED,
            instance_id=message.instance_id,
            message_id=message.id
        )
        
        # Process the message (async — runs LangGraph via astream)
        result = await self.manager._process_message_with_tracking(message)
        
        # Mark complete (sync DB ops)
        await asyncio.to_thread(self.manager._message_repo.complete, message.id)
        await asyncio.to_thread(self.manager._event_repo.create,
            type=EventType.PROCESSING_COMPLETED,
            instance_id=message.instance_id,
            message_id=message.id,
            data={"result": result}
        )
        await asyncio.to_thread(self.manager._task_repo.complete_task, task.id, result)
```

## Crash Recovery

<!-- FIX: W5 — configurable threshold, default 15 min (not 5 min — LLM calls can be slow) -->
### Stale Task Detection

A recovery task runs periodically (every 60 seconds):

```python
def recover_stale_tasks(self, stale_threshold_minutes: int = 15):
    """Reset tasks that have been running too long (worker crashed).
    
    Default threshold is 15 minutes because LLM calls can legitimately 
    take 5-10 minutes. Uses last_activity_at if available, otherwise started_at.
    """
    stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_threshold_minutes)
    
    stale_tasks = self.task_repo.find_stale_running_tasks(stale_threshold)
    
    for task in stale_tasks:
        logger.warning(f"Recovering stale task {task.id} from worker {task.worker_id}")
        self.task_repo.reset_to_pending(task.id)
        
        # Also reset the associated message
        if task.message_id:
            self.message_repo.reset_to_pending(task.message_id)
```

### Restart Recovery (on app startup)

```python
async def recover_from_crash(self):
    """Called on InstanceManager startup to recover from previous crash."""
    # 1. Find all tasks in 'running' state
    # 2. Reset them to 'pending' (workers may have crashed)
    # 3. Find all messages in 'processing' state
    # 4. Reset them to 'pending' (allow re-processing)
    # 5. This is safe because LangGraph state is checkpointed
    
    logger.info("Recovering from crash, resetting stale tasks and messages")
    self.task_repo.reset_all_stale()
    self.message_repo.reset_all_stale()
```

## Testing Strategy

### Unit Tests

| Test | Scenario |
|------|----------|
| `test_worker_claims_task` | Worker claims pending task |
| `test_worker_skips_claimed_task` | Worker doesn't claim already-claimed task |
| `test_worker_processes_task` | Worker delegates to TaskProcessor |
| `test_worker_handles_failure` | Worker calls fail_task on exception |
| `test_worker_respects_stop` | Worker stops cleanly |
| `test_pool_creates_workers` | Pool creates N workers |
| `test_pool_stops_workers` | Pool stops all workers |
| `test_stale_task_detection` | Finds tasks running >15 minutes (configurable threshold) <!-- FIX: W5 --> |
| `test_task_reset` | Resets stale task to pending |

### Integration Tests

| Test | Scenario |
|------|----------|
| `test_concurrent_workers_claim_different_tasks` | Two workers claim different tasks |
| `test_concurrent_workers_dont_claim_same_task` | Two workers never claim same task |
| `test_worker_crash_recovery` | Simulate worker crash, verify task recovery |
| `test_pool_restart` | Stop pool, start new pool, verify tasks continue |

## Deliverables

- [ ] `daemon/worker.py` — Worker class with poll loop and async bridge via `run_coroutine_threadsafe`
- [ ] `daemon/worker_pool.py` — WorkerPool class with lifecycle management
- [ ] `daemon/task_processor.py` — Async TaskProcessor implementations
- [ ] `daemon/manager.py` modifications — WorkerPool integration, main_loop reference
- [ ] Unit tests for worker pool
- [ ] Integration tests for concurrent workers
- [ ] Crash recovery functionality works (configurable stale threshold)
- [ ] Application starts with worker pool running

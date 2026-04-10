# Phase 6: Config & Wiring

## Objective

Add new configuration fields to ServicesConfig and config.yaml, wire them through the startup code in manager.setup_worker_pool(), and validate the full integration with an end-to-end test.

## Coupling

- **Depends on**: Phase 4 (Worker needs config values), Phase 5 (StaleTaskRecovery needs config values)
- **Coupling type**: loose — default values mean everything works without config changes
- **Shared files with other phases**: `daemon/config.py`, `daemon/manager.py`, `config.yaml`
- **Shared APIs/interfaces**: ServicesConfig fields
- **Why this coupling**: Phase 4/5 constructors accept config values; this phase provides them

## Context

### Current Config

```yaml
services:
  worker_poll_interval: 0.5
  stale_task_recovery_interval: 60
```

```python
class ServicesConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SERVICES_")
    worker_poll_interval: float = Field(default=0.5, ...)
    stale_task_recovery_interval: int = Field(default=60, ...)
```

### Current Wiring (manager.py:setup_worker_pool)

```python
def setup_worker_pool(self, num_workers=4):
    task_repo = TaskRepository(engine=self._engine)
    event_repo = EventRepository(engine=self._engine)
    
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repo,
        message_repository=self._queue_repository,
        event_repository=event_repo,
        check_interval_seconds=self.config.services.stale_task_recovery_interval,
    )
    
    self._task_processor = TaskProcessor(
        task_repo=task_repo,
        instance_manager=self,
        event_repo=event_repo,
    )
    
    self._worker_pool = WorkerPool(
        task_processor=self._task_processor,
        num_workers=num_workers,
        poll_interval=self.config.services.worker_poll_interval,
    )
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add new fields to ServicesConfig | task_timeout_minutes, max_task_retries, task_retry_backoff_base, task_retry_backoff_max, stale_task_cancel_grace_seconds | `daemon/config.py` |
| 2 | Update config.yaml | Add new fields with documented defaults | `config.yaml` |
| 3 | Update manager.setup_worker_pool() | Pass new config values to WorkerPool and StaleTaskRecovery constructors | `daemon/manager.py` |
| 4 | Write end-to-end integration test | Full flow: enqueue → timeout → cancel → retry → complete | `tests/message_queue_redesign/test_timeout_retry_e2e.py` (new) |
| 5 | Update API stats endpoint | Include retry/cancel stats in worker pool stats (if applicable) | `daemon/api.py` (minor) |
| 6 | Verify backward compatibility | Test with config that has no new fields (uses defaults) | Existing tests |

## Key Files

- `daemon/config.py` — ServicesConfig class
- `config.yaml` — Default configuration
- `daemon/manager.py` — setup_worker_pool() method
- `daemon/api.py` — Stats endpoint (minor update)

## Detailed Implementation

### 1. ServicesConfig Enhancement

```python
class ServicesConfig(BaseSettings):
    """Worker pool and background service configuration."""
    model_config = SettingsConfigDict(env_prefix="SERVICES_")

    worker_poll_interval: float = Field(
        default=0.5,
        description="How often workers poll for tasks (seconds)."
    )
    stale_task_recovery_interval: int = Field(
        default=60,
        description="How often to check for stale tasks and recover them (seconds)."
    )
    
    # NEW: Task timeout and retry configuration
    task_timeout_minutes: float = Field(
        default=15.0,
        description="Maximum time a task can run before being cancelled (minutes). "
                    "Set to 0 to disable timeout."
    )
    max_task_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for failed/timed-out tasks. "
                    "Set to 0 to disable retries."
    )
    task_retry_backoff_base: int = Field(
        default=60,
        description="Base delay for exponential backoff between retries (seconds). "
                    "Actual delay: base * 2^retry_count."
    )
    task_retry_backoff_max: int = Field(
        default=3600,
        description="Maximum delay between retries (seconds). Default: 1 hour."
    )
    stale_task_cancel_grace_seconds: int = Field(
        default=10,
        description="Seconds to wait for graceful shutdown after requesting "
                    "task cancellation in stale task recovery."
    )
```

### 2. config.yaml

```yaml
services:
  worker_poll_interval: 0.5
  stale_task_recovery_interval: 60
  
  # Task timeout and retry
  task_timeout_minutes: 15          # Max task execution time (0 = no timeout)
  max_task_retries: 3               # Max retry attempts (0 = no retry)
  task_retry_backoff_base: 60       # Base backoff in seconds (1 min)
  task_retry_backoff_max: 3600      # Max backoff in seconds (1 hour)
  stale_task_cancel_grace_seconds: 10  # Grace period for worker shutdown
```

### 3. Updated setup_worker_pool()

```python
def setup_worker_pool(self, num_workers: int = 4) -> None:
    """Set up the worker pool for message processing."""
    
    if os.environ.get("USE_WORKER_POOL", "").lower() in ("false", "0", "no"):
        logger.info("Worker pool disabled (USE_WORKER_POOL=false)")
        return
    
    from .services.worker_pool import WorkerPool
    from .services.task_processor import TaskProcessor
    from .services.stale_task_recovery import StaleTaskRecovery
    
    MainLoopBridge.set_loop(self._loop)
    
    task_repo = TaskRepository(engine=self._engine)
    event_repo = EventRepository(engine=self._engine)
    
    svc = self.config.services  # Shorthand
    
    # 1. StaleTaskRecovery
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repo,
        message_repository=self._queue_repository,
        event_repository=event_repo,
        threshold_minutes=int(svc.task_timeout_minutes),
        check_interval_seconds=svc.stale_task_recovery_interval,
        cancel_grace_seconds=svc.stale_task_cancel_grace_seconds,
        max_retries=svc.max_task_retries,
        retry_backoff_base=svc.task_retry_backoff_base,
        retry_backoff_max=svc.task_retry_backoff_max,
    )
    stale_recovery.recover_on_startup()
    stale_recovery.start()
    self._stale_recovery = stale_recovery
    
    # 2. TaskProcessor
    self._task_processor = TaskProcessor(
        task_repo=task_repo,
        instance_manager=self,
        event_repo=event_repo,
    )
    
    # 3. WorkerPool
    self._worker_pool = WorkerPool(
        task_processor=self._task_processor,
        num_workers=num_workers,
        poll_interval=svc.worker_poll_interval,
        timeout_minutes=svc.task_timeout_minutes,
        max_retries=svc.max_task_retries,
        retry_backoff_base=svc.task_retry_backoff_base,
        retry_backoff_max=svc.task_retry_backoff_max,
    )
    self._worker_pool.start()
```

### 4. End-to-End Integration Test

```python
"""End-to-end test for task timeout and retry flow."""

import pytest
import time
from unittest.mock import MagicMock, patch


class TestTimeoutRetryE2E:
    """Test the full timeout → cancel → retry → complete flow."""
    
    def test_timeout_triggers_retry_and_completion(self):
        """Full flow: task times out → retry scheduled → succeeds on retry."""
        # This test requires:
        # - Real TaskRepository (in-memory SQLite)
        # - Real CancellationTokenSource + TimeoutMonitor
        # - Mock manager that simulates slow execution on first attempt
        # - Mock manager that succeeds on second attempt
        # - Real Worker thread
        
        # Setup
        engine = create_engine("sqlite:///:memory:", ...)
        task_repo = TaskRepository(engine=engine)
        
        # Create task
        task = task_repo.create("process_message", "inst-1", "msg-1")
        
        # Configure worker with short timeout
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_processor,  # Returns mock that hangs then succeeds
            poll_interval=0.1,
            timeout_minutes=0.05,  # 3 seconds timeout
            max_retries=2,
            retry_backoff_base=1,  # 1 second backoff
            retry_backoff_max=10,
        )
        
        worker.start()
        time.sleep(5)  # Wait for timeout + retry
        worker.stop()
        
        # Verify: original task CANCELLED, retry task COMPLETED
        parent = task_repo.get(task.id)
        assert parent.status == TaskStatus.CANCELLED.value
        
        chain = task_repo.get_retry_chain("inst-1", "msg-1")
        assert len(chain) == 2
        assert chain[1].retry_count == 1
        assert chain[1].status == TaskStatus.COMPLETED.value
    
    def test_max_retries_permanent_failure(self):
        """Task fails permanently after max retries."""
        ...
    
    def test_config_defaults_work(self):
        """System works with no explicit config (all defaults)."""
        # Don't set any new config fields
        # Verify defaults: timeout=15min, max_retries=3, backoff_base=60
        ...
    
    def test_zero_timeout_disables_timeout(self):
        """task_timeout_minutes=0 means no timeout."""
        ...
    
    def test_zero_retries_disables_retry(self):
        """max_task_retries=0 means no retry on failure."""
        ...
```

## Constraints

- All new config fields have safe defaults — system works without config changes
- Environment variable override must work (SERVICES_TASK_TIMEOUT_MINUTES etc.)
- Config changes should not require restart (worker pool reads on creation)
- Backward compatible: old config.yaml without new fields works fine

## Deliverables

- [ ] ServicesConfig has 5 new fields with documented defaults
- [ ] config.yaml updated with commented defaults
- [ ] manager.setup_worker_pool() passes config to WorkerPool and StaleTaskRecovery
- [ ] End-to-end test: timeout → cancel → retry → complete
- [ ] End-to-end test: max retries → permanent failure
- [ ] Backward compatibility test: old config works
- [ ] All existing tests pass

"""Pytest configuration and fixtures for message queue redesign tests."""

import pytest
import threading
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.task.models import TaskType


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing.

    Uses StaticPool to reuse the same connection across threads.
    Required because asyncio.to_thread() runs workers in different threads,
    and SQLite in-memory databases are per-thread by default.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine):
    """Create TaskRepository instance with fresh database."""
    return TaskRepository(engine)


@pytest.fixture
def sample_task_data():
    """Sample task creation data."""
    return {
        "task_type": TaskType.PROCESS_MESSAGE.value,
        "instance_id": "test-instance-123",
        "message_id": "test-message-456",
    }


# ============================================================================
# Mock Classes for Testing
# ============================================================================


class MockTask:
    """Mock task for testing."""
    def __init__(
        self,
        task_id=1,
        task_type="process_message",
        instance_id="test-instance",
        message_id="test-message",
        status="pending",
        worker_id=None,
        retry_count=0,
        retry_scheduled=False,
        cancel_requested=False,
    ):
        self.id = task_id
        self.task_type = task_type
        self.instance_id = instance_id
        self.message_id = message_id
        self.status = status
        self.worker_id = worker_id
        self.retry_count = retry_count
        self.retry_scheduled = retry_scheduled
        self.cancel_requested = cancel_requested
        self.result = None
        self.error = None
        self.created_at = datetime.now(timezone.utc)
        self.started_at = None
        self.completed_at = None


class MockTaskProcessor:
    """Mock task processor for testing."""
    def __init__(self):
        self.claim_count = 0
        self.run_count = 0
        self.claimed_tasks = []
        self.should_claim = True
        self.claim_delay = 0
        self.tasks_to_return = []
        # Mock task repository exposing the metrics Worker checks
        # on the empty-claim path. Defaults to "nothing blocked".
        self._task_repo = self._MockTaskRepoForMetrics()

    class _MockTaskRepoForMetrics:
        def has_pending_tasks_blocked_by_busy_instance(self):
            return False
    
    def claim_task(self, worker_id):
        self.claim_count += 1
        if self.should_claim and self.tasks_to_return:
            task = self.tasks_to_return.pop(0)
            task.worker_id = worker_id
            self.claimed_tasks.append(task)
            return task
        return None
    
    def run_task(self, task):
        self.run_count += 1
    
    def get_pending_count(self):
        return len(self.tasks_to_return)


class MockTaskRepository:
    """Mock task repository for testing."""
    def __init__(self):
        self.tasks = {}  # task_id -> task
        self.stale_tasks = []  # For find_stale_running_tasks
        self._next_task_id = 1000  # Start high to avoid collision
        self._retry_tasks = []  # Tasks to return from schedule_retry
        self.reset_count = 0
    
    def find_stale_running_tasks(self, threshold_minutes):
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
        return [t for t in self.stale_tasks if t.started_at and t.started_at < threshold]
    
    def find_cancellable_tasks(self, threshold_minutes):
        """Find running tasks past threshold that haven't been flagged for cancel."""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
        return [
            t for t in self.stale_tasks
            if t.status == "running"
            and t.started_at and t.started_at < threshold
            and not t.cancel_requested
        ]
    
    def request_cancel(self, task_id):
        """Request cancellation of a task."""
        task = self.tasks.get(task_id)
        if task and task.status == "running" and not task.cancel_requested:
            task.cancel_requested = True
            return True
        return False
    
    def get(self, task_id):
        """Get task by ID."""
        return self.tasks.get(task_id)
    
    def force_cancel_and_schedule_retry(self, task_id, max_retries, reason, backoff_base=60, backoff_max=3600):
        """Atomically cancel and schedule retry."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        
        # Check retry limit
        if task.retry_count >= max_retries:
            return None
        
        # Check retry_scheduled guard
        if task.retry_scheduled:
            return None
        
        # Mark parent as cancelled
        task.status = "cancelled"
        task.retry_scheduled = True
        task.error = f"Force cancelled: {reason}"
        
        # Create retry task
        retry_task = MockTask(
            task_id=self._next_task_id,
            task_type=task.task_type,
            instance_id=task.instance_id,
            message_id=task.message_id,
            status="pending",
            retry_count=task.retry_count + 1,
        )
        self._next_task_id += 1
        self.tasks[retry_task.id] = retry_task
        return retry_task
    
    def schedule_retry(self, task_id, max_retries, backoff_base=60, backoff_max=3600):
        """Schedule retry for a cancelled task."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        
        # Check retry_scheduled guard
        if task.retry_scheduled:
            return None
        
        # Check retry limit
        if task.retry_count >= max_retries:
            return None
        
        # Mark parent as cancelled with retry_scheduled
        task.status = "cancelled"
        task.retry_scheduled = True
        
        # Create retry task
        retry_task = MockTask(
            task_id=self._next_task_id,
            task_type=task.task_type,
            instance_id=task.instance_id,
            message_id=task.message_id,
            status="pending",
            retry_count=task.retry_count + 1,
        )
        self._next_task_id += 1
        self.tasks[retry_task.id] = retry_task
        return retry_task
    
    def fail_task(self, task_id, error):
        """Mark task as failed."""
        task = self.tasks.get(task_id)
        if task:
            task.status = "failed"
            task.error = error
            return task
        return None
    
    def find_orphaned_cancelled_tasks(self):
        """Find cancelled tasks without retry scheduled."""
        return [
            t for t in self.tasks.values()
            if t.status == "cancelled" and not t.retry_scheduled
        ]
    
    def reset_stale_tasks(self, threshold_minutes):
        self.reset_count += 1
        return len(self.find_stale_running_tasks(threshold_minutes))


class MockMessageRepository:
    """Mock message repository for testing."""
    def __init__(self):
        self.fail_count = 0
    
    def fail(self, message_id, error):
        self.fail_count += 1


class MockEventRepository:
    """Mock event repository for testing."""
    def __init__(self):
        self.events = []
    
    def create_event(self, instance_id, kind, data):
        self.events.append({"instance_id": instance_id, "kind": kind, "data": data})


# ============================================================================
# Fixtures for Mock Objects
# ============================================================================


@pytest.fixture
def mock_task_processor():
    """Create a MockTaskProcessor instance."""
    return MockTaskProcessor()


class MockWorkerPool:
    """Mock worker pool for testing."""
    def __init__(self, wait_timeout: float = 0.1):
        self._condition = threading.Condition()
        self._notification_count = 0
        self._wait_timeout = wait_timeout
        # Stats dict mirroring the real WorkerPool. Worker.run() increments
        # empty_claim_attempts via incr_stat(); the new
        # claims_skipped_due_to_busy_instance metric is also tracked here
        # for tests that inspect it. The lock mirrors the real pool's
        # _stats_lock so tests that read the dict while a worker thread
        # is incrementing don't see torn values.
        self._stats_lock = threading.Lock()
        self._stats = {
            "notifications_sent": 0,
            "empty_claim_attempts": 0,
            "workers_woken_by_timeout": 0,
            "claims_skipped_due_to_busy_instance": 0,
        }
    
    def notify_work(self):
        with self._condition:
            self._notification_count += 1
            self._condition.notify_all()

    def wait_for_work(self, timeout: float = 3.0, stop_event=None):
        # Mirror the real WorkerPool.wait_for_work: a set stop_event
        # returns False immediately so workers exit their main loop
        # instead of waiting out the full timeout.
        if stop_event is not None and stop_event.is_set():
            return False
        with self._condition:
            if self._notification_count > 0:
                self._notification_count -= 1
                return True
            # Use shorter timeout for tests
            self._condition.wait(timeout=self._wait_timeout)
            if stop_event is not None and stop_event.is_set():
                return False
            if self._notification_count > 0:
                self._notification_count -= 1
                return True
            return False


@pytest.fixture
def mock_worker_pool():
    """Create a MockWorkerPool instance."""
    return MockWorkerPool()


@pytest.fixture
def mock_task_repository():
    """Create a MockTaskRepository instance."""
    return MockTaskRepository()


@pytest.fixture
def mock_message_repository():
    """Create a MockMessageRepository instance."""
    return MockMessageRepository()


@pytest.fixture
def mock_event_repository():
    """Create a MockEventRepository instance."""
    return MockEventRepository()


@pytest.fixture
def mock_task():
    """Create a MockTask instance."""
    return MockTask()

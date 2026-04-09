"""Pytest configuration and fixtures for message queue redesign tests."""

import pytest
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
    def __init__(self, task_id=1, task_type="process_message", instance_id="test-instance"):
        self.id = task_id
        self.task_type = task_type
        self.instance_id = instance_id
        self.message_id = "test-message"
        self.status = "pending"
        self.worker_id = None
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
        self.stale_tasks = []
        self.reset_count = 0
    
    def find_stale_running_tasks(self, threshold_minutes):
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
        return [t for t in self.stale_tasks if t.started_at and t.started_at < threshold]
    
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

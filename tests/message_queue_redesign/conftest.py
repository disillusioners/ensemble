"""Pytest configuration and fixtures for message queue redesign tests."""

import pytest
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

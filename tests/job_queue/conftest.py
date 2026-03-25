"""Pytest configuration and fixtures for job queue tests."""

import pytest
from datetime import datetime

from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine):
    """Create JobRepository instance with fresh database."""
    repo = JobRepository(engine)
    yield repo
    # Clean up after test
    repo.delete_completed()
    repo.delete_by_project("test-project")


@pytest.fixture
def lock_manager():
    """Create fresh JobLockManager instance."""
    manager = JobLockManager()
    yield manager
    manager.clear()


@pytest.fixture
def job_queue_service(repository, lock_manager):
    """Create JobQueueService with repository and lock manager."""
    return JobQueueService(repository, lock_manager)


@pytest.fixture
def sample_task_data():
    """Sample task creation data for repository tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test task message",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "job_metadata": {"test": True},
    }


@pytest.fixture
def sample_task_data_service():
    """Sample task creation data for service tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test task message",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "metadata": {"test": True},
    }


@pytest.fixture
def sample_task_data_no_project():
    """Sample task creation data without project_id for repository."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test task without project",
        "source": "api",
        "project_id": None,
        "priority": 5,
        "job_metadata": None,
    }


@pytest.fixture
def sample_task_data_no_project_service():
    """Sample task creation data without project_id for service."""
    return {
        "agent_id": "test-agent",
        "message": "Test task without project",
        "source": "api",
        "project_id": None,
        "priority": 5,
        "metadata": None,
    }


@pytest.fixture
def sample_task_data_service_no_project(sample_task_data_no_project_service):
    """Alias for sample_task_data_no_project_service (for backward compatibility)."""
    return sample_task_data_no_project_service


@pytest.fixture
def high_priority_task_data():
    """High priority task data for repository ordering tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "High priority task",
        "source": "api",
        "project_id": "test-project",
        "priority": 10,  # Highest priority
        "job_metadata": None,
    }


@pytest.fixture
def high_priority_task_data_service():
    """High priority task data for service ordering tests."""
    return {
        "agent_id": "test-agent",
        "message": "High priority task",
        "source": "api",
        "project_id": "test-project",
        "priority": 10,  # Highest priority
        "metadata": None,
    }


@pytest.fixture
def low_priority_task_data():
    """Low priority task data for repository ordering tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Low priority task",
        "source": "api",
        "project_id": "test-project",
        "priority": 1,  # Lowest priority
        "job_metadata": None,
    }


@pytest.fixture
def low_priority_task_data_service():
    """Low priority task data for service ordering tests."""
    return {
        "agent_id": "test-agent",
        "message": "Low priority task",
        "source": "api",
        "project_id": "test-project",
        "priority": 1,  # Lowest priority
        "metadata": None,
    }

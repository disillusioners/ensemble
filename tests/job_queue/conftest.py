"""Pytest configuration and fixtures for job queue tests."""

import pytest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


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
    """Create JobRepository instance with fresh database."""
    repo = JobRepository(engine)
    yield repo
    # Clean up after test (use hard_delete since tests may create jobs in various states)
    repo.hard_delete_completed()
    repo.hard_delete_by_project("test-project")


@pytest.fixture
def lock_manager():
    """Create fresh JobLockManager instance."""
    manager = JobLockManager()
    yield manager
    manager.clear()


@pytest.fixture
def queue_repository(engine):
    """Create JobQueueRepository instance with fresh database."""
    repo = JobQueueRepository(engine)
    yield repo


@pytest.fixture
def queue_repository_with_system_queues(engine):
    """Create JobQueueRepository with system queues pre-provisioned."""
    repo = JobQueueRepository(engine)
    # Pre-provision system queues for test-project
    repo.create(
        project_id="test-project",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    repo.create(
        project_id="test-project",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=3,
        is_system=True,
    )
    # Also set up for project-1 and project-2 used in some tests
    repo.create(
        project_id="project-1",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    repo.create(
        project_id="project-2",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    yield repo


@pytest.fixture
def job_queue_service(repository, lock_manager, queue_repository_with_system_queues):
    """Create JobQueueService with system queues pre-provisioned.
    
    This fixture sets up system queues for test-project, project-1, and project-2
    so that tests with project_id can properly route jobs to their queues.
    """
    return JobQueueService(repository, lock_manager, queue_repository_with_system_queues)


@pytest.fixture
def sample_job_data():
    """Sample job creation data for repository tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test job message",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "job_metadata": {"test": True},
    }


@pytest.fixture
def sample_job_data_service():
    """Sample job creation data for service tests."""
    return {
        "agent_id": "coder",  # Use existing agent
        "message": "Test job message",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "metadata": {"test": True},
    }


@pytest.fixture
def sample_job_data_no_project():
    """Sample job creation data without project_id for repository."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test job without project",
        "source": "api",
        "project_id": None,
        "priority": 5,
        "job_metadata": None,
    }


@pytest.fixture
def sample_job_data_no_project_service():
    """Sample job creation data without project_id for service."""
    return {
        "agent_id": "coder",  # Use existing agent
        "message": "Test job without project",
        "source": "api",
        "project_id": None,
        "priority": 5,
        "metadata": None,
    }


@pytest.fixture
def sample_job_data_service_no_project(sample_job_data_no_project_service):
    """Alias for sample_job_data_no_project_service (for backward compatibility)."""
    return sample_job_data_no_project_service


@pytest.fixture
def high_priority_job_data():
    """High priority job data for repository ordering tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "High priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 10,  # Highest priority
        "job_metadata": None,
    }


@pytest.fixture
def high_priority_job_data_service():
    """High priority job data for service ordering tests."""
    return {
        "agent_id": "coder",  # Use existing agent
        "message": "High priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 10,  # Highest priority
        "metadata": None,
    }


@pytest.fixture
def low_priority_job_data():
    """Low priority job data for repository ordering tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Low priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 1,  # Lowest priority
        "job_metadata": None,
    }


@pytest.fixture
def low_priority_job_data_service():
    """Low priority job data for service ordering tests."""
    return {
        "agent_id": "coder",  # Use existing agent
        "message": "Low priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 1,  # Lowest priority
        "metadata": None,
    }

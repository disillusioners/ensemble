"""Pytest configuration and fixtures for job queue tests."""

import pytest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService
from daemon.services import project_normalizer
from daemon import constants

# ── Shared Test Constants ────────────────────────────────────────────────────────

TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


# ── Autouse Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_system_default_project():
    """Set SYSTEM_DEFAULT_PROJECT_ID for tests that call enqueue() which normalizes project_id.

    project_normalizer uses daemon.constants.SYSTEM_DEFAULT_PROJECT_ID via module attribute access,
    so we only need to update the constants module binding.
    """
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID

    yield

    constants.SYSTEM_DEFAULT_PROJECT_ID = original


@pytest.fixture(autouse=True)
def _truncate_tables(engine):
    """Clear all tables around each test (function-scoped).

    Belt-and-braces isolation: truncate both BEFORE yield (to clear any
    data left over from session-scoped engine setup or earlier fixtures
    that populate data) and AFTER yield (to leave a clean slate for the
    next test). Mirrors the PostgreSQL ``_pg_truncate_tables`` pattern in
    ``tests/postgres/conftest.py``, adapted to ``DELETE FROM`` for SQLite
    compatibility.
    """
    def _truncate():
        from sqlmodel import Session, text
        with Session(engine) as session:
            for table in SQLModel.metadata.tables:
                session.exec(text(f'DELETE FROM "{table}"'))
            session.commit()

    _truncate()
    yield
    _truncate()


@pytest.fixture(scope="session")
def engine():
    """Create in-memory SQLite engine for testing (session-scoped).

    Session-scoped to avoid re-creating 27+ tables for every test.
    Uses StaticPool to reuse the same connection across threads.
    Required because asyncio.to_thread() runs workers in different threads,
    and SQLite in-memory databases are per-thread by default.
    Tables are created once at session start; _truncate_tables clears data
    between tests for isolation.
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
def lock_repo(engine):
    """Create LockRepository instance with fresh database."""
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo):
    """Create fresh JobLockManager instance with lock_repo."""
    manager = JobLockManager(lock_repo=lock_repo)
    yield manager
    # Clean up locks using lock_repo directly (clear() raises NotImplementedError)
    all_locks = lock_repo.get_all_locks()
    for lock in all_locks:
        lock_repo.release(lock.lock_id)


# ── File-backed SQLite fixtures for concurrent tests (F11) ─────────────────
#
# These fixtures back the engine with a file on disk rather than the
# ``:memory:`` URL + StaticPool used by the regular fixtures above. They
# are REQUIRED for tests that exercise the new atomic slot-claim contract
# in ``LockRepository.try_acquire_slot`` (C5) under multi-thread fan-out:
#
#   * StaticPool shares a single connection across threads, so SQLite's
#     per-connection locking serialises the cursor access and we never
#     observe a real race. The race we want to test is the DB-level UNIQUE
#     conflict on ``uq_job_locks_slot`` — which only fires when two
#     connections race for the same slot.
#   * File-backed SQLite (default QueuePool) hands each thread its own
#     connection, exposing the real cross-connection UNIQUE conflict
#     path that the production code uses.
#
# Both fixtures are file-scoped per-test (tmp_path is function-scoped by
# pytest default), so concurrent tests do not pollute each other.

@pytest.fixture
def concurrent_lock_repo(tmp_path):
    """LockRepository backed by a file on disk (default QueuePool).

    Use this in tests that exercise concurrent ``try_acquire_slot``
    against the same (project_id, queue_id, lock_slot) triple. The
    file-backed engine hands each thread its own SQLite connection,
    which is necessary to observe the cross-connection UNIQUE
    conflict that makes the slot-claim invariant visible.
    """
    db_path = tmp_path / "job_locks_concurrent.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield LockRepository(eng)
    finally:
        eng.dispose()


@pytest.fixture
def concurrent_lock_manager(concurrent_lock_repo):
    """JobLockManager backed by a file-backed ``LockRepository`` (F11).

    Pair this with ``concurrent_lock_repo`` (or use it directly) in
    concurrent tests. Switches from ``:memory:`` + StaticPool to a
    file-backed SQLite with the default QueuePool, so multi-thread
    acquires each get their own connection and the DB-level UNIQUE
    conflict path is actually exercised.
    """
    return JobLockManager(lock_repo=concurrent_lock_repo)


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
    repo.create(
        project_id="test-project",
        queue_name="system_kb_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
        description="System FIFO queue for Knowledge Base import jobs",
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
        project_id="project-1",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=3,
        is_system=True,
    )
    repo.create(
        project_id="project-1",
        queue_name="system_kb_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
        description="System FIFO queue for Knowledge Base import jobs",
    )
    repo.create(
        project_id="project-2",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    repo.create(
        project_id="project-2",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=3,
        is_system=True,
    )
    repo.create(
        project_id="project-2",
        queue_name="system_kb_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
        description="System FIFO queue for Knowledge Base import jobs",
    )
    # Also set up for the test system project ID (used when normalize_project_id() is called with None)
    repo.create(
        project_id=TEST_SYSTEM_PROJECT_ID,
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

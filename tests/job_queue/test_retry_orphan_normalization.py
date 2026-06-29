"""Tests for orphan job project_id normalization.

These tests verify that jobs with project_id=None (orphans) are properly
normalized to SYSTEM_DEFAULT_PROJECT_ID when enqueued or retried.
"""

from unittest.mock import patch
from datetime import datetime
import pytest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID
from daemon.repositories.job_queue import AdmissionState, JobRepository, JobQueueRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


# Store original value to restore after tests
_original_system_default_project_id = SYSTEM_DEFAULT_PROJECT_ID


@pytest.fixture(autouse=True)
def setup_system_project_id():
    """Set up SYSTEM_DEFAULT_PROJECT_ID for testing and restore after.
    
    This fixture runs automatically for every test in this module.
    """
    import daemon.constants as constants
    
    # Set a test value for SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = "__test_system_default__"
    
    yield
    
    # Restore original value after test
    constants.SYSTEM_DEFAULT_PROJECT_ID = _original_system_default_project_id


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
    # Clean up after test
    repo.hard_delete_terminal()
    repo.hard_delete_by_project("__test_system_default__")


@pytest.fixture
def lock_repo(engine):
    """Create LockRepository instance with fresh database."""
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo):
    """Create fresh JobLockManager instance with lock_repo."""
    manager = JobLockManager(lock_repo=lock_repo)
    yield manager
    # Clean up locks
    all_locks = lock_repo.get_all_locks()
    for lock in all_locks:
        lock_repo.release(lock.lock_id)


@pytest.fixture
def queue_repository(engine):
    """Create JobQueueRepository instance with fresh database."""
    return JobQueueRepository(engine)


@pytest.fixture
def queue_repository_with_system_queues(engine):
    """Create JobQueueRepository with system queues pre-provisioned."""
    repo = JobQueueRepository(engine)
    # Pre-provision system queue for the test system default project
    repo.create(
        project_id="__test_system_default__",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    yield repo


@pytest.fixture
def job_queue_service(repository, lock_manager, queue_repository_with_system_queues):
    """Create JobQueueService with system queues pre-provisioned."""
    return JobQueueService(
        repository, lock_manager, queue_repository_with_system_queues
    )


@pytest.mark.asyncio
async def test_retry_orphan_job_gets_system_project_id():
    """Test that retry_job() on an orphan job (project_id=None) gets SYSTEM_DEFAULT_PROJECT_ID.
    
    This test verifies that the normalization chokepoint in enqueue() works
    for internal callers like retry_job(). When retry_job() passes project_id=None
    to enqueue(), the resulting retried job should have project_id set to
    SYSTEM_DEFAULT_PROJECT_ID, not None.
    """
    # Create engine and repository for this specific test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    
    try:
        repository = JobRepository(engine)
        lock_repo = LockRepository(engine)
        lock_manager = JobLockManager(lock_repo=lock_repo)
        queue_repo = JobQueueRepository(engine)
        
        # Pre-provision system queue for the test system default project
        queue_repo.create(
            project_id="__test_system_default__",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
        
        service = JobQueueService(repository, lock_manager, queue_repo)
        
        # Mock normalize_project_id to return our test value
        # Patch at the location where it's used (job_queue_service module)
        with patch(
            "daemon.services.job_queue_service.normalize_project_id",
            return_value="__test_system_default__"
        ):
            # Create a FAILED job with project_id=None (orphan job)
            # This simulates a job that was created before the system had a project
            orphan_job = repository.create(
                agent_id="developer",
                agent_dir="./agents/developer",
                message="Orphan job that failed",
                source="test",
                project_id=None,  # This is the orphan
                priority=5,
                job_metadata={"test": True},
            )
            
            # Verify the orphan job has project_id=None
            assert orphan_job.project_id is None
            assert orphan_job.admission_state == AdmissionState.QUEUED.value
            
            # Transition to PROCESSING then FAILED for retry
            # First start the job (moves to PROCESSING)
            repository.start_job(orphan_job.job_id, instance_id="test-instance-001")
            
            # Now fail it
            repository.fail_job(orphan_job.job_id, error_message="Test failure")
            
            # Phase 4 cleanup: ``status`` is frozen at the INSERT default
            # and only ``admission_state`` moves. ``fail_job`` now sets
            # ``admission_state='done'`` (verified below) but no longer
            # writes the legacy ``status`` column. ``service.retry_job``
            # has not yet been migrated off the legacy
            # ``status='failed'`` precondition, so establish that
            # precondition directly — mirroring what the pre-Phase-4
            # ``fail_job`` used to write — so the retry path proceeds to
            # the enqueue() normalization logic this test exercises.
            #
            # Phase 5 cleanup: the ``status`` column was dropped from
            # the JobItem model in Phase B. The retry engine keys off
            # ``admission_state='done'`` (with ``failed_at`` set as the
            # FAILED-path retry marker), so seed that state directly
            # instead of writing the now-removed ``status`` mirror.
            from sqlmodel import Session
            from sqlalchemy import text as _sa_text
            with Session(engine) as _session:
                _session.execute(
                    _sa_text(
                        "UPDATE job_queue_items "
                        "SET admission_state = 'done', "
                        "    failed_at = :failed_at "
                        "WHERE job_id = :jid"
                    ),
                    {
                        "jid": orphan_job.job_id,
                        "failed_at": datetime.utcnow().isoformat(),
                    },
                )
                _session.commit()
            
            # Verify it's now FAILED
            failed_job = repository.get(orphan_job.job_id)
            assert failed_job is not None
            assert failed_job.admission_state == AdmissionState.DONE.value
            assert failed_job.project_id is None  # Still None before retry
            
            # Now retry the orphan job (mock is still active)
            retried_job = await service.retry_job(orphan_job.job_id)
            
            # Assert: The retried job should have project_id set to SYSTEM_DEFAULT_PROJECT_ID
            assert retried_job is not None
            assert retried_job.project_id == "__test_system_default__"
            assert retried_job.project_id is not None
            assert retried_job.project_id != orphan_job.project_id  # Should be different from None
        
        # Clean up locks
        for lock in lock_repo.get_all_locks():
            lock_repo.release(lock.lock_id)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_orphan_job_gets_system_project_id():
    """Test that enqueue() with project_id=None results in project_id=SYSTEM_DEFAULT_PROJECT_ID.
    
    This test verifies the direct case: calling enqueue() with project_id=None
    should normalize the None to SYSTEM_DEFAULT_PROJECT_ID via the normalize_project_id()
    chokepoint.
    """
    # Create engine and repository for this specific test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    
    try:
        repository = JobRepository(engine)
        lock_repo = LockRepository(engine)
        lock_manager = JobLockManager(lock_repo=lock_repo)
        queue_repo = JobQueueRepository(engine)
        
        # Pre-provision system queue for the test system default project
        queue_repo.create(
            project_id="__test_system_default__",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
        
        service = JobQueueService(repository, lock_manager, queue_repo)
        
        # Mock normalize_project_id to return our test value
        # Patch at the location where it's used (job_queue_service module)
        with patch(
            "daemon.services.job_queue_service.normalize_project_id",
            return_value="__test_system_default__"
        ):
            # Enqueue a job with project_id=None (orphan)
            job = await service.enqueue(
                agent_id="developer",
                message="Job with no project",
                source="test",
                project_id=None,  # This is the orphan
                priority=5,
                metadata={"test": True},
            )
            
            # Assert: The job should have project_id set to SYSTEM_DEFAULT_PROJECT_ID
            assert job is not None
            assert job.project_id == "__test_system_default__"
            assert job.project_id is not None
        
        # Clean up locks
        for lock in lock_repo.get_all_locks():
            lock_repo.release(lock.lock_id)
    finally:
        engine.dispose()

"""Integration tests for Task 3.9 — DLQ receives normalized project_id.

Tests verify that when a job fails and is moved to the Dead Letter Queue,
the DLQ item has a normalized project_id (system default, never NULL or empty).

Test strategy:
- Create a job with project_id=None (orphan)
- Fail the job (transition to FAILED state)
- Move to DLQ via DeadLetterService.move_to_dlq_standalone()
- Verify DLQ item's project_id == SYSTEM_DEFAULT_PROJECT_ID

Run with:
    pytest tests/integration/test_dlq_project_normalization.py -v
"""

import asyncio
import pytest
from datetime import datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.routers.jobs import router, set_job_queue_service, set_dead_letter_service
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.repositories.project.repository import SQLModelProjectRepository


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def queue_repository(engine):
    """Create JobQueueRepository with test engine."""
    return JobQueueRepository(engine)


@pytest.fixture
def job_repository(engine):
    """Create JobRepository with test engine."""
    return JobRepository(engine)


@pytest.fixture
def dlq_repository(engine):
    """Create DeadLetterRepository with test engine."""
    return DeadLetterRepository(engine)


@pytest.fixture
def lock_repo(engine):
    """Create LockRepository with test engine."""
    return LockRepository(engine)


@pytest.fixture
def project_repository(engine):
    """Create SQLModelProjectRepository with test engine."""
    return SQLModelProjectRepository(engine)


@pytest.fixture
def lock_manager(lock_repo):
    """Create fresh JobLockManager instance."""
    return JobLockManager(lock_repo=lock_repo)


@pytest.fixture
def job_queue_mgmt_service(queue_repository, job_repository):
    """Create JobQueueMgmtService with real repositories."""
    return JobQueueMgmtService(
        queue_repo=queue_repository,
        job_repo=job_repository,
    )


@pytest.fixture
def system_default_project_id(project_repository):
    """Bootstrap the system default project and return its ID.

    Sets SYSTEM_DEFAULT_PROJECT_ID globally so normalize_project_id() works.
    """
    from daemon import constants

    # Ensure system default project exists
    project_id = project_repository.ensure_system_default_project()

    # Set the global constant so normalize_project_id() works
    constants.SYSTEM_DEFAULT_PROJECT_ID = project_id

    yield project_id

    # Reset after test
    constants.SYSTEM_DEFAULT_PROJECT_ID = None


@pytest.fixture
def job_queue_service(
    job_repository,
    lock_manager,
    queue_repository,
    job_queue_mgmt_service,
    system_default_project_id,
):
    """Create JobQueueService with system queues provisioned."""
    service = JobQueueService(
        repository=job_repository,
        lock_manager=lock_manager,
        queue_repo=queue_repository,
    )

    async def provision():
        await job_queue_mgmt_service.auto_provision_system_queues(system_default_project_id)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(provision())
    finally:
        loop.close()

    return service


@pytest.fixture
def dlq_service(job_repository, dlq_repository, job_queue_service):
    """Create DeadLetterService with real repositories."""
    service = DeadLetterService(
        job_repository=job_repository,
        dlq_repository=dlq_repository,
    )
    service._job_queue_service = job_queue_service
    return service


@pytest.fixture
def test_app(system_default_project_id, job_queue_service, dlq_service):
    """Create FastAPI test app with jobs router."""
    app = FastAPI()
    app.include_router(router)
    set_job_queue_service(job_queue_service)
    set_dead_letter_service(dlq_service)
    yield app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


# =============================================================================
# Task 3.9 Tests: DLQ receives normalized project_id
# =============================================================================

class TestDLQProjectNormalization:
    """Tests for Task 3.9: DLQ items get normalized project_id (never NULL/empty).
    
    When a job is moved to the DLQ, the project_id should be the system default
    (because the job was already normalized at creation time).
    """

    def test_dlq_item_gets_normalized_project_id_on_move(
        self,
        client,
        dlq_service,
        dlq_repository,
        job_repository,
        system_default_project_id,
    ):
        """Moving a FAILED orphan job to DLQ results in normalized project_id.
        
        Steps:
        1. Create job with project_id=None (orphan)
        2. Transition to FAILED
        3. Move to DLQ
        4. Verify DLQ item's project_id == system default (not NULL)
        """
        # Step 1: Create orphan job via API
        response = client.post(
            "/jobs",
            json={
                "agent_id": "coder",
                "message": "Orphan job for DLQ test",
                "project_id": None,
            },
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        
        # Verify job has system default project_id
        job = job_repository.get(job_id)
        assert job is not None
        assert job.project_id == system_default_project_id, (
            f"Job should have system default project_id, got {job.project_id}"
        )
        
        # Step 2: Transition to FAILED (simulate failure)
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id="test-instance",
        )
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at=datetime.utcnow().isoformat(),
            error_message="Simulated failure for DLQ test",
        )
        
        # Verify job is FAILED
        job = job_repository.get(job_id)
        assert job.status == JobStatus.FAILED.value
        assert job.project_id == system_default_project_id
        
        # Step 3: Move to DLQ
        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=job_id,
            reason="MAX_RETRIES",
        )
        
        # Step 4: Verify DLQ item has normalized project_id
        assert dlq_item is not None
        assert dlq_item.project_id == system_default_project_id, (
            f"DLQ item has project_id={dlq_item.project_id}, expected {system_default_project_id}"
        )
        
        # Verify in database too
        dlq_db = dlq_repository.get(dlq_item.dlq_id)
        assert dlq_db is not None
        assert dlq_db.project_id == system_default_project_id, (
            f"DLQ DB entry has project_id={dlq_db.project_id}, expected {system_default_project_id}"
        )
        
        # Verify DLQ item is NOT NULL and NOT empty
        assert dlq_item.project_id is not None
        assert dlq_item.project_id != ""

    def test_dlq_item_never_has_null_project_id(
        self,
        client,
        dlq_service,
        dlq_repository,
        job_repository,
    ):
        """DLQ item project_id can never be NULL (enforced by normalization chain).
        
        Even if somehow a job with NULL project_id reaches the DLQ flow,
        the normalization at creation time prevents it.
        """
        # Create orphan job
        response = client.post(
            "/jobs",
            json={
                "agent_id": "coder",
                "message": "Verify no null in DLQ",
                "project_id": None,
            },
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        
        # Fail it
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id="test-instance-2",
        )
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at=datetime.utcnow().isoformat(),
            error_message="Error",
        )
        
        # Move to DLQ
        dlq_item = dlq_service.move_to_dlq_standalone(job_id=job_id, reason="MANUAL")

        # Count items where project_id IS NULL or empty (using list_dlq)
        items, total = dlq_service.list_dlq(limit=1000)
        null_or_empty = sum(
            1 for item in items
            if item.project_id is None or item.project_id == ""
        )

        assert null_or_empty == 0, (
            f"{null_or_empty} DLQ items have NULL or empty project_id"
        )

    def test_dlq_item_project_id_matches_source_job(
        self,
        client,
        dlq_service,
        dlq_repository,
        job_repository,
        system_default_project_id,
    ):
        """DLQ item project_id exactly matches the source job's project_id.
        
        The DLQ preserves the job's project_id, which should already be normalized.
        """
        # Create job with project_id=None (orphan → system default)
        response = client.post(
            "/jobs",
            json={
                "agent_id": "coder",
                "message": "Project ID preservation test",
                "project_id": None,
            },
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        
        # Get job's normalized project_id
        job = job_repository.get(job_id)
        assert job.project_id == system_default_project_id
        
        # Fail and move to DLQ
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id="test-instance-3",
        )
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at=datetime.utcnow().isoformat(),
            error_message="Error",
        )
        
        dlq_item = dlq_service.move_to_dlq_standalone(job_id=job_id, reason="MAX_RETRIES")
        
        # DLQ item project_id should match job project_id exactly
        assert dlq_item.project_id == job.project_id, (
            f"DLQ item project_id={dlq_item.project_id} != job project_id={job.project_id}"
        )

    def test_dlq_item_queue_id_also_normalized(
        self,
        client,
        dlq_service,
        dlq_repository,
        job_repository,
        queue_repository,
        system_default_project_id,
    ):
        """DLQ item inherits queue_id from the normalized job (system FIFO queue).
        
        The queue_id should also be set correctly when the job is moved to DLQ.
        """
        # Get the actual system FIFO queue to know the expected queue_id
        system_fifo_queue = queue_repository.get_by_name(system_default_project_id, "system_fifo_queue")
        assert system_fifo_queue is not None
        system_fifo_queue_id = system_fifo_queue.queue_id
        
        # Create orphan job
        response = client.post(
            "/jobs",
            json={
                "agent_id": "coder",
                "message": "Queue ID inheritance test",
                "project_id": None,
            },
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        
        # Verify job has queue_id
        job = job_repository.get(job_id)
        assert job.queue_id == system_fifo_queue_id
        
        # Fail and move to DLQ
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id="test-instance-4",
        )
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at=datetime.utcnow().isoformat(),
            error_message="Error",
        )
        
        dlq_item = dlq_service.move_to_dlq_standalone(job_id=job_id, reason="MAX_RETRIES")
        
        # DLQ item should have queue_id matching system FIFO queue
        assert dlq_item.queue_id == system_fifo_queue_id, (
            f"DLQ item has queue_id={dlq_item.queue_id}, expected {system_fifo_queue_id}"
        )

    def test_dlq_list_filter_by_system_project(
        self,
        client,
        dlq_service,
        job_repository,
        system_default_project_id,
    ):
        """DLQ items with system default project_id can be listed/filtered.
        
        After normalization, listing DLQ items filtered by system default
        project_id should return the orphaned DLQ items.
        """
        # Create and fail 2 orphan jobs
        job_ids = []
        for i in range(2):
            response = client.post(
                "/jobs",
                json={
                    "agent_id": "coder",
                    "message": f"DLQ filter test {i}",
                    "project_id": None,
                },
            )
            assert response.status_code == 201
            job_id = response.json()["job_id"]
            job_ids.append(job_id)
            
            # Fail and move to DLQ
            job_repository.atomic_transition(
                job_id,
                from_status=JobStatus.PENDING.value,
                to_status=JobStatus.PROCESSING.value,
                started_at=datetime.utcnow().isoformat(),
                instance_id=f"test-instance-{i}",
            )
            job_repository.atomic_transition(
                job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.FAILED.value,
                completed_at=datetime.utcnow().isoformat(),
                error_message=f"Error {i}",
            )
            dlq_service.move_to_dlq_standalone(job_id=job_id, reason="MAX_RETRIES")
        
        # List DLQ items filtered by system default project
        items, total = dlq_service.list_dlq(project_id=system_default_project_id)
        
        # Should find at least the 2 orphan DLQ items we created
        assert total >= 2, f"Expected at least 2 DLQ items for system project, got {total}"
        
        # All returned items should have the system default project_id
        for item in items:
            assert item.project_id == system_default_project_id, (
                f"DLQ item {item.dlq_id} has project_id={item.project_id}, "
                f"expected {system_default_project_id}"
            )

    def test_dlq_count_filter_by_system_project(
        self,
        client,
        dlq_service,
        job_repository,
        system_default_project_id,
    ):
        """DLQ count filtered by system default project_id returns correct count."""
        # Create and fail 1 orphan job
        response = client.post(
            "/jobs",
            json={
                "agent_id": "coder",
                "message": "DLQ count filter test",
                "project_id": None,
            },
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id="test-instance-count",
        )
        job_repository.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at=datetime.utcnow().isoformat(),
            error_message="Error",
        )
        dlq_service.move_to_dlq_standalone(job_id=job_id, reason="MAX_RETRIES")
        
        # Count DLQ items for system default project
        count = dlq_service.count_dlq(project_id=system_default_project_id)
        assert count >= 1, f"Expected >= 1 DLQ item for system project, got {count}"
        
        # Count DLQ items for non-existent project should be 0
        count_none = dlq_service.count_dlq(project_id="non-existent-project")
        assert count_none == 0, f"Expected 0 DLQ items for non-existent project, got {count_none}"

"""
Integration tests for POST /jobs endpoint - project_id normalization.

Tests verify the end-to-end flow where creating a job via HTTP API with
null or missing project_id results in a DB row with the system default project ID.

Normalization chain:
1. Schema level: JobCreateRequest.normalize_project_id_field() calls normalize_project_id()
2. Router level: create_job() calls normalize_project_id() again (defense-in-depth)
3. Service level: JobQueueService.enqueue() calls normalize_project_id() again (canonical)

Run with:
    pytest tests/integration/test_job_create.py -v
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.routers.jobs import router, set_job_queue_service, set_dead_letter_service
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.repositories.project.models import Project, ProjectStatus
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing.
    
    Uses StaticPool to reuse the same connection across threads.
    Required because asyncio.to_thread() runs workers in different threads.
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
def project_repository(engine):
    """Create SQLModelProjectRepository with test engine."""
    return SQLModelProjectRepository(engine)


@pytest.fixture
def system_default_project_id(project_repository):
    """Bootstrap the system default project and return its ID.
    
    This ensures SYSTEM_DEFAULT_PROJECT_ID is set and the project + queues exist.
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
def job_queue_service(
    job_repository,
    lock_manager,
    queue_repository,
    job_queue_mgmt_service,
    system_default_project_id,
):
    """Create JobQueueService with real repositories.
    
    Also provisions system queues for the system default project.
    """
    import asyncio
    
    service = JobQueueService(
        repository=job_repository,
        lock_manager=lock_manager,
        queue_repo=queue_repository,
    )
    
    # Provision system queues for the system default project
    async def provision():
        await job_queue_mgmt_service.auto_provision_system_queues(system_default_project_id)
    
    # Run the async provisioning in a new event loop
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
    # Wire job_queue_service for watcher notifications
    service._job_queue_service = job_queue_service
    return service


@pytest.fixture
def test_app(system_default_project_id, job_queue_service, dlq_service):
    """Create FastAPI test app with jobs router.
    
    system_default_project_id is a dependency to ensure SYSTEM_DEFAULT_PROJECT_ID
    is set before any HTTP requests are processed (schema validation happens
    during request parsing, before the endpoint handler is called).
    """
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
# Tests
# =============================================================================

class TestCreateJobWithNullProjectId:
    """Tests for POST /jobs with project_id set to null."""
    
    def test_create_job_with_null_project_id_uses_system_default(
        self,
        client,
        job_repository,
        system_default_project_id,
    ):
        """POST with project_id: null should result in system default project ID in DB.
        
        This tests the full normalization chain:
        1. API receives request with project_id: null
        2. JobCreateRequest.normalize_project_id_field() normalizes null → system default
        3. create_job() normalizes again (defense-in-depth)
        4. JobQueueService.enqueue() normalizes again (canonical)
        5. Job is stored in DB with system default project ID
        """
        response = client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "Test job with null project_id",
                "project_id": None,
                "priority": 5,
            },
        )
        
        # Should succeed with 201 Created
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.json()}"
        
        data = response.json()
        
        # Verify response contains the system default project ID
        assert data["project_id"] == system_default_project_id, (
            f"Expected project_id={system_default_project_id}, got {data['project_id']}"
        )
        
        # Verify job_id is present
        assert "job_id" in data
        job_id = data["job_id"]
        
        # Verify DB row has the correct project_id
        job = job_repository.get(job_id)
        assert job is not None, f"Job {job_id} not found in DB"
        assert job.project_id == system_default_project_id, (
            f"DB job has project_id={job.project_id}, expected {system_default_project_id}"
        )
        
        # Verify other fields
        assert job.status == "pending"
        assert job.agent_id == "developer"
        assert job.message == "Test job with null project_id"
        assert job.priority == 5


class TestCreateJobWithMissingProjectId:
    """Tests for POST /jobs without project_id field."""
    
    def test_create_job_with_missing_project_id_uses_system_default(
        self,
        client,
        job_repository,
        system_default_project_id,
    ):
        """POST without project_id field should result in system default project ID.
        
        The project_id field is optional in JobCreateRequest, so omitting it
        should be equivalent to passing null.
        """
        response = client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "Test job without project_id field",
                "priority": 7,
            },
        )
        
        # Should succeed with 201 Created
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.json()}"
        
        data = response.json()
        
        # Verify response contains the system default project ID
        assert data["project_id"] == system_default_project_id, (
            f"Expected project_id={system_default_project_id}, got {data['project_id']}"
        )
        
        # Verify job_id is present
        assert "job_id" in data
        job_id = data["job_id"]
        
        # Verify DB row has the correct project_id
        job = job_repository.get(job_id)
        assert job is not None, f"Job {job_id} not found in DB"
        assert job.project_id == system_default_project_id, (
            f"DB job has project_id={job.project_id}, expected {system_default_project_id}"
        )


class TestCreateJobWithExplicitProjectId:
    """Tests for POST /jobs with explicit project_id (sanity checks)."""
    
    def test_create_job_with_explicit_project_id_preserved(
        self,
        client,
        job_repository,
        queue_repository,
        system_default_project_id,
    ):
        """POST with explicit project_id should preserve that ID (not override to system default).
        
        Only null/missing project_id should be normalized to system default.
        Note: We need to provision a system queue for the custom project since
        the job queue service requires a system queue for any project.
        """
        explicit_project_id = "my-custom-project-123"
        
        # Provision system queue for the custom project
        queue_repository.create(
            project_id=explicit_project_id,
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
        
        response = client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "Test job with explicit project_id",
                "project_id": explicit_project_id,
                "priority": 5,
            },
        )
        
        # Should succeed with 201 Created
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.json()}"
        
        data = response.json()
        
        # Verify response preserves the explicit project_id
        assert data["project_id"] == explicit_project_id, (
            f"Expected project_id={explicit_project_id}, got {data['project_id']}"
        )
        
        # Verify DB row has the explicit project_id
        job_id = data["job_id"]
        job = job_repository.get(job_id)
        assert job is not None
        assert job.project_id == explicit_project_id


# =============================================================================
# Task 3.8 Tests: Orphan jobs get assigned to system project with queue_id
# =============================================================================

class TestOrphanJobAssignedToSystemProject:
    """Tests for Task 3.8: Orphan job (null project_id) goes to system project.
    
    When a job is created without a project_id, it should:
    1. Get project_id = SYSTEM_DEFAULT_PROJECT_ID (normalization)
    2. Get queue_id = system FIFO queue (auto-assignment via queue provisioning)
    """

    def test_orphan_job_gets_system_project_id(
        self,
        client,
        job_repository,
        system_default_project_id,
    ):
        """POST /jobs with project_id=None results in DB row with system default project_id."""
        response = client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "Orphan job test",
                "project_id": None,
            },
        )
        
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.json()}"
        
        job_id = response.json()["job_id"]
        job = job_repository.get(job_id)
        
        assert job is not None, f"Job {job_id} not found in DB"
        assert job.project_id == system_default_project_id, (
            f"Orphan job has project_id={job.project_id}, expected {system_default_project_id}"
        )

    def test_orphan_job_gets_queue_id(
        self,
        client,
        job_repository,
        queue_repository,
        system_default_project_id,
    ):
        """POST /jobs with project_id=None results in DB row with queue_id assigned.
        
        Phase 3 requirement: After normalization, the job should be assigned to
        the system FIFO queue so it can be processed by workers.
        The queue_id is the UUID assigned when the system queue was provisioned.
        """
        # Get the actual system FIFO queue to know the expected queue_id
        system_fifo_queue = queue_repository.get_by_name(system_default_project_id, "system_fifo_queue")
        assert system_fifo_queue is not None, "System FIFO queue not found"
        system_fifo_queue_id = system_fifo_queue.queue_id
        
        response = client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "Orphan job queue assignment test",
                "project_id": None,
            },
        )
        
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.json()}"
        
        job_id = response.json()["job_id"]
        job = job_repository.get(job_id)
        
        assert job is not None, f"Job {job_id} not found in DB"
        assert job.queue_id is not None, (
            f"Orphan job has NULL queue_id (not assigned to system FIFO queue)"
        )
        assert job.queue_id == system_fifo_queue_id, (
            f"Orphan job has queue_id={job.queue_id}, expected {system_fifo_queue_id}"
        )

    def test_orphan_job_missing_project_id_field_also_gets_queue_id(
        self,
        client,
        job_repository,
        queue_repository,
        system_default_project_id,
    ):
        """POST /jobs without project_id field results in queue_id assignment.
        
        Omitting the project_id field entirely should behave the same as
        passing null — both get normalized to system default with queue assigned.
        """
        # Get the actual system FIFO queue
        system_fifo_queue = queue_repository.get_by_name(system_default_project_id, "system_fifo_queue")
        assert system_fifo_queue is not None
        system_fifo_queue_id = system_fifo_queue.queue_id
        
        response = client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "Missing project_id field test",
            },
        )
        
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.json()}"
        
        job_id = response.json()["job_id"]
        job = job_repository.get(job_id)
        
        assert job is not None
        assert job.project_id == system_default_project_id
        assert job.queue_id is not None
        assert job.queue_id == system_fifo_queue_id

    def test_orphan_job_queue_id_responds_to_response(
        self,
        client,
        queue_repository,
        system_default_project_id,
    ):
        """POST /jobs response includes the queue_id for orphan jobs."""
        # Get the actual system FIFO queue
        system_fifo_queue = queue_repository.get_by_name(system_default_project_id, "system_fifo_queue")
        assert system_fifo_queue is not None
        system_fifo_queue_id = system_fifo_queue.queue_id
        
        response = client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "Response queue_id test",
                "project_id": None,
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        
        assert "queue_id" in data, "Response should include queue_id"
        assert data["queue_id"] == system_fifo_queue_id, (
            f"Response queue_id={data['queue_id']}, expected {system_fifo_queue_id}"
        )

    def test_multiple_orphan_jobs_all_get_queue_id(
        self,
        client,
        job_repository,
        queue_repository,
        system_default_project_id,
    ):
        """Multiple orphan jobs all get the system FIFO queue assigned."""
        # Get the actual system FIFO queue
        system_fifo_queue = queue_repository.get_by_name(system_default_project_id, "system_fifo_queue")
        assert system_fifo_queue is not None
        system_fifo_queue_id = system_fifo_queue.queue_id
        
        job_ids = []
        for i in range(3):
            response = client.post(
                "/jobs",
                json={
                    "agent_id": "developer",
                    "message": f"Orphan job {i}",
                    "project_id": None,
                },
            )
            assert response.status_code == 201
            job_ids.append(response.json()["job_id"])
        
        for job_id in job_ids:
            job = job_repository.get(job_id)
            assert job is not None
            assert job.project_id == system_default_project_id
            assert job.queue_id == system_fifo_queue_id

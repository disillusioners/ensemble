"""Tests for Job Soft Delete API endpoints.

This module tests the soft delete, cancel, and restore API endpoints:
- DELETE /jobs/{job_id} - Soft delete terminal jobs, cancel non-terminal jobs
- POST /jobs/{job_id}/cancel - Explicit cancel for non-terminal jobs
- POST /jobs/{job_id}/restore - Restore soft-deleted non-terminal jobs
- GET /jobs - List jobs with include_deleted parameter
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.routers.jobs import router, set_job_queue_service, set_dead_letter_service
from daemon.services.job_queue_service import JobQueueService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_lock_manager import JobLockManager
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobStatus


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
def job_repository(engine):
    """Create JobRepository with test engine."""
    return JobRepository(engine)


@pytest.fixture
def queue_repository(engine):
    """Create JobQueueRepository with test engine and system queues."""
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
    return repo


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
    manager = JobLockManager(lock_repo=lock_repo)
    yield manager
    # Clean up using lock_repo directly
    all_locks = lock_repo.get_all_locks()
    for lock in all_locks:
        lock_repo.release(lock.lock_id)


@pytest.fixture
def job_queue_service(job_repository, lock_manager, queue_repository):
    """Create JobQueueService with real repositories."""
    return JobQueueService(
        repository=job_repository,
        lock_manager=lock_manager,
        queue_repo=queue_repository,
    )


@pytest.fixture
def dlq_service(job_repository, dlq_repository):
    """Create DeadLetterService with real repositories."""
    return DeadLetterService(
        job_repository=job_repository,
        dlq_repository=dlq_repository,
    )


@pytest.fixture
def test_app(job_queue_service, dlq_service):
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
# Helper Functions
# =============================================================================

def create_terminal_job(job_repository, status: str):
    """Create a job that is already in a terminal state.
    
    Args:
        job_repository: The job repository.
        status: One of COMPLETED, FAILED, CANCELLED.
        
    Returns:
        The created JobItem.
    """
    job = job_repository.create(
        agent_id="test-agent",
        agent_dir="/test/agent",
        message="Test terminal job",
        source="api",
        project_id="test-project",
        priority=5,
    )
    
    # Transition to terminal state
    job_repository.start_job(job.job_id, "test-instance")
    
    if status == JobStatus.COMPLETED.value:
        job_repository.complete_job(job.job_id, "Done")
    elif status == JobStatus.FAILED.value:
        job_repository.fail_job(job.job_id, "Test error")
    elif status == JobStatus.CANCELLED.value:
        job_repository.cancel_job(job.job_id)
    
    return job_repository.get(job.job_id)


def soft_delete_job(job_repository, job_id: str):
    """Soft delete a job directly via repository.
    
    Args:
        job_repository: The job repository.
        job_id: The job ID to soft delete.
    """
    job_repository.soft_delete(job_id)


# =============================================================================
# Test Delete Job Endpoint
# =============================================================================

class TestDeleteJobEndpoint:
    """Tests for DELETE /jobs/{job_id} endpoint."""

    def test_delete_terminal_completed_job_soft_deletes(self, client, job_repository):
        """Test DELETE on completed job soft-deletes and returns 200."""
        job = create_terminal_job(job_repository, JobStatus.COMPLETED.value)
        job_id = job.job_id
        
        response = client.delete(f"/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["deleted_at"] is not None
        # Job should still exist in DB
        assert job_repository.get(job_id) is not None

    def test_delete_terminal_failed_job_soft_deletes(self, client, job_repository):
        """Test DELETE on failed job soft-deletes and returns 200."""
        job = create_terminal_job(job_repository, JobStatus.FAILED.value)
        job_id = job.job_id
        
        response = client.delete(f"/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["deleted_at"] is not None

    def test_delete_terminal_cancelled_job_soft_deletes(self, client, job_repository):
        """Test DELETE on cancelled job soft-deletes and returns 200."""
        job = create_terminal_job(job_repository, JobStatus.CANCELLED.value)
        job_id = job.job_id
        
        response = client.delete(f"/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["deleted_at"] is not None

    def test_delete_pending_job_cancels(self, client, job_repository):
        """Test DELETE on pending job cancels it and returns 200."""
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Pending job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job_id = job.job_id
        
        response = client.delete(f"/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["admission_state"] == AdmissionState.DONE.value
        assert data["deleted_at"] is None  # Not soft-deleted

    def test_delete_processing_job_cancels(self, client, job_repository):
        """Test DELETE on processing job cancels it and returns 200."""
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Processing job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job_repository.start_job(job.job_id, "test-instance")
        job_id = job.job_id
        
        response = client.delete(f"/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        # Job should be cancelled (PROCESSING -> CANCELLED)
        assert data["admission_state"] == AdmissionState.DONE.value
        assert data["deleted_at"] is None  # Not soft-deleted

    def test_delete_already_deleted_job_returns_400(self, client, job_repository):
        """Test DELETE on already soft-deleted job returns 400."""
        job = create_terminal_job(job_repository, JobStatus.COMPLETED.value)
        job_id = job.job_id
        
        # Soft delete via API
        response = client.delete(f"/jobs/{job_id}")
        assert response.status_code == 200
        
        # Try to delete again
        response = client.delete(f"/jobs/{job_id}")
        
        assert response.status_code == 400
        assert "already deleted" in response.json()["detail"]["error"].lower()

    def test_delete_nonexistent_job_returns_404(self, client):
        """Test DELETE on non-existent job returns 404."""
        response = client.delete("/jobs/nonexistent-job-id")
        
        assert response.status_code == 404


# =============================================================================
# Test Cancel Job Endpoint
# =============================================================================

class TestCancelJobEndpoint:
    """Tests for POST /jobs/{job_id}/cancel endpoint."""

    def test_cancel_pending_job_succeeds(self, client, job_repository):
        """Test cancel on pending job succeeds and returns 200."""
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Pending job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job_id = job.job_id
        
        response = client.post(f"/jobs/{job_id}/cancel")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["admission_state"] == AdmissionState.DONE.value

    def test_cancel_processing_job_succeeds(self, client, job_repository):
        """Test cancel on processing job succeeds and returns 200."""
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Processing job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job_repository.start_job(job.job_id, "test-instance")
        job_id = job.job_id
        
        response = client.post(f"/jobs/{job_id}/cancel")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["admission_state"] == AdmissionState.DONE.value

    def test_cancel_terminal_job_returns_400(self, client, job_repository):
        """Test cancel on terminal job returns 400."""
        job = create_terminal_job(job_repository, JobStatus.COMPLETED.value)
        job_id = job.job_id
        
        response = client.post(f"/jobs/{job_id}/cancel")
        
        assert response.status_code == 400
        # Check that message mentions terminal state
        assert "terminal" in response.json()["detail"]["message"].lower()

    def test_cancel_deleted_job_returns_400(self, client, job_repository):
        """Test cancel on soft-deleted job returns 400."""
        job = create_terminal_job(job_repository, JobStatus.COMPLETED.value)
        job_id = job.job_id
        
        # Soft delete
        soft_delete_job(job_repository, job_id)
        
        response = client.post(f"/jobs/{job_id}/cancel")
        
        assert response.status_code == 400
        # Check that message mentions deleted
        assert "deleted" in response.json()["detail"]["message"].lower()

    def test_cancel_nonexistent_job_returns_404(self, client):
        """Test cancel on non-existent job returns 404."""
        response = client.post("/jobs/nonexistent-job-id/cancel")
        
        assert response.status_code == 404


# =============================================================================
# Test Restore Job Endpoint
# =============================================================================

class TestRestoreJobEndpoint:
    """Tests for POST /jobs/{job_id}/restore endpoint."""

    def test_restore_non_terminal_deleted_job_succeeds(self, client, job_repository):
        """Test restore on soft-deleted non-terminal job succeeds."""
        # Create a pending job and soft delete it
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Deleted pending job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job_id = job.job_id
        
        # Soft delete via repository
        soft_delete_job(job_repository, job_id)
        
        # Restore via API
        response = client.post(f"/jobs/{job_id}/restore")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["deleted_at"] is None
        assert data["admission_state"] == AdmissionState.QUEUED.value

    def test_restore_non_deleted_job_returns_400(self, client, job_repository):
        """Test restore on non-deleted job returns 400."""
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Active job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job_id = job.job_id
        
        response = client.post(f"/jobs/{job_id}/restore")
        
        assert response.status_code == 400
        assert "not been soft-deleted" in response.json()["detail"]["message"].lower()

    def test_restore_terminal_deleted_job_returns_400(self, client, job_repository):
        """Test restore on soft-deleted terminal job returns 400."""
        # Create a completed job and soft delete it
        job = create_terminal_job(job_repository, JobStatus.COMPLETED.value)
        job_id = job.job_id
        
        # Soft delete
        soft_delete_job(job_repository, job_id)
        
        response = client.post(f"/jobs/{job_id}/restore")
        
        assert response.status_code == 400
        # Check that message mentions terminal state
        assert "terminal" in response.json()["detail"]["message"].lower()

    def test_restore_nonexistent_job_returns_404(self, client):
        """Test restore on non-existent job returns 404."""
        response = client.post("/jobs/nonexistent-job-id/restore")
        
        assert response.status_code == 404


# =============================================================================
# Test List Jobs with Deleted
# =============================================================================

class TestListJobsWithDeleted:
    """Tests for GET /jobs endpoint with include_deleted parameter."""

    def test_list_excludes_deleted_by_default(self, client, job_repository):
        """Test list excludes soft-deleted jobs by default."""
        job1 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Active job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job2 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Deleted job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        
        # Soft delete job2
        soft_delete_job(job_repository, job2.job_id)
        
        response = client.get("/jobs")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["job_id"] == job1.job_id

    def test_list_includes_deleted_with_param(self, client, job_repository):
        """Test list includes soft-deleted jobs when include_deleted=true."""
        job1 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Active job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job2 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Deleted job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        
        # Soft delete job2
        soft_delete_job(job_repository, job2.job_id)
        
        response = client.get("/jobs", params={"include_deleted": "true"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["jobs"]) == 2
        job_ids = [j["job_id"] for j in data["jobs"]]
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids

    def test_list_response_includes_deleted_at(self, client, job_repository):
        """Test list response includes deleted_at field on each job."""
        job1 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Active job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job2 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Deleted job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        
        # Soft delete job2
        soft_delete_job(job_repository, job2.job_id)
        
        response = client.get("/jobs", params={"include_deleted": "true"})
        
        assert response.status_code == 200
        data = response.json()
        
        # Find both jobs in response
        active_job = next(j for j in data["jobs"] if j["job_id"] == job1.job_id)
        deleted_job = next(j for j in data["jobs"] if j["job_id"] == job2.job_id)
        
        # Active job should have deleted_at as None
        assert active_job["deleted_at"] is None
        
        # Deleted job should have deleted_at set
        assert deleted_job["deleted_at"] is not None
        assert "T" in deleted_job["deleted_at"]  # ISO format

    def test_list_with_project_id_excludes_deleted(self, client, job_repository):
        """Test list with project_id filter excludes deleted."""
        job1 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Project job",
            source="api",
            project_id="test-project",
            priority=5,
        )
        job2 = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Other project job",
            source="api",
            project_id="other-project",
            priority=5,
        )
        
        # Soft delete job1 (test-project)
        soft_delete_job(job_repository, job1.job_id)
        
        response = client.get("/jobs", params={"project_id": "test-project"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["jobs"]) == 0

"""
Test suite for queue management API endpoints (router level).

Tests all 7 queue endpoints via FastAPI TestClient:
- GET    /projects/{project_id}/queues         - List queues
- POST   /projects/{project_id}/queues         - Create queue
- GET    /projects/{project_id}/queues/{queue_id} - Get queue
- PATCH  /projects/{project_id}/queues/{queue_id} - Update queue
- DELETE /projects/{project_id}/queues/{queue_id} - Delete queue
- POST   /projects/{project_id}/queues/{queue_id}/start - Resume queue
- POST   /projects/{project_id}/queues/{queue_id}/stop  - Pause queue

Architecture:
- In-memory SQLite with StaticPool (same as conftest.py)
- Dependency injection via set_job_queue_mgmt_service()
- System queues pre-provisioned for test projects
"""

import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.routers.queues import router, set_job_queue_mgmt_service
from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository


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
def queue_repository(engine):
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
    
    # Also set up queues for project-1 and project-2 (for IDOR tests)
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
        project_id="project-2",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    
    yield repo


@pytest.fixture
def job_repository(engine):
    """Create JobRepository."""
    return JobRepository(engine)


@pytest.fixture
def job_queue_service(queue_repository, job_repository):
    """Create JobQueueMgmtService with repositories."""
    service = JobQueueMgmtService(
        queue_repo=queue_repository,
        job_repo=job_repository,
    )
    return service


@pytest.fixture
def test_app(job_queue_service):
    """Create FastAPI test app with queue router."""
    app = FastAPI()
    app.include_router(router)
    set_job_queue_mgmt_service(job_queue_service)
    yield app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


# =============================================================================
# TestQueueCreate - POST /projects/{project_id}/queues
# =============================================================================

class TestQueueCreate:
    """Tests for POST /projects/{project_id}/queues endpoint."""

    def test_create_custom_queue_returns_201(self, client):
        """Create a custom parallel queue, verify response fields."""
        response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "my-custom-queue",
                "queue_type": "parallel",
                "concurrency_limit": 5,
                "description": "My custom processing queue",
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["queue_name"] == "my-custom-queue"
        assert data["queue_type"] == "parallel"
        assert data["concurrency_limit"] == 5
        assert data["description"] == "My custom processing queue"
        assert data["is_system"] is False
        assert data["is_paused"] is False
        assert "queue_id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_fifo_queue_with_concurrency_1(self, client):
        """Create FIFO queue with concurrency_limit=1."""
        response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "fifo-queue",
                "queue_type": "fifo",
                "concurrency_limit": 1,
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["queue_type"] == "fifo"
        assert data["concurrency_limit"] == 1

    def test_create_queue_default_type_is_fifo(self, client):
        """Create without queue_type, verify defaults to 'fifo'."""
        response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "default-queue",
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["queue_type"] == "fifo"
        assert data["concurrency_limit"] == 1  # FIFO default

    def test_create_queue_with_description(self, client):
        """Create with optional description."""
        response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "described-queue",
                "description": "This is a test queue with a description",
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["description"] == "This is a test queue with a description"

    def test_create_queue_duplicate_name_returns_409(self, client):
        """Create same name twice → 409 Conflict."""
        # Create first queue
        response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "duplicate-test"},
        )
        assert response.status_code == 201
        
        # Try to create duplicate
        response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "duplicate-test"},
        )
        assert response.status_code == 409
        data = response.json()
        assert "already exists" in data["detail"]["error"].lower()

    def test_create_queue_reserved_name_returns_422(self, client):
        """Use 'system_fifo_queue' as name → 422 (Pydantic validation catches reserved names)."""
        response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "system_fifo_queue"},
        )
        
        # Pydantic validation returns 422 before business logic
        assert response.status_code == 422

    def test_create_queue_fifo_with_concurrency_gt_1_returns_422(self, client):
        """FIFO with concurrency 2 → 422 Unprocessable Entity."""
        response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "invalid-fifo",
                "queue_type": "fifo",
                "concurrency_limit": 2,
            },
        )
        
        assert response.status_code == 422

    def test_create_queue_empty_name_returns_422(self, client):
        """Empty name → 422."""
        response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": ""},
        )
        
        assert response.status_code == 422

    def test_create_queue_invalid_type_returns_422(self, client):
        """Invalid queue_type → 422."""
        response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "invalid-type-queue",
                "queue_type": "invalid",
            },
        )
        
        assert response.status_code == 422


# =============================================================================
# TestQueueList - GET /projects/{project_id}/queues
# =============================================================================

class TestQueueList:
    """Tests for GET /projects/{project_id}/queues endpoint."""

    def test_list_queues_returns_system_queues(self, client):
        """List queues for project with system queues, verify 2 system queues returned."""
        response = client.get("/projects/test-project/queues")
        
        assert response.status_code == 200
        data = response.json()
        assert "queues" in data
        assert "total" in data
        assert data["total"] == 2
        
        queue_names = {q["queue_name"] for q in data["queues"]}
        assert "system_fifo_queue" in queue_names
        assert "system_parallel_queue" in queue_names

    def test_list_queues_includes_custom_queues(self, client):
        """Create custom queue, then list, verify 3 total."""
        # Create a custom queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "custom-list-test",
                "queue_type": "parallel",
                "concurrency_limit": 3,
            },
        )
        assert create_response.status_code == 201
        
        # List queues
        response = client.get("/projects/test-project/queues")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        
        queue_names = {q["queue_name"] for q in data["queues"]}
        assert "custom-list-test" in queue_names
        assert "system_fifo_queue" in queue_names
        assert "system_parallel_queue" in queue_names

    def test_list_queues_empty_project(self, client):
        """List queues for project without any queues → empty list."""
        response = client.get("/projects/nonexistent-project/queues")
        
        assert response.status_code == 200
        data = response.json()
        assert data["queues"] == []
        assert data["total"] == 0

    def test_list_queues_returns_total_count(self, client):
        """Verify total field matches queue count."""
        # Create two custom queues
        client.post(
            "/projects/test-project/queues",
            json={"queue_name": "count-test-1", "queue_type": "parallel", "concurrency_limit": 2},
        )
        client.post(
            "/projects/test-project/queues",
            json={"queue_name": "count-test-2", "queue_type": "parallel", "concurrency_limit": 2},
        )
        
        response = client.get("/projects/test-project/queues")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == len(data["queues"])
        assert data["total"] == 4  # 2 system + 2 custom


# =============================================================================
# TestQueueGet - GET /projects/{project_id}/queues/{queue_id}
# =============================================================================

class TestQueueGet:
    """Tests for GET /projects/{project_id}/queues/{queue_id} endpoint."""

    def test_get_queue_by_id(self, client):
        """Get a specific queue, verify fields."""
        # First create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={
                "queue_name": "get-test-queue",
                "queue_type": "parallel",
                "concurrency_limit": 4,
                "description": "Queue for get test",
            },
        )
        assert create_response.status_code == 201
        queue_id = create_response.json()["queue_id"]
        
        # Get the queue
        response = client.get(f"/projects/test-project/queues/{queue_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["queue_id"] == queue_id
        assert data["queue_name"] == "get-test-queue"
        assert data["queue_type"] == "parallel"
        assert data["concurrency_limit"] == 4
        assert data["description"] == "Queue for get test"
        assert data["is_system"] is False
        assert data["is_paused"] is False

    def test_get_queue_not_found_returns_404(self, client):
        """Non-existent queue_id → 404."""
        response = client.get(
            "/projects/test-project/queues/nonexistent-queue-id"
        )
        
        assert response.status_code == 404

    def test_get_queue_wrong_project_returns_404(self, client):
        """Queue exists for project-1, request with project-2 → 404 (IDOR protection)."""
        # Create queue for project-1
        create_response = client.post(
            "/projects/project-1/queues",
            json={
                "queue_name": "idor-test-queue",
                "queue_type": "parallel",
                "concurrency_limit": 2,
            },
        )
        assert create_response.status_code == 201
        queue_id = create_response.json()["queue_id"]
        
        # Try to get it via project-2
        response = client.get(f"/projects/project-2/queues/{queue_id}")
        
        assert response.status_code == 404


# =============================================================================
# TestQueueUpdate - PATCH /projects/{project_id}/queues/{queue_id}
# =============================================================================

class TestQueueUpdate:
    """Tests for PATCH /projects/{project_id}/queues/{queue_id} endpoint."""

    def test_update_queue_name(self, client):
        """Update queue name, verify response."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "old-name", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Update name
        response = client.patch(
            f"/projects/test-project/queues/{queue_id}",
            json={"queue_name": "new-name"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["queue_name"] == "new-name"

    def test_update_queue_description(self, client):
        """Update description."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "desc-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Update description
        response = client.patch(
            f"/projects/test-project/queues/{queue_id}",
            json={"description": "Updated description"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description"

    def test_update_queue_concurrency_limit(self, client):
        """Update concurrency_limit for parallel queue."""
        # Create a parallel queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "conc-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Update concurrency
        response = client.patch(
            f"/projects/test-project/queues/{queue_id}",
            json={"concurrency_limit": 5},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["concurrency_limit"] == 5

    def test_update_queue_pause(self, client):
        """Set is_paused=True."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "pause-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Pause the queue
        response = client.patch(
            f"/projects/test-project/queues/{queue_id}",
            json={"is_paused": True},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["is_paused"] is True

    def test_update_queue_no_fields_returns_400(self, client):
        """Empty body → 400."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "empty-update-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Try empty update
        response = client.patch(
            f"/projects/test-project/queues/{queue_id}",
            json={},
        )
        
        assert response.status_code == 400

    def test_update_queue_not_found_returns_404(self, client):
        """Non-existent queue_id → 404."""
        response = client.patch(
            "/projects/test-project/queues/nonexistent-id",
            json={"description": "New description"},
        )
        
        assert response.status_code == 404

    def test_update_queue_wrong_project_returns_404(self, client):
        """IDOR protection - queue exists for project-1 but request via project-2."""
        # Create queue for project-1
        create_response = client.post(
            "/projects/project-1/queues",
            json={"queue_name": "update-idor-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Try to update via project-2
        response = client.patch(
            f"/projects/project-2/queues/{queue_id}",
            json={"description": "Hacked!"},
        )
        
        assert response.status_code == 404

    def test_update_queue_reserved_name_returns_422(self, client):
        """Try to rename to system_fifo_queue → 422 (Pydantic validation)."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "rename-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Try to rename to reserved name - Pydantic validation returns 422
        response = client.patch(
            f"/projects/test-project/queues/{queue_id}",
            json={"queue_name": "system_fifo_queue"},
        )
        
        assert response.status_code == 422


# =============================================================================
# TestQueueDelete - DELETE /projects/{project_id}/queues/{queue_id}
# =============================================================================

class TestQueueDelete:
    """Tests for DELETE /projects/{project_id}/queues/{queue_id} endpoint."""

    def test_delete_custom_queue_returns_200(self, client):
        """Delete a custom queue successfully."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "delete-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Delete it
        response = client.delete(f"/projects/test-project/queues/{queue_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] is True
        
        # Verify it's gone
        get_response = client.get(f"/projects/test-project/queues/{queue_id}")
        assert get_response.status_code == 404

    def test_delete_system_queue_returns_403(self, client):
        """Delete system_fifo_queue → 403 Forbidden."""
        # Get the system queue ID
        list_response = client.get("/projects/test-project/queues")
        queues = list_response.json()["queues"]
        system_fifo = next(q for q in queues if q["queue_name"] == "system_fifo_queue")
        
        # Try to delete it
        response = client.delete(f"/projects/test-project/queues/{system_fifo['queue_id']}")
        
        assert response.status_code == 403
        data = response.json()
        assert "system queue" in data["detail"]["error"].lower()

    def test_delete_queue_not_found_returns_404(self, client):
        """Non-existent queue → 404."""
        response = client.delete(
            "/projects/test-project/queues/nonexistent-queue-id"
        )
        
        assert response.status_code == 404

    def test_delete_queue_wrong_project_returns_404(self, client):
        """IDOR protection."""
        # Create queue for project-1
        create_response = client.post(
            "/projects/project-1/queues",
            json={"queue_name": "delete-idor-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Try to delete via project-2
        response = client.delete(f"/projects/project-2/queues/{queue_id}")
        
        assert response.status_code == 404

    def test_delete_queue_with_processing_jobs_returns_409(self, client, job_queue_service, queue_repository, job_repository):
        """Create a queue, add a PROCESSING job, try to delete → 409."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "processing-delete-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        assert create_response.status_code == 201
        queue_id = create_response.json()["queue_id"]
        
        # Create a job in PENDING state, then update status to PROCESSING
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/test/agents/test-agent",
            message="Test job",
            project_id="test-project",
            queue_id=queue_id,
        )
        
        # Update status to PROCESSING
        job_repository.start_job_atomic(job.job_id, "test-instance")
        
        # Try to delete the queue
        response = client.delete(f"/projects/test-project/queues/{queue_id}")
        
        assert response.status_code == 409
        data = response.json()
        assert "processing" in data["detail"]["error"].lower()


# =============================================================================
# TestQueueStartStop - POST /projects/{project_id}/queues/{queue_id}/start and /stop
# =============================================================================

class TestQueueStartStop:
    """Tests for POST /projects/{project_id}/queues/{queue_id}/start and /stop endpoints."""

    def test_start_queue_resumes_paused_queue(self, client):
        """Pause then start, verify is_paused=False."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "start-stop-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Pause it
        pause_response = client.post(f"/projects/test-project/queues/{queue_id}/stop")
        assert pause_response.status_code == 200
        assert pause_response.json()["is_paused"] is True
        
        # Start it
        start_response = client.post(f"/projects/test-project/queues/{queue_id}/start")
        
        assert start_response.status_code == 200
        data = start_response.json()
        assert data["is_paused"] is False

    def test_stop_queue_pauses_running_queue(self, client):
        """Stop a running queue, verify is_paused=True."""
        # Create a queue
        create_response = client.post(
            "/projects/test-project/queues",
            json={"queue_name": "stop-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Stop it
        response = client.post(f"/projects/test-project/queues/{queue_id}/stop")
        
        assert response.status_code == 200
        data = response.json()
        assert data["is_paused"] is True

    def test_start_queue_not_found_returns_404(self, client):
        """Non-existent queue → 404."""
        response = client.post(
            "/projects/test-project/queues/nonexistent-id/start"
        )
        
        assert response.status_code == 404

    def test_stop_queue_not_found_returns_404(self, client):
        """Non-existent queue → 404."""
        response = client.post(
            "/projects/test-project/queues/nonexistent-id/stop"
        )
        
        assert response.status_code == 404

    def test_start_queue_wrong_project_returns_404(self, client):
        """IDOR protection for start."""
        # Create queue for project-1
        create_response = client.post(
            "/projects/project-1/queues",
            json={"queue_name": "start-idor-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Try to start via project-2
        response = client.post(f"/projects/project-2/queues/{queue_id}/start")
        
        assert response.status_code == 404

    def test_stop_queue_wrong_project_returns_404(self, client):
        """IDOR protection for stop."""
        # Create queue for project-1
        create_response = client.post(
            "/projects/project-1/queues",
            json={"queue_name": "stop-idor-test", "queue_type": "parallel", "concurrency_limit": 2},
        )
        queue_id = create_response.json()["queue_id"]
        
        # Try to stop via project-2
        response = client.post(f"/projects/project-2/queues/{queue_id}/stop")
        
        assert response.status_code == 404

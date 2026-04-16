"""Tests for DLQ API endpoints.

This module tests the DLQ API endpoints using FastAPI TestClient:
- GET /projects/{project_id}/dlq - List DLQ items
- GET /projects/{project_id}/dlq/{dlq_id} - Get single DLQ item
- POST /projects/{project_id}/dlq/{dlq_id}/replay - Replay a DLQ item
- DELETE /projects/{project_id}/dlq/{dlq_id} - Delete DLQ item
- DELETE /projects/{project_id}/dlq - Bulk cleanup
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.routers.dlq import router, set_dead_letter_service
from daemon.services.dead_letter_service import DeadLetterService, DLQItemNotFoundError
from daemon.repositories.job_queue.models import DeadLetterItem, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository, set_dead_letter_repository


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
def dlq_repository(engine):
    """Create DeadLetterRepository with test engine."""
    return DeadLetterRepository(engine)


@pytest.fixture
def dlq_service(job_repository, dlq_repository):
    """Create DeadLetterService with real repositories."""
    return DeadLetterService(
        job_repository=job_repository,
        dlq_repository=dlq_repository,
    )


@pytest.fixture
def test_app(dlq_service, dlq_repository):
    """Create FastAPI test app with DLQ router."""
    app = FastAPI()
    app.include_router(router)
    set_dead_letter_service(dlq_service)
    set_dead_letter_repository(dlq_repository)  # Set singleton for cleanup endpoint
    yield app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


@pytest.fixture
def sample_dlq_item():
    """Create a sample DLQ item."""
    return DeadLetterItem(
        dlq_id="dlq-123",
        job_id="job-456",
        agent_id="coder",
        agent_dir="/agents/coder",
        message="Fix the login bug",
        source="api",
        project_id="project-abc",
        queue_id="queue-xyz",
        priority=5,
        error_message="Connection timeout after 3 retries",
        retry_count=3,
        failed_at="2025-03-15T10:00:00",
        moved_to_dlq_at="2025-03-15T10:05:00",
        reason="MAX_RETRIES",
        metadata_json={"user_id": "user-123"},
    )


# =============================================================================
# Test DLQ List Endpoint
# =============================================================================

class TestListDLQItems:
    """Tests for GET /projects/{project_id}/dlq endpoint."""

    def test_list_dlq_items_empty(self, client, dlq_service):
        """Test listing DLQ items returns empty list for project with no items."""
        response = client.get("/projects/project-abc/dlq")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_dlq_items(self, client, dlq_service, dlq_repository):
        """Test listing DLQ items returns items for project."""
        # Create a DLQ item in the repository
        item = DeadLetterItem(
            job_id="job-123",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Test error",
            retry_count=1,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        dlq_repository.enqueue(item)
        
        response = client.get("/projects/project-abc/dlq")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["dlq_id"] == item.dlq_id
        assert data["items"][0]["job_id"] == "job-123"

    def test_list_dlq_items_with_filters(self, client, dlq_service, dlq_repository):
        """Test listing DLQ items with queue_id and reason filters."""
        # Create items with different filters
        item1 = DeadLetterItem(
            job_id="job-1",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Message 1",
            source="api",
            project_id="project-abc",
            queue_id="queue-1",
            priority=5,
            error_message="Error 1",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        item2 = DeadLetterItem(
            job_id="job-2",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Message 2",
            source="api",
            project_id="project-abc",
            queue_id="queue-2",
            priority=5,
            error_message="Error 2",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MANUAL",
        )
        dlq_repository.enqueue(item1)
        dlq_repository.enqueue(item2)
        
        # Filter by queue_id
        response = client.get(
            "/projects/project-abc/dlq",
            params={"queue_id": "queue-1"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["queue_id"] == "queue-1"
        
        # Filter by reason
        response = client.get(
            "/projects/project-abc/dlq",
            params={"reason": "MANUAL"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["reason"] == "MANUAL"

    def test_list_dlq_items_pagination(self, client, dlq_service, dlq_repository):
        """Test listing DLQ items with pagination."""
        # Create 5 items
        for i in range(5):
            item = DeadLetterItem(
                job_id=f"job-{i}",
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"Message {i}",
                source="api",
                project_id="project-abc",
                queue_id="queue-xyz",
                priority=5,
                error_message=f"Error {i}",
                retry_count=0,
                failed_at=datetime.utcnow().isoformat(),
                reason="MAX_RETRIES",
            )
            dlq_repository.enqueue(item)
        
        # Test limit
        response = client.get(
            "/projects/project-abc/dlq",
            params={"limit": 2}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        
        # Test offset
        response = client.get(
            "/projects/project-abc/dlq",
            params={"offset": 3}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2  # 5 total - 3 offset


# =============================================================================
# Test DLQ Get Item Endpoint
# =============================================================================

class TestGetDLQItem:
    """Tests for GET /projects/{project_id}/dlq/{dlq_id} endpoint."""

    def test_get_dlq_item(self, client, dlq_service, dlq_repository, sample_dlq_item):
        """Test getting a single DLQ item returns 200."""
        dlq_repository.enqueue(sample_dlq_item)
        
        response = client.get(f"/projects/project-abc/dlq/{sample_dlq_item.dlq_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["dlq_id"] == sample_dlq_item.dlq_id
        assert data["job_id"] == "job-456"
        assert data["error_message"] == "Connection timeout after 3 retries"

    def test_get_dlq_item_not_found(self, client, dlq_service):
        """Test getting a non-existent DLQ item returns 404."""
        response = client.get("/projects/project-abc/dlq/non-existent-id")
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]["error"].lower()

    def test_idor_protection_wrong_project(self, client, dlq_service, dlq_repository, sample_dlq_item):
        """Test accessing DLQ item from wrong project returns 404."""
        dlq_repository.enqueue(sample_dlq_item)
        
        # Try to access via different project
        response = client.get(f"/projects/wrong-project/dlq/{sample_dlq_item.dlq_id}")
        
        assert response.status_code == 404


# =============================================================================
# Test DLQ Replay Endpoint
# =============================================================================

class TestReplayDLQItem:
    """Tests for POST /projects/{project_id}/dlq/{dlq_id}/replay endpoint."""

    def test_replay_dlq_item(
        self, client, dlq_service, dlq_repository, job_repository, engine
    ):
        """Test replaying a DLQ item transitions job to PENDING and deletes DLQ entry."""
        from sqlmodel import Session
        
        # Create a DLQ item
        dlq_item = DeadLetterItem(
            dlq_id="dlq-replay-test",
            job_id="job-replay-test",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Test error",
            retry_count=3,
            failed_at=datetime.utcnow().isoformat(),
            moved_to_dlq_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        dlq_repository.enqueue(dlq_item)
        
        # Create the job in DEAD_LETTER status
        job = JobItem(
            job_id="job-replay-test",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            status="dead_letter",
            retry_count=3,
            error_message="Test error",
        )
        with Session(engine) as session:
            session.add(job)
            session.commit()
        
        response = client.post(
            "/projects/project-abc/dlq/dlq-replay-test/replay"
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job-replay-test"
        assert data["status"] == "pending"
        
        # Verify DLQ item was deleted
        assert dlq_repository.get("dlq-replay-test") is None
        
        # Verify job was updated
        updated_job = job_repository.get("job-replay-test")
        assert updated_job is not None
        assert updated_job.status == "pending"
        assert updated_job.retry_count == 0  # Reset

    def test_replay_dlq_item_not_found(self, client, dlq_service):
        """Test replaying non-existent DLQ item returns 404."""
        response = client.post(
            "/projects/project-abc/dlq/non-existent-id/replay"
        )
        
        assert response.status_code == 404

    def test_replay_dlq_item_wrong_project(self, client, dlq_service, dlq_repository, sample_dlq_item):
        """Test replaying DLQ item from wrong project returns 404."""
        dlq_repository.enqueue(sample_dlq_item)
        
        response = client.post(
            f"/projects/wrong-project/dlq/{sample_dlq_item.dlq_id}/replay"
        )
        
        assert response.status_code == 404


# =============================================================================
# Test DLQ Delete Endpoint
# =============================================================================

class TestDeleteDLQItem:
    """Tests for DELETE /projects/{project_id}/dlq/{dlq_id} endpoint."""

    def test_delete_dlq_item(self, client, dlq_service, dlq_repository, sample_dlq_item):
        """Test deleting a DLQ item returns 204."""
        dlq_repository.enqueue(sample_dlq_item)
        
        response = client.delete(
            f"/projects/project-abc/dlq/{sample_dlq_item.dlq_id}"
        )
        
        assert response.status_code == 204
        
        # Verify item was deleted
        assert dlq_repository.get(sample_dlq_item.dlq_id) is None

    def test_delete_dlq_item_not_found(self, client, dlq_service):
        """Test deleting non-existent DLQ item returns 404."""
        response = client.delete(
            "/projects/project-abc/dlq/non-existent-id"
        )
        
        assert response.status_code == 404

    def test_delete_dlq_item_wrong_project(self, client, dlq_service, dlq_repository, sample_dlq_item):
        """Test deleting DLQ item from wrong project returns 404."""
        dlq_repository.enqueue(sample_dlq_item)
        
        response = client.delete(
            f"/projects/wrong-project/dlq/{sample_dlq_item.dlq_id}"
        )
        
        assert response.status_code == 404


# =============================================================================
# Test DLQ Cleanup Endpoint
# =============================================================================

class TestCleanupDLQ:
    """Tests for DELETE /projects/{project_id}/dlq endpoint (bulk cleanup)."""

    def test_cleanup_dlq(self, client, dlq_service, dlq_repository):
        """Test bulk cleanup deletes old DLQ items."""
        # Create old items (30 days ago)
        old_time = datetime.utcnow() - timedelta(days=30)
        for i in range(3):
            item = DeadLetterItem(
                job_id=f"old-job-{i}",
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"Old message {i}",
                source="api",
                project_id="project-abc",
                queue_id="queue-xyz",
                priority=5,
                error_message=f"Old error {i}",
                retry_count=0,
                failed_at=old_time.isoformat(),
                moved_to_dlq_at=old_time.isoformat(),
                reason="MAX_RETRIES",
            )
            dlq_repository.enqueue(item)
        
        # Create recent items (1 day ago)
        recent_time = datetime.utcnow() - timedelta(days=1)
        for i in range(2):
            item = DeadLetterItem(
                job_id=f"recent-job-{i}",
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"Recent message {i}",
                source="api",
                project_id="project-abc",
                queue_id="queue-xyz",
                priority=5,
                error_message=f"Recent error {i}",
                retry_count=0,
                failed_at=recent_time.isoformat(),
                moved_to_dlq_at=recent_time.isoformat(),
                reason="MAX_RETRIES",
            )
            dlq_repository.enqueue(item)
        
        # Cleanup items older than 7 days
        response = client.delete(
            "/projects/project-abc/dlq",
            params={"max_age_days": 7}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 3
        
        # Recent items should still exist
        for i in range(2):
            assert dlq_repository.get_by_job_id(f"recent-job-{i}") is not None

    def test_cleanup_dlq_with_reason_filter(self, client, dlq_service, dlq_repository):
        """Test cleanup with reason filter only deletes matching items."""
        old_time = datetime.utcnow() - timedelta(days=30)
        
        # Create old MAX_RETRIES item
        item1 = DeadLetterItem(
            job_id="old-max-retries",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Old message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Old error",
            retry_count=0,
            failed_at=old_time.isoformat(),
            moved_to_dlq_at=old_time.isoformat(),
            reason="MAX_RETRIES",
        )
        dlq_repository.enqueue(item1)
        
        # Create old MANUAL item (should not be deleted)
        item2 = DeadLetterItem(
            job_id="old-manual",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Old manual message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Old manual error",
            retry_count=0,
            failed_at=old_time.isoformat(),
            moved_to_dlq_at=old_time.isoformat(),
            reason="MANUAL",
        )
        dlq_repository.enqueue(item2)
        
        # Cleanup MAX_RETRIES items older than 7 days
        response = client.delete(
            "/projects/project-abc/dlq",
            params={"max_age_days": 7, "reason": "MAX_RETRIES"}
        )
        
        assert response.status_code == 200
        
        # MAX_RETRIES item should be deleted
        assert dlq_repository.get_by_job_id("old-max-retries") is None
        
        # MANUAL item should still exist
        assert dlq_repository.get_by_job_id("old-manual") is not None

    def test_cleanup_dlq_invalid_max_age(self, client, dlq_service):
        """Test cleanup with negative max_age_days returns 400."""
        response = client.delete(
            "/projects/project-abc/dlq",
            params={"max_age_days": -1}
        )
        
        assert response.status_code == 400
        assert "non-negative" in response.json()["detail"]["message"].lower()


# =============================================================================
# Test DLQ Response Schemas
# =============================================================================

class TestDLQSchemas:
    """Tests for DLQ response schema validation."""

    def test_dlq_item_response_all_fields(self, client, dlq_service, dlq_repository, sample_dlq_item):
        """Test DLQItemResponse includes all expected fields."""
        dlq_repository.enqueue(sample_dlq_item)
        
        response = client.get(f"/projects/project-abc/dlq/{sample_dlq_item.dlq_id}")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify all expected fields are present
        assert "dlq_id" in data
        assert "job_id" in data
        assert "agent_id" in data
        assert "agent_dir" in data
        assert "message" in data
        assert "source" in data
        assert "project_id" in data
        assert "queue_id" in data
        assert "priority" in data
        assert "error_message" in data
        assert "retry_count" in data
        assert "failed_at" in data
        assert "moved_to_dlq_at" in data
        assert "reason" in data
        assert "metadata" in data

    def test_dlq_list_response_structure(self, client, dlq_service):
        """Test DLQListResponse has correct structure."""
        response = client.get("/projects/project-abc/dlq")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)

    def test_dlq_replay_response_structure(self, client, dlq_service, dlq_repository, job_repository, engine):
        """Test DLQReplayResponse has correct structure."""
        from sqlmodel import Session
        
        # Create DLQ item
        dlq_item = DeadLetterItem(
            dlq_id="dlq-replay-struct",
            job_id="job-replay-struct",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Test",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Error",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            moved_to_dlq_at=datetime.utcnow().isoformat(),
            reason="MANUAL",
        )
        dlq_repository.enqueue(dlq_item)
        
        # Create job
        job = JobItem(
            job_id="job-replay-struct",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Test",
            source="api",
            project_id="project-abc",
            status="dead_letter",
        )
        with Session(engine) as session:
            session.add(job)
            session.commit()
        
        response = client.post("/projects/project-abc/dlq/dlq-replay-struct/replay")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "job_id" in data
        assert "status" in data
        assert "message" in data

    def test_dlq_cleanup_response_structure(self, client, dlq_service):
        """Test DLQCleanupResponse has correct structure."""
        response = client.delete(
            "/projects/project-abc/dlq",
            params={"max_age_days": 30}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert "deleted_count" in data
        assert "message" in data
        assert isinstance(data["deleted_count"], int)

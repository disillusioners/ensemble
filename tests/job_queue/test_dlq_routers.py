"""Tests for DLQ (Dead Letter Queue) API endpoints."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.routers.dlq import router, set_dead_letter_service
from daemon.services.dead_letter_service import DeadLetterService, DLQItemNotFoundError
from daemon.repositories.job_queue.models import DeadLetterItem
from daemon.repositories.job_queue.repository import JobRepository


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
    """Create JobRepository."""
    return JobRepository(engine)


@pytest.fixture
def mock_dlq_repository():
    """Create a mock DLQ repository for testing."""
    return MagicMock()


@pytest.fixture
def dlq_service(job_repository, mock_dlq_repository):
    """Create DeadLetterService with mock repository."""
    return DeadLetterService(
        job_repository=job_repository,
        dlq_repository=mock_dlq_repository,
    )


@pytest.fixture
def test_app(dlq_service):
    """Create FastAPI test app with DLQ router."""
    app = FastAPI()
    app.include_router(router)
    set_dead_letter_service(dlq_service)
    yield app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


# =============================================================================
# Test DLQ Router Endpoints
# =============================================================================

class TestDLQEndpoints:
    """Tests for DLQ router endpoints."""

    def test_list_dlq_empty(self, client, dlq_service):
        """Test listing DLQ items returns empty list."""
        # Mock list_dlq to return empty list
        dlq_service.list_dlq = MagicMock(return_value=([], 0))
        
        response = client.get("/projects/project-abc/dlq")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_dlq_with_items(self, client, dlq_service, mock_dlq_repository):
        """Test listing DLQ items returns items."""
        # Create a sample DLQ item
        sample_item = DeadLetterItem(
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
        
        # Mock the list_dlq method to return our sample
        dlq_service.list_dlq = MagicMock(return_value=([sample_item], 1))
        
        response = client.get("/projects/project-abc/dlq")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["dlq_id"] == "dlq-123"
        assert data["items"][0]["job_id"] == "job-456"
        assert data["items"][0]["reason"] == "MAX_RETRIES"

    def test_list_dlq_with_filters(self, client, dlq_service):
        """Test listing DLQ items with queue_id and reason filters."""
        dlq_service.list_dlq = MagicMock(return_value=([], 0))
        
        response = client.get(
            "/projects/project-abc/dlq",
            params={"queue_id": "queue-xyz", "reason": "MAX_RETRIES", "limit": 10}
        )
        
        assert response.status_code == 200
        dlq_service.list_dlq.assert_called_once()
        call_kwargs = dlq_service.list_dlq.call_args[1]
        assert call_kwargs["project_id"] == "project-abc"
        assert call_kwargs["queue_id"] == "queue-xyz"
        assert call_kwargs["reason"] == "MAX_RETRIES"

    def test_get_dlq_item_success(self, client, dlq_service):
        """Test getting a single DLQ item returns 200."""
        sample_item = DeadLetterItem(
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
        dlq_service.get_dlq = MagicMock(return_value=sample_item)
        
        response = client.get("/projects/project-abc/dlq/dlq-123")
        
        assert response.status_code == 200
        data = response.json()
        assert data["dlq_id"] == "dlq-123"
        assert data["job_id"] == "job-456"
        assert data["error_message"] == "Connection timeout after 3 retries"

    def test_get_dlq_item_not_found(self, client, dlq_service):
        """Test getting a non-existent DLQ item returns 404."""
        dlq_service.get_dlq = MagicMock(return_value=None)
        
        response = client.get("/projects/project-abc/dlq/invalid-dlq-id")
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]["error"].lower()

    def test_get_dlq_item_wrong_project(self, client, dlq_service):
        """Test getting a DLQ item from wrong project returns 404."""
        sample_item = DeadLetterItem(
            dlq_id="dlq-123",
            job_id="job-456",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Fix the login bug",
            source="api",
            project_id="project-abc",  # Different project
            queue_id="queue-xyz",
            priority=5,
            error_message="Error",
            retry_count=0,
            failed_at="2025-03-15T10:00:00",
            moved_to_dlq_at="2025-03-15T10:05:00",
            reason="MANUAL",
            metadata_json={},
        )
        dlq_service.get_dlq = MagicMock(return_value=sample_item)
        
        response = client.get("/projects/wrong-project/dlq/dlq-123")
        
        assert response.status_code == 404

    def test_replay_dlq_item_success(self, client, dlq_service):
        """Test replaying a DLQ item returns 200 and job details."""
        sample_item = DeadLetterItem(
            dlq_id="dlq-123",
            job_id="job-456",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Fix the login bug",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Error",
            retry_count=0,
            failed_at="2025-03-15T10:00:00",
            moved_to_dlq_at="2025-03-15T10:05:00",
            reason="MANUAL",
            metadata_json={},
        )
        
        # Mock job returned after replay
        mock_job = MagicMock()
        mock_job.job_id = "job-456"
        mock_job.status = "pending"
        
        dlq_service.get_dlq = MagicMock(return_value=sample_item)
        dlq_service.replay_from_dlq = MagicMock(return_value=mock_job)
        
        response = client.post("/projects/project-abc/dlq/dlq-123/replay")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job-456"
        assert data["status"] == "pending"
        assert "replay" in data["message"].lower()

    def test_replay_dlq_item_not_found(self, client, dlq_service):
        """Test replaying a non-existent DLQ item returns 404."""
        dlq_service.get_dlq = MagicMock(return_value=None)
        
        response = client.post("/projects/project-abc/dlq/invalid-dlq-id/replay")
        
        assert response.status_code == 404

    def test_delete_dlq_item_success(self, client, dlq_service):
        """Test deleting a DLQ item returns 204."""
        sample_item = DeadLetterItem(
            dlq_id="dlq-123",
            job_id="job-456",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Fix the login bug",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Error",
            retry_count=0,
            failed_at="2025-03-15T10:00:00",
            moved_to_dlq_at="2025-03-15T10:05:00",
            reason="MANUAL",
            metadata_json={},
        )
        dlq_service.get_dlq = MagicMock(return_value=sample_item)
        dlq_service.delete_dlq = MagicMock(return_value=True)
        
        response = client.delete("/projects/project-abc/dlq/dlq-123")
        
        assert response.status_code == 204

    def test_delete_dlq_item_not_found(self, client, dlq_service):
        """Test deleting a non-existent DLQ item returns 404."""
        dlq_service.get_dlq = MagicMock(return_value=None)
        
        response = client.delete("/projects/project-abc/dlq/invalid-dlq-id")
        
        assert response.status_code == 404

    def test_cleanup_dlq_invalid_max_age(self, client, dlq_service):
        """Test cleanup with invalid max_age_days returns 400."""
        response = client.delete(
            "/projects/project-abc/dlq",
            params={"max_age_days": -1}
        )
        
        assert response.status_code == 400
        assert "non-negative" in response.json()["detail"]["message"].lower()


# =============================================================================
# Test DLQ Schemas
# =============================================================================

class TestDLQSchemas:
    """Test class for DLQ schema validation."""

    def test_dlq_item_response_schema(self):
        """Test DLQItemResponse schema with all fields."""
        from daemon.routers.dlq import DLQItemResponse
        
        item = DLQItemResponse(
            dlq_id="dlq-123",
            job_id="job-456",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Fix the login bug",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Connection timeout",
            retry_count=3,
            failed_at="2025-03-15T10:00:00",
            moved_to_dlq_at="2025-03-15T10:05:00",
            reason="MAX_RETRIES",
            metadata={"key": "value"},
        )
        
        assert item.dlq_id == "dlq-123"
        assert item.job_id == "job-456"
        assert item.priority == 5

    def test_dlq_list_response_schema(self):
        """Test DLQListResponse schema."""
        from daemon.routers.dlq import DLQListResponse, DLQItemResponse
        
        item = DLQItemResponse(
            dlq_id="dlq-123",
            job_id="job-456",
            agent_id="coder",
            agent_dir="/agents/coder",
            message="Test",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Error",
            retry_count=0,
            failed_at="2025-03-15T10:00:00",
            moved_to_dlq_at="2025-03-15T10:05:00",
            reason="MANUAL",
            metadata={},
        )
        
        response = DLQListResponse(items=[item], total=1)
        
        assert len(response.items) == 1
        assert response.total == 1

    def test_dlq_replay_response_schema(self):
        """Test DLQReplayResponse schema."""
        from daemon.routers.dlq import DLQReplayResponse
        
        response = DLQReplayResponse(
            job_id="job-456",
            status="pending",
            message="Job queued for replay",
        )
        
        assert response.job_id == "job-456"
        assert response.status == "pending"

    def test_dlq_cleanup_response_schema(self):
        """Test DLQCleanupResponse schema."""
        from daemon.routers.dlq import DLQCleanupResponse
        
        response = DLQCleanupResponse(
            deleted_count=5,
            message="Deleted 5 DLQ items",
        )
        
        assert response.deleted_count == 5

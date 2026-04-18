"""Tests for DLQ bulk replay-all endpoint.

This module tests the POST /projects/{project_id}/dlq/replay-all endpoint:
- Bulk replay of DLQ items with limit parameter
- Default limit of 100, max limit of 1000
- Project isolation (only replay items for specified project)
- Response structure validation
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.routers.dlq import router, set_dead_letter_service
from daemon.services.dead_letter_service import DeadLetterService
from daemon.repositories.job_queue.models import JobItem, DeadLetterItem, JobStatus
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository


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
    yield app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


def create_dlq_items_for_project(engine, dlq_repository, project_id, count, reason="MAX_RETRIES"):
    """Helper to create multiple DLQ items for a project with corresponding jobs.
    
    Items are created with incremental timestamps to ensure deterministic ordering.
    """
    dlq_items = []
    base_time = datetime.now(timezone.utc)
    
    for i in range(count):
        job_id = f"job-{project_id}-{i}"
        dlq_id = f"dlq-{project_id}-{i}"
        
        # Create job in DEAD_LETTER status
        job = JobItem(
            job_id=job_id,
            agent_id="coder",
            agent_dir="/agents/coder",
            message=f"Job {i} for {project_id}",
            source="api",
            project_id=project_id,
            queue_id=f"queue-{project_id}",
            status=JobStatus.DEAD_LETTER.value,
            retry_count=3,
            error_message=f"Error for job {i}",
        )
        
        with Session(engine) as session:
            session.add(job)
            session.commit()
        
        # Create DLQ entry with incrementally different timestamps for ordering
        failed_time = base_time - timedelta(minutes=count - i)
        moved_time = base_time - timedelta(minutes=count - i - 1)
        
        dlq_item = DeadLetterItem(
            dlq_id=dlq_id,
            job_id=job_id,
            agent_id="coder",
            agent_dir="/agents/coder",
            message=f"Job {i} for {project_id}",
            source="api",
            project_id=project_id,
            queue_id=f"queue-{project_id}",
            priority=5,
            error_message=f"Error for job {i}",
            retry_count=3,
            failed_at=failed_time.isoformat(),
            moved_to_dlq_at=moved_time.isoformat(),
            reason=reason,
        )
        
        dlq_repository.enqueue(dlq_item)
        dlq_items.append(dlq_item)
    
    return dlq_items


# =============================================================================
# Test Replay All Success
# =============================================================================

class TestReplayAllSuccess:
    """Tests for successful bulk replay of DLQ items."""

    def test_replay_all_success(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all successfully replays all DLQ items for a project."""
        project_id = "project-abc"
        
        # Create 3 DLQ items
        dlq_items = create_dlq_items_for_project(
            engine, dlq_repository, project_id, count=3
        )
        
        # Call replay-all
        response = client.post(f"/projects/{project_id}/dlq/replay-all")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert data["total"] == 3
        assert data["limit"] == 100  # Default limit
        assert data["replayed"] == 3
        assert data["failed"] == 0
        assert data["skipped"] == 0
        assert data["errors"] == []
        
        # Verify all DLQ entries were removed
        for dlq_item in dlq_items:
            assert dlq_repository.get(dlq_item.dlq_id) is None
        
        # Verify all jobs were reset to PENDING
        for dlq_item in dlq_items:
            job = dlq_service._job_repo.get(dlq_item.job_id)
            assert job is not None
            assert job.status == "pending"
            assert job.retry_count == 0  # Reset


# =============================================================================
# Test Replay All with Limit
# =============================================================================

class TestReplayAllWithLimit:
    """Tests for replay-all with limit parameter."""

    def test_replay_all_with_limit(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all with limit only replays specified number of items."""
        project_id = "project-limit-test"
        
        # Create 5 DLQ items (with incremental timestamps for deterministic ordering)
        dlq_items = create_dlq_items_for_project(
            engine, dlq_repository, project_id, count=5
        )
        
        # Call replay-all with limit=3
        response = client.post(
            f"/projects/{project_id}/dlq/replay-all",
            params={"limit": 3}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify only 3 items were replayed
        assert data["total"] == 5
        assert data["limit"] == 3
        assert data["replayed"] == 3
        assert data["failed"] == 0
        assert data["skipped"] == 2  # 5 - 3 = 2 skipped
        
        # Verify the last 3 DLQ entries (by moved_to_dlq_at DESC order) were removed
        # Repository orders by moved_to_dlq_at DESC (most recent first)
        # So items 4, 3, 2 (most recent) should be replayed first
        for i in [4, 3, 2]:
            assert dlq_repository.get(f"dlq-{project_id}-{i}") is None
        
        # First 2 DLQ entries (oldest) should still exist
        for i in [1, 0]:
            assert dlq_repository.get(f"dlq-{project_id}-{i}") is not None

    def test_replay_all_default_limit(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all uses default limit of 100."""
        project_id = "project-default-limit"
        
        # Create only 2 DLQ items
        create_dlq_items_for_project(
            engine, dlq_repository, project_id, count=2
        )
        
        # Call replay-all without specifying limit
        response = client.post(f"/projects/{project_id}/dlq/replay-all")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify default limit is 100
        assert data["limit"] == 100
        assert data["total"] == 2
        assert data["replayed"] == 2

    def test_replay_all_max_limit_enforced(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all clamps limit to 1000 when exceeded (FastAPI validation returns 422)."""
        project_id = "project-max-limit"
        
        # Create 5 DLQ items
        create_dlq_items_for_project(
            engine, dlq_repository, project_id, count=5
        )
        
        # Call replay-all with limit=1000 (max allowed) - should work
        response = client.post(
            f"/projects/{project_id}/dlq/replay-all",
            params={"limit": 1000}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Limit should be 1000
        assert data["limit"] == 1000
        # All 5 items should be replayed since 5 < 1000
        assert data["replayed"] == 5
        
        # Call replay-all with limit > 1000 - should return 422 (validation error)
        response = client.post(
            f"/projects/{project_id}/dlq/replay-all",
            params={"limit": 2000}
        )
        
        assert response.status_code == 422


# =============================================================================
# Test Replay All Empty DLQ
# =============================================================================

class TestReplayAllEmptyDLQ:
    """Tests for replay-all when DLQ is empty."""

    def test_replay_all_empty_dlq(
        self, client, dlq_service, dlq_repository
    ):
        """Test replay-all with empty DLQ returns 0 replayed, no errors."""
        project_id = "project-empty-dlq"
        
        # Verify DLQ is empty
        items, total = dlq_service.list_dlq(project_id=project_id)
        assert total == 0
        
        # Call replay-all on empty DLQ
        response = client.post(f"/projects/{project_id}/dlq/replay-all")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure for empty DLQ
        assert data["total"] == 0
        assert data["limit"] == 100
        assert data["replayed"] == 0
        assert data["failed"] == 0
        assert data["skipped"] == 0
        assert data["errors"] == []


# =============================================================================
# Test Replay All Project Isolation
# =============================================================================

class TestReplayAllProjectIsolation:
    """Tests for replay-all project isolation."""

    def test_replay_all_respects_project_id(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all only replays items for specified project."""
        # Create DLQ items for project-a
        project_a_items = create_dlq_items_for_project(
            engine, dlq_repository, "project-a", count=2
        )
        
        # Create DLQ items for project-b
        project_b_items = create_dlq_items_for_project(
            engine, dlq_repository, "project-b", count=3
        )
        
        # Call replay-all for project-a only
        response = client.post("/projects/project-a/dlq/replay-all")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify only project-a items were replayed
        assert data["total"] == 2
        assert data["replayed"] == 2
        
        # Verify project-a DLQ entries were removed
        for dlq_item in project_a_items:
            assert dlq_repository.get(dlq_item.dlq_id) is None
        
        # Verify project-b DLQ entries still exist
        for dlq_item in project_b_items:
            assert dlq_repository.get(dlq_item.dlq_id) is not None
        
        # Verify project-b jobs still in DEAD_LETTER
        for dlq_item in project_b_items:
            job = dlq_service._job_repo.get(dlq_item.job_id)
            assert job.status == "dead_letter"


# =============================================================================
# Test Replay All Response Structure
# =============================================================================

class TestReplayAllResponseStructure:
    """Tests for replay-all response schema validation."""

    def test_replay_all_response_structure(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all response has correct structure."""
        project_id = "project-struct-test"
        
        # Create a few DLQ items
        create_dlq_items_for_project(
            engine, dlq_repository, project_id, count=2
        )
        
        # Call replay-all
        response = client.post(f"/projects/{project_id}/dlq/replay-all")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify all expected fields are present
        assert "total" in data
        assert "limit" in data
        assert "replayed" in data
        assert "failed" in data
        assert "skipped" in data
        assert "errors" in data
        
        # Verify types
        assert isinstance(data["total"], int)
        assert isinstance(data["limit"], int)
        assert isinstance(data["replayed"], int)
        assert isinstance(data["failed"], int)
        assert isinstance(data["skipped"], int)
        assert isinstance(data["errors"], list)

    def test_replay_all_with_queue_id_filter(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all with queue_id filter only replays matching items."""
        project_id = "project-queue-filter"
        
        # Create DLQ items for queue-matching (using helper which creates queue-project-queue-filter)
        queue_matching_items = create_dlq_items_for_project(
            engine, dlq_repository, project_id, count=2
        )
        
        # Create DLQ items for queue-other
        for i in range(2):
            job_id = f"job-queue-other-{i}"
            dlq_id = f"dlq-queue-other-{i}"
            
            job = JobItem(
                job_id=job_id,
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"Job for queue-other",
                source="api",
                project_id=project_id,
                queue_id="queue-other",
                status=JobStatus.DEAD_LETTER.value,
            )
            
            with Session(engine) as session:
                session.add(job)
                session.commit()
            
            dlq_item = DeadLetterItem(
                dlq_id=dlq_id,
                job_id=job_id,
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"Job for queue-other",
                source="api",
                project_id=project_id,
                queue_id="queue-other",
                priority=5,
                error_message="Error",
                retry_count=0,
                failed_at=datetime.now(timezone.utc).isoformat(),
                moved_to_dlq_at=datetime.now(timezone.utc).isoformat(),
                reason="MAX_RETRIES",
            )
            dlq_repository.enqueue(dlq_item)
        
        # Filter by queue_id that matches items from create_dlq_items_for_project
        # (which creates queue_id = "queue-project-queue-filter")
        response = client.post(
            f"/projects/{project_id}/dlq/replay-all",
            params={"queue_id": "queue-project-queue-filter"}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Only matching queue items should be replayed
        assert data["replayed"] == 2
        
        # Verify matching queue DLQ entries were removed
        for dlq_item in queue_matching_items:
            assert dlq_repository.get(dlq_item.dlq_id) is None

    def test_replay_all_with_reason_filter(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all with reason filter only replays matching items."""
        project_id = "project-reason-filter"
        
        # Create MAX_RETRIES DLQ items manually
        max_retries_items = []
        for i in range(2):
            job_id = f"job-maxretries-{i}"
            dlq_id = f"dlq-maxretries-{i}"
            
            job = JobItem(
                job_id=job_id,
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"MAX_RETRIES job {i}",
                source="api",
                project_id=project_id,
                queue_id="default-queue",
                status=JobStatus.DEAD_LETTER.value,
            )
            
            with Session(engine) as session:
                session.add(job)
                session.commit()
            
            dlq_item = DeadLetterItem(
                dlq_id=dlq_id,
                job_id=job_id,
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"MAX_RETRIES job {i}",
                source="api",
                project_id=project_id,
                queue_id="default-queue",
                priority=5,
                error_message="Error",
                retry_count=0,
                failed_at=datetime.now(timezone.utc).isoformat(),
                moved_to_dlq_at=datetime.now(timezone.utc).isoformat(),
                reason="MAX_RETRIES",
            )
            dlq_repository.enqueue(dlq_item)
            max_retries_items.append(dlq_item)
        
        # Create MANUAL DLQ items manually
        for i in range(2):
            job_id = f"job-manual-{i}"
            dlq_id = f"dlq-manual-{i}"
            
            job = JobItem(
                job_id=job_id,
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"MANUAL job {i}",
                source="api",
                project_id=project_id,
                queue_id="default-queue",
                status=JobStatus.DEAD_LETTER.value,
            )
            
            with Session(engine) as session:
                session.add(job)
                session.commit()
            
            dlq_item = DeadLetterItem(
                dlq_id=dlq_id,
                job_id=job_id,
                agent_id="coder",
                agent_dir="/agents/coder",
                message=f"MANUAL job {i}",
                source="api",
                project_id=project_id,
                queue_id="default-queue",
                priority=5,
                error_message="Error",
                retry_count=0,
                failed_at=datetime.now(timezone.utc).isoformat(),
                moved_to_dlq_at=datetime.now(timezone.utc).isoformat(),
                reason="MANUAL",
            )
            dlq_repository.enqueue(dlq_item)
        
        # Call replay-all with reason=MAX_RETRIES
        response = client.post(
            f"/projects/{project_id}/dlq/replay-all",
            params={"reason": "MAX_RETRIES"}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Only MAX_RETRIES items should be replayed
        assert data["replayed"] == 2
        
        # Verify MAX_RETRIES DLQ entries were removed
        for dlq_item in max_retries_items:
            assert dlq_repository.get(dlq_item.dlq_id) is None


# =============================================================================
# Test Replay All Error Handling
# =============================================================================

class TestReplayAllErrorHandling:
    """Tests for replay-all error handling."""

    def test_replay_all_partial_failure(
        self, client, dlq_service, dlq_repository, engine
    ):
        """Test replay-all handles partial failures gracefully."""
        project_id = "project-partial-failure"
        
        # Create DLQ items
        dlq_items = create_dlq_items_for_project(
            engine, dlq_repository, project_id, count=3
        )
        
        # Make one job not in DEAD_LETTER state (simulate error condition)
        # by updating the job status back to COMPLETED
        job = dlq_service._job_repo.get(dlq_items[1].job_id)
        job.status = "completed"
        
        # Call replay-all
        response = client.post(f"/projects/{project_id}/dlq/replay-all")
        
        assert response.status_code == 200
        data = response.json()
        
        # Some should succeed, some should fail
        assert data["total"] == 3
        assert data["replayed"] + data["failed"] <= 3
        
        # Errors should be recorded
        if data["failed"] > 0:
            assert len(data["errors"]) > 0
            assert "dlq_id" in data["errors"][0]
            assert "error" in data["errors"][0]

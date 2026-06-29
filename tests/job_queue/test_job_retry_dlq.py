"""Tests for Job Retry endpoint with DEAD_LETTER handling.

This module tests the POST /api/jobs/{job_id}/retry endpoint:
- DEAD_LETTER jobs are replayed via DLQ service (job reset to PENDING)
- FAILED jobs are retried via JobQueueService (new job created)
- Invalid states return appropriate error responses
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.routers.jobs import router, set_job_queue_service, set_dead_letter_service
from daemon.services.job_queue_service import JobQueueService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.repositories.job_queue.models import (
    AdmissionState,
    DeadLetterItem,
    JobItem,
    JobStatus,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")


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
def mock_job_queue_service():
    """Create a mock JobQueueService for testing.

    Phase 1 (Job as Queue Proxy): ``MagicMock(spec=JobQueueService)``
    auto-mocks ``get_work`` (and every other async attribute) to return
    an ``AsyncMock`` — which then fails JSON serialization inside
    ``_job_to_response`` because ``record.status`` is itself an
    ``AsyncMock``. These tests exercise the retry/DLQ HTTP contract on
    the legacy JobItem-mirror path (the resolver is not wired here),
    so we explicitly pin ``get_work`` to ``None`` to force the
    ``work_record is None`` branch in ``_job_to_response`` and the
    legacy ``job.status`` fallback in ``_resolve_job_status``. See
    ``daemon/routers/jobs_management.py::_resolve_job_status`` for the
    matching ``None`` contract.
    """
    service = MagicMock(spec=JobQueueService)
    # Make get_job, get_work, and retry_job async-compatible.
    service.get_job = AsyncMock()
    # Phase 1: resolver not wired → ``get_work`` returns None. Without
    # this the auto-AsyncMock return trips ``TypeError: Object of type
    # AsyncMock is not JSON serializable`` when ``_job_to_response``
    # tries to project it onto a ``JobResponse``.
    service.get_work = AsyncMock(return_value=None)
    service.retry_job = AsyncMock()
    return service


@pytest.fixture
def test_app(mock_job_queue_service, dlq_service):
    """Create FastAPI test app with jobs router."""
    app = FastAPI()
    app.include_router(router)
    set_job_queue_service(mock_job_queue_service)
    set_dead_letter_service(dlq_service)
    yield app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


def create_dead_letter_job(engine, job_repo, dlq_repo, job_id, dlq_id, status=JobStatus.DEAD_LETTER):
    """Helper to create a job in DEAD_LETTER status with a DLQ entry."""
    now = datetime.now(timezone.utc).isoformat()
    
    # Create job in DEAD_LETTER status
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        message="Failed job message",
        source="api",
        project_id="project-abc",
        queue_id="queue-xyz",

        admission_state=status_to_admission(status.value),
        retry_count=3,
        failed_at=now,
    )
    
    # Create corresponding DLQ entry
    dlq_item = DeadLetterItem(
        dlq_id=dlq_id,
        job_id=job_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        message="Failed job message",
        source="api",
        project_id="project-abc",
        queue_id="queue-xyz",
        priority=5,
        error_message="Connection timeout after 3 retries",
        retry_count=3,
        failed_at=now,
        moved_to_dlq_at=now,
        reason="MAX_RETRIES",
    )
    
    with Session(engine) as session:
        session.add(job)
        session.commit()
    
    dlq_repo.enqueue(dlq_item)
    
    return job, dlq_item


# =============================================================================
# Test Retry DEAD_LETTER Job
# =============================================================================

class TestRetryDeadLetterJob:
    """Tests for retrying DEAD_LETTER jobs."""

    def test_retry_dead_letter_job_success(
        self, client, mock_job_queue_service, dlq_service, dlq_repository, engine
    ):
        """Test retrying a DEAD_LETTER job transitions it to PENDING and cleans up DLQ entry."""
        job_id = "dead-letter-job-123"
        dlq_id = "dlq-entry-123"
        
        # Create the job and DLQ entry
        job, dlq_item = create_dead_letter_job(
            engine, dlq_service._job_repo, dlq_repository, job_id, dlq_id
        )
        
        # Mock get_job to return the job from repository (async)
        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )

        # Call retry endpoint
        response = client.post(f"/jobs/{job_id}/retry")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        # Phase 5: ``replay_from_dlq`` transitions the job to
        # ``admission_state="queued"`` (``JobStatus.PENDING``). The
        # ``status`` field therefore reflects the *post-replay* state
        # (``"pending"``), not the pre-replay legacy ``"dead_letter"``.
        # The authoritative check that the job is now retriable is
        # ``data["admission_state"] == "queued"`` (see below).
        assert data["status"] == "pending"
        assert data["admission_state"] == AdmissionState.QUEUED.value
        assert "replay" in data["message"].lower()
        
        # Verify DLQ entry was cleaned up
        assert dlq_repository.get(dlq_id) is None
        assert dlq_repository.get_by_job_id(job_id) is None
        
        # Verify job was updated to PENDING
        updated_job = dlq_service._job_repo.get(job_id)
        assert updated_job is not None
        assert updated_job.admission_state == "queued"
        assert updated_job.retry_count == 0  # Reset to 0

    def test_retry_dead_letter_job_preserves_dlq_info_in_response(
        self, client, mock_job_queue_service, dlq_service, dlq_repository, engine
    ):
        """Test retrying a DEAD_LETTER job includes DLQ info in response."""
        job_id = "dead-letter-job-456"
        dlq_id = "dlq-entry-456"
        
        # Create the job and DLQ entry
        job, dlq_item = create_dead_letter_job(
            engine, dlq_service._job_repo, dlq_repository, job_id, dlq_id
        )
        
        # Mock get_job to return the job from repository (async)
        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )
        
        # Call retry endpoint
        response = client.post(f"/jobs/{job_id}/retry")
        
        assert response.status_code == 200
        data = response.json()
        
        # Response should include DLQ info from before replay
        assert data["dlq_reason"] == "MAX_RETRIES"
        assert data["retry_count"] == 3  # Original retry count from DLQ entry


# =============================================================================
# Test Retry FAILED Job (existing behavior)
# =============================================================================

class TestRetryFailedJob:
    """Tests for retrying FAILED jobs (Phase 5 admission vocabulary)."""

    def test_retry_failed_job_rejected_under_done_admission(
        self, client, mock_job_queue_service, dlq_service, engine
    ):
        """Phase 5 (Job-as-Queue-Proxy): a legacy ``FAILED`` job's
        ``admission_state`` collapses with ``COMPLETED``/``CANCELLED``
        into the single ``done`` admission value. The retry endpoint
        only routes through DLQ replay (``done → dead → done`` via
        ``DeadLetterService.replay_from_dlq``) for ``dead`` rows; a
        ``done`` row that was never dead-lettered has no DLQ entry to
        replay and so the endpoint returns 400 — same status code the
        legacy endpoint returned for "job is not in a retriable state".

        The pre-Phase-5 "FAILED → create new job with same params" path
        is no longer reachable: ``JobStatus.FAILED`` (``"failed"``)
        isn't a canonical value of the 4-value ``AdmissionState`` enum,
        and the router's ``ADMISSION_STATE_TO_STATUS`` fallback maps
        ``done`` → ``"completed"`` (which ``!= "failed"``). This test
        pins that contract so a future batch that wants to re-introduce
        a FAILED-retry path sees the regression here first.
        """
        job_id = "failed-job-456"

        # Seed: a legacy FAILED job (``admission_state="done"``).
        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Failed job message",
            source="api",
            project_id="project-abc",

            admission_state=status_to_admission(JobStatus.FAILED.value),
            retry_count=1,
        )

        with Session(engine) as session:
            session.add(job)
            session.commit()

        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )

        # retry_job should NOT be invoked — the FAILED-retry branch is
        # dead under the Phase 5 admission vocabulary.
        mock_job_queue_service.retry_job = AsyncMock(
            return_value=JobItem(
                job_id="new-job-789",
                agent_id="developer",
                agent_dir="/agents/developer",
                message="Failed job message",
                source="api",
                project_id="project-abc",
                admission_state=status_to_admission(JobStatus.PENDING.value),
                retry_count=0,
            )
        )

        response = client.post(f"/jobs/{job_id}/retry")

        assert response.status_code == 400
        data = response.json()
        assert "cannot be retried" in data["detail"]["error"].lower()
        # Phase 5: ``done`` admission maps to ``completed`` via
        # ``ADMISSION_STATE_TO_STATUS`` — only ``dead`` is retryable.
        assert data["detail"]["current_status"] == "completed"

        # The dead FAILED-retry branch must NOT have been invoked.
        mock_job_queue_service.retry_job.assert_not_called()


# =============================================================================
# Test Retry Non-Existent Job
# =============================================================================

class TestRetryNonexistentJob:
    """Tests for retrying non-existent jobs."""

    def test_retry_nonexistent_job(
        self, client, mock_job_queue_service, dlq_service
    ):
        """Test retrying a non-existent job returns 404."""
        mock_job_queue_service.get_job = AsyncMock(return_value=None)
        
        response = client.post("/jobs/nonexistent-job-id/retry")
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"]["error"].lower()


# =============================================================================
# Test Retry Invalid States
# =============================================================================

class TestRetryInvalidStates:
    """Tests for retrying jobs in invalid states."""

    def test_retry_job_in_invalid_state_completed(
        self, client, mock_job_queue_service, dlq_service, engine
    ):
        """Test retrying a COMPLETED job returns 400."""
        job_id = "completed-job-123"
        
        # Create job in COMPLETED status
        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Completed job",
            source="api",
            project_id="project-abc",

            admission_state=status_to_admission(JobStatus.COMPLETED.value),
        )

        with Session(engine) as session:
            session.add(job)
            session.commit()

        # Mock get_job to return the job (async) - fetch from repo to avoid detached
        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )

        response = client.post(f"/jobs/{job_id}/retry")

        assert response.status_code == 400
        data = response.json()
        assert "cannot be retried" in data["detail"]["error"].lower()
        # Phase 5: a legacy FAILED job's admission_state="done"
        # collapses to the canonical JobStatus.COMPLETED ("completed")
        # — only DEAD_LETTER ("dead") is retryable via this endpoint
        # under the 4-value admission vocabulary.
        assert data["detail"]["current_status"] == "completed"

    def test_retry_job_in_invalid_state_processing(
        self, client, mock_job_queue_service, dlq_service, engine
    ):
        """Test retrying a PROCESSING job returns 400."""
        job_id = "processing-job-456"
        
        # Create job in PROCESSING status (legacy "processing" → "active")
        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Processing job",
            source="api",
            project_id="project-abc",

            admission_state=status_to_admission(JobStatus.PROCESSING.value),
        )

        with Session(engine) as session:
            session.add(job)
            session.commit()

        # Mock get_job to return the job (async) - fetch from repo to avoid detached
        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )

        response = client.post(f"/jobs/{job_id}/retry")

        assert response.status_code == 400
        data = response.json()
        assert "cannot be retried" in data["detail"]["error"].lower()
        assert data["detail"]["current_status"] == "processing"

    def test_retry_job_in_invalid_state_pending(
        self, client, mock_job_queue_service, dlq_service, engine
    ):
        """Test retrying a PENDING job returns 400."""
        job_id = "pending-job-789"

        # Create job in PENDING status (legacy "pending" → "queued")
        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Pending job",
            source="api",
            project_id="project-abc",

            admission_state=status_to_admission(JobStatus.PENDING.value),
        )

        with Session(engine) as session:
            session.add(job)
            session.commit()

        # Mock get_job to return the job (async) - fetch from repo to avoid detached
        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )

        response = client.post(f"/jobs/{job_id}/retry")

        assert response.status_code == 400
        data = response.json()
        assert "cannot be retried" in data["detail"]["error"].lower()
        # Phase 5: a legacy PENDING job's admission_state="queued"
        # surfaces as the canonical JobStatus.PENDING ("pending")
        # under the 4-value admission vocabulary.
        assert data["detail"]["current_status"] == "pending"


# =============================================================================
# Test Retry DEAD_LETTER without DLQ Entry
# =============================================================================

class TestRetryDeadLetterWithoutDLQEntry:
    """Tests for edge case: DEAD_LETTER job without DLQ entry."""

    def test_retry_dead_letter_job_no_dlq_entry(
        self, client, mock_job_queue_service, dlq_service, engine
    ):
        """Test retrying a DEAD_LETTER job without DLQ entry returns 422."""
        job_id = "dead-letter-no-dlq-entry"
        
        # Create a job in DEAD_LETTER status but without DLQ entry
        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Dead letter job without DLQ entry",
            source="api",
            project_id="project-abc",

            admission_state=status_to_admission(JobStatus.DEAD_LETTER.value),
            retry_count=0,
        )
        
        with Session(engine) as session:
            session.add(job)
            session.commit()
        
        # Mock get_job to return the job (async) - fetch from repo to avoid detached
        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )
        
        # Call retry endpoint
        response = client.post(f"/jobs/{job_id}/retry")
        
        assert response.status_code == 422
        data = response.json()
        # Error message contains "DEAD_LETTER entry not found"
        assert "dead_letter" in data["detail"]["error"].lower()
        assert data["detail"]["job_id"] == job_id


# =============================================================================
# Test Retry Response Structure
# =============================================================================

class TestRetryResponseStructure:
    """Tests for retry response schema validation."""

    def test_retry_response_includes_job_fields(
        self, client, mock_job_queue_service, dlq_service, dlq_repository, engine
    ):
        """Test retry response includes all expected job fields."""
        job_id = "dead-letter-job-struct"
        dlq_id = "dlq-entry-struct"
        
        # Create the job and DLQ entry
        job, dlq_item = create_dead_letter_job(
            engine, dlq_service._job_repo, dlq_repository, job_id, dlq_id
        )
        
        # Mock get_job to return the job from repository (async)
        mock_job_queue_service.get_job = AsyncMock(
            side_effect=lambda jid: dlq_service._job_repo.get(jid)
        )
        
        # Call retry endpoint
        response = client.post(f"/jobs/{job_id}/retry")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify all expected fields are present
        assert "job_id" in data
        assert "status" in data
        assert "agent_id" in data
        assert "message" in data
        assert "project_id" in data
        assert "retry_count" in data

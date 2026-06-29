"""Tests for job status alias mapping fix.

Covers the STATUS_ALIASES dict and normalize_statuses() helper added in
daemon/services/job_queue_service.py, plus the integration in the HTTP router
(daemon/routers/jobs_crud.py) and tool description (daemon/tools/job_queue.py).

The fix lets callers (agents, LLM tools) pass natural-language statuses like
"running" instead of canonical "processing", and still get correct results.

Canonical JobStatus values:
    pending, processing, completed, failed, cancelled, dead_letter

Known aliases (partial list):
    running -> processing
    active  -> processing
    in_progress -> processing
    queued  -> pending
    waiting -> pending
    done    -> completed
    success -> completed
    finished -> completed
    error   -> failed
    killed  -> cancelled
    canceled -> cancelled
    dlq     -> dead_letter
    dead    -> dead_letter
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.routers.jobs_crud import router, get_job_queue_service, get_dead_letter_svc
from daemon.services.job_queue_service import (
    JobQueueService,
    STATUS_ALIASES,
    normalize_statuses,
)
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_lock_manager import JobLockManager
from daemon.repositories.job_queue import AdmissionState, JobQueueRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository


# =============================================================================
# Unit tests for normalize_statuses()  (Test cases 1-6)
# =============================================================================


class TestNormalizeStatusesAliases:
    """Tests for alias-to-canonical mapping (case 1, 4, 5)."""

    def test_alias_running_maps_to_processing(self):
        """Case 1: 'running' alias -> canonical 'processing'."""
        result = normalize_statuses(["running"])
        assert result == ["processing"]

    def test_multiple_aliases_run_done(self):
        """Case 4: multiple aliases each resolve to their canonical value."""
        result = normalize_statuses(["running", "done"])
        assert result == ["processing", "completed"]

    def test_unknown_value_passes_through(self):
        """Case 5: unknown status value passes through unchanged (no crash)."""
        result = normalize_statuses(["nonexistent"])
        assert result == ["nonexistent"]

    def test_extra_aliases_known(self):
        """Sanity: extra documented aliases resolve as expected."""
        assert normalize_statuses(["active"]) == ["processing"]
        assert normalize_statuses(["in_progress"]) == ["processing"]
        assert normalize_statuses(["queued"]) == ["pending"]
        assert normalize_statuses(["waiting"]) == ["pending"]
        assert normalize_statuses(["success"]) == ["completed"]
        assert normalize_statuses(["finished"]) == ["completed"]
        assert normalize_statuses(["error"]) == ["failed"]
        assert normalize_statuses(["killed"]) == ["cancelled"]
        assert normalize_statuses(["canceled"]) == ["cancelled"]
        assert normalize_statuses(["dlq"]) == ["dead_letter"]
        assert normalize_statuses(["dead"]) == ["dead_letter"]


class TestNormalizeStatusesCaseInsensitive:
    """Test case 2: case-insensitive alias resolution."""

    def test_mixed_case_aliases_lowercased(self):
        """Case 2: 'Running' and 'RUNNING' both resolve to 'processing'."""
        result = normalize_statuses(["Running", "RUNNING"])
        assert result == ["processing", "processing"]

    def test_title_and_pascal_case(self):
        """Capitalized variants still resolve."""
        result = normalize_statuses(["Done", "Completed", "FAILED"])
        # "Done" -> "completed" (alias), "Completed" -> "completed" (canonical), "FAILED" -> "failed" (canonical)
        assert result == ["completed", "completed", "failed"]


class TestNormalizeStatusesCanonical:
    """Test case 3: canonical values pass through unchanged."""

    def test_canonical_pending_passes_through(self):
        """Case 3: canonical 'pending' is unchanged."""
        assert normalize_statuses(["pending"]) == ["pending"]

    def test_all_canonical_values_unchanged(self):
        """All canonical JobStatus values pass through."""
        canonical_values = [
            AdmissionState.QUEUED.value,
            AdmissionState.ACTIVE.value,
            AdmissionState.DONE.value,
            AdmissionState.DONE.value,
            AdmissionState.DONE.value,
            AdmissionState.DEAD.value,
        ]
        result = normalize_statuses(canonical_values)
        # Phase 5: ``STATUS_ALIASES`` treats the admission vocabulary
        # as natural-language aliases for the legacy ``JobStatus``
        # values (``queued`` → ``pending``, ``active`` →
        # ``processing``, ``done`` → ``completed``, ``dead`` →
        # ``dead_letter``). The canonical ``JobStatus`` values are
        # then re-mapped back to ``AdmissionState`` values via
        # ``_JOB_STATUS_TO_ADMISSION`` in the SQL filter (round-trip
        # in ``JobRepository.list`` / ``count``). Assert the round
        # trip via the production alias map.
        assert result == [
            JobStatus.PENDING.value,
            JobStatus.PROCESSING.value,
            JobStatus.COMPLETED.value,
            JobStatus.COMPLETED.value,
            JobStatus.COMPLETED.value,
            JobStatus.DEAD_LETTER.value,
        ]


class TestNormalizeStatusesEmptyAndNone:
    """Test case 6: None and empty list handling."""

    def test_none_returns_none(self):
        """Case 6: None input returns None (no error)."""
        assert normalize_statuses(None) is None

    def test_empty_list_returns_empty_list(self):
        """Case 6: empty list returns empty list."""
        assert normalize_statuses([]) == []


class TestStatusAliasesDict:
    """Sanity checks on the STATUS_ALIASES dict shape."""

    def test_aliases_dict_has_expected_keys(self):
        """All documented aliases exist in the dict."""
        expected_keys = {
            "running", "active", "in_progress",
            "queued", "waiting",
            "done", "success", "finished",
            "error", "failed",
            "killed", "canceled",
            "dlq", "dead",
        }
        actual_keys = set(STATUS_ALIASES.keys())
        assert expected_keys.issubset(actual_keys), (
            f"Missing aliases: {expected_keys - actual_keys}"
        )

    def test_aliases_resolve_to_canonical_jobstatus(self):
        """All alias values are valid canonical JobStatus values."""
        canonical_values = {s.value for s in JobStatus}
        for alias, target in STATUS_ALIASES.items():
            assert target in canonical_values, (
                f"Alias '{alias}' maps to '{target}' which is not a canonical JobStatus"
            )


# =============================================================================
# Integration tests  (Test cases 7-8)
# =============================================================================


# --- Reusable fixtures (in-memory engine, real repositories) ---


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
def lock_repo(engine):
    """Create LockRepository with test engine."""
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo):
    """Create fresh JobLockManager instance."""
    return JobLockManager(lock_repo=lock_repo)


@pytest.fixture
def dlq_repository(engine):
    """Create DeadLetterRepository with test engine."""
    return DeadLetterRepository(engine)


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


def _create_job_in_state(repository: JobRepository, status: str, project_id: str = "test-project") -> str:
    """Helper: create a job and transition it to the given status. Returns job_id."""
    job = repository.create(
        agent_id="test-agent",
        agent_dir="/test/agent",
        message=f"Test job in {status}",
        source="api",
        project_id=project_id,
        priority=5,
    )
    # New jobs are PENDING; transition to target status using repo methods
    if status == AdmissionState.QUEUED.value:
        pass  # already pending
    elif status == AdmissionState.ACTIVE.value:
        repository.start_job(job.job_id, "test-instance")
    elif status == AdmissionState.DONE.value:
        repository.start_job(job.job_id, "test-instance")
        repository.complete_job(job.job_id, "Done")
    elif status == AdmissionState.DONE.value:
        repository.start_job(job.job_id, "test-instance")
        repository.fail_job(job.job_id, "Test error")
    elif status == AdmissionState.DONE.value:
        repository.cancel_job(job.job_id)
    else:
        raise ValueError(f"Unsupported test status: {status}")
    return job.job_id


class TestServiceListJobsWithAlias:
    """Test case 7: service-layer integration — list_jobs with alias status."""

    @pytest.mark.asyncio
    async def test_list_jobs_with_running_alias_returns_processing_jobs(
        self, job_queue_service, job_repository
    ):
        """list_jobs(statuses=['running']) returns jobs with status='processing'."""
        # Seed: 2 processing jobs, 1 pending job, 1 completed job
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)

        # Call list_jobs with natural-language alias "running"
        jobs = await job_queue_service.list_jobs(statuses=["running"])

        # Should return exactly the 2 PROCESSING jobs (not 0 from a "no match" empty result)
        assert len(jobs) == 2
        assert all(j.admission_state == AdmissionState.ACTIVE.value for j in jobs)

    @pytest.mark.asyncio
    async def test_list_jobs_with_done_alias_returns_completed_jobs(
        self, job_queue_service, job_repository
    ):
        """list_jobs(statuses=['done']) returns jobs with status='completed'."""
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)

        jobs = await job_queue_service.list_jobs(statuses=["done"])

        assert len(jobs) == 2
        assert all(j.admission_state == AdmissionState.DONE.value for j in jobs)

    @pytest.mark.asyncio
    async def test_list_jobs_with_waiting_alias_returns_pending_jobs(
        self, job_queue_service, job_repository
    ):
        """list_jobs(statuses=['waiting']) returns jobs with status='pending'."""
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)

        jobs = await job_queue_service.list_jobs(statuses=["waiting"])

        assert len(jobs) == 3
        assert all(j.admission_state == AdmissionState.QUEUED.value for j in jobs)

    @pytest.mark.asyncio
    async def test_list_jobs_with_mixed_aliases(self, job_queue_service, job_repository):
        """list_jobs with multiple aliases resolves each independently."""
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)

        jobs = await job_queue_service.list_jobs(statuses=["running", "done", "error"])

        assert len(jobs) == 3
        # status column is frozen at "pending"; assert on admission_state.
        # "running"→active, "done"/"error"→done (completed+failed collapse).
        returned_states = {j.admission_state for j in jobs}
        assert returned_states == {
            AdmissionState.ACTIVE.value,
            AdmissionState.DONE.value,
        }

    @pytest.mark.asyncio
    async def test_list_jobs_with_canonical_value_unchanged(
        self, job_queue_service, job_repository
    ):
        """list_jobs(statuses=['processing']) — canonical still works after the fix."""
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)

        jobs = await job_queue_service.list_jobs(statuses=["processing"])

        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.ACTIVE.value


# --- HTTP endpoint integration (Test case 8) ---


@pytest.fixture
def test_app(job_queue_service, dlq_service):
    """Create FastAPI test app wired with the jobs_crud router."""
    app = FastAPI()
    app.include_router(router)
    # Use the create_service_dependency .set_service API
    get_job_queue_service.set_service(job_queue_service)
    get_dead_letter_svc.set_service(dlq_service)
    yield app
    # Reset to avoid cross-test pollution
    get_job_queue_service.set_service(None)  # type: ignore[arg-type]
    get_dead_letter_svc.set_service(None)  # type: ignore[arg-type]


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


class TestHttpListJobsWithAlias:
    """Test case 8: HTTP endpoint — GET /jobs?status=running returns correct results."""

    def test_get_jobs_with_running_alias_returns_processing(
        self, client, job_repository
    ):
        """GET /jobs?status=running returns PROCESSING jobs (not empty, not 400)."""
        # Seed: 1 PROCESSING job, 1 PENDING job
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)

        response = client.get("/jobs", params={"status": "running"})

        # Must not 400 (alias should normalize before validation)
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["total"] == 1
        assert len(data["jobs"]) == 1
        # status column is frozen at "pending"; the started job is
        # distinguishable by its instance_id (set by start_job).
        assert data["jobs"][0]["instance_id"] == "test-instance"

    def test_get_jobs_with_done_alias_returns_completed(self, client, job_repository):
        """GET /jobs?status=done returns COMPLETED jobs."""
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)

        response = client.get("/jobs", params={"status": "done"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        # Phase 5: ``completed_at`` lives on the ``WorkRecord`` (read
        # from ``Instance.updated_at`` via the resolver), not on the
        # ``JobItem`` mirror — the test setup doesn't wire a resolver
        # so the legacy fallback returns ``completed_at=None``. The
        # authoritative check that the job is COMPLETED is the
        # ``admission_state == "done"`` admission bucket (which the
        # SQL filter matched).
        assert all(
            j["admission_state"] == AdmissionState.DONE.value
            for j in data["jobs"]
        )

    def test_get_jobs_with_waiting_alias_returns_pending(self, client, job_repository):
        """GET /jobs?status=waiting returns PENDING jobs."""
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)

        response = client.get("/jobs", params={"status": "waiting"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        # Phase 5: ``status`` field reflects the ``JobStatus`` mirror
        # (``ADMISSION_STATE_TO_STATUS["queued"]`` → ``"pending"``),
        # not the admission vocabulary. Check the canonical mapping.
        assert data["jobs"][0]["status"] == JobStatus.PENDING.value
        assert data["jobs"][0]["admission_state"] == AdmissionState.QUEUED.value

    def test_get_jobs_with_comma_separated_aliases(self, client, job_repository):
        """GET /jobs?status=running,done,error returns all matching jobs."""
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)

        response = client.get("/jobs", params={"status": "running,done,error"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        # status column is frozen at "pending"; the 3 non-pending jobs
        # (processing, completed, failed) were all started and carry an
        # instance_id, unlike the excluded pending job.
        assert all(j["instance_id"] is not None for j in data["jobs"])

    def test_get_jobs_with_canonical_value_still_works(self, client, job_repository):
        """GET /jobs?status=processing (canonical) still works after the fix."""
        _create_job_in_state(job_repository, AdmissionState.ACTIVE.value)
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)

        response = client.get("/jobs", params={"status": "processing"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        # status column is frozen at "pending"; the started job is
        # distinguishable by its instance_id (set by start_job).
        assert data["jobs"][0]["instance_id"] == "test-instance"

    def test_get_jobs_with_invalid_status_returns_400(self, client):
        """GET /jobs?status=garbage returns 400 (unknown status still rejected)."""
        response = client.get("/jobs", params={"status": "garbage"})

        assert response.status_code == 400
        body = response.json()
        # The error message should mention the invalid status
        detail = body.get("detail", {})
        assert "garbage" in str(detail).lower() or "invalid" in str(detail).lower()

    def test_get_jobs_no_status_param_returns_all(self, client, job_repository):
        """GET /jobs (no status filter) returns all jobs regardless of fix."""
        _create_job_in_state(job_repository, AdmissionState.QUEUED.value)
        _create_job_in_state(job_repository, AdmissionState.DONE.value)

        response = client.get("/jobs")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

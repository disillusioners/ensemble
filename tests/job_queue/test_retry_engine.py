"""Tests for JobRetryEngine.

This module tests the JobRetryEngine including:
- Exponential backoff calculation
- Retry decision logic
- Retry execution
- Dead letter queue integration
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session as SQLModelSession

from daemon.config import JobSystemConfig
from daemon.repositories.job_queue import JobItem, JobRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_retry_engine import JobRetryEngine


# =============================================================================
# Fixtures (leveraging patterns from conftest.py)
# =============================================================================

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
def job_repo(engine):
    """Create JobRepository with test engine."""
    return JobRepository(engine)


@pytest.fixture
def queue_repo(engine):
    """Create JobQueueRepository with test engine."""
    return JobQueueRepository(engine)


@pytest.fixture
def dlq_repo(engine):
    """Create DeadLetterRepository with test engine."""
    return DeadLetterRepository(engine)


@pytest.fixture
def dlq_service(job_repo, dlq_repo):
    """Create DeadLetterService."""
    return DeadLetterService(
        job_repository=job_repo,
        dlq_repository=dlq_repo,
    )


@pytest.fixture
def default_config():
    """Create default JobSystemConfig for retry tests."""
    return JobSystemConfig(
        default_max_retries=3,
        retry_backoff_base_seconds=60,
        retry_backoff_max_seconds=3600,
        retry_backoff_multiplier=2.0,
        dlq_enabled=True,
    )


@pytest.fixture
def retry_engine(job_repo, queue_repo, dlq_service, default_config):
    """Create JobRetryEngine with test dependencies."""
    return JobRetryEngine(
        job_repo=job_repo,
        queue_repo=queue_repo,
        dlq_service=dlq_service,
        config=default_config,
    )


def create_job_in_session(engine, **kwargs) -> JobItem:
    """Helper to create a job directly in the database with specific fields.

    Phase 3: when ``status`` is supplied, also compute and set the
    corresponding ``admission_state`` via :func:`status_to_admission`.
    Production code keeps the two columns in sync via the dual-write in
    atomic_transition / atomic_retry / start_job — this helper bypasses
    those paths and must maintain the invariant itself so the new
    ``admission_state``-based queries see the correct state.

    Args:
        engine: SQLAlchemy engine
        **kwargs: Fields to set on the JobItem

    Returns:
        The created JobItem
    """
    from daemon.repositories.job_queue.models import status_to_admission

    defaults = {
        "agent_id": "test-agent",
        "agent_dir": "/agents/test-agent",
        "message": "Test message",
        "source": "test",
        "status": JobStatus.PENDING.value,
        # admission_state mirrors status via the dual-write helper. The
        # JobItem model default is QUEUED, so when callers pass an
        # explicit status we must compute the corresponding admission
        # state here — otherwise the row carries status='failed' but
        # admission_state='queued' and the find_retryable_jobs query
        # (which filters on admission_state='queued') would pick it up.
        "admission_state": status_to_admission(JobStatus.PENDING.value),
        "priority": 5,
        "retry_count": 0,
        "project_id": "test-project",  # Required for move_to_dlq
        "queue_id": "queue-123",  # Required for move_to_dlq
    }
    defaults.update(kwargs)

    # If the caller overrode status but didn't override admission_state,
    # keep the two columns in sync (mirrors production dual-write).
    if "status" in kwargs and "admission_state" not in kwargs:
        defaults["admission_state"] = status_to_admission(kwargs["status"])

    # Ensure required fields
    if "job_id" not in defaults:
        import uuid
        defaults["job_id"] = str(uuid.uuid4())

    job = JobItem(**defaults)

    with SQLModelSession(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)

    return job


# =============================================================================
# Test Class: TestCalculateBackoff
# =============================================================================

class TestCalculateBackoff:
    """Tests for JobRetryEngine.calculate_backoff() method."""

    def test_backoff_exponential_growth(self, retry_engine, default_config):
        """Test that delay grows exponentially with retry_count."""
        # With base=60, multiplier=2.0:
        # retry 0: 60 * 2^0 = 60
        # retry 1: 60 * 2^1 = 120
        # retry 2: 60 * 2^2 = 240
        # retry 3: 60 * 2^3 = 480
        
        delays = []
        for retry_count in range(5):
            delay = retry_engine.calculate_backoff(retry_count, default_config)
            delays.append(delay)
        
        # Each delay should roughly double with exponential growth
        # We test that the base (without jitter) follows exponential pattern
        # retry 0: ~60, retry 1: ~120, retry 2: ~240, retry 3: ~480
        # With jitter, the comparison is less strict, but we verify trend
        assert delays[1] >= delays[0], "Delay should increase with retry count"
        assert delays[2] >= delays[1], "Delay should increase with retry count"
        assert delays[3] >= delays[2], "Delay should increase with retry count"
        
        # Verify exponential pattern holds (base * 2^count should be approximately met)
        # For retry_count=3, base=60, multiplier=2: expect ~480
        # With max jitter of 30, delay should be between 480 and 510
        assert 480 <= delays[3] <= 510, f"Expected delay ~480 for retry 3, got {delays[3]}"

    def test_backoff_jitter_range(self, retry_engine, default_config):
        """Test that jitter is in valid range [0, base * 0.5]."""
        # Run multiple times and collect values for retry_count=0
        base = default_config.retry_backoff_base_seconds
        max_jitter = base * 0.5  # 30 seconds
        
        delays = [retry_engine.calculate_backoff(0, default_config) for _ in range(20)]
        
        # Minimum should be base (no jitter)
        # Maximum should be base + max_jitter
        for delay in delays:
            assert base <= delay <= base + max_jitter

    def test_backoff_capped_at_max_delay(self, retry_engine, default_config):
        """Test that delay is capped at max_delay."""
        max_delay = default_config.retry_backoff_max_seconds  # 3600
        
        # Even with high retry_count, should not exceed max
        for retry_count in [8, 10, 15, 20, 100]:
            delay = retry_engine.calculate_backoff(retry_count, default_config)
            assert delay <= max_delay
            assert delay == max_delay

    def test_backoff_with_custom_config(self):
        """Test backoff with custom configuration values."""
        custom_config = JobSystemConfig(
            default_max_retries=5,
            retry_backoff_base_seconds=30,
            retry_backoff_max_seconds=600,
            retry_backoff_multiplier=3.0,
            dlq_enabled=True,
        )
        
        engine = JobRetryEngine(
            job_repo=MagicMock(),
            queue_repo=MagicMock(),
            dlq_service=MagicMock(),
            config=custom_config,
        )
        
        # With base=30, multiplier=3.0:
        # retry 0: 30 * 3^0 = 30
        # retry 1: 30 * 3^1 = 90
        delay = engine.calculate_backoff(1, custom_config)
        
        # Should be around 90 (plus jitter up to 15)
        assert 90 <= delay <= 105

    def test_backoff_uses_instance_config_as_fallback(self):
        """Test that calculate_backoff uses instance config when no config passed."""
        custom_config = JobSystemConfig(
            retry_backoff_base_seconds=100,
            retry_backoff_max_seconds=1000,
            retry_backoff_multiplier=1.5,
        )
        
        engine = JobRetryEngine(
            job_repo=MagicMock(),
            queue_repo=MagicMock(),
            dlq_service=MagicMock(),
            config=custom_config,
        )
        
        # When no config passed, should use instance config
        delay = engine.calculate_backoff(0)  # No config passed
        assert 100 <= delay <= 150  # base + jitter


# =============================================================================
# Test Class: TestShouldRetry
# =============================================================================

class TestShouldRetry:
    """Tests for JobRetryEngine.should_retry() method."""

    def test_should_retry_true_when_failed_and_under_limit(self, retry_engine, engine):
        """Test returns True when FAILED and retry_count < max_retries."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.FAILED.value,
            retry_count=1,
            max_retries=3,
        )
        
        result = retry_engine.should_retry(job)
        
        assert result is True

    def test_should_retry_false_when_retry_count_at_limit(self, retry_engine, engine):
        """Test returns False when retry_count >= max_retries."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.FAILED.value,
            retry_count=3,
            max_retries=3,  # Exhausted
        )
        
        result = retry_engine.should_retry(job)
        
        assert result is False

    def test_should_retry_false_when_retry_count_exceeds_limit(self, retry_engine, engine):
        """Test returns False when retry_count > max_retries."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.FAILED.value,
            retry_count=5,
            max_retries=3,
        )
        
        result = retry_engine.should_retry(job)
        
        assert result is False

    def test_should_retry_false_when_not_failed(self, retry_engine, engine):
        """Test returns False when job not in FAILED state."""
        for status in [JobStatus.PENDING, JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.CANCELLED]:
            job = create_job_in_session(
                engine,
                job_id=f"job-{status.value}",
                status=status.value,
                retry_count=0,
                max_retries=3,
            )
            
            result = retry_engine.should_retry(job)
            
            assert result is False, f"Expected False for status={status.value}"

    def test_should_retry_false_when_dlq_disabled(self, engine, job_repo, queue_repo, dlq_service):
        """Test returns False when DLQ is disabled in config."""
        config = JobSystemConfig(
            dlq_enabled=False,
            default_max_retries=3,
            retry_backoff_base_seconds=60,
            retry_backoff_max_seconds=3600,
            retry_backoff_multiplier=2.0,
        )
        retry_engine = JobRetryEngine(job_repo, queue_repo, dlq_service, config)
        
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.FAILED.value,
            retry_count=0,
        )
        
        result = retry_engine.should_retry(job)
        
        assert result is False

    def test_should_retry_false_when_max_retries_zero(self, retry_engine, engine):
        """Test returns False when max_retries=0."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.FAILED.value,
            retry_count=0,
            max_retries=0,  # Explicitly disabled
        )
        
        result = retry_engine.should_retry(job)
        
        assert result is False


# =============================================================================
# Test Class: TestGetMaxRetries
# =============================================================================

class TestGetMaxRetries:
    """Tests for JobRetryEngine.get_max_retries() method."""

    def test_get_max_retries_from_job(self, retry_engine, engine):
        """Test job.max_retries takes precedence."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            max_retries=5,
        )
        
        max_retries = retry_engine.get_max_retries(job)
        
        assert max_retries == 5

    def test_get_max_retries_falls_back_to_queue(self, retry_engine, queue_repo, engine):
        """Test falls back to queue.default_max_retries when job.max_retries is None."""
        # Create queue with custom max_retries directly in the engine's queue_repo
        from daemon.repositories.job_queue.models import JobQueue
        
        queue_obj = JobQueue(
            queue_id="queue-123",
            project_id="project-abc",
            queue_name="test-queue",
            queue_name_lower="test-queue",
            default_max_retries=7,
        )
        with SQLModelSession(engine) as session:
            session.add(queue_obj)
            session.commit()
        
        # Create job without max_retries but with queue_id
        job = create_job_in_session(
            engine,
            job_id="job-123",
            max_retries=None,
            queue_id="queue-123",
        )
        
        # Pass any non-None value for queue parameter to trigger queue lookup
        # The engine uses self._queue_repo.get() internally
        max_retries = retry_engine.get_max_retries(job, queue=queue_repo)
        
        assert max_retries == 7

    def test_get_max_retries_falls_back_to_config(self, retry_engine, engine):
        """Test falls back to config.default_max_retries when job.max_retries is None."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            max_retries=None,
        )
        
        max_retries = retry_engine.get_max_retries(job)
        
        # Should fall back to config default of 3
        assert max_retries == 3

    def test_get_max_retries_hard_cap_100(self, retry_engine, engine):
        """Test hard cap at 100."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            max_retries=150,  # Over the cap
        )
        
        max_retries = retry_engine.get_max_retries(job)
        
        assert max_retries == 100


# =============================================================================
# Test Class: TestMaybeRetry
# =============================================================================

class TestMaybeRetry:
    """Tests for JobRetryEngine.maybe_retry() method."""

    def test_maybe_retry_retry_path(self, retry_engine, engine):
        """Test FAILED job with retries left transitions to PENDING."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.FAILED.value,
            retry_count=1,
            max_retries=3,
            error_message="Connection timeout",
            failed_at=datetime.utcnow().isoformat(),
        )
        
        result = retry_engine.maybe_retry("job-123")
        
        assert result is not None
        assert result.status == JobStatus.PENDING.value
        assert result.retry_count == 2  # Incremented
        assert result.next_retry_at is not None
        assert result.error_message is None  # Cleared
        assert result.failed_at is None  # Cleared

    def test_maybe_retry_dlq_path(self, retry_engine, dlq_repo, engine):
        """Test FAILED job with no retries moves to DEAD_LETTER."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.FAILED.value,
            retry_count=3,  # Exhausted
            max_retries=3,
            queue_id="queue-123",
            error_message="Connection timeout",
            failed_at=datetime.utcnow().isoformat(),
        )
        
        result = retry_engine.maybe_retry("job-123")
        
        # Should return None (moved to DLQ)
        assert result is None
        
        # Verify job was moved to DLQ
        dlq_item = dlq_repo.get_by_job_id("job-123")
        assert dlq_item is not None
        assert dlq_item.reason == "MAX_RETRIES"

    def test_maybe_retry_returns_none_for_pending_job(self, retry_engine, engine):
        """Test returns None for non-FAILED jobs (PENDING)."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.PENDING.value,
        )
        
        result = retry_engine.maybe_retry("job-123")
        
        assert result is None

    def test_maybe_retry_returns_none_for_completed_job(self, retry_engine, engine):
        """Test returns None for COMPLETED jobs."""
        job = create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.COMPLETED.value,
        )
        
        result = retry_engine.maybe_retry("job-123")
        
        assert result is None

    def test_maybe_retry_returns_none_for_nonexistent_job(self, retry_engine):
        """Test returns None for non-existent jobs."""
        result = retry_engine.maybe_retry("non-existent-job")
        
        assert result is None

    def test_maybe_retry_preserves_job_id(self, retry_engine, engine):
        """Test that retry preserves the original job_id."""
        job = create_job_in_session(
            engine,
            job_id="job-specific-id",
            status=JobStatus.FAILED.value,
            retry_count=0,
            max_retries=3,
        )
        
        result = retry_engine.maybe_retry("job-specific-id")
        
        assert result is not None
        assert result.job_id == "job-specific-id"


# =============================================================================
# Test Class: TestFindRetryableJobs
# =============================================================================

class TestFindRetryableJobs:
    """Tests for JobRetryEngine.find_retryable_jobs() method."""

    def test_find_retryable_jobs_past_due(self, retry_engine, job_repo, engine):
        """Test finds jobs with next_retry_at in the past.

        Phase 3: under the new model, a "retryable" job is one that
        atomic_retry has already scheduled (status='pending',
        admission_state='queued', next_retry_at set) and whose retry
        window has passed. The old model used status='failed' here.
        """
        past_time = datetime.utcnow() - timedelta(hours=1)
        create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.PENDING.value,  # Phase 3: post-atomic_retry state
            retry_count=1,
            max_retries=3,
            next_retry_at=past_time.isoformat(),
        )

        results = retry_engine.find_retryable_jobs()

        assert len(results) == 1
        assert results[0].job_id == "job-123"

    def test_find_retryable_jobs_excludes_future(self, retry_engine, engine):
        """Test jobs with future next_retry_at are excluded."""
        future_time = datetime.utcnow() + timedelta(hours=1)
        create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.PENDING.value,  # Phase 3
            retry_count=1,
            max_retries=3,
            next_retry_at=future_time.isoformat(),
        )

        results = retry_engine.find_retryable_jobs()

        assert len(results) == 0

    def test_find_retryable_jobs_with_project_filter(self, retry_engine, engine):
        """Test filtering retryable jobs by project_id."""
        past_time = datetime.utcnow() - timedelta(hours=1)

        create_job_in_session(
            engine,
            job_id="job-1",
            project_id="project-a",
            status=JobStatus.PENDING.value,  # Phase 3
            retry_count=1,
            next_retry_at=past_time.isoformat(),
        )
        create_job_in_session(
            engine,
            job_id="job-2",
            project_id="project-b",
            status=JobStatus.PENDING.value,  # Phase 3
            retry_count=1,
            next_retry_at=past_time.isoformat(),
        )

        results = retry_engine.find_retryable_jobs(project_id="project-a")

        assert len(results) == 1
        assert results[0].job_id == "job-1"

    def test_find_retryable_jobs_excludes_non_failed(self, retry_engine, engine):
        """Test that jobs in a non-queued admission state are excluded.

        Phase 3: COMPLETED maps to admission_state='done' (terminal),
        so it is correctly excluded from find_retryable_jobs.
        """
        past_time = datetime.utcnow() - timedelta(hours=1)
        create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.COMPLETED.value,  # Not retry-eligible (terminal)
            retry_count=1,
            next_retry_at=past_time.isoformat(),
        )

        results = retry_engine.find_retryable_jobs()

        assert len(results) == 0

    def test_find_retryable_jobs_excludes_without_next_retry_at(self, retry_engine, engine):
        """Test that jobs without next_retry_at are excluded."""
        create_job_in_session(
            engine,
            job_id="job-123",
            status=JobStatus.PENDING.value,  # Phase 3
            retry_count=1,
            next_retry_at=None,  # No retry scheduled
        )

        results = retry_engine.find_retryable_jobs()

        assert len(results) == 0

    def test_find_retryable_jobs_empty_database(self, retry_engine, engine):
        """Test returns empty list when no jobs exist."""
        results = retry_engine.find_retryable_jobs()

        assert results == []

    def test_find_retryable_jobs_multiple_mixed(self, retry_engine, engine):
        """Test with multiple jobs in various states.

        Phase 3: "should be found" jobs use status='pending' (post-
        atomic_retry state with next_retry_at set). "Should be excluded"
        jobs use various terminal states or no next_retry_at.
        """
        past_time = datetime.utcnow() - timedelta(hours=1)
        future_time = datetime.utcnow() + timedelta(hours=1)

        # Should be found: past due, post-atomic_retry, has next_retry_at
        create_job_in_session(
            engine,
            job_id="job-found",
            project_id="project-a",
            status=JobStatus.PENDING.value,
            next_retry_at=past_time.isoformat(),
        )

        # Should be excluded: future next_retry_at
        create_job_in_session(
            engine,
            job_id="job-future",
            status=JobStatus.PENDING.value,
            next_retry_at=future_time.isoformat(),
        )

        # Should be excluded: completed (terminal, admission_state='done')
        create_job_in_session(
            engine,
            job_id="job-completed",
            status=JobStatus.COMPLETED.value,
            next_retry_at=past_time.isoformat(),
        )

        # Should be excluded: no next_retry_at
        create_job_in_session(
            engine,
            job_id="job-no-retry",
            status=JobStatus.PENDING.value,
            next_retry_at=None,
        )

        # Should be found: project filter matches
        create_job_in_session(
            engine,
            job_id="job-project-b",
            project_id="project-b",
            status=JobStatus.PENDING.value,
            next_retry_at=past_time.isoformat(),
        )

        all_results = retry_engine.find_retryable_jobs()
        assert len(all_results) == 2  # job-found + job-project-b

        project_a_results = retry_engine.find_retryable_jobs(project_id="project-a")
        assert len(project_a_results) == 1
        assert project_a_results[0].job_id == "job-found"


# =============================================================================
# Integration Tests
# =============================================================================

class TestRetryEngineIntegration:
    """Integration tests combining multiple retry operations."""

    def test_full_retry_cycle(self, retry_engine, job_repo, engine):
        """Test complete retry cycle: create -> fail -> retry -> fail -> DLQ."""
        # Step 1: Create job
        create_job_in_session(
            engine,
            job_id="job-cycle",
            status=JobStatus.FAILED.value,
            retry_count=0,
            max_retries=2,
            error_message="First failure",
            failed_at=datetime.utcnow().isoformat(),
        )
        
        # Verify job is failed
        job = job_repo.get("job-cycle")
        assert job.status == JobStatus.FAILED.value
        assert job.retry_count == 0
        
        # Step 2: First retry
        result1 = retry_engine.maybe_retry("job-cycle")
        assert result1 is not None
        assert result1.status == JobStatus.PENDING.value
        assert result1.retry_count == 1
        
        # Simulate failure by directly updating the job in the database
        # (bypassing state machine for test purposes). Phase 4: also
        # flip ``admission_state`` to ``'done'`` so the dual-write
        # mapping ``status_to_admission('failed')='done'`` is
        # honored — ``atomic_retry``'s SQL guard checks both the
        # admission_state AND status columns.
        with SQLModelSession(engine) as session:
            from daemon.repositories.job_queue.models import JobItem
            job_update = session.get(JobItem, "job-cycle")
            job_update.status = JobStatus.FAILED.value
            job_update.admission_state = "done"
            job_update.error_message = "Second failure"
            job_update.failed_at = datetime.utcnow().isoformat()
            job_update.next_retry_at = None  # Clear next_retry_at for retry
            session.commit()

        # Step 3: Second retry
        result2 = retry_engine.maybe_retry("job-cycle")
        assert result2 is not None
        assert result2.status == JobStatus.PENDING.value
        assert result2.retry_count == 2

        # Simulate failure again (exhaust retries). Phase 4: also
        # flip ``admission_state`` (see comment above).
        with SQLModelSession(engine) as session:
            from daemon.repositories.job_queue.models import JobItem
            job_update = session.get(JobItem, "job-cycle")
            job_update.status = JobStatus.FAILED.value
            job_update.admission_state = "done"
            job_update.error_message = "Third failure"
            job_update.failed_at = datetime.utcnow().isoformat()
            job_update.next_retry_at = None  # Clear next_retry_at for retry
            session.commit()
        
        # Step 4: Third retry should go to DLQ
        result3 = retry_engine.maybe_retry("job-cycle")
        assert result3 is None  # Moved to DLQ

    def test_config_affects_retry_behavior(self, engine, job_repo, queue_repo, dlq_repo):
        """Test that config changes affect retry engine behavior."""
        # Config with max_retries=5
        config = JobSystemConfig(
            default_max_retries=5,
            retry_backoff_base_seconds=60,
            retry_backoff_max_seconds=3600,
            retry_backoff_multiplier=2.0,
            dlq_enabled=True,
        )
        retry_engine = JobRetryEngine(job_repo, queue_repo, dlq_repo, config)
        
        # Create job with no explicit max_retries
        job = create_job_in_session(
            engine,
            job_id="job-config-test",
            status=JobStatus.FAILED.value,
            retry_count=0,
            max_retries=None,  # Should use config default of 5
        )
        
        # Should be able to retry (count 0 < max 5)
        assert retry_engine.should_retry(job) is True
        
        # Exhaust retries
        job2 = job_repo.get("job-config-test")
        job2.retry_count = 5
        with SQLModelSession(engine) as session:
            session.commit()
        
        # Should NOT retry (count 5 >= max 5)
        assert retry_engine.should_retry(job2) is False

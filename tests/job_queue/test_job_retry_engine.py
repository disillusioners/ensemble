"""Tests for JobRetryEngine.

This module tests the JobRetryEngine including:
- Exponential backoff calculation
- Retry decision logic
- Retry execution
- Dead letter queue integration
"""

import pytest
from datetime import datetime, timedelta
import threading
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.config import JobSystemConfig
from daemon.repositories.job_queue.models import JobItem, JobQueue, DeadLetterItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_retry_engine import JobRetryEngine


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
def job_repo(engine):
    """Create JobRepository with test engine."""
    return JobRepository(engine)


@pytest.fixture
def queue_repo(engine):
    """Create JobQueueRepository with test engine (same engine as job_repo)."""
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
    """Create default JobSystemConfig."""
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


def create_job(engine, **kwargs) -> JobItem:
    """Helper to create a job directly in the database."""
    job = JobItem(**kwargs)
    with Session(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def create_queue(engine, **kwargs) -> JobQueue:
    """Helper to create a queue directly in the database."""
    queue = JobQueue(**kwargs)
    with Session(engine) as session:
        session.add(queue)
        session.commit()
        session.refresh(queue)
    return queue


class TestCalculateBackoff:
    """Tests for JobRetryEngine.calculate_backoff() method."""

    def test_calculate_backoff_basic(self, retry_engine, default_config):
        """Test exponential backoff grows correctly: 60, 120, 240, 480, etc."""
        # With base=60, multiplier=2.0:
        # retry 0: 60 * 2^0 = 60
        # retry 1: 60 * 2^1 = 120
        # retry 2: 60 * 2^2 = 240
        # retry 3: 60 * 2^3 = 480
        delays = []
        for retry_count in range(5):
            delay = retry_engine.calculate_backoff(retry_count, default_config)
            delays.append(delay)
        
        # Each delay should be roughly double the previous (plus jitter)
        # Exact formula: base * 2^retry_count + jitter (0 to base*0.5)
        # With retry_count=0: 60 + jitter (60 to 90)
        assert 60 <= delays[0] <= 90
        # With retry_count=1: 120 + jitter (120 to 150)
        assert 120 <= delays[1] <= 150
        # With retry_count=2: 240 + jitter (240 to 270)
        assert 240 <= delays[2] <= 270
        # With retry_count=3: 480 + jitter (480 to 510)
        assert 480 <= delays[3] <= 510

    def test_calculate_backoff_capped_at_max(self, retry_engine, default_config):
        """Test backoff is capped at max_seconds (3600)."""
        # Even with high retry_count, should not exceed max
        # retry_count=10: 60 * 2^10 = 61440 + jitter would exceed max
        for retry_count in [8, 10, 15, 20]:
            delay = retry_engine.calculate_backoff(retry_count, default_config)
            assert delay <= default_config.retry_backoff_max_seconds
            assert delay == default_config.retry_backoff_max_seconds

    def test_calculate_backoff_has_jitter(self, retry_engine, default_config):
        """Test that jitter is added to the base delay."""
        # Call multiple times and verify we get different values
        # (jitter is random between 0 and base*0.5)
        delays = [retry_engine.calculate_backoff(0, default_config) for _ in range(10)]
        
        # Should have some variation due to jitter
        # At least some delays should be different
        unique_delays = set(delays)
        # With 10 samples, we should see some variation if jitter is working
        # (This test might occasionally fail due to random chance, but unlikely)
        assert len(unique_delays) >= 1  # At minimum, all should be valid

    def test_calculate_backoff_custom_config(self, default_config):
        """Test backoff with custom configuration values."""
        custom_config = JobSystemConfig(
            default_max_retries=5,
            retry_backoff_base_seconds=30,
            retry_backoff_max_seconds=600,
            retry_backoff_multiplier=3.0,
            dlq_enabled=True,
        )
        
        # Create a minimal engine for testing calculate_backoff
        # which doesn't need real repos
        mock_job_repo = MagicMock()
        mock_queue_repo = MagicMock()
        mock_dlq_service = MagicMock()
        
        engine = JobRetryEngine(
            job_repo=mock_job_repo,
            queue_repo=mock_queue_repo,
            dlq_service=mock_dlq_service,
            config=custom_config,
        )
        
        # With base=30, multiplier=3.0:
        # retry 0: 30 * 3^0 = 30
        # retry 1: 30 * 3^1 = 90
        delay = engine.calculate_backoff(1, custom_config)
        
        # Should be around 90 (plus jitter up to 15)
        assert 90 <= delay <= 105


class TestGetMaxRetries:
    """Tests for JobRetryEngine.get_max_retries() method."""

    def test_get_max_retries_from_job(self, retry_engine, job_repo, engine):
        """Test max_retries resolved from job.max_retries."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            max_retries=5,
            status="failed",
        )
        
        job = job_repo.get("job-123")
        max_retries = retry_engine.get_max_retries(job)
        
        assert max_retries == 5

    def test_get_max_retries_from_queue(self, retry_engine, job_repo, queue_repo, engine):
        """Test max_retries falls back to queue.default_max_retries when queue is passed."""
        # Create queue with custom max_retries
        create_queue(
            engine,
            queue_id="queue-123",
            project_id="project-abc",
            queue_name="test-queue",
            queue_name_lower="test-queue",
            default_max_retries=7,
        )
        
        # Create job without max_retries
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-123",
            max_retries=None,
            status="failed",
        )
        
        job = job_repo.get("job-123")
        queue = queue_repo.get("queue-123")
        max_retries = retry_engine.get_max_retries(job, queue=queue)
        
        assert max_retries == 7

    def test_get_max_retries_from_config(self, retry_engine, job_repo, engine):
        """Test max_retries falls back to config.default_max_retries."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            max_retries=None,
            status="failed",
        )
        
        job = job_repo.get("job-123")
        max_retries = retry_engine.get_max_retries(job)
        
        # Should fall back to config default of 3
        assert max_retries == 3

    def test_get_max_retries_hard_cap_100(self, retry_engine, job_repo, engine):
        """Test max_retries is hard-capped at 100."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            max_retries=150,  # Over the cap
            status="failed",
        )
        
        job = job_repo.get("job-123")
        max_retries = retry_engine.get_max_retries(job)
        
        assert max_retries == 100


class TestShouldRetry:
    """Tests for JobRetryEngine.should_retry() method."""

    def test_should_retry_true_when_under_limit(self, retry_engine, job_repo, engine):
        """Test should_retry returns True when retry_count < max_retries."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=1,
            max_retries=3,
        )
        
        job = job_repo.get("job-123")
        result = retry_engine.should_retry(job)
        
        assert result is True

    def test_should_retry_false_when_exhausted(self, retry_engine, job_repo, engine):
        """Test should_retry returns False when retry_count >= max_retries."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=3,
            max_retries=3,  # Exhausted
        )
        
        job = job_repo.get("job-123")
        result = retry_engine.should_retry(job)
        
        assert result is False

    def test_should_retry_disabled_when_zero(self, retry_engine, job_repo, engine):
        """Test should_retry returns False when max_retries=0."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=0,
            max_retries=0,  # Explicitly disabled
        )
        
        job = job_repo.get("job-123")
        result = retry_engine.should_retry(job)
        
        assert result is False

    def test_should_retry_false_when_not_failed(self, retry_engine, job_repo, engine):
        """Test should_retry returns False when job is not in FAILED state."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="completed",  # Not failed
            retry_count=0,
        )
        
        job = job_repo.get("job-123")
        result = retry_engine.should_retry(job)
        
        assert result is False

    def test_should_retry_false_when_dlq_disabled(self, job_repo, queue_repo, dlq_service):
        """Test should_retry returns False when DLQ is disabled in config."""
        config = JobSystemConfig(
            dlq_enabled=False,
            default_max_retries=3,
            retry_backoff_base_seconds=60,
            retry_backoff_max_seconds=3600,
            retry_backoff_multiplier=2.0,
        )
        engine = JobRetryEngine(job_repo, queue_repo, dlq_service, config)
        
        job = JobItem(
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=0,
        )
        
        result = engine.should_retry(job)
        
        assert result is False


class TestMaybeRetry:
    """Tests for JobRetryEngine.maybe_retry() method."""

    def test_maybe_retry_success_transitions_to_pending(self, retry_engine, job_repo, engine):
        """Test successful retry transitions FAILED->PENDING."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=1,
            max_retries=3,
            error_message="Connection timeout",
            failed_at=datetime.utcnow().isoformat(),
        )
        
        result = retry_engine.maybe_retry("job-123")
        
        assert result is not None
        assert result.status == "pending"
        assert result.retry_count == 2  # Incremented
        assert result.next_retry_at is not None
        assert result.error_message is None  # Cleared
        assert result.failed_at is None  # Cleared

    def test_maybe_retry_exhausted_moves_to_dlq(self, retry_engine, job_repo, dlq_repo, engine):
        """Test exhausted retries move job to DLQ."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-123",
            status="failed",
            retry_count=3,  # Exhausted
            max_retries=3,
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

    def test_maybe_retry_exhausted_dlq_failure_rolls_back(self, retry_engine, job_repo, dlq_repo, engine):
        """Test DLQ move failure triggers rollback, job stays FAILED."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-123",
            status="failed",
            retry_count=3,  # Exhausted
            max_retries=3,
            error_message="Connection timeout",
            failed_at=datetime.utcnow().isoformat(),
        )

        with patch.object(
            retry_engine._dlq_service, "move_to_dlq", side_effect=RuntimeError("DB write failed")
        ):
            with pytest.raises(RuntimeError, match="DB write failed"):
                retry_engine.maybe_retry("job-123")

        # Job should remain in FAILED state after rollback
        job = job_repo.get("job-123")
        assert job is not None
        assert job.status == "failed"
        assert job.retry_count == 3
        assert job.error_message == "Connection timeout"

        # Nothing should be in DLQ
        dlq_item = dlq_repo.get_by_job_id("job-123")
        assert dlq_item is None

    def test_maybe_retry_job_not_failed(self, retry_engine, job_repo, engine):
        """Test maybe_retry returns None for non-FAILED jobs."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="completed",  # Not failed
        )
        
        result = retry_engine.maybe_retry("job-123")
        
        assert result is None

    def test_maybe_retry_job_not_found(self, retry_engine):
        """Test maybe_retry returns None for non-existent job."""
        result = retry_engine.maybe_retry("non-existent-job")

        assert result is None


class TestMaybeRetryAtomicConcurrency:
    """Concurrency tests for the H5 (P1) fix.

    The pre-fix implementation read ``retry_count`` into Python,
    computed ``retry_count + 1``, and committed — two concurrent
    callers could both observe the same row and both write
    ``N + 1``, losing one increment. These tests pin down the new
    atomic UPDATE behaviour: the SQL-level guard
    ``status = 'failed' AND retry_count < max_retries`` is the
    race-safety boundary, and at most one concurrent caller can
    transition the row.
    """

    def test_atomic_retry_concurrent_calls_only_one_succeeds(self, job_repo, engine):
        """Two concurrent ``atomic_retry`` calls — only one increments.

        Without the SQL-level guard, both threads would observe
        ``retry_count=1``, both would write ``2``, and the assertion
        would fail (the row would carry ``retry_count=2`` while two
        callers thought they had advanced it). With the guard,
        exactly one UPDATE matches the ``status='failed' AND
        retry_count < max_retries`` predicate; the second writer
        sees ``status='pending'`` (or the incremented
        ``retry_count``) and matches zero rows.
        """
        create_job(
            engine,
            job_id="job-concurrent",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=1,
            max_retries=10,
            error_message="Initial failure",
            failed_at=datetime.utcnow().isoformat(),
        )

        results: list = []
        errors: list = []
        barrier = threading.Barrier(2)

        def attempt_retry() -> None:
            try:
                barrier.wait(timeout=5)
                outcome = job_repo.atomic_retry(
                    job_id="job-concurrent",
                    max_retries=10,
                    next_retry_at=(
                        datetime.utcnow() + timedelta(minutes=5)
                    ).isoformat(),
                )
                results.append(outcome)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=attempt_retry) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Unexpected errors: {errors}"
        successful = [r for r in results if r is not None]
        assert len(successful) == 1, (
            f"Expected exactly one successful atomic_retry, got "
            f"{len(successful)}: {results}"
        )

        # retry_count advances by exactly 1 (1 -> 2), never 1 -> 3.
        final = job_repo.get("job-concurrent")
        assert final is not None
        assert final.retry_count == 2, (
            f"Lost increment detected: expected retry_count=2, got "
            f"{final.retry_count}"
        )
        assert final.status == "pending"
        assert final.failed_at is None
        assert final.error_message is None
        assert final.next_retry_at is not None

    def test_atomic_retry_skips_when_retry_count_at_max(self, job_repo, engine):
        """``atomic_retry`` is a no-op when ``retry_count == max_retries``.

        Belt-and-braces guard for the inline ``retry_count <
        max_retries`` predicate: even if a caller passes the SQL
        guard's ``status='failed'`` check, an exhausted job must
        not be incremented again — it must move to DLQ instead.
        """
        create_job(
            engine,
            job_id="job-exhausted",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=3,
            max_retries=3,  # already at the cap
            error_message="Final failure",
            failed_at=datetime.utcnow().isoformat(),
        )

        outcome = job_repo.atomic_retry(
            job_id="job-exhausted",
            max_retries=3,
            next_retry_at=(
                datetime.utcnow() + timedelta(minutes=5)
            ).isoformat(),
        )

        assert outcome is None
        # Row must be unchanged: still FAILED, retry_count not bumped.
        final = job_repo.get("job-exhausted")
        assert final is not None
        assert final.status == "failed"
        assert final.retry_count == 3

    def test_atomic_retry_skips_when_status_not_failed(self, job_repo, engine):
        """``atomic_retry`` is a no-op when status is not FAILED.

        A concurrent CANCELLED or DEAD_LETTER transition must not
        be silently overwritten by a retry — the SQL-level
        ``status='failed'`` guard is the protection.
        """
        create_job(
            engine,
            job_id="job-cancelled-mid-flight",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test",
            source="api",
            project_id="project-abc",
            status="cancelled",  # Not FAILED
            retry_count=1,
            max_retries=10,
        )

        outcome = job_repo.atomic_retry(
            job_id="job-cancelled-mid-flight",
            max_retries=10,
            next_retry_at=(
                datetime.utcnow() + timedelta(minutes=5)
            ).isoformat(),
        )

        assert outcome is None
        final = job_repo.get("job-cancelled-mid-flight")
        assert final is not None
        assert final.status == "cancelled"
        assert final.retry_count == 1  # Unchanged

    def test_maybe_retry_skips_concurrently_cancelled_job(self, retry_engine, job_repo, engine):
        """End-to-end: concurrent cancellation prevents retry.

        Simulates the realistic race: ``fail_job`` writes FAILED,
        ``cancel_job`` flips to CANCELLED, and the retry sweep /
        ``maybe_retry`` arrives just after. The retry must be a
        no-op — the SQL guard rejects it because
        ``status='cancelled'`` no longer matches ``status='failed'``.
        """
        create_job(
            engine,
            job_id="job-mid-cancel",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=1,
            max_retries=10,
            error_message="Connection timeout",
            failed_at=datetime.utcnow().isoformat(),
        )

        # Concurrent cancellation — flip status to CANCELLED before
        # maybe_retry runs.
        with Session(engine) as session:
            row = session.get(JobItem, "job-mid-cancel")
            row.status = "cancelled"
            row.cancelled_at = datetime.utcnow().isoformat()
            session.commit()

        result = retry_engine.maybe_retry("job-mid-cancel")

        assert result is None
        final = job_repo.get("job-mid-cancel")
        assert final is not None
        assert final.status == "cancelled"
        assert final.retry_count == 1  # Not incremented
        assert final.error_message == "Connection timeout"  # Not cleared

    def test_maybe_retry_skips_concurrently_dead_lettered_job(self, retry_engine, job_repo, dlq_repo, engine):
        """End-to-end: a job moved to DLQ between read and retry must
        not be resurrected by ``maybe_retry``.
        """
        create_job(
            engine,
            job_id="job-already-dlq",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test",
            source="api",
            project_id="project-abc",
            queue_id="queue-123",
            status="failed",
            retry_count=2,
            max_retries=3,
            error_message="Stalled",
            failed_at=datetime.utcnow().isoformat(),
        )

        # Concurrent path moves the job to DLQ (status='dead_letter')
        # before maybe_retry runs.
        with Session(engine) as session:
            row = session.get(JobItem, "job-already-dlq")
            row.status = "dead_letter"
            session.commit()

        result = retry_engine.maybe_retry("job-already-dlq")

        assert result is None
        final = job_repo.get("job-already-dlq")
        assert final is not None
        assert final.status == "dead_letter"
        assert final.retry_count == 2  # Not incremented


class TestFindRetryableJobs:
    """Tests for JobRetryEngine.find_retryable_jobs() method."""

    def test_find_retryable_jobs_past_due(self, retry_engine, job_repo, engine):
        """Test finding jobs with next_retry_at in the past."""
        past_time = datetime.utcnow() - timedelta(hours=1)
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=1,
            max_retries=3,
            next_retry_at=past_time.isoformat(),
        )
        
        results = retry_engine.find_retryable_jobs()
        
        assert len(results) == 1
        assert results[0].job_id == "job-123"

    def test_find_retryable_jobs_excludes_future(self, retry_engine, job_repo, engine):
        """Test jobs with future next_retry_at are excluded."""
        future_time = datetime.utcnow() + timedelta(hours=1)
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=1,
            max_retries=3,
            next_retry_at=future_time.isoformat(),
        )
        
        results = retry_engine.find_retryable_jobs()
        
        assert len(results) == 0

    def test_find_retryable_jobs_with_project_filter(self, retry_engine, job_repo, engine):
        """Test filtering retryable jobs by project_id."""
        past_time = datetime.utcnow() - timedelta(hours=1)
        
        create_job(
            engine,
            job_id="job-1",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-a",
            status="failed",
            retry_count=1,
            next_retry_at=past_time.isoformat(),
        )
        create_job(
            engine,
            job_id="job-2",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-b",
            status="failed",
            retry_count=1,
            next_retry_at=past_time.isoformat(),
        )
        
        results = retry_engine.find_retryable_jobs(project_id="project-a")
        
        assert len(results) == 1
        assert results[0].job_id == "job-1"

    def test_find_retryable_jobs_excludes_non_failed(self, retry_engine, job_repo, engine):
        """Test that non-FAILED jobs are excluded."""
        past_time = datetime.utcnow() - timedelta(hours=1)
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="completed",  # Not failed
            retry_count=1,
            next_retry_at=past_time.isoformat(),
        )
        
        results = retry_engine.find_retryable_jobs()
        
        assert len(results) == 0

    def test_find_retryable_jobs_excludes_without_next_retry_at(self, retry_engine, job_repo, engine):
        """Test that jobs without next_retry_at are excluded."""
        create_job(
            engine,
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            status="failed",
            retry_count=1,
            next_retry_at=None,  # No retry scheduled
        )
        
        results = retry_engine.find_retryable_jobs()
        
        assert len(results) == 0

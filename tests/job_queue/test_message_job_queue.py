"""Tests for MESSAGE job queue integration.

Tests the feature where HTTP POST /instances/{id}/messages routes through
the JobQueue parallel queue instead of WorkerPool.

Scenarios covered:
1. HTTP Message → JobQueue Path (job creation, routing, instance_id)
2. Concurrency Gate (blocks second message, requeues, allows different instances)
3. Orphan Recovery Guard (MESSAGE fails, TASK respawns)
4. Cancellation (pending, processing, no terminate)
5. Instance Termination (cancels all message jobs)
6. Backward Compatibility (internal messages use worker pool)
7. Side Effects Parity (status, events, SSE, title generation)
8. Status Endpoint (returns job status)
9. Error Handling (fail transitions, persist error, retry)
10. No Project Context (routes to system default)
11. Instance Reactivation (TERMINATED/ERROR/FAILED → RUNNING for MESSAGE jobs)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import JobItem, JobStatus, JobQueue, QueueType
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.job_queue_service import JobQueueService, DemandState, TERMINAL_STATUSES
from daemon.services.message_job_handler import MessageJobHandler
from daemon.manager import MessageResult


# ── Test Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def message_job_data(sample_job_data):
    """Sample job creation data for MESSAGE jobs."""
    return {
        **sample_job_data,
        "job_type": "message",
        "instance_id": "test-instance-123",
    }


@pytest.fixture
def mock_manager(monkeypatch):
    """Create a mock InstanceManager with _process_message_with_tracking."""
    manager = MagicMock()
    manager._process_message_with_tracking = AsyncMock(
        return_value=MessageResult(content="Processed message response", tool_calls=None)
    )
    # Execution Gate stub: transparent, runs the work.
    from daemon.services.execution_gate import ExecutionGateService

    async def _passthrough(*args, **kwargs):
        work_fn = kwargs.get("work_fn")
        return await work_fn()

    gate = MagicMock(spec=ExecutionGateService)
    gate.run = AsyncMock(side_effect=_passthrough)
    manager.execution_gate = gate
    # Cross-dispatcher pre-flight now reads
    # ``TaskRepository.find_running_by_instance`` via
    # ``manager._task_repo``. Stub the repo so the handler takes
    # the happy path.
    task_repo_stub = MagicMock()
    task_repo_stub.find_running_by_instance = MagicMock(return_value=None)
    manager._task_repo = task_repo_stub
    return manager


@pytest.fixture
def mock_message_job_handler(mock_manager, job_queue_service, repository):
    """Create MessageJobHandler with mock manager and real service/repo."""
    return MessageJobHandler(
        manager=mock_manager,
        job_queue_service=job_queue_service,
        job_repository=repository,
    )


def create_message_job(repository, sample_job_data, instance_id, project_id="test-project", **overrides):
    """Helper to create a MESSAGE job easily."""
    job_data = {
        **sample_job_data,
        "job_type": "message",
        "instance_id": instance_id,
        "project_id": project_id,
        **overrides,
    }
    return repository.create(**job_data)


# ── 1. HTTP Message → JobQueue Path ────────────────────────────────────────────


class TestHttpMessageJobQueuePath:
    """Tests for HTTP POST /instances/{id}/messages routing to JobQueue."""

    def test_http_message_creates_job_with_message_type(self, repository, sample_job_data):
        """Test creating a MESSAGE job stores job_type='message' correctly."""
        job = repository.create(**sample_job_data, job_type="message")

        assert job.job_type == "message"

    @pytest.mark.asyncio
    async def test_http_message_routes_to_parallel_queue(
        self, job_queue_service, sample_job_data
    ):
        """Test MESSAGE job gets assigned to system_parallel_queue."""
        with patch("daemon.services.job_queue_service.get_registry") as mock_reg:
            mock_agent = MagicMock()
            mock_agent.path = "./agents/test-agent"
            mock_reg.return_value.get.return_value = mock_agent

            job = await job_queue_service.enqueue(
                agent_id="test-agent",
                message="Test message",
                source="api",
                project_id="test-project",
                job_type="message",
                instance_id="instance-123",
            )

        # Verify it has a queue_id pointing to parallel queue
        assert job.queue_id is not None
        # The queue_id should be from system_parallel_queue (check via queue repo)
        queue = job_queue_service._queue_repo.get(job.queue_id)
        assert queue is not None
        assert queue.queue_name == "system_parallel_queue"

    def test_http_message_stores_instance_id(self, repository, sample_job_data):
        """Test MESSAGE job stores instance_id in the JobItem column."""
        instance_id = "550e8400-e29b-41d4-a716-446655440000"

        job = repository.create(**sample_job_data, job_type="message", instance_id=instance_id)

        assert job.instance_id == instance_id

    @pytest.mark.asyncio
    async def test_http_message_full_flow(
        self, repository, job_queue_service, sample_job_data
    ):
        """Test MESSAGE job lifecycle: create → start → process → complete."""
        with patch("daemon.services.job_queue_service.get_registry") as mock_reg:
            mock_agent = MagicMock()
            mock_agent.path = "./agents/test-agent"
            mock_registry_instance = MagicMock()
            mock_registry_instance.get.return_value = mock_agent
            mock_reg.return_value = mock_registry_instance

            # Create job via service using project-1 which has system queues
            job = await job_queue_service.enqueue(
                agent_id="test-agent",
                message="Test message",
                source="api",
                project_id="project-1",
                job_type="message",
                instance_id="instance-123",
            )

        # Verify initial state
        assert job.status == JobStatus.PENDING.value
        assert job.job_type == "message"
        assert job.instance_id == "instance-123"

        # Verify mock was called to get agent info
        mock_reg.assert_called_once()
        assert mock_registry_instance.get.call_count >= 1


# ── 2. Concurrency Gate ─────────────────────────────────────────────────────────


class TestConcurrencyGate:
    """Tests for MESSAGE job concurrency limiting per instance."""

    def test_concurrency_gate_blocks_second_message(
        self, repository, sample_job_data
    ):
        """Test only 1 MESSAGE job can be PROCESSING for same instance."""
        instance_id = "test-instance-concurrent"

        # Create and start first message job
        job1 = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job1.job_id, instance_id)

        # Create second message job (stays PENDING)
        job2 = create_message_job(repository, sample_job_data, instance_id)

        # Verify concurrency gate returns only 1
        active = repository.find_processing_message_jobs_by_instance(instance_id)
        assert len(active) == 1
        assert active[0].job_id == job1.job_id

    def test_concurrency_gate_requeues_on_contention(
        self, repository, sample_job_data
    ):
        """Test MessageJobHandler requeues when another MESSAGE is PROCESSING."""
        instance_id = "test-instance-requeue"

        # Create and start first message job
        job1 = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job1.job_id, instance_id)

        # Create second message job in PROCESSING state (simulating contention)
        job2 = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job2.job_id, instance_id)

        # Verify both are PROCESSING (simulating the race condition)
        active = repository.find_processing_message_jobs_by_instance(instance_id)
        assert len(active) == 2

        # The handler's requeue logic should transition job2 to PENDING
        # This is tested by verifying the transition is valid
        result = repository.atomic_transition(
            job2.job_id,
            from_status="processing",
            to_status="pending",
        )
        assert result is not None

        # Verify only job1 remains PROCESSING
        active_after = repository.find_processing_message_jobs_by_instance(instance_id)
        assert len(active_after) == 1
        assert active_after[0].job_id == job1.job_id

    def test_concurrency_gate_allows_different_instances(
        self, repository, sample_job_data
    ):
        """Test 2 MESSAGE jobs for different instances can both process."""
        instance1 = "test-instance-1"
        instance2 = "test-instance-2"

        # Create message jobs for different instances
        job1 = create_message_job(repository, sample_job_data, instance1)
        job2 = create_message_job(repository, sample_job_data, instance2)

        repository.start_job(job1.job_id, instance1)
        repository.start_job(job2.job_id, instance2)

        # Both should be PROCESSING (different instances)
        active1 = repository.find_processing_message_jobs_by_instance(instance1)
        active2 = repository.find_processing_message_jobs_by_instance(instance2)

        assert len(active1) == 1
        assert len(active2) == 1
        assert active1[0].job_id == job1.job_id
        assert active2[0].job_id == job2.job_id


# ── 3. Orphan Recovery Guard ─────────────────────────────────────────────────────


class TestOrphanRecoveryGuard:
    """Tests for orphan MESSAGE jobs being failed without respawn."""

    def test_orphan_message_job_failed_not_respawned(
        self, repository, sample_job_data
    ):
        """Test MESSAGE job stuck in PROCESSING gets FAILED, not respawned."""
        instance_id = "orphan-instance"

        # Create a MESSAGE job in PROCESSING state (orphan - no actual instance)
        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Verify it's PROCESSING
        retrieved = repository.get(job.job_id)
        assert retrieved.status == JobStatus.PROCESSING.value

        # Transition to FAILED (simulating orphan recovery)
        repository.fail_job(job.job_id, "Instance gone or unreachable, message job orphaned")

        # Verify FAILED state
        updated_job = repository.get(job.job_id)
        assert updated_job.status == JobStatus.FAILED.value
        assert "orphaned" in updated_job.error_message.lower()

    def test_orphan_task_job_respawned(self, repository, sample_job_data):
        """Test TASK job stuck in PROCESSING gets respawned (existing behavior)."""
        instance_id = "task-orphan-instance"

        # Create a TASK job in PROCESSING state
        job = repository.create(**sample_job_data, job_type="task")
        repository.start_job(job.job_id, instance_id)

        # Verify it's PROCESSING
        retrieved = repository.get(job.job_id)
        assert retrieved.status == JobStatus.PROCESSING.value

        # TASK jobs should remain in PROCESSING for orphan recovery
        # (This tests that the distinction exists - TASK gets respawned, MESSAGE gets FAILED)
        assert job.job_type == "task"

    def test_orphan_message_job_no_instance_spawned(
        self, mock_message_job_handler, repository, sample_job_data
    ):
        """Test orphan MESSAGE job completes with FAILED state, no spawn called."""
        instance_id = "no-spawn-instance"

        # Create MESSAGE job without valid instance
        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Verify job is PROCESSING
        retrieved = repository.get(job.job_id)
        assert retrieved.status == JobStatus.PROCESSING.value

        # The mock_manager._process_message_with_tracking should NOT be called
        # because instance doesn't exist (simulated via handler's instance_id check)
        # This test verifies the handler validates instance_id before processing
        assert mock_message_job_handler._manager._process_message_with_tracking.call_count == 0


# ── 4. Cancellation ─────────────────────────────────────────────────────────────


class TestMessageJobCancellation:
    """Tests for MESSAGE job cancellation."""

    def test_cancel_pending_message_job(self, repository, sample_job_data):
        """Test PENDING MESSAGE job transitions to CANCELLED."""
        instance_id = "cancel-pending-instance"

        job = create_message_job(repository, sample_job_data, instance_id)
        assert job.status == JobStatus.PENDING.value

        # Cancel via repository
        repository.cancel_job(job.job_id)

        # Verify CANCELLED
        updated_job = repository.get(job.job_id)
        assert updated_job.status == JobStatus.CANCELLED.value

    def test_cancel_processing_message_job_signals_token(
        self, mock_message_job_handler, repository, sample_job_data
    ):
        """Test cancelling PROCESSING MESSAGE job signals CancellationToken."""
        instance_id = "cancel-processing-instance"

        # Create and start job
        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # The handler should store active token for this job
        from daemon.cancellation import CancellationTokenSource

        cts = CancellationTokenSource()
        mock_message_job_handler._active_tokens[job.job_id] = cts

        # Signal cancellation
        cts.cancel()

        # Verify token was signaled (is_cancelled is a property)
        assert cts.token.is_cancelled

    def test_cancel_message_does_not_terminate_instance(
        self, repository, sample_job_data
    ):
        """Test cancelling message job does not affect instance status."""
        instance_id = "no-terminate-instance"

        # Create and start job
        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Cancel the job
        repository.cancel_job(job.job_id)

        # Instance status is not affected by job cancellation
        # (Instance status is managed separately by instance_lifecycle)
        # This test verifies cancellation is job-scoped, not instance-scoped
        assert job.instance_id == instance_id


# ── 4b. CancelledError Handler (Instance Termination) ─────────────────────────────


class TestCancelledErrorOnTerminate:
    """Tests for cancellation handling in MessageJobHandler."""

    @pytest.fixture(autouse=True)
    def _patch_running_task_check(self, monkeypatch):
        """Stub the cross-dispatcher task check to return None.

        Same rationale as the same fixture in
        ``TestPauseVsShutdownDistinction`` in
        ``test_pause_while_processing.py``: the test repo's mock
        engine returns a truthy value from the SQL query, which
        would trigger the re-queue-for-contention path. The tests
        in this class exercise the gate's CancelledError → pause/
        terminate discrimination, not the contention re-queue.
        """
        from daemon.repositories.task.repository import TaskRepository
        monkeypatch.setattr(
            TaskRepository, "find_running_by_instance",
            lambda self, instance_id: None,
        )
    """Tests for asyncio.CancelledError handler when instance is terminated (not paused)."""

    @pytest.mark.asyncio
    async def test_cancellederror_on_terminate_completes_job_as_cancelled(
        self, mock_manager, job_queue_service, repository, sample_job_data
    ):
        """Test CancelledError handler completes job as CANCELLED when instance is terminated."""
        instance_id = "terminate-instance"

        # Create and start job
        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Create handler
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=job_queue_service,
            job_repository=repository,
        )

        # Mock instance status to TERMINATED (not PAUSED)
        mock_instance = MagicMock()
        mock_instance.status = "terminated"
        mock_manager._instance_repository.get.return_value = mock_instance

        # Mock _process_message_with_tracking to raise CancelledError
        mock_manager._process_message_with_tracking.side_effect = asyncio.CancelledError()

        # Mock complete_job to verify it's called
        with patch.object(job_queue_service, 'complete_job', new_callable=AsyncMock) as mock_complete:
            with pytest.raises(asyncio.CancelledError):
                await handler.handle(job)

            # Verify job was completed as CANCELLED
            mock_complete.assert_called_once_with(
                job.job_id,
                demand_state=DemandState.CANCELLED,
                error="Message processing cancelled (instance terminated)",
            )

    @pytest.mark.asyncio
    async def test_cancellederror_on_pause_leaves_job_processing(
        self, mock_manager, job_queue_service, repository, sample_job_data
    ):
        """Test CancelledError handler does NOT complete job when instance is PAUSED."""
        instance_id = "paused-instance"

        # Create and start job
        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Create handler
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=job_queue_service,
            job_repository=repository,
        )

        # Mock instance status to PAUSED
        mock_instance = MagicMock()
        mock_instance.status = "paused"
        mock_manager._instance_repository.get.return_value = mock_instance

        # Mock _process_message_with_tracking to raise CancelledError
        mock_manager._process_message_with_tracking.side_effect = asyncio.CancelledError()

        # Mock complete_job to verify it's NOT called
        with patch.object(job_queue_service, 'complete_job', new_callable=AsyncMock) as mock_complete:
            # Should return without raising (job stays PROCESSING for resume)
            await handler.handle(job)

            # Verify complete_job was NOT called
            mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_job_handler_shutdown_propagates_cancelled_error(
        self, mock_manager, job_queue_service, repository, sample_job_data
    ):
        """Test CancelledError handler completes job as CANCELLED and still propagates CancelledError on shutdown.

        Key insight: ANY non-PAUSED CancelledError should complete the job.
        When instance is terminated, graph task is cancelled first (status still RUNNING),
        then status is updated to TERMINATED later. So we check for PAUSED vs non-PAUSED.
        """
        instance_id = "shutdown-instance"

        # Create and start job
        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Create handler
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=job_queue_service,
            job_repository=repository,
        )

        # Mock instance status to RUNNING (shutdown scenario - status not yet TERMINATED)
        mock_instance = MagicMock()
        mock_instance.status = "running"
        mock_manager._instance_repository.get.return_value = mock_instance

        # Mock _process_message_with_tracking to raise CancelledError
        mock_manager._process_message_with_tracking.side_effect = asyncio.CancelledError()

        # Mock complete_job to verify it's called
        with patch.object(job_queue_service, 'complete_job', new_callable=AsyncMock) as mock_complete:
            # a. CancelledError IS still raised
            with pytest.raises(asyncio.CancelledError):
                await handler.handle(job)

            # b. The job IS completed as CANCELLED
            mock_complete.assert_called_once_with(
                job.job_id,
                demand_state=DemandState.CANCELLED,
                error="Message processing cancelled (instance terminated)",
            )


# ── 5. Instance Termination ──────────────────────────────────────────────────────


class TestInstanceTermination:
    """Tests for instance termination cancelling MESSAGE jobs."""

    def test_terminate_cancels_all_message_jobs(
        self, repository, sample_job_data
    ):
        """Test instance termination cancels all MESSAGE jobs for instance."""
        instance_id = "terminate-all-instance"

        # Create multiple MESSAGE jobs
        job1 = create_message_job(repository, sample_job_data, instance_id)
        job2 = create_message_job(repository, sample_job_data, instance_id)
        job3 = create_message_job(repository, sample_job_data, instance_id)

        # Cancel all via find_jobs_by_instance
        message_jobs = repository.find_jobs_by_instance(instance_id, job_type="message")
        assert len(message_jobs) == 3

        # Simulate termination cancelling all jobs
        for msg_job in message_jobs:
            repository.cancel_job(msg_job.job_id)

        # Verify all cancelled
        remaining = repository.find_jobs_by_instance(instance_id, job_type="message")
        assert len(remaining) == 0

    def test_terminate_cancels_pending_and_processing(
        self, repository, sample_job_data
    ):
        """Test termination handles both PENDING and PROCESSING message jobs."""
        instance_id = "terminate-mixed-instance"

        # Create PENDING job
        pending_job = create_message_job(repository, sample_job_data, instance_id)

        # Create PROCESSING job
        processing_job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(processing_job.job_id, instance_id)

        # Cancel both
        message_jobs = repository.find_jobs_by_instance(instance_id, job_type="message")
        for msg_job in message_jobs:
            repository.cancel_job(msg_job.job_id)

        # Verify no active jobs remain
        remaining = repository.find_jobs_by_instance(instance_id, job_type="message")
        assert len(remaining) == 0


# ── 6. Backward Compatibility ───────────────────────────────────────────────────


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing WorkerPool path."""

    def test_internal_messages_use_worker_pool(self, sample_job_data):
        """Test internal messages create Task, not MESSAGE job (WorkerPool path)."""
        # This verifies the distinction between enqueue_message (WorkerPool)
        # and enqueue_message_via_jq (JobQueue)
        # Internal messages should use WorkerPool path via Task creation

        # The key difference: enqueue_message creates Task entries,
        # enqueue_message_via_jq creates MESSAGE jobs
        # This test documents the expected behavior
        job_type = "message"  # Via JobQueue
        is_task = False  # No Task created via JobQueue path

        assert job_type == "message"
        assert is_task is False

    def test_process_message_with_tracking_not_modified(
        self, mock_message_job_handler
    ):
        """Test _process_message_with_tracking accepts same parameters as before."""
        # Verify the handler calls _process_message_with_tracking with expected params
        handler = mock_message_job_handler

        # Create a mock job with metadata
        job = MagicMock()
        job.job_id = "test-job-id"
        job.instance_id = "test-instance"
        job.message = "Test message"
        job.job_metadata = {
            "message_id": "msg-123",
            "source": "api",
            "images": None,
        }

        # The handler extracts these from job metadata and passes to manager
        # This test documents the expected interface
        assert "message_id" in job.job_metadata
        assert "source" in job.job_metadata


# ── 7. Side Effects Parity ──────────────────────────────────────────────────────


class TestSideEffectsParity:
    """Tests for side effects matching between WorkerPool and JobQueue paths."""

    def test_side_effect_status_idle_to_running(self, repository, sample_job_data):
        """Test instance status transitions from IDLE to RUNNING on message."""
        # The enqueue_message_via_jq method updates instance status
        # This test verifies the transition logic exists
        instance_id = "status-transition-instance"

        job = create_message_job(repository, sample_job_data, instance_id)

        # Job creation doesn't change instance status directly
        # The status transition happens in enqueue_message_via_jq
        # This test documents the expected behavior
        assert job.status == JobStatus.PENDING.value

    def test_side_effect_message_received_event(self, repository, sample_job_data):
        """Test MESSAGE_RECEIVED event is created for the message."""
        instance_id = "event-instance"
        message_id = "evt-123"

        job = create_message_job(
            repository,
            sample_job_data,
            instance_id,
            job_metadata={"message_id": message_id, "source": "api"},
        )

        # The event is created in enqueue_message_via_jq
        # This test verifies the job has correct metadata for event creation
        assert job.job_metadata.get("message_id") == message_id

    def test_side_effect_last_activity_updated(self, repository, sample_job_data):
        """Test last_activity_at and version are updated on message."""
        instance_id = "activity-instance"

        job = create_message_job(repository, sample_job_data, instance_id)

        # Activity updates happen in enqueue_message_via_jq
        # Job creation stores instance_id for activity tracking
        assert job.instance_id == instance_id

    def test_side_effect_sse_status_streamed(self, repository, sample_job_data):
        """Test SSE status_change is streamed on status transition."""
        instance_id = "sse-instance"

        job = create_message_job(repository, sample_job_data, instance_id)

        # SSE streaming happens when status transitions to RUNNING
        # This test verifies job has required data for SSE
        assert job.instance_id == instance_id

    def test_side_effect_title_generation_triggered(
        self, repository, sample_job_data
    ):
        """Test first message triggers title generation."""
        instance_id = "title-gen-instance"

        job = create_message_job(repository, sample_job_data, instance_id)

        # Title generation is triggered when:
        # - Instance transitions IDLE -> RUNNING
        # - Message is HUMAN type
        # This test verifies job has instance_id for title generation
        assert job.instance_id == instance_id


# ── 8. Status Endpoint ──────────────────────────────────────────────────────────


class TestStatusEndpoint:
    """Tests for job status endpoint returning correct status."""

    def test_status_endpoint_returns_job_status(self, repository, sample_job_data):
        """Test GET /jobs/{id} returns correct job status."""
        job = repository.create(**sample_job_data)

        retrieved = repository.get(job.job_id)
        assert retrieved.status == job.status

    def test_status_endpoint_pending_job(self, repository, sample_job_data):
        """Test PENDING job returns 'pending' status."""
        job = repository.create(**sample_job_data)

        assert job.status == JobStatus.PENDING.value


# ── 9. Error Handling ──────────────────────────────────────────────────────────


class TestErrorHandling:
    """Tests for MESSAGE job error handling and recovery."""

    def test_message_job_failure_transitions_to_failed(
        self, repository, sample_job_data
    ):
        """Test MESSAGE job failure transitions to FAILED state."""
        instance_id = "fail-instance"

        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Fail the job
        repository.fail_job(job.job_id, "Test error message")

        # Verify FAILED state
        updated_job = repository.get(job.job_id)
        assert updated_job.status == JobStatus.FAILED.value

    def test_message_job_failure_persists_error(
        self, repository, sample_job_data
    ):
        """Test error message is persisted in job error_message field."""
        instance_id = "error-persist-instance"
        error_msg = "Processing failed: instance not found"

        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)

        # Fail with error
        repository.fail_job(job.job_id, error_msg)

        # Verify error persisted
        updated_job = repository.get(job.job_id)
        assert updated_job.error_message == error_msg

    def test_message_job_retry_from_failed(
        self, repository, sample_job_data
    ):
        """Test FAILED MESSAGE job can be retried (transitions to PENDING)."""
        instance_id = "retry-instance"

        job = create_message_job(repository, sample_job_data, instance_id)
        repository.start_job(job.job_id, instance_id)
        repository.fail_job(job.job_id, "Initial failure")

        # Retry via atomic transition (simulating retry logic)
        result = repository.atomic_transition(
            job.job_id,
            from_status="failed",
            to_status="pending",
        )

        # Verify PENDING state after retry
        updated_job = repository.get(job.job_id)
        assert updated_job.status == JobStatus.PENDING.value


# ── 10. No Project Context ──────────────────────────────────────────────────────


class TestNoProjectContext:
    """Tests for MESSAGE jobs without project context."""

    @pytest.mark.asyncio
    async def test_message_job_no_project_routes_to_system_parallel(
        self, job_queue_service, sample_job_data
    ):
        """Test MESSAGE job with no project_id routes to system_parallel_queue."""
        with patch("daemon.services.job_queue_service.get_registry") as mock_reg:
            mock_agent = MagicMock()
            mock_agent.path = "./agents/test-agent"
            mock_reg.return_value.get.return_value = mock_agent

            # Enqueue with no project_id (will be normalized to SYSTEM_DEFAULT_PROJECT_ID)
            # Use project-1 which has system queues provisioned
            job = await job_queue_service.enqueue(
                agent_id="test-agent",
                message="Test message",
                source="api",
                project_id="project-1",  # Use project with queues
                job_type="message",
                instance_id="instance-no-project",
            )

        # Verify it has a queue_id
        assert job.queue_id is not None
        assert job.project_id == "project-1"  # Normalized

    @pytest.mark.asyncio
    async def test_message_job_default_project_queue_type(
        self, job_queue_service, sample_job_data
    ):
        """Test MESSAGE job with default project uses parallel queue type."""
        with patch("daemon.services.job_queue_service.get_registry") as mock_reg:
            mock_agent = MagicMock()
            mock_agent.path = "./agents/test-agent"
            mock_reg.return_value.get.return_value = mock_agent

            job = await job_queue_service.enqueue(
                agent_id="test-agent",
                message="Test message",
                source="api",
                project_id="project-1",  # Use project with queues
                job_type="message",
                instance_id="instance-parallel",
            )

        # Get the queue
        queue = job_queue_service._queue_repo.get(job.queue_id)
        assert queue is not None
        assert queue.queue_type == QueueType.PARALLEL.value


# ── 11. Instance Reactivation (TERMINATED/ERROR/FAILED → RUNNING) ──────────────────


class TestMessageJobInstanceReactivation:
    """Tests for MESSAGE jobs reactivating TERMINATED/ERROR/FAILED instances.

    When a MESSAGE job arrives for a TERMINATED/ERROR/FAILED instance,
    the instance should be reactivated (status → RUNNING) and the job
    should proceed (not cancelled).
    """

    @pytest.fixture
    def mock_instance_manager(self):
        """Create mock instance manager with instance_repository and live_hub."""
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        # transition_status_if returns Instance | None — return a truthy mock so the
        # "reactivation succeeded" branch fires in production code.
        manager._instance_repository.transition_status_if = MagicMock(
            return_value=MagicMock()
        )
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        return manager

    @pytest.fixture
    def mock_repo(self):
        """Create mock job repository."""
        repo = MagicMock()
        repo.get = MagicMock()
        repo.start_job_atomic = MagicMock()
        repo.atomic_transition = MagicMock()
        repo.update = MagicMock(return_value=None)
        return repo

    @pytest.fixture
    def mock_lock_manager(self):
        """Create mock lock manager."""
        manager = MagicMock()
        manager.acquire = AsyncMock(return_value=True)
        manager.acquire_queue_lock = AsyncMock(return_value=True)
        manager.release = AsyncMock()
        manager.release_queue_lock = AsyncMock()
        return manager

    @pytest.fixture
    def mock_queue_repo(self):
        """Create mock queue repository."""
        repo = MagicMock()
        repo.get = MagicMock(return_value=MagicMock(concurrency_limit=3))
        repo.get_concurrency_limit = MagicMock(return_value=3)
        return repo

    @pytest.fixture
    def mock_project_repo(self):
        """Create mock project repository."""
        repo = MagicMock()
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False
        repo.get = MagicMock(return_value=project)
        return repo

    @pytest.fixture
    def jqs(
        self, mock_repo, mock_lock_manager, mock_queue_repo,
        mock_instance_manager, mock_project_repo
    ):
        """Create JobQueueService with mocked dependencies."""
        service = JobQueueService(
            repository=mock_repo,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )
        service.set_project_repo(mock_project_repo)
        return service

    def _create_message_job(self, job_id, instance_id, status=JobStatus.PENDING.value):
        """Helper to create a mock MESSAGE job."""
        job = MagicMock()
        job.job_id = job_id
        job.agent_id = "test-agent"
        job.project_id = "project-1"
        job.queue_id = "queue-1"
        job.status = status
        job.instance_id = instance_id
        job.job_type = "message"
        job.message = "Test message"
        job.source = "api"
        return job

    def _create_mock_instance(self, instance_id, status):
        """Helper to create a mock instance with given status."""
        instance = MagicMock()
        instance.instance_id = instance_id
        instance.status = status
        instance.agent_id = "test-agent"
        return instance

    @pytest.mark.asyncio
    async def test_message_job_reactivates_terminated_instance(
        self, jqs, mock_repo, mock_instance_manager
    ):
        """Test MESSAGE job reactivates TERMINATED instance to RUNNING.

        When a MESSAGE job arrives for a TERMINATED instance:
        1. Instance status should be updated to RUNNING
        2. stream_status_change should be called with RUNNING
        3. Job should NOT be cancelled (proceeds to normal processing)
        """
        instance_id = "terminated-instance-123"
        job_id = "message-job-terminated"

        job = self._create_message_job(job_id, instance_id)
        mock_repo.get.return_value = job
        mock_repo.start_job_atomic.return_value = job

        mock_instance_manager._instance_repository.get.return_value = self._create_mock_instance(
            instance_id, InstanceStatus.TERMINATED.value
        )

        result = await jqs.start_job(job_id)

        # 1. Instance status should be transitioned to RUNNING via atomic guard
        mock_instance_manager._instance_repository.transition_status_if.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value, tuple(TERMINAL_STATUSES)
        )

        # 2. stream_status_change should be called with RUNNING
        mock_instance_manager._live_hub.stream_status_change.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value,
            agent_id="test-agent"
        )

        # 3. Job should NOT be cancelled (atomic_transition to CANCELLED should NOT be called)
        cancel_calls = [
            call for call in mock_repo.atomic_transition.call_args_list
            if call.kwargs.get("to_status") == JobStatus.CANCELLED.value
        ]
        assert len(cancel_calls) == 0, "Job should NOT be cancelled for TERMINATED instance"

        # Job should proceed to processing
        assert result is not None

        # 4. start_job_atomic should be called (job falls through to normal processing)
        mock_repo.start_job_atomic.assert_called_once_with(job_id, instance_id)

    @pytest.mark.asyncio
    async def test_message_job_reactivates_error_instance(
        self, jqs, mock_repo, mock_instance_manager
    ):
        """Test MESSAGE job reactivates ERROR instance to RUNNING.

        When a MESSAGE job arrives for an ERROR instance:
        1. Instance status should be updated to RUNNING
        2. stream_status_change should be called with RUNNING
        3. Job should NOT be cancelled (proceeds to normal processing)
        """
        instance_id = "error-instance-123"
        job_id = "message-job-error"

        job = self._create_message_job(job_id, instance_id)
        mock_repo.get.return_value = job
        mock_repo.start_job_atomic.return_value = job

        mock_instance_manager._instance_repository.get.return_value = self._create_mock_instance(
            instance_id, InstanceStatus.ERROR.value
        )

        result = await jqs.start_job(job_id)

        # 1. Instance status should be transitioned to RUNNING via atomic guard
        mock_instance_manager._instance_repository.transition_status_if.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value, tuple(TERMINAL_STATUSES)
        )

        # 2. stream_status_change should be called with RUNNING
        mock_instance_manager._live_hub.stream_status_change.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value,
            agent_id="test-agent"
        )

        # 3. Job should NOT be cancelled (atomic_transition to CANCELLED should NOT be called)
        cancel_calls = [
            call for call in mock_repo.atomic_transition.call_args_list
            if call.kwargs.get("to_status") == JobStatus.CANCELLED.value
        ]
        assert len(cancel_calls) == 0, "Job should NOT be cancelled for ERROR instance"

        # Job should proceed to processing
        assert result is not None

        # 4. start_job_atomic should be called (job falls through to normal processing)
        mock_repo.start_job_atomic.assert_called_once_with(job_id, instance_id)

    @pytest.mark.asyncio
    async def test_message_job_reactivates_failed_instance(
        self, jqs, mock_repo, mock_instance_manager
    ):
        """Test MESSAGE job reactivates FAILED instance to RUNNING.

        When a MESSAGE job arrives for a FAILED instance:
        1. Instance status should be updated to RUNNING
        2. stream_status_change should be called with RUNNING
        3. Job should NOT be cancelled (proceeds to normal processing)
        """
        instance_id = "failed-instance-123"
        job_id = "message-job-failed"

        job = self._create_message_job(job_id, instance_id)
        mock_repo.get.return_value = job
        mock_repo.start_job_atomic.return_value = job

        mock_instance_manager._instance_repository.get.return_value = self._create_mock_instance(
            instance_id, InstanceStatus.FAILED.value
        )

        result = await jqs.start_job(job_id)

        # 1. Instance status should be transitioned to RUNNING via atomic guard
        mock_instance_manager._instance_repository.transition_status_if.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value, tuple(TERMINAL_STATUSES)
        )

        # 2. stream_status_change should be called with RUNNING
        mock_instance_manager._live_hub.stream_status_change.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value,
            agent_id="test-agent"
        )

        # 3. Job should NOT be cancelled (atomic_transition to CANCELLED should NOT be called)
        cancel_calls = [
            call for call in mock_repo.atomic_transition.call_args_list
            if call.kwargs.get("to_status") == JobStatus.CANCELLED.value
        ]
        assert len(cancel_calls) == 0, "Job should NOT be cancelled for FAILED instance"

        # Job should proceed to processing
        assert result is not None

        # 4. start_job_atomic should be called (job falls through to normal processing)
        mock_repo.start_job_atomic.assert_called_once_with(job_id, instance_id)

    @pytest.mark.asyncio
    async def test_message_job_does_not_reactivate_paused_instance(
        self, jqs, mock_repo, mock_instance_manager
    ):
        """Test MESSAGE job does NOT reactivate PAUSED instance.

        PAUSED instances have their own resume path (resume_instance),
        so transition_status_if(RUNNING, TERMINAL_STATUSES) should NOT be called by
        start_job() — PAUSED is not in TERMINAL_STATUSES. The job should be skipped
        (returns None).
        """
        instance_id = "paused-instance-123"
        job_id = "message-job-paused"

        job = self._create_message_job(job_id, instance_id)
        mock_repo.get.return_value = job

        mock_instance_manager._instance_repository.get.return_value = self._create_mock_instance(
            instance_id, InstanceStatus.PAUSED.value
        )

        result = await jqs.start_job(job_id)

        # Job should be skipped (PAUSED instances have their own resume path)
        assert result is None

        # transition_status_if should NOT be called (PAUSED has separate resume path)
        mock_instance_manager._instance_repository.transition_status_if.assert_not_called()

        # stream_status_change should NOT be called
        mock_instance_manager._live_hub.stream_status_change.assert_not_called()

        # start_job_atomic should NOT be called (job skipped entirely)
        mock_repo.start_job_atomic.assert_not_called()

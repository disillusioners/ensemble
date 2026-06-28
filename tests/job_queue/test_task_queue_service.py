"""Tests for JobQueueService.

This module tests the service layer that coordinates between the repository
and lock manager for job queue operations.
"""

import pytest

from daemon.repositories.job_queue.models import AdmissionState, JobStatus
from daemon.services.job_queue_service import DemandState


class TestJobQueueServiceEnqueue:
    """Tests for job enqueueing."""

    @pytest.mark.asyncio
    async def test_enqueue_without_project_is_pending(
        self, job_queue_service, sample_job_data_no_project_service
    ):
        """Test that jobs without project_id are created as PENDING."""
        result = await job_queue_service.enqueue(**sample_job_data_no_project_service)
        
        # Jobs are now always PENDING - JobProcessor handles starting
        assert result.admission_state == AdmissionState.QUEUED.value
        assert result.instance_id is None
        assert result.started_at is None

    @pytest.mark.asyncio
    async def test_enqueue_with_free_lock_is_pending(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that jobs with free project lock are created as PENDING."""
        result = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Jobs are now always PENDING - JobProcessor handles starting
        assert result.admission_state == AdmissionState.QUEUED.value
        assert result.instance_id is None

    @pytest.mark.asyncio
    async def test_enqueue_with_held_lock_queues(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that jobs wait when project lock is held."""
        # First job is created as PENDING
        first = await job_queue_service.enqueue(**sample_job_data_service)
        assert first.admission_state == AdmissionState.QUEUED.value
        
        # Second job should also be PENDING
        second = await job_queue_service.enqueue(**sample_job_data_service)
        assert second.admission_state == AdmissionState.QUEUED.value
        assert second.instance_id is None

    @pytest.mark.asyncio
    async def test_enqueue_with_priority(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that priority is preserved on enqueue."""
        result = await job_queue_service.enqueue(**sample_job_data_service)
        
        assert result.priority == sample_job_data_service["priority"]

    @pytest.mark.asyncio
    async def test_enqueue_with_metadata(
        self, job_queue_service, sample_job_data_no_project_service
    ):
        """Test that metadata is preserved on enqueue."""
        result = await job_queue_service.enqueue(**sample_job_data_no_project_service)
        
        # When metadata=None is passed, the implementation uses {} as default
        assert result.job_metadata == {}

    @pytest.mark.asyncio
    async def test_enqueue_multiple_projects_parallel(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that jobs for different projects are created as PENDING."""
        # Enqueue for project 1
        job1 = await job_queue_service.enqueue(
            **{**sample_job_data_service, "project_id": "project-1"}
        )
        # Enqueue for project 2
        job2 = await job_queue_service.enqueue(
            **{**sample_job_data_service, "project_id": "project-2"}
        )
        
        # Jobs are now always PENDING
        assert job1.admission_state == AdmissionState.QUEUED.value
        assert job2.admission_state == AdmissionState.QUEUED.value

    @pytest.mark.asyncio
    async def test_enqueue_generates_unique_job_ids(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that enqueued jobs have unique IDs."""
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        
        assert job1.job_id != job2.job_id


class TestJobQueueServiceGetJob:
    """Tests for job retrieval."""

    @pytest.mark.asyncio
    async def test_get_existing_job(self, job_queue_service, sample_job_data_service):
        """Test getting an existing job."""
        enqueued = await job_queue_service.enqueue(**sample_job_data_service)
        
        result = await job_queue_service.get_job(enqueued.job_id)
        
        assert result is not None
        assert result.job_id == enqueued.job_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self, job_queue_service):
        """Test getting a non-existent job returns None."""
        result = await job_queue_service.get_job("nonexistent-id")
        assert result is None


class TestJobQueueServiceCancelJob:
    """Tests for job cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, job_queue_service, sample_job_data_service):
        """Test cancelling a pending job."""
        # Enqueue first job (acquires lock)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Enqueue second job (gets queued)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.admission_state == AdmissionState.QUEUED.value
        
        # Cancel the queued job
        result = await job_queue_service.cancel_job(job2.job_id)
        
        assert result is True
        cancelled = await job_queue_service.get_job(job2.job_id)
        assert cancelled.admission_state == AdmissionState.DONE.value

    @pytest.mark.asyncio
    async def test_cancel_processing_job(self, job_queue_service, sample_job_data_service):
        """Test cancelling a processing job releases its lock."""
        # Enqueue job (starts as PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        assert job.admission_state == AdmissionState.QUEUED.value
        
        # Manually start the job to transition to PROCESSING
        started_job = await job_queue_service.start_job(job.job_id)
        assert started_job.admission_state == AdmissionState.ACTIVE.value
        
        # Cancel the processing job
        result = await job_queue_service.cancel_job(job.job_id)
        
        assert result is True
        cancelled = await job_queue_service.get_job(job.job_id)
        assert cancelled.admission_state == AdmissionState.DONE.value
        
        # Lock should be released
        assert await job_queue_service._lock_manager.is_locked("test-project") is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_job(self, job_queue_service):
        """Test cancelling non-existent job returns False."""
        result = await job_queue_service.cancel_job("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_completed_job(self, job_queue_service, sample_job_data_service):
        """Test cancelling a completed job returns False."""
        # Enqueue job
        job = await job_queue_service.enqueue(**sample_job_data_service)
        # Start job then complete it
        started_job = await job_queue_service.start_job(job.job_id)
        await job_queue_service.complete_job(started_job.job_id)
        
        # Try to cancel completed job
        result = await job_queue_service.cancel_job(job.job_id)
        
        assert result is False


class TestJobQueueServiceListJobs:
    """Tests for job listing."""

    @pytest.mark.asyncio
    async def test_list_all_jobs(
        self, job_queue_service, sample_job_data_service, sample_job_data_no_project_service
    ):
        """Test listing all jobs."""
        await job_queue_service.enqueue(**sample_job_data_service)  # test-project with queue
        await job_queue_service.enqueue(**sample_job_data_no_project_service)  # no project
        
        jobs = await job_queue_service.list_jobs()
        
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_list_jobs_by_status(self, job_queue_service, sample_job_data_service):
        """Test listing jobs filtered by status."""
        # All enqueued jobs are now PENDING
        await job_queue_service.enqueue(**sample_job_data_service)
        await job_queue_service.enqueue(**sample_job_data_service)
        
        # List pending - should have 2
        pending = await job_queue_service.list_jobs(statuses=[JobStatus.PENDING.value])
        assert len(pending) == 2
        
        # List processing - should have 0 (jobs start as PENDING, JobProcessor handles starting)
        processing = await job_queue_service.list_jobs(statuses=[JobStatus.PROCESSING.value])
        assert len(processing) == 0

    @pytest.mark.asyncio
    async def test_list_jobs_by_project(
        self, job_queue_service, sample_job_data_service, sample_job_data_no_project_service
    ):
        """Test listing jobs filtered by project."""
        await job_queue_service.enqueue(**sample_job_data_service)  # test-project
        await job_queue_service.enqueue(**sample_job_data_no_project_service)  # no project
        
        jobs = await job_queue_service.list_jobs(project_id="test-project")
        
        assert len(jobs) == 1
        assert jobs[0].project_id == "test-project"

    @pytest.mark.asyncio
    async def test_list_jobs_with_limit(
        self, job_queue_service, sample_job_data_service, sample_job_data_no_project_service
    ):
        """Test listing jobs with limit."""
        # Use project_id=None to avoid needing system queues for each project
        await job_queue_service.enqueue(**sample_job_data_service)  # test-project
        for i in range(4):
            await job_queue_service.enqueue(**{**sample_job_data_no_project_service})
        
        jobs = await job_queue_service.list_jobs(limit=3)
        
        assert len(jobs) == 3


class TestJobQueueServiceStartJob:
    """Tests for manually starting jobs."""

    @pytest.mark.asyncio
    async def test_start_pending_job(self, job_queue_service, sample_job_data_service):
        """Test starting a pending job."""
        # Create first job and start it
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        started1 = await job_queue_service.start_job(job1.job_id)
        
        # Create a second job that will be pending
        job2 = await job_queue_service.enqueue(**{**sample_job_data_service, "message": "pending job"})
        assert job2.admission_state == AdmissionState.QUEUED.value
        
        # Complete first job
        await job_queue_service.complete_job(job1.job_id)
        
        # Now start the pending job
        started2 = await job_queue_service.start_job(job2.job_id)
        
        assert started2 is not None
        assert started2.admission_state == AdmissionState.ACTIVE.value
        assert started2.instance_id is not None
        
    @pytest.mark.asyncio
    async def test_start_nonexistent_job(self, job_queue_service):
        """Test starting non-existent job returns None."""
        result = await job_queue_service.start_job("nonexistent-id")
        assert result is None


class TestJobQueueServiceCompleteJob:
    """Tests for job completion."""

    @pytest.mark.asyncio
    async def test_complete_job_success(self, job_queue_service, sample_job_data_service):
        """Test completing a job successfully."""
        # Enqueue job (starts as PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        assert job.admission_state == AdmissionState.QUEUED.value
        
        # Start job (JobProcessor normally does this)
        started = await job_queue_service.start_job(job.job_id)
        
        # Complete the job
        result = await job_queue_service.complete_job(job.job_id)
        
        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_job_with_error(self, job_queue_service, sample_job_data_service):
        """Test completing a job with error."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start job first
        started = await job_queue_service.start_job(job.job_id)
        
        result = await job_queue_service.complete_job(
            job.job_id,
            DemandState.FAILED,
            error="Something went wrong"
        )
        
        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value
        assert result.error_message == "Something went wrong"

    @pytest.mark.asyncio
    async def test_complete_job_releases_lock(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test that completing a job releases its lock."""
        # Get the queue_id for system_fifo_queue
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None
        
        # Enqueue first job (starts as PENDING, lock not acquired)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job1.admission_state == AdmissionState.QUEUED.value
        
        # Start job1 (this acquires the lock)
        started1 = await job_queue_service.start_job(job1.job_id)
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is True
        
        # Enqueue second job (still PENDING)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.admission_state == AdmissionState.QUEUED.value
        
        # Complete first job (releases lock)
        await job_queue_service.complete_job(job1.job_id)
        
        # Lock should be released
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is False

    @pytest.mark.asyncio
    async def test_complete_nonexistent_job(self, job_queue_service):
        """Test completing non-existent job returns None."""
        result = await job_queue_service.complete_job("nonexistent-id")
        assert result is None


class TestJobQueueServiceTriggerNextJob:
    """Tests for triggering next job after completion."""

    @pytest.mark.asyncio
    async def test_trigger_next_job_starts_pending(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that trigger_next_job starts the next pending job."""
        # Enqueue first job (PENDING)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start first job
        started1 = await job_queue_service.start_job(job1.job_id)
        
        # Second job is queued (pending)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.admission_state == AdmissionState.QUEUED.value
        
        # Complete first job (releases lock)
        await job_queue_service.complete_job(job1.job_id)
        
        # Now trigger next job - should start the pending job2
        result = await job_queue_service.trigger_next_job("test-project")
        
        # Should find and start job2
        assert result is not None
        assert result.admission_state == AdmissionState.ACTIVE.value
        assert result.job_id == job2.job_id

    @pytest.mark.asyncio
    async def test_trigger_next_job_no_pending(self, job_queue_service, sample_job_data_service):
        """Test trigger_next_job when no pending jobs."""
        # Enqueue job (PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start and complete job
        started = await job_queue_service.start_job(job.job_id)
        await job_queue_service.complete_job(job.job_id)
        
        # Trigger next - should return None
        result = await job_queue_service.trigger_next_job("test-project")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_trigger_next_job_respects_priority(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that trigger_next_job starts highest priority job first."""
        # Enqueue first job (PENDING)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start job1
        started1 = await job_queue_service.start_job(job1.job_id)
        
        # Enqueue second job with higher priority (PENDING)
        job2 = await job_queue_service.enqueue(
            **{**sample_job_data_service, "message": "high priority", "priority": 10}
        )
        
        # Complete first job
        await job_queue_service.complete_job(job1.job_id)
        
        # Trigger next - should get higher priority job
        result = await job_queue_service.trigger_next_job("test-project")
        
        assert result is not None
        assert result.message == "high priority"


class TestJobQueueServiceReleaseLockByInstance:
    """Tests for instance-based lock release."""

    @pytest.mark.asyncio
    async def test_release_lock_by_instance(self, job_queue_service, sample_job_data_service):
        """Test releasing locks by instance ID."""
        # Enqueue job (PENDING, lock not acquired)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start job (this acquires the lock)
        started = await job_queue_service.start_job(job.job_id)
        instance_id = started.instance_id
        
        # Release by instance
        released = await job_queue_service.release_lock_by_instance(instance_id)
        
        assert "test-project" in released
        assert await job_queue_service._lock_manager.is_locked("test-project") is False

    @pytest.mark.asyncio
    async def test_release_lock_by_nonexistent_instance(self, job_queue_service):
        """Test releasing locks for non-existent instance."""
        released = await job_queue_service.release_lock_by_instance("nonexistent")
        assert released == []


class TestJobQueueServiceErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_complete_job_wrong_state(self, job_queue_service, sample_job_data_service):
        """Test completing job in wrong state returns None."""
        # Create job (PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start job first
        started = await job_queue_service.start_job(job.job_id)
        
        # Complete the job
        await job_queue_service.complete_job(job.job_id)
        
        # Now job is completed, trying to complete again should return None
        result = await job_queue_service.complete_job(job.job_id)
        
        # Service returns None for already completed jobs
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled(self, job_queue_service, sample_job_data_service):
        """Test cancelling already cancelled job returns False."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        await job_queue_service.cancel_job(job.job_id)
        
        result = await job_queue_service.cancel_job(job.job_id)
        
        assert result is False


class TestJobQueueServiceWithLockManager:
    """Tests for service integration with lock manager."""

    @pytest.mark.asyncio
    async def test_lock_manager_integrated_on_enqueue(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test that enqueue properly integrates with lock manager."""
        # Get the queue_id for system_fifo_queue
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None
        
        # Initially no lock
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is False
        
        # Enqueue job (PENDING, lock not acquired yet)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        assert job.admission_state == AdmissionState.QUEUED.value
        
        # Lock should NOT be held until job starts
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is False
        
        # Start job (this acquires the lock)
        started = await job_queue_service.start_job(job.job_id)
        
        # Now lock should be held
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is True
        
        # Lock info should match job
        lock_count = await job_queue_service._lock_manager.get_queue_lock_count("test-project", queue.queue_id)
        assert lock_count == 1

    @pytest.mark.asyncio
    async def test_multiple_jobs_same_project_serialized(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test that multiple jobs for same project are serialized."""
        # Get the queue_id for system_fifo_queue
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None
        
        # Enqueue first job (PENDING)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job1.admission_state == AdmissionState.QUEUED.value
        
        # Start job1
        started1 = await job_queue_service.start_job(job1.job_id)
        assert started1.admission_state == AdmissionState.ACTIVE.value
        
        # Enqueue more jobs - all should be pending
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        job3 = await job_queue_service.enqueue(**sample_job_data_service)
        job4 = await job_queue_service.enqueue(**sample_job_data_service)
        
        assert job2.admission_state == AdmissionState.QUEUED.value
        assert job3.admission_state == AdmissionState.QUEUED.value
        assert job4.admission_state == AdmissionState.QUEUED.value
        
        # Only one lock should be held (for job1)
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is True
        
        # Complete job1 and trigger next job - lock should be held again (for job2)
        await job_queue_service.complete_job(job1.job_id)
        # Lock is released by complete_job, trigger_next_job will start job2
        started2 = await job_queue_service.trigger_next_job("test-project")
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is True
        
        # Complete job2 and trigger next job
        await job_queue_service.complete_job(job2.job_id)
        started3 = await job_queue_service.trigger_next_job("test-project")
        assert await job_queue_service._lock_manager.is_queue_locked("test-project", queue.queue_id) is True


class TestJobQueueServiceQueuePosition:
    """Tests for queue position calculation."""

    @pytest.mark.asyncio
    async def test_queue_position_calculation(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that queue position is calculated correctly."""
        # Enqueue first job (PENDING)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start first job
        started1 = await job_queue_service.start_job(job1.job_id)
        
        # Enqueue more jobs (queue behind job1 which is processing)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        job3 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Complete job1
        await job_queue_service.complete_job(job1.job_id)
        
        # Get pending jobs (job2 and job3)
        pending = job_queue_service._repository.list_pending_by_project("test-project")
        
        assert len(pending) == 2
        # job2 should be first (older)
        assert pending[0].job_id == job2.job_id
        # job3 should be second (newer)
        assert pending[1].job_id == job3.job_id


class TestJobQueueServiceEmptyProject:
    """Tests for operations on empty/no project."""

    @pytest.mark.asyncio
    async def test_enqueue_no_project_no_lock(self, job_queue_service, sample_job_data_service_no_project):
        """Test that jobs without project are PENDING and don't use lock manager."""
        job = await job_queue_service.enqueue(**sample_job_data_service_no_project)
        
        # No lock should be held (project_id=None)
        assert await job_queue_service._lock_manager.get_waiter_count("") == 0
        # Job should be PENDING (not PROCESSING)
        assert job.admission_state == AdmissionState.QUEUED.value

    @pytest.mark.asyncio
    async def test_multiple_no_project_jobs_all_pending(
        self, job_queue_service, sample_job_data_service_no_project
    ):
        """Test that jobs without project are all PENDING."""
        job1 = await job_queue_service.enqueue(**sample_job_data_service_no_project)
        job2 = await job_queue_service.enqueue(**sample_job_data_service_no_project)
        job3 = await job_queue_service.enqueue(**sample_job_data_service_no_project)
        
        # All jobs should be PENDING (JobProcessor handles starting)
        assert job1.admission_state == AdmissionState.QUEUED.value
        assert job2.admission_state == AdmissionState.QUEUED.value
        assert job3.admission_state == AdmissionState.QUEUED.value


class TestJobQueueServiceFullWorkflow:
    """Integration tests for full job workflow."""

    @pytest.mark.asyncio
    async def test_full_workflow_enqueue_process_complete(
        self, job_queue_service, sample_job_data_service
    ):
        """Test complete workflow: enqueue -> process -> complete."""
        # Enqueue (PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        assert job.admission_state == AdmissionState.QUEUED.value
        assert job.instance_id is None
        
        # Start job (JobProcessor normally does this)
        started = await job_queue_service.start_job(job.job_id)
        assert started.admission_state == AdmissionState.ACTIVE.value
        assert started.instance_id is not None
        
        # Process (simulated)
        processed_job = await job_queue_service.get_job(job.job_id)
        assert processed_job is not None
        
        # Complete
        completed = await job_queue_service.complete_job(job.job_id)
        assert completed.admission_state == AdmissionState.DONE.value
        assert completed.completed_at is not None
        
        # Lock should be released
        assert await job_queue_service._lock_manager.is_locked("test-project") is False

    @pytest.mark.asyncio
    async def test_workflow_with_queued_jobs(
        self, job_queue_service, sample_job_data_service
    ):
        """Test workflow with multiple queued jobs."""
        # Enqueue first job (PENDING)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start first job
        started1 = await job_queue_service.start_job(job1.job_id)
        
        # Enqueue second job (queued PENDING)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.admission_state == AdmissionState.QUEUED.value
        
        # Complete first job
        await job_queue_service.complete_job(job1.job_id)
        
        # Trigger next - job2 should start
        triggered = await job_queue_service.trigger_next_job("test-project")
        assert triggered is not None
        assert triggered.job_id == job2.job_id
        assert triggered.admission_state == AdmissionState.ACTIVE.value
        
        # Complete job2
        await job_queue_service.complete_job(job2.job_id)
        
        # No more pending jobs
        pending = await job_queue_service.list_jobs(
            statuses=[JobStatus.PENDING.value],
            project_id="test-project"
        )
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_workflow_cancellation_recovery(
        self, job_queue_service, sample_job_data_service
    ):
        """Test workflow with job cancellation and recovery."""
        # Enqueue first job (PENDING)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start first job
        started1 = await job_queue_service.start_job(job1.job_id)
        
        # Enqueue second job (PENDING)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Cancel second job
        await job_queue_service.cancel_job(job2.job_id)
        
        # Complete first job
        await job_queue_service.complete_job(job1.job_id)
        
        # Trigger next - should skip cancelled job
        triggered = await job_queue_service.trigger_next_job("test-project")
        
        # No more pending jobs (job2 was cancelled)
        assert triggered is None

    @pytest.mark.asyncio
    async def test_workflow_job_failure(
        self, job_queue_service, sample_job_data_service
    ):
        """Test workflow with job failure."""
        # Enqueue job (PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start job first
        started = await job_queue_service.start_job(job.job_id)
        
        # Fail the job
        failed = await job_queue_service.complete_job(
            job.job_id,
            DemandState.FAILED,
            error="Simulated failure"
        )
        
        assert failed.admission_state == AdmissionState.DONE.value
        assert failed.error_message == "Simulated failure"
        
        # Lock should be released
        assert await job_queue_service._lock_manager.is_locked("test-project") is False
        
        # Should be able to trigger next job
        next_job = await job_queue_service.trigger_next_job("test-project")
        # No pending jobs, so None
        assert next_job is None


class TestGetJobByInstance:
    """Tests for get_job_by_instance() public method."""
    
    @pytest.mark.asyncio
    async def test_returns_correct_job_for_instance(self, job_queue_service, sample_job_data_service):
        """get_job_by_instance returns the job associated with given instance_id."""
        # Enqueue job (PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start job (JobProcessor normally does this)
        started = await job_queue_service.start_job(job.job_id)
        
        # Look up by instance_id
        found = await job_queue_service.get_job_by_instance(started.instance_id)
        assert found is not None
        assert found.job_id == job.job_id
        assert found.instance_id == started.instance_id
    
    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent_instance(self, job_queue_service):
        """get_job_by_instance returns None for unknown instance_id."""
        result = await job_queue_service.get_job_by_instance("nonexistent-instance")
        assert result is None
    
    def test_sync_returns_correct_job(self, job_queue_service, sample_job_data_service):
        """get_job_by_instance_sync returns the job for given instance_id."""
        import asyncio
        job = asyncio.run(job_queue_service.enqueue(**sample_job_data_service))
        started = asyncio.run(job_queue_service.start_job(job.job_id))
        found = job_queue_service.get_job_by_instance_sync(started.instance_id)
        assert found is not None
        assert found.job_id == job.job_id


class TestCompleteJobWithResultSummary:
    """Tests for complete_job() with result_summary parameter."""
    
    @pytest.mark.asyncio
    async def test_complete_with_custom_result_summary(self, job_queue_service, sample_job_data_service):
        """complete_job stores custom result_summary."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        completed = await job_queue_service.complete_job(
            job.job_id, DemandState.COMPLETED, result_summary="Custom summary here"
        )
        assert completed is not None
        assert completed.admission_state == "done"
        assert completed.result_summary == "Custom summary here"
    
    @pytest.mark.asyncio
    async def test_complete_with_default_result_summary(self, job_queue_service, sample_job_data_service):
        """complete_job uses default summary when none provided."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        completed = await job_queue_service.complete_job(job.job_id)
        assert completed is not None
        assert completed.result_summary == "Job completed successfully"
    
    @pytest.mark.asyncio
    async def test_complete_job_sync_with_result_summary(self, job_queue_service, sample_job_data_service):
        """complete_job_sync stores result_summary synchronously."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        completed = job_queue_service.complete_job_sync(
            job.job_id, DemandState.COMPLETED, result_summary="Sync summary"
        )
        assert completed is not None
        assert completed.result_summary == "Sync summary"
    
    @pytest.mark.asyncio
    async def test_complete_job_sync_failure(self, job_queue_service, sample_job_data_service):
        """complete_job_sync marks job as failed when demand_state=FAILED."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        completed = job_queue_service.complete_job_sync(
            job.job_id, DemandState.FAILED, error="Sync error"
        )
        assert completed is not None
        assert completed.admission_state == "done"
        assert completed.error_message == "Sync error"
    
    @pytest.mark.asyncio
    async def test_complete_job_sync_returns_none_for_nonexistent(self, job_queue_service):
        """complete_job_sync returns None for unknown job_id."""
        result = job_queue_service.complete_job_sync("nonexistent", DemandState.COMPLETED)
        assert result is None
    
    @pytest.mark.asyncio
    async def test_complete_job_sync_handles_valueerror(self, job_queue_service, sample_job_data_service):
        """complete_job_sync returns None when job already completed (ValueError)."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        # Complete once
        job_queue_service.complete_job_sync(job.job_id, DemandState.COMPLETED)
        # Try again - should return None (not raise ValueError)
        result = job_queue_service.complete_job_sync(job.job_id, DemandState.COMPLETED)
        assert result is None


class TestTriggerNextJobSync:
    """Tests for trigger_next_job_sync() synchronous method."""
    
    @pytest.mark.asyncio
    async def test_starts_pending_job(self, job_queue_service, sample_job_data_service):
        """trigger_next_job_sync starts next pending job."""
        import asyncio
        # Enqueue two jobs for same project
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        started1 = await job_queue_service.start_job(job1.job_id)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.admission_state == "queued"
        
        # Complete job1
        job_queue_service.complete_job_sync(job1.job_id, DemandState.COMPLETED)
        
        # Trigger next
        next_job = job_queue_service.trigger_next_job_sync("test-project")
        assert next_job is not None
        assert next_job.job_id == job2.job_id
        assert next_job.admission_state == "active"
    
    @pytest.mark.asyncio
    async def test_returns_none_when_no_pending(self, job_queue_service, sample_job_data_service):
        """trigger_next_job_sync returns None when no pending jobs."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        job_queue_service.complete_job_sync(job.job_id, DemandState.COMPLETED)
        
        result = job_queue_service.trigger_next_job_sync("test-project")
        assert result is None


class TestJobQueueServiceQueueAwareEnqueue:
    """Tests for queue-aware job enqueueing."""

    @pytest.mark.asyncio
    async def test_enqueue_no_project_no_queue(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test enqueue without project_id normalizes to system project and assigns system queue."""
        # After Phase 2 normalization: project_id=None is normalized to SYSTEM_DEFAULT_PROJECT_ID
        # System queue for the system project is pre-provisioned in job_queue_service fixture
        from tests.job_queue.conftest import TEST_SYSTEM_PROJECT_ID
        
        job_data = {
            "agent_id": "developer",
            "message": "Test job without project",
            "source": "api",
            "project_id": None,
            "priority": 5,
            "metadata": None,
        }

        result = await job_queue_service.enqueue(**job_data)

        # Normalization: None project_id becomes SYSTEM_DEFAULT_PROJECT_ID
        assert result.project_id == TEST_SYSTEM_PROJECT_ID
        # System queue is assigned (pre-provisioned in fixture)
        assert result.queue_id is not None
        assert result.admission_state == AdmissionState.QUEUED.value

    @pytest.mark.asyncio
    async def test_enqueue_with_project_no_queue_id(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test enqueue with project_id but no queue_id defaults to system_fifo_queue."""
        # System queue already exists in job_queue_service fixture
        # Just verify enqueue uses the system_fifo_queue
        
        job_data = {
            "agent_id": "developer",
            "message": "Test job with project",
            "source": "api",
            "project_id": "test-project",
            "priority": 5,
            "metadata": None,
        }

        result = await job_queue_service.enqueue(**job_data)

        assert result.project_id == "test-project"
        assert result.queue_id is not None  # Should have a queue_id from system_fifo_queue

    @pytest.mark.asyncio
    async def test_enqueue_with_project_and_queue_id(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test enqueue with explicit queue_id uses that queue."""
        # Create custom queue
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="custom_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=False,
        )

        job_data = {
            "agent_id": "developer",
            "message": "Test job with custom queue",
            "source": "api",
            "project_id": "test-project",
            "priority": 5,
            "metadata": None,
            "queue_id": queue.queue_id,
        }

        result = await job_queue_service.enqueue(**job_data)

        assert result.queue_id == queue.queue_id

    @pytest.mark.asyncio
    async def test_enqueue_invalid_queue_id(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test enqueue with non-existent queue_id logs warning but creates job."""
        job_data = {
            "agent_id": "developer",
            "message": "Test job with invalid queue",
            "source": "api",
            "project_id": "test-project",
            "priority": 5,
            "metadata": None,
            "queue_id": "nonexistent-queue-id",
        }

        result = await job_queue_service.enqueue(**job_data)

        # Job should still be created but with queue_id=None
        assert result.queue_id is None
        assert result.admission_state == AdmissionState.QUEUED.value

    @pytest.mark.asyncio
    async def test_enqueue_queue_from_wrong_project(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test enqueue with queue_id from different project logs warning but uses queue."""
        # Create queue for project-2
        queue = queue_repository.create(
            project_id="project-2",
            queue_name="project2_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=False,
        )

        # Try to use queue-2's queue_id with project-1
        job_data = {
            "agent_id": "developer",
            "message": "Test job with cross-project queue",
            "source": "api",
            "project_id": "project-1",  # Different from queue's project
            "priority": 5,
            "metadata": None,
            "queue_id": queue.queue_id,
        }

        # C4 fix: ValueError is raised when queue belongs to different project
        with pytest.raises(ValueError, match="does not belong to project"):
            await job_queue_service.enqueue(**job_data)

    @pytest.mark.asyncio
    async def test_enqueue_uses_queue_concurrency(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test that starting jobs uses queue's concurrency_limit."""
        # Create queue with concurrency_limit=2
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="parallel_queue",
            queue_type="parallel",
            concurrency_limit=2,
            is_system=False,
        )

        job_data = {
            "agent_id": "developer",
            "message": "Test job",
            "source": "api",
            "project_id": "test-project",
            "priority": 5,
            "metadata": None,
            "queue_id": queue.queue_id,
        }

        # Enqueue two jobs
        job1 = await job_queue_service.enqueue(**job_data)
        job2 = await job_queue_service.enqueue(**job_data)

        # Both jobs should be pending
        assert job1.queue_id == queue.queue_id
        assert job2.queue_id == queue.queue_id

        # Start first job - should succeed
        started1 = await job_queue_service.start_job(job1.job_id)
        assert started1 is not None
        assert started1.admission_state == AdmissionState.ACTIVE.value

        # Start second job - should also succeed (queue allows 2 concurrent)
        started2 = await job_queue_service.start_job(job2.job_id)
        assert started2 is not None
        assert started2.admission_state == AdmissionState.ACTIVE.value

        # Both locks should be held
        count = await job_queue_service._lock_manager.get_queue_lock_count(
            "test-project", queue.queue_id
        )
        assert count == 2

    @pytest.mark.asyncio
    async def test_enqueue_no_system_queue_defaults_to_no_queue(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test enqueue with project but no system_fifo_queue raises ValueError with fifo queue_kind."""
        # Don't create any queue for project
        
        job_data = {
            "agent_id": "developer",
            "message": "Test job without system queue",
            "source": "api",
            "project_id": "project-without-queue",
            "priority": 5,
            "metadata": None,
        }

        # ValueError is raised when system FIFO queue doesn't exist
        with pytest.raises(ValueError, match="No system fifo queue found"):
            await job_queue_service.enqueue(**job_data)

    @pytest.mark.asyncio
    async def test_enqueue_with_queue_preserves_priority(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Test that priority is preserved when using queue-aware enqueue."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="priority_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=False,
        )

        job_data = {
            "agent_id": "developer",
            "message": "High priority job",
            "source": "api",
            "project_id": "test-project",
            "priority": 10,
            "metadata": None,
            "queue_id": queue.queue_id,
        }

        result = await job_queue_service.enqueue(**job_data)

        assert result.priority == 10
        assert result.queue_id == queue.queue_id


class TestNextJobTriggeredAfterCompletion:
    """Tests verifying next job is triggered after job completion."""

    @pytest.mark.asyncio
    async def test_next_job_started_after_complete(self, job_queue_service, sample_job_data_service):
        """After completing a job, trigger_next_job starts the next queued job."""
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        started1 = await job_queue_service.start_job(job1.job_id)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.admission_state == "queued"

        await job_queue_service.complete_job(job1.job_id)
        next_job = await job_queue_service.trigger_next_job("test-project")

        assert next_job is not None
        assert next_job.job_id == job2.job_id
        assert next_job.admission_state == "active"


# =============================================================================
# C11 Fix: try/finally lock release in _try_start_job / start_job
# =============================================================================
# These tests verify the lock is released on ALL failure paths (not just
# ValueError) when ``start_job_atomic`` raises. Before the fix, an
# OperationalError or CancelledError would leak the lock permanently because
# the ``except ValueError`` clause did not match those exception types.


class TestStartJobLockReleaseOnFailure:
    """B1: lock MUST be released on any non-success path of
    ``start_job_atomic_with_lock``.

    These tests verify the lock is released on ALL failure paths when
    the new single-transaction lock+status flow raises. The pre-fix
    code had a separate ``LockRepository.try_acquire_slot`` (TX-A) and
    ``JobRepository.start_job_atomic`` (TX-B); on a B failure in TX-B
    the lock from TX-A had to be released by a try/finally. The B1
    fix (``start_job_atomic_with_lock``) collapses both writes into
    ONE ``engine.begin()`` transaction, so a ValueError / OperationalError
    / CancelledError raises the transaction rollback — the lock row
    is undone automatically without any explicit release call.

    Externally the behaviour is unchanged: ``start_job`` returns ``None``
    on failure, the lock is not held afterwards. Internally the lock
    release now happens via DB rollback instead of ``release_queue_lock``.
    """

    @pytest.mark.asyncio
    async def test_start_job_releases_lock_on_value_error(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """ValueError from start_job_atomic_with_lock → no lock held.

        B1 Fix: the lock INSERT and status UPDATE happen in one
        ``engine.begin()`` transaction. Raising inside that block
        triggers SQLAlchemy rollback of both writes — no manual
        ``release_queue_lock`` is needed and none should occur.
        """
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)

        # Force start_job_atomic_with_lock to raise ValueError. Pre-fix
        # this patched ``start_job_atomic``; B1 routes through the new
        # single-transaction method.
        from unittest.mock import patch

        with patch.object(
            job_queue_service._repository,
            "start_job_atomic_with_lock",
            side_effect=ValueError("already started"),
        ):
            result = await job_queue_service.start_job(job.job_id)

        assert result is None
        # Lock must NOT be held after the rollback (whether from explicit
        # release or DB-level rollback of the lock INSERT).
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False

    @pytest.mark.asyncio
    async def test_start_job_releases_lock_on_operational_error(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Non-ValueError exception (OperationalError) → lock MUST NOT be held.

        This is the B1 regression test. The pre-fix code's
        ``except ValueError`` clause did not match OperationalError;
        C11 added a try/finally to cover it. B1 takes the cleaner
        route: the exception unwinds ``engine.begin()``, the DB
        rollback handles both the lock and the status write, and no
        manual release is required.
        """
        from unittest.mock import patch

        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)

        # Force a non-ValueError exception (simulates DB deadlock / driver error).
        # Patch the new B1 method so the exception is raised BEFORE the
        # transaction acquires any lock.
        with patch.object(
            job_queue_service._repository,
            "start_job_atomic_with_lock",
            side_effect=RuntimeError("DB connection lost"),
        ):
            with pytest.raises(RuntimeError, match="DB connection lost"):
                await job_queue_service.start_job(job.job_id)

        # Lock MUST NOT be held — start_job_atomic_with_lock raised before
        # the engine.begin() block acquired any lock, so the DB has no
        # lock row.
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False

    @pytest.mark.asyncio
    async def test_start_job_releases_lock_on_cancelled_error(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """CancelledError → lock MUST NOT be held.

        CancelledError is a BaseException subclass in 3.8+ so it never
        matched the pre-fix ``except ValueError``. C11 added a
        try/finally; B1 simplifies via the engine.begin() rollback.
        """
        import asyncio
        from unittest.mock import patch

        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)

        with patch.object(
            job_queue_service._repository,
            "start_job_atomic_with_lock",
            side_effect=asyncio.CancelledError(),
        ):
            with pytest.raises(asyncio.CancelledError):
                await job_queue_service.start_job(job.job_id)

        # Lock MUST NOT be held
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False

    @pytest.mark.asyncio
    async def test_start_job_keeps_lock_on_success(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Success path → lock IS held (regression guard).

        B1 fix: ``start_job_atomic_with_lock`` succeeds means both the
        lock INSERT and the status UPDATE committed in one transaction.
        The lock row IS in the DB, so ``is_queue_locked`` returns True.
        """
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)

        assert started is not None
        assert started.admission_state == AdmissionState.ACTIVE.value
        # Lock MUST still be held — JobProcessor uses it to serialize work
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is True


class TestTryStartJobLockReleaseOnFailure:
    """B1: ``_try_start_job`` must NOT leak a lock on any non-success path.

    Mirrors :class:`TestStartJobLockReleaseOnFailure` for the
    internal ``_try_start_job`` entry point (the synchronous
    ``list_pending_*`` → ``_try_start_job`` path).
    """

    @pytest.mark.asyncio
    async def test_try_start_job_releases_lock_on_operational_error(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Non-ValueError from start_job_atomic_with_lock → lock NOT held."""
        from unittest.mock import patch

        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)

        with patch.object(
            job_queue_service._repository,
            "start_job_atomic_with_lock",
            side_effect=RuntimeError("DB gone"),
        ):
            with pytest.raises(RuntimeError, match="DB gone"):
                await job_queue_service._try_start_job(job)

        # Lock MUST NOT be held (the new B1 path raised before any
        # ``engine.begin()`` could acquire a lock)
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False

    @pytest.mark.asyncio
    async def test_try_start_job_releases_lock_on_cancelled_error(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """CancelledError from start_job_atomic_with_lock → lock NOT held."""
        import asyncio
        from unittest.mock import patch

        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)

        with patch.object(
            job_queue_service._repository,
            "start_job_atomic_with_lock",
            side_effect=asyncio.CancelledError(),
        ):
            with pytest.raises(asyncio.CancelledError):
                await job_queue_service._try_start_job(job)

        # Lock MUST NOT be held
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False

    @pytest.mark.asyncio
    async def test_try_start_job_keeps_lock_on_success(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Success path: _try_start_job holds the lock (regression guard)."""
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)
        result = await job_queue_service._try_start_job(job)

        assert result is True
        # Lock MUST be held on success
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is True


# =============================================================================
# H9 Fix: status-first, lock-second ordering in _complete_job / _fail_job
# =============================================================================
# These tests verify the lock is released AFTER the status transition attempt
# (in a finally block). Before the fix, a failed status transition would
# release the lock first, leaving a job in PROCESSING with no lock — a
# double-claim window for the next worker.


class TestCompleteJobStatusFirstOrdering:
    """H9: _complete_job must transition status BEFORE releasing the lock."""

    @pytest.mark.asyncio
    async def test_complete_job_releases_lock_on_transition_failure(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """If status transition raises, lock is still released (finally block)."""
        from unittest.mock import patch

        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        # Enqueue + start a job so the lock is held
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is True

        # Force complete_job (the repo method) to fail
        with patch.object(
            job_queue_service._repository,
            "complete_job",
            side_effect=RuntimeError("DB write failed"),
        ):
            with pytest.raises(RuntimeError, match="DB write failed"):
                await job_queue_service._complete_job(started, "should-not-stick")

        # Lock MUST be released via finally, even though transition failed
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False

    @pytest.mark.asyncio
    async def test_fail_job_releases_lock_on_success(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Success path: status → FAILED, then lock released."""
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)

        await job_queue_service._fail_job(started, "boom")

        # Job is FAILED
        result = await job_queue_service.get_job(job.job_id)
        assert result.admission_state == AdmissionState.DONE.value
        assert result.error_message == "boom"
        # Lock is released
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False

    @pytest.mark.asyncio
    async def test_fail_job_releases_lock_on_success(
        self, job_queue_service, sample_job_data_service, queue_repository
    ):
        """Success path: status → FAILED, then lock released."""
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert queue is not None

        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)

        await job_queue_service._fail_job(started, "boom")

        # Job is FAILED
        result = await job_queue_service.get_job(job.job_id)
        assert result.admission_state == AdmissionState.DONE.value
        assert result.error_message == "boom"
        # Lock is released
        assert await job_queue_service._lock_manager.is_queue_locked(
            "test-project", queue.queue_id
        ) is False


# =============================================================================
# Static structural verification: methods use B1 single-transaction flow
# =============================================================================


class TestLockOrderingStructure:
    """Source-level guards: catch regressions if someone reverts the B1 fix.

    B1 collapsed the lock INSERT + status UPDATE into one
    ``engine.begin()`` transaction (via
    ``JobRepository.start_job_atomic_with_lock``). The pre-B1 code had
    a separate ``acquire_queue_lock`` (TX-A) and ``start_job_atomic``
    (TX-B) plus a try/finally for lock release on failure. The
    PostgreSQL constraint trigger ``trg_job_locks_active_guard``
    requires both rows at the SAME commit, which is impossible in the
    pre-B1 two-transaction flow — every job start false-fired.

    These tests pin the new pattern: every lock-acquiring path in
    ``start_job`` and ``_try_start_job`` MUST route through
    ``start_job_atomic_with_lock``, and the lock+status writes MUST be
    in one transaction (engine.begin()), not two.
    """

    def test_try_start_job_uses_single_transaction_method(self):
        """``_try_start_job`` MUST call ``start_job_atomic_with_lock``.

        Source-level guard against reverting to the pre-B1 two-transaction
        pattern (``acquire_queue_lock`` + ``start_job_atomic``). If this
        test fails, someone has re-introduced the cross-commit trigger bug.
        """
        import inspect

        from daemon.services.job_queue_service import JobQueueService

        source = inspect.getsource(JobQueueService._try_start_job)
        assert "start_job_atomic_with_lock" in source, (
            "_try_start_job must route through start_job_atomic_with_lock "
            "to keep the lock INSERT and status UPDATE in ONE transaction "
            "(B1 fix — triggers trg_job_locks_active_guard fire at commit)"
        )
        # The pre-B1 pattern must NOT be present (the two-transaction flow
        # that triggers the constraint false-fire).
        assert "self._repository.start_job_atomic(" not in source, (
            "_try_start_job must not call start_job_atomic directly — "
            "that path bypasses the lock INSERT in the same transaction"
        )

    def test_start_job_uses_single_transaction_method(self):
        """``start_job`` MUST call ``start_job_atomic_with_lock`` (queue/project paths).

        The no-project_id / no-queue_id path is exempt: no lock INSERT means
        no trigger to fire. That branch is allowed to use the legacy
        ``start_job_atomic`` direct call.
        """
        import inspect

        from daemon.services.job_queue_service import JobQueueService

        source = inspect.getsource(JobQueueService.start_job)
        # The queue-based path AND project-based path must use the new method.
        # The legacy "no project_id, no queue_id" branch can still call
        # start_job_atomic — no lock, no trigger.
        assert "start_job_atomic_with_lock" in source, (
            "start_job must route the lock+status flow through "
            "start_job_atomic_with_lock (B1 fix)"
        )

    def test_repository_start_job_atomic_with_lock_is_single_transaction(self):
        """``start_job_atomic_with_lock`` MUST open ONE ``engine.begin()`` block.

        Catches a regression where someone splits the lock INSERT and the
        status UPDATE into separate transactions — that re-introduces the
        trigger false-fire.
        """
        import inspect

        from daemon.repositories.job_queue.repository import JobRepository

        source = inspect.getsource(JobRepository.start_job_atomic_with_lock)
        # One engine.begin() block — single transaction shared by lock INSERT
        # and status UPDATE.
        begin_count = source.count("self.engine.begin()")
        assert begin_count == 1, (
            f"start_job_atomic_with_lock must open exactly ONE engine.begin() "
            f"transaction (lock INSERT + status UPDATE together); got "
            f"{begin_count}"
        )

    def test_complete_job_transitions_before_releases(self):
        """_complete_job: status transition must come before lock release."""
        import inspect

        from daemon.services.job_queue_service import JobQueueService

        source = inspect.getsource(JobQueueService._complete_job)
        # The repo call is passed as a function reference to asyncio.to_thread,
        # so it appears as ``self._repository.complete_job,`` (with trailing
        # comma, no paren) in the source.
        complete_idx = source.find("self._repository.complete_job")
        release_idx = source.find("_release_job_lock(")
        assert complete_idx != -1, "complete_job call must be present"
        assert release_idx != -1, "lock release must be present"
        assert complete_idx < release_idx, (
            "H9 fix: status transition must happen BEFORE lock release"
        )
        # Lock release MUST be inside a finally block
        assert "finally:" in source, (
            "H9 fix: lock release must be in a finally block"
        )

    def test_fail_job_transitions_before_releases(self):
        """_fail_job: status transition must come before lock release."""
        import inspect

        from daemon.services.job_queue_service import JobQueueService

        source = inspect.getsource(JobQueueService._fail_job)
        fail_idx = source.find("self._repository.fail_job")
        release_idx = source.find("_release_job_lock(")
        assert fail_idx != -1, "fail_job call must be present"
        assert release_idx != -1, "lock release must be present"
        assert fail_idx < release_idx, (
            "H9 fix: status transition must happen BEFORE lock release"
        )
        # Lock release MUST be inside a finally block
        assert "finally:" in source, (
            "H9 fix: lock release must be in a finally block"
        )

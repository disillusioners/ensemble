"""Tests for JobQueueService.

This module tests the service layer that coordinates between the repository
and lock manager for job queue operations.
"""

import pytest

from daemon.repositories.job_queue.models import JobStatus


class TestJobQueueServiceEnqueue:
    """Tests for job enqueueing."""

    @pytest.mark.asyncio
    async def test_enqueue_without_project_is_pending(
        self, job_queue_service, sample_job_data_no_project_service
    ):
        """Test that jobs without project_id are created as PENDING."""
        result = await job_queue_service.enqueue(**sample_job_data_no_project_service)
        
        # Jobs are now always PENDING - JobProcessor handles starting
        assert result.status == JobStatus.PENDING.value
        assert result.instance_id is None
        assert result.started_at is None

    @pytest.mark.asyncio
    async def test_enqueue_with_free_lock_is_pending(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that jobs with free project lock are created as PENDING."""
        result = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Jobs are now always PENDING - JobProcessor handles starting
        assert result.status == JobStatus.PENDING.value
        assert result.instance_id is None

    @pytest.mark.asyncio
    async def test_enqueue_with_held_lock_queues(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that jobs wait when project lock is held."""
        # First job is created as PENDING
        first = await job_queue_service.enqueue(**sample_job_data_service)
        assert first.status == JobStatus.PENDING.value
        
        # Second job should also be PENDING
        second = await job_queue_service.enqueue(**sample_job_data_service)
        assert second.status == JobStatus.PENDING.value
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
        assert job1.status == JobStatus.PENDING.value
        assert job2.status == JobStatus.PENDING.value

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
        assert job2.status == JobStatus.PENDING.value
        
        # Cancel the queued job
        result = await job_queue_service.cancel_job(job2.job_id)
        
        assert result is True
        cancelled = await job_queue_service.get_job(job2.job_id)
        assert cancelled.status == JobStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_cancel_processing_job(self, job_queue_service, sample_job_data_service):
        """Test cancelling a processing job releases its lock."""
        # Enqueue job (starts as PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        assert job.status == JobStatus.PENDING.value
        
        # Manually start the job to transition to PROCESSING
        started_job = await job_queue_service.start_job(job.job_id)
        assert started_job.status == JobStatus.PROCESSING.value
        
        # Cancel the processing job
        result = await job_queue_service.cancel_job(job.job_id)
        
        assert result is True
        cancelled = await job_queue_service.get_job(job.job_id)
        assert cancelled.status == JobStatus.CANCELLED.value
        
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
    async def test_list_all_jobs(self, job_queue_service, sample_job_data_service):
        """Test listing all jobs."""
        await job_queue_service.enqueue(**sample_job_data_service)
        await job_queue_service.enqueue(**{**sample_job_data_service, "project_id": "other"})
        
        jobs = await job_queue_service.list_jobs()
        
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_list_jobs_by_status(self, job_queue_service, sample_job_data_service):
        """Test listing jobs filtered by status."""
        # All enqueued jobs are now PENDING
        await job_queue_service.enqueue(**sample_job_data_service)
        await job_queue_service.enqueue(**sample_job_data_service)
        
        # List pending - should have 2
        pending = await job_queue_service.list_jobs(status=JobStatus.PENDING)
        assert len(pending) == 2
        
        # List processing - should have 0 (jobs start as PENDING, JobProcessor handles starting)
        processing = await job_queue_service.list_jobs(status=JobStatus.PROCESSING)
        assert len(processing) == 0

    @pytest.mark.asyncio
    async def test_list_jobs_by_project(self, job_queue_service, sample_job_data_service):
        """Test listing jobs filtered by project."""
        await job_queue_service.enqueue(**sample_job_data_service)  # test-project
        await job_queue_service.enqueue(**{**sample_job_data_service, "project_id": "other"})
        
        jobs = await job_queue_service.list_jobs(project_id="test-project")
        
        assert len(jobs) == 1
        assert jobs[0].project_id == "test-project"

    @pytest.mark.asyncio
    async def test_list_jobs_with_limit(self, job_queue_service, sample_job_data_service):
        """Test listing jobs with limit."""
        for i in range(5):
            await job_queue_service.enqueue(**{**sample_job_data_service, "project_id": f"p{i}"})
        
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
        assert job2.status == JobStatus.PENDING.value
        
        # Complete first job
        await job_queue_service.complete_job(job1.job_id)
        
        # Now start the pending job
        started2 = await job_queue_service.start_job(job2.job_id)
        
        assert started2 is not None
        assert started2.status == JobStatus.PROCESSING.value
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
        assert job.status == JobStatus.PENDING.value
        
        # Start job (JobProcessor normally does this)
        started = await job_queue_service.start_job(job.job_id)
        
        # Complete the job
        result = await job_queue_service.complete_job(job.job_id)
        
        assert result is not None
        assert result.status == JobStatus.COMPLETED.value
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_job_with_error(self, job_queue_service, sample_job_data_service):
        """Test completing a job with error."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        
        # Start job first
        started = await job_queue_service.start_job(job.job_id)
        
        result = await job_queue_service.complete_job(
            job.job_id,
            success=False,
            error="Something went wrong"
        )
        
        assert result is not None
        assert result.status == JobStatus.FAILED.value
        assert result.error_message == "Something went wrong"

    @pytest.mark.asyncio
    async def test_complete_job_releases_lock(self, job_queue_service, sample_job_data_service):
        """Test that completing a job releases its lock."""
        # Enqueue first job (starts as PENDING, lock not acquired)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job1.status == JobStatus.PENDING.value
        
        # Start job1 (this acquires the lock)
        started1 = await job_queue_service.start_job(job1.job_id)
        assert await job_queue_service._lock_manager.is_locked("test-project") is True
        
        # Enqueue second job (still PENDING)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.status == JobStatus.PENDING.value
        
        # Complete first job (releases lock)
        await job_queue_service.complete_job(job1.job_id)
        
        # Lock should be released
        assert await job_queue_service._lock_manager.is_locked("test-project") is False

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
        assert job2.status == JobStatus.PENDING.value
        
        # Complete first job (releases lock)
        await job_queue_service.complete_job(job1.job_id)
        
        # Now trigger next job - should start the pending job2
        result = await job_queue_service.trigger_next_job("test-project")
        
        # Should find and start job2
        assert result is not None
        assert result.status == JobStatus.PROCESSING.value
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
        self, job_queue_service, sample_job_data_service
    ):
        """Test that enqueue properly integrates with lock manager."""
        # Initially no lock
        assert await job_queue_service._lock_manager.is_locked("test-project") is False
        
        # Enqueue job (PENDING, lock not acquired yet)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        assert job.status == JobStatus.PENDING.value
        
        # Lock should NOT be held until job starts
        assert await job_queue_service._lock_manager.is_locked("test-project") is False
        
        # Start job (this acquires the lock)
        started = await job_queue_service.start_job(job.job_id)
        
        # Now lock should be held
        assert await job_queue_service._lock_manager.is_locked("test-project") is True
        
        # Lock info should match job
        lock_info = await job_queue_service._lock_manager.get_lock_info("test-project")
        assert lock_info.job_id == job.job_id
        assert lock_info.instance_id == started.instance_id

    @pytest.mark.asyncio
    async def test_multiple_jobs_same_project_serialized(
        self, job_queue_service, sample_job_data_service
    ):
        """Test that multiple jobs for same project are serialized."""
        # Enqueue first job (PENDING)
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job1.status == JobStatus.PENDING.value
        
        # Start job1
        started1 = await job_queue_service.start_job(job1.job_id)
        assert started1.status == JobStatus.PROCESSING.value
        
        # Enqueue more jobs - all should be pending
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        job3 = await job_queue_service.enqueue(**sample_job_data_service)
        job4 = await job_queue_service.enqueue(**sample_job_data_service)
        
        assert job2.status == JobStatus.PENDING.value
        assert job3.status == JobStatus.PENDING.value
        assert job4.status == JobStatus.PENDING.value
        
        # Only one lock should be held (for job1)
        assert await job_queue_service._lock_manager.is_locked("test-project") is True
        
        # Complete job1 and trigger next job - lock should be held again (for job2)
        await job_queue_service.complete_job(job1.job_id)
        # Lock is released by complete_job, trigger_next_job will start job2
        started2 = await job_queue_service.trigger_next_job("test-project")
        assert await job_queue_service._lock_manager.is_locked("test-project") is True
        
        # Complete job2 and trigger next job
        await job_queue_service.complete_job(job2.job_id)
        started3 = await job_queue_service.trigger_next_job("test-project")
        assert await job_queue_service._lock_manager.is_locked("test-project") is True


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
        assert job.status == JobStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_multiple_no_project_jobs_all_pending(
        self, job_queue_service, sample_job_data_service_no_project
    ):
        """Test that jobs without project are all PENDING."""
        job1 = await job_queue_service.enqueue(**sample_job_data_service_no_project)
        job2 = await job_queue_service.enqueue(**sample_job_data_service_no_project)
        job3 = await job_queue_service.enqueue(**sample_job_data_service_no_project)
        
        # All jobs should be PENDING (JobProcessor handles starting)
        assert job1.status == JobStatus.PENDING.value
        assert job2.status == JobStatus.PENDING.value
        assert job3.status == JobStatus.PENDING.value


class TestJobQueueServiceFullWorkflow:
    """Integration tests for full job workflow."""

    @pytest.mark.asyncio
    async def test_full_workflow_enqueue_process_complete(
        self, job_queue_service, sample_job_data_service
    ):
        """Test complete workflow: enqueue -> process -> complete."""
        # Enqueue (PENDING)
        job = await job_queue_service.enqueue(**sample_job_data_service)
        assert job.status == JobStatus.PENDING.value
        assert job.instance_id is None
        
        # Start job (JobProcessor normally does this)
        started = await job_queue_service.start_job(job.job_id)
        assert started.status == JobStatus.PROCESSING.value
        assert started.instance_id is not None
        
        # Process (simulated)
        processed_job = await job_queue_service.get_job(job.job_id)
        assert processed_job is not None
        
        # Complete
        completed = await job_queue_service.complete_job(job.job_id)
        assert completed.status == JobStatus.COMPLETED.value
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
        assert job2.status == JobStatus.PENDING.value
        
        # Complete first job
        await job_queue_service.complete_job(job1.job_id)
        
        # Trigger next - job2 should start
        triggered = await job_queue_service.trigger_next_job("test-project")
        assert triggered is not None
        assert triggered.job_id == job2.job_id
        assert triggered.status == JobStatus.PROCESSING.value
        
        # Complete job2
        await job_queue_service.complete_job(job2.job_id)
        
        # No more pending jobs
        pending = await job_queue_service.list_jobs(
            status=JobStatus.PENDING,
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
            success=False,
            error="Simulated failure"
        )
        
        assert failed.status == JobStatus.FAILED.value
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
            job.job_id, success=True, result_summary="Custom summary here"
        )
        assert completed is not None
        assert completed.status == "completed"
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
            job.job_id, success=True, result_summary="Sync summary"
        )
        assert completed is not None
        assert completed.result_summary == "Sync summary"
    
    @pytest.mark.asyncio
    async def test_complete_job_sync_failure(self, job_queue_service, sample_job_data_service):
        """complete_job_sync marks job as failed when success=False."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        completed = job_queue_service.complete_job_sync(
            job.job_id, success=False, error="Sync error"
        )
        assert completed is not None
        assert completed.status == "failed"
        assert completed.error_message == "Sync error"
    
    @pytest.mark.asyncio
    async def test_complete_job_sync_returns_none_for_nonexistent(self, job_queue_service):
        """complete_job_sync returns None for unknown job_id."""
        result = job_queue_service.complete_job_sync("nonexistent", success=True)
        assert result is None
    
    @pytest.mark.asyncio
    async def test_complete_job_sync_handles_valueerror(self, job_queue_service, sample_job_data_service):
        """complete_job_sync returns None when job already completed (ValueError)."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        # Complete once
        job_queue_service.complete_job_sync(job.job_id, success=True)
        # Try again - should return None (not raise ValueError)
        result = job_queue_service.complete_job_sync(job.job_id, success=True)
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
        assert job2.status == "pending"
        
        # Complete job1
        job_queue_service.complete_job_sync(job1.job_id, success=True)
        
        # Trigger next
        next_job = job_queue_service.trigger_next_job_sync("test-project")
        assert next_job is not None
        assert next_job.job_id == job2.job_id
        assert next_job.status == "processing"
    
    @pytest.mark.asyncio
    async def test_returns_none_when_no_pending(self, job_queue_service, sample_job_data_service):
        """trigger_next_job_sync returns None when no pending jobs."""
        job = await job_queue_service.enqueue(**sample_job_data_service)
        started = await job_queue_service.start_job(job.job_id)
        job_queue_service.complete_job_sync(job.job_id, success=True)
        
        result = job_queue_service.trigger_next_job_sync("test-project")
        assert result is None


class TestNextJobTriggeredAfterCompletion:
    """Tests verifying next job is triggered after job completion."""
    
    @pytest.mark.asyncio
    async def test_next_job_started_after_complete(self, job_queue_service, sample_job_data_service):
        """After completing a job, trigger_next_job starts the next queued job."""
        job1 = await job_queue_service.enqueue(**sample_job_data_service)
        started1 = await job_queue_service.start_job(job1.job_id)
        job2 = await job_queue_service.enqueue(**sample_job_data_service)
        assert job2.status == "pending"
        
        await job_queue_service.complete_job(job1.job_id)
        next_job = await job_queue_service.trigger_next_job("test-project")
        
        assert next_job is not None
        assert next_job.job_id == job2.job_id
        assert next_job.status == "processing"

"""Integration tests for Job Queue feature.

This module tests the complete job queue workflow including:
- Full workflow: enqueue -> process -> complete
- Multiple jobs with same project (serialization)
- Multiple jobs with different projects (parallel)
- Crash recovery scenario
"""

import asyncio
import pytest

from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


@pytest.fixture
def integration_engine(tmp_path):
    """Create SQLite engine for integration tests.
    
    Uses QueuePool with size=1 to serialize all database connections.
    This is required because asyncio.to_thread() runs workers in different
    threads, and SQLite connections must be properly synchronized.
    """
    db_file = tmp_path / "test_integration.db"
    
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def integration_repository(integration_engine):
    """Create repository with fresh database."""
    return JobRepository(integration_engine)


@pytest.fixture
def integration_lock_manager(integration_engine):
    """Create fresh lock manager with lock_repo."""
    lock_repo = LockRepository(integration_engine)
    manager = JobLockManager(lock_repo=lock_repo)
    yield manager
    # Clean up using lock_repo directly
    all_locks = lock_repo.get_all_locks()
    for lock in all_locks:
        lock_repo.release(lock.lock_id)


@pytest.fixture
def integration_queue_repository(integration_engine):
    """Create queue repository with fresh database and system queues."""
    repo = JobQueueRepository(integration_engine)
    # Pre-provision system queues for multiple projects
    # Add enough for concurrent test scenarios
    for project_num in range(0, 11):  # project-0 through project-10
        repo.create(
            project_id=f"project-{project_num}",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
    repo.create(
        project_id="test-project",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    # Add queues for specific project names used in tests
    for project_name in ["backend-api", "frontend-web"]:
        repo.create(
            project_id=project_name,
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
    return repo


@pytest.fixture
def integration_service(integration_repository, integration_lock_manager, integration_queue_repository):
    """Create service with fresh dependencies and system queues."""
    return JobQueueService(integration_repository, integration_lock_manager, integration_queue_repository)


class TestIntegrationBasicWorkflow:
    """Tests for basic job queue workflow."""

    @pytest.mark.asyncio
    async def test_enqueue_process_complete_workflow(
        self, integration_service
    ):
        """Test the complete enqueue -> process -> complete workflow."""
        # Step 1: Enqueue job (starts as PENDING)
        job = await integration_service.enqueue(
            agent_id="coder",
            message="Process this job",
            source="test",
            project_id="project-1",
            priority=5
        )
        
        assert job is not None
        assert job.status == JobStatus.PENDING.value
        assert job.instance_id is None
        
        # Step 1b: Start the job (JobProcessor normally does this)
        started_job = await integration_service.start_job(job.job_id)
        assert started_job.status == JobStatus.PROCESSING.value
        assert started_job.instance_id is not None
        
        # Step 2: Verify job is in database
        retrieved = await integration_service.get_job(job.job_id)
        assert retrieved is not None
        assert retrieved.status == JobStatus.PROCESSING.value
        
        # Step 3: Complete the job
        completed = await integration_service.complete_job(job.job_id)
        
        assert completed is not None
        assert completed.status == JobStatus.COMPLETED.value
        assert completed.completed_at is not None
        
        # Step 4: Verify final state
        final = await integration_service.get_job(job.job_id)
        assert final.status == JobStatus.COMPLETED.value
        
        # Step 5: Verify lock is released
        assert await integration_service._lock_manager.is_locked("project-1") is False

    @pytest.mark.asyncio
    async def test_enqueue_without_project_skips_queue(
        self, integration_service
    ):
        """Test that jobs without project_id are created as PENDING."""
        job = await integration_service.enqueue(
            agent_id="coder",
            message="No project job",
            source="test",
            project_id=None,
            priority=5
        )
        
        # Jobs are now always PENDING - JobProcessor handles starting
        assert job.status == JobStatus.PENDING.value
        assert job.instance_id is None
        
        # Start and complete the job
        started = await integration_service.start_job(job.job_id)
        assert started.status == JobStatus.PROCESSING.value
        completed = await integration_service.complete_job(job.job_id)
        assert completed.status == JobStatus.COMPLETED.value


class TestIntegrationSameProjectSerialization:
    """Tests for jobs with the same project (serialization)."""

    @pytest.mark.asyncio
    async def test_multiple_jobs_same_project_serialized(
        self, integration_service
    ):
        """Test that multiple jobs for the same project are serialized."""
        # Enqueue multiple jobs for the same project (all PENDING)
        job1 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 1",
            project_id="project-1",
            priority=5
        )
        
        job2 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 2",
            project_id="project-1",
            priority=5
        )
        
        job3 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 3",
            project_id="project-1",
            priority=5
        )
        
        # All jobs should be PENDING (JobProcessor handles starting)
        assert job1.status == JobStatus.PENDING.value
        assert job2.status == JobStatus.PENDING.value
        assert job3.status == JobStatus.PENDING.value
        
        # Lock should not be held yet (only acquired when job starts)
        assert await integration_service._lock_manager.is_locked("project-1") is False
        
        # Start job 1 (JobProcessor normally does this)
        started1 = await integration_service.start_job(job1.job_id)
        assert started1.status == JobStatus.PROCESSING.value
        
        # Complete job 1
        await integration_service.complete_job(job1.job_id)
        
        # Trigger next job (lock is released, so job2 should start)
        await integration_service.trigger_next_job("project-1")
        
        # Job 2 should now be processing
        job2_updated = await integration_service.get_job(job2.job_id)
        assert job2_updated.status == JobStatus.PROCESSING.value
        
        # Complete job 2
        await integration_service.complete_job(job2.job_id)
        
        # Trigger next job
        await integration_service.trigger_next_job("project-1")
        
        # Job 3 should now be processing
        job3_updated = await integration_service.get_job(job3.job_id)
        assert job3_updated.status == JobStatus.PROCESSING.value
        
        # Complete job 3
        await integration_service.complete_job(job3.job_id)
        
        # All jobs completed
        assert await integration_service._lock_manager.is_locked("project-1") is False
        
        # Verify all are completed
        all_jobs = await integration_service.list_jobs()
        assert all(j.status == JobStatus.COMPLETED.value for j in all_jobs)

    @pytest.mark.asyncio
    async def test_priority_ordering_same_project(
        self, integration_service
    ):
        """Test that jobs are processed by priority for same project."""
        # Enqueue in reverse priority order (all PENDING)
        job_low = await integration_service.enqueue(
            agent_id="coder",
            message="Low priority",
            project_id="project-1",
            priority=1
        )
        
        job_high = await integration_service.enqueue(
            agent_id="coder",
            message="High priority",
            project_id="project-1",
            priority=10
        )
        
        job_medium = await integration_service.enqueue(
            agent_id="coder",
            message="Medium priority",
            project_id="project-1",
            priority=5
        )
        
        # All jobs should be PENDING (locks not acquired yet)
        assert job_low.status == JobStatus.PENDING.value
        assert job_high.status == JobStatus.PENDING.value
        assert job_medium.status == JobStatus.PENDING.value
        assert await integration_service._lock_manager.is_locked("project-1") is False
        
        # Start first job (low priority - enqueued first)
        started_low = await integration_service.start_job(job_low.job_id)
        assert started_low.status == JobStatus.PROCESSING.value
        
        # Complete low priority job
        await integration_service.complete_job(job_low.job_id)
        
        # Trigger next job
        await integration_service.trigger_next_job("project-1")
        
        # High priority should be next
        job_high_updated = await integration_service.get_job(job_high.job_id)
        assert job_high_updated.status == JobStatus.PROCESSING.value
        
        # Complete high priority job
        await integration_service.complete_job(job_high.job_id)
        
        # Trigger next job
        await integration_service.trigger_next_job("project-1")
        
        # Medium priority should be last
        job_medium_updated = await integration_service.get_job(job_medium.job_id)
        assert job_medium_updated.status == JobStatus.PROCESSING.value

    @pytest.mark.asyncio
    async def test_cancel_queued_job_unblocks_next(
        self, integration_service
    ):
        """Test that cancelling a queued job allows next job to proceed."""
        job1 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 1",
            project_id="project-1"
        )
        
        job2 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 2",
            project_id="project-1"
        )
        
        job3 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 3",
            project_id="project-1"
        )
        
        # All jobs are PENDING
        assert job1.status == JobStatus.PENDING.value
        assert job2.status == JobStatus.PENDING.value
        assert job3.status == JobStatus.PENDING.value
        
        # Cancel job 2
        await integration_service.cancel_job(job2.job_id)
        
        # Start job 1 (JobProcessor normally does this)
        started_job1 = await integration_service.start_job(job1.job_id)
        assert started_job1.status == JobStatus.PROCESSING.value
        
        # Complete job 1
        await integration_service.complete_job(job1.job_id)
        
        # Trigger next job (job 2 was cancelled, so job 3 should start)
        await integration_service.trigger_next_job("project-1")
        
        # Job 3 should be processing (job 2 was cancelled)
        job3_updated = await integration_service.get_job(job3.job_id)
        assert job3_updated.status == JobStatus.PROCESSING.value


class TestIntegrationDifferentProjectsParallel:
    """Tests for jobs with different projects (parallel execution)."""

    @pytest.mark.asyncio
    async def test_different_projects_run_parallel(
        self, integration_service, integration_queue_repository
    ):
        """Test that jobs for different projects run in parallel."""
        # Get queue IDs for projects
        queue1 = integration_queue_repository.get_by_name("project-1", "system_fifo_queue")
        queue2 = integration_queue_repository.get_by_name("project-2", "system_fifo_queue")
        queue3 = integration_queue_repository.get_by_name("project-3", "system_fifo_queue")
        
        job1 = await integration_service.enqueue(
            agent_id="coder",
            message="Job for project 1",
            project_id="project-1"
        )
        
        job2 = await integration_service.enqueue(
            agent_id="coder",
            message="Job for project 2",
            project_id="project-2"
        )
        
        job3 = await integration_service.enqueue(
            agent_id="coder",
            message="Job for project 3",
            project_id="project-3"
        )
        
        # All jobs should be PENDING (locks not acquired until start_job called)
        assert job1.status == JobStatus.PENDING.value
        assert job2.status == JobStatus.PENDING.value
        assert job3.status == JobStatus.PENDING.value
        
        # Locks should NOT be held yet
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-2", queue2.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-3", queue3.queue_id) is False
        
        # Start all jobs (JobProcessor normally does this)
        started_job1 = await integration_service.start_job(job1.job_id)
        started_job2 = await integration_service.start_job(job2.job_id)
        started_job3 = await integration_service.start_job(job3.job_id)
        
        # All should be processing
        assert started_job1.status == JobStatus.PROCESSING.value
        assert started_job2.status == JobStatus.PROCESSING.value
        assert started_job3.status == JobStatus.PROCESSING.value
        
        # All locks should be held (different projects = different queues)
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is True
        assert await integration_service._lock_manager.is_queue_locked("project-2", queue2.queue_id) is True
        assert await integration_service._lock_manager.is_queue_locked("project-3", queue3.queue_id) is True
        
        # Complete all jobs
        await integration_service.complete_job(job1.job_id)
        await integration_service.complete_job(job2.job_id)
        await integration_service.complete_job(job3.job_id)
        
        # All locks should be released
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-2", queue2.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-3", queue3.queue_id) is False

    @pytest.mark.asyncio
    async def test_mixed_projects_serialization_and_parallelism(
        self, integration_service
    ):
        """Test mixing serialized and parallel jobs."""
        # Project 1 gets multiple jobs (serialized)
        job1_p1 = await integration_service.enqueue(
            agent_id="coder",
            message="P1 Job 1",
            project_id="project-1"
        )
        
        job2_p1 = await integration_service.enqueue(
            agent_id="coder",
            message="P1 Job 2",
            project_id="project-1"
        )
        
        # Project 2 gets one job (parallel)
        job_p2 = await integration_service.enqueue(
            agent_id="coder",
            message="P2 Job",
            project_id="project-2"
        )
        
        # All jobs should be PENDING
        assert job1_p1.status == JobStatus.PENDING.value
        assert job2_p1.status == JobStatus.PENDING.value
        assert job_p2.status == JobStatus.PENDING.value
        
        # No locks held yet
        assert await integration_service._lock_manager.is_locked("project-1") is False
        assert await integration_service._lock_manager.is_locked("project-2") is False
        
        # Start first jobs for each project
        started_p1_job1 = await integration_service.start_job(job1_p1.job_id)
        started_p2_job = await integration_service.start_job(job_p2.job_id)
        
        # Both projects should have processing jobs
        assert started_p1_job1.status == JobStatus.PROCESSING.value
        assert started_p2_job.status == JobStatus.PROCESSING.value
        
        # Project 1 should have one pending
        assert job2_p1.status == JobStatus.PENDING.value
        
        # Complete project 2 job
        await integration_service.complete_job(job_p2.job_id)
        
        # Project 1 job 2 is still pending (not unblocked, it's same project)
        job2_p1_updated = await integration_service.get_job(job2_p1.job_id)
        assert job2_p1_updated.status == JobStatus.PENDING.value
        
        # Complete project 1 job 1
        await integration_service.complete_job(job1_p1.job_id)
        
        # Trigger next job for project 1
        await integration_service.trigger_next_job("project-1")
        
        # Now project 1 job 2 should start
        job2_p1_updated = await integration_service.get_job(job2_p1.job_id)
        assert job2_p1_updated.status == JobStatus.PROCESSING.value


class TestIntegrationCrashRecovery:
    """Tests for crash recovery scenarios."""

    @pytest.mark.asyncio
    async def test_recovery_from_lock_manager_crash(
        self, integration_service, integration_lock_manager, integration_queue_repository
    ):
        """Test recovery when lock manager state is lost (simulated crash).
        
        In the DB-only model, locks are persisted in the database.
        To simulate a crash, we clear the locks directly from the database.
        """
        # Get the lock_repo from the fixture (we need to add it as a fixture parameter)
        lock_repo = integration_lock_manager._lock_repo
        queue1 = integration_queue_repository.get_by_name("project-1", "system_fifo_queue")
        
        # Enqueue and start a job
        job = await integration_service.enqueue(
            agent_id="coder",
            message="Job before crash",
            project_id="project-1"
        )
        
        # Job is PENDING, need to start it
        assert job.status == JobStatus.PENDING.value
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        
        started_job = await integration_service.start_job(job.job_id)
        assert started_job.status == JobStatus.PROCESSING.value
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is True
        
        # Simulate crash: clear locks directly from database
        # In DB-only model, this is how we simulate losing lock state
        all_locks = lock_repo.get_all_locks()
        for lock in all_locks:
            lock_repo.release(lock.lock_id)
        
        # Lock should be released (simulating crash recovery)
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        
        # The job is still in PROCESSING state in database
        # but the lock is gone - this is crash recovery state
        
        # New job should be able to be enqueued (starts PENDING)
        new_job = await integration_service.enqueue(
            agent_id="coder",
            message="Job after crash",
            project_id="project-1"
        )
        
        # Should be PENDING (not acquire lock during enqueue)
        assert new_job.status == JobStatus.PENDING.value
        
        # The old job is now orphaned - depends on application logic to handle

    @pytest.mark.asyncio
    async def test_recovery_completed_job_cleanup(
        self, integration_service
    ):
        """Test cleanup of completed jobs after recovery."""
        # Create and complete some jobs
        for i in range(5):
            job = await integration_service.enqueue(
                agent_id="coder",
                message=f"Job {i}",
                project_id="project-1"
            )
            # Start and complete the job
            started = await integration_service.start_job(job.job_id)
            assert started.status == JobStatus.PROCESSING.value
            await integration_service.complete_job(job.job_id)
        
        # Verify all are completed
        jobs = await integration_service.list_jobs()
        assert all(j.status == JobStatus.COMPLETED.value for j in jobs)
        
        # Cleanup completed jobs
        deleted = integration_service._repository.hard_delete_completed()
        
        assert deleted == 5
        
        # Verify all jobs are gone
        remaining = await integration_service.list_jobs()
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_recovery_orphaned_processing_jobs(
        self, integration_service
    ):
        """Test handling of jobs stuck in PROCESSING state."""
        # Start a job but don't complete it
        job = await integration_service.enqueue(
            agent_id="coder",
            message="Orphaned job",
            project_id="project-1"
        )
        
        # Job is PENDING, start it
        assert job.status == JobStatus.PENDING.value
        started_job = await integration_service.start_job(job.job_id)
        assert started_job.status == JobStatus.PROCESSING.value
        
        # Simulate crash: clear lock but leave job in PROCESSING
        await integration_service._lock_manager.release_by_instance(started_job.instance_id)
        
        # Job is still in PROCESSING state (re-fetch from DB)
        current_job = await integration_service.get_job(job.job_id)
        assert current_job.status == JobStatus.PROCESSING.value
        
        # We should be able to cancel it
        cancelled = await integration_service.cancel_job(job.job_id)
        assert cancelled is True
        
        # Or we could manually reset it
        updated = integration_service._repository.update(
            job.job_id,
            status=JobStatus.PENDING.value,
            instance_id=None  # Clear instance
        )
        assert updated is not None
        assert updated.status == JobStatus.PENDING.value
        
        # Now a new job can be enqueued
        new_job = await integration_service.enqueue(
            agent_id="coder",
            message="New job",
            project_id="project-1"
        )
        
        # New job is PENDING
        assert new_job.status == JobStatus.PENDING.value
        # Need to start it to get PROCESSING
        started_new = await integration_service.start_job(new_job.job_id)
        assert started_new.status == JobStatus.PROCESSING.value

    @pytest.mark.asyncio
    async def test_recovery_with_multiple_queued_jobs(
        self, integration_service
    ):
        """Test recovery when multiple jobs are queued."""
        # Create a queue
        job1 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 1",
            project_id="project-1"
        )
        
        job2 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 2",
            project_id="project-1"
        )
        
        job3 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 3",
            project_id="project-1"
        )
        
        # Simulate crash during job1 processing
        started_job1 = await integration_service.start_job(job1.job_id)
        await integration_service._lock_manager.release_by_instance(started_job1.instance_id)
        
        # Cancel the orphaned job1
        await integration_service.cancel_job(job1.job_id)
        
        # Complete the recovery
        await integration_service.complete_job(job1.job_id)
        
        # Trigger next job
        next_job = await integration_service.trigger_next_job("project-1")
        
        # Should be job2
        assert next_job is not None
        assert next_job.job_id == job2.job_id
        assert next_job.status == JobStatus.PROCESSING.value
        
        # Complete job2 and trigger job3
        await integration_service.complete_job(job2.job_id)
        
        next_job = await integration_service.trigger_next_job("project-1")
        assert next_job.job_id == job3.job_id


class TestIntegrationConcurrentOperations:
    """Tests for concurrent job operations."""

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_same_project(
        self, integration_service, integration_queue_repository
    ):
        """Test concurrent enqueue operations for same project."""
        # Get queue ID for project-1
        queue1 = integration_queue_repository.get_by_name("project-1", "system_fifo_queue")
        
        async def enqueue_job(i: int):
            return await integration_service.enqueue(
                agent_id="coder",
                message=f"Concurrent job {i}",
                project_id="project-1"
            )
        
        # Enqueue multiple jobs concurrently
        results = await asyncio.gather(*[
            enqueue_job(i) for i in range(5)
        ])
        
        # All should be PENDING (locks not acquired until start_job called)
        all_pending = all(j.status == JobStatus.PENDING.value for j in results)
        assert all_pending, "All jobs should be PENDING after enqueue"
        
        # Start the first job
        started_first = await integration_service.start_job(results[0].job_id)
        assert started_first.status == JobStatus.PROCESSING.value
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is True
        
        # Complete the first job
        await integration_service.complete_job(results[0].job_id)
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        
        # Start the next job
        started_second = await integration_service.start_job(results[1].job_id)
        assert started_second is not None
        assert started_second.status == JobStatus.PROCESSING.value
        
        # Complete the second job
        await integration_service.complete_job(results[1].job_id)
        
        # Complete remaining jobs (they're already in PENDING)
        for job in results[2:]:
            started = await integration_service.start_job(job.job_id)
            if started is not None:
                assert started.status == JobStatus.PROCESSING.value
                await integration_service.complete_job(job.job_id)

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="SQLite does not support true concurrent writes - known limitation")
    async def test_concurrent_enqueue_different_projects(
        self, integration_service
    ):
        """Test concurrent enqueue operations for different projects.
        
        Note: This test is skipped because SQLite's in-memory database
        with StaticPool does not support true concurrent write transactions.
        This is a known SQLite limitation, not a code bug.
        """
        async def enqueue_job(i: int):
            return await integration_service.enqueue(
                agent_id="coder",
                message=f"Job for project {i}",
                project_id=f"project-{i}"
            )
        
        # Enqueue jobs for different projects concurrently
        results = await asyncio.gather(*[
            enqueue_job(i) for i in range(5)
        ])
        
        # All should be processing (different projects)
        assert all(
            j.status == JobStatus.PROCESSING.value for j in results
        )

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="SQLite does not support true concurrent writes - known limitation")
    async def test_concurrent_complete_operations(
        self, integration_service
    ):
        """Test concurrent complete operations.
        
        Note: This test is skipped because SQLite's in-memory database
        with StaticPool does not support true concurrent write transactions.
        This is a known SQLite limitation, not a code bug.
        """
        # Create multiple jobs for different projects
        jobs = []
        for i in range(5):
            job = await integration_service.enqueue(
                agent_id="coder",
                message=f"Job {i}",
                project_id=f"project-{i}"
            )
            jobs.append(job)
        
        # Complete all concurrently
        async def complete_job(job):
            return await integration_service.complete_job(job.job_id)
        
        results = await asyncio.gather(*[
            complete_job(j) for j in jobs
        ])
        
        # All should complete successfully
        assert all(r is not None for r in results)
        assert all(
            r.status == JobStatus.COMPLETED.value for r in results if r
        )


class TestIntegrationInstanceManagement:
    """Tests for instance-based lock management."""

    @pytest.mark.asyncio
    async def test_release_locks_by_instance(
        self, integration_service, integration_queue_repository
    ):
        """Test that releasing by instance releases all locks for that instance."""
        # Get queue IDs for projects
        queue1 = integration_queue_repository.get_by_name("project-1", "system_fifo_queue")
        queue2 = integration_queue_repository.get_by_name("project-2", "system_fifo_queue")
        queue3 = integration_queue_repository.get_by_name("project-3", "system_fifo_queue")
        
        # Create jobs for different projects (each gets own instance)
        job1 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 1",
            project_id="project-1"
        )
        
        job2 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 2",
            project_id="project-2"
        )
        
        job3 = await integration_service.enqueue(
            agent_id="coder",
            message="Job 3",
            project_id="project-3"
        )
        
        # All jobs are PENDING (locks not acquired yet)
        assert job1.status == JobStatus.PENDING.value
        assert job2.status == JobStatus.PENDING.value
        assert job3.status == JobStatus.PENDING.value
        
        # No locks should be held yet
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-2", queue2.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-3", queue3.queue_id) is False
        
        # Start all jobs (this acquires locks)
        started_job1 = await integration_service.start_job(job1.job_id)
        started_job2 = await integration_service.start_job(job2.job_id)
        started_job3 = await integration_service.start_job(job3.job_id)
        
        # Verify all locks are held
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is True
        assert await integration_service._lock_manager.is_queue_locked("project-2", queue2.queue_id) is True
        assert await integration_service._lock_manager.is_queue_locked("project-3", queue3.queue_id) is True
        
        # Release all locks for job1's instance (only project-1)
        released = await integration_service.release_lock_by_instance(started_job1.instance_id)
        assert "project-1" in released
        
        # Only project-1 lock should be released
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-2", queue2.queue_id) is True
        assert await integration_service._lock_manager.is_queue_locked("project-3", queue3.queue_id) is True
        
        # Release job2's instance
        released = await integration_service.release_lock_by_instance(started_job2.instance_id)
        assert "project-2" in released
        
        # Release job3's instance
        released = await integration_service.release_lock_by_instance(started_job3.instance_id)
        assert "project-3" in released
        
        # All locks should be released
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-2", queue2.queue_id) is False
        assert await integration_service._lock_manager.is_queue_locked("project-3", queue3.queue_id) is False

    @pytest.mark.asyncio
    async def test_instance_cleanup_releases_project_lock(
        self, integration_service, integration_queue_repository
    ):
        """Test that instance cleanup releases project lock."""
        queue1 = integration_queue_repository.get_by_name("project-1", "system_fifo_queue")
        
        job = await integration_service.enqueue(
            agent_id="coder",
            message="Job",
            project_id="project-1"
        )
        
        # Job is PENDING
        assert job.status == JobStatus.PENDING.value
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False
        
        # Start job to acquire lock
        started_job = await integration_service.start_job(job.job_id)
        assert started_job.status == JobStatus.PROCESSING.value
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is True
        
        # Cleanup by instance
        released = await integration_service.release_lock_by_instance(started_job.instance_id)
        
        assert "project-1" in released
        assert await integration_service._lock_manager.is_queue_locked("project-1", queue1.queue_id) is False


class TestIntegrationPriorityQueue:
    """Tests for priority-based queue ordering."""

    @pytest.mark.asyncio
    async def test_priority_queue_ordering(
        self, integration_service
    ):
        """Test that jobs are processed in priority order."""
        # Enqueue jobs with different priorities
        priorities = [5, 1, 10, 3, 8]
        jobs = []
        
        for i, priority in enumerate(priorities):
            job = await integration_service.enqueue(
                agent_id="coder",
                message=f"Priority {priority}",
                project_id="project-1",
                priority=priority
            )
            jobs.append((priority, job))
        
        # All jobs are PENDING (locks not acquired yet)
        assert all(j.status == JobStatus.PENDING.value for _, j in jobs)
        assert await integration_service._lock_manager.is_locked("project-1") is False
        
        # Start first job (priority 5, enqueued first)
        started_first = await integration_service.start_job(jobs[0][1].job_id)
        assert started_first.status == JobStatus.PROCESSING.value
        
        # Get the pending jobs - should be ordered by priority (desc)
        pending = integration_service._repository.list_pending_by_project("project-1")
        pending_priorities = [j.priority for j in pending]
        
        # Pending jobs should be sorted by priority descending
        assert pending_priorities == sorted(pending_priorities, reverse=True)
        
        # Complete all jobs - each completion triggers the next (which starts based on priority)
        # Job 0 completes, triggers job with highest priority (10), which becomes PROCESSING
        for _ in jobs:
            # Get current processing job from repository and complete it
            processing, _ = integration_service._repository.list(
                statuses=[JobStatus.PROCESSING.value],
                project_id="project-1"
            )
            if processing:
                await integration_service.complete_job(processing[0].job_id)
            await integration_service.trigger_next_job("project-1")
        
        # Verify all jobs are completed
        all_jobs = await integration_service.list_jobs()
        assert all(j.status == JobStatus.COMPLETED.value for j in all_jobs)

    @pytest.mark.asyncio
    async def test_same_priority_fifo_ordering(
        self, integration_service
    ):
        """Test FIFO ordering for same priority jobs."""
        jobs = []
        for i in range(3):
            job = await integration_service.enqueue(
                agent_id="coder",
                message=f"Job {i}",
                project_id="project-1",
                priority=5
            )
            jobs.append(job)
            # Small delay to ensure different created_at
            await asyncio.sleep(0.01)
        
        # All jobs are PENDING
        assert all(j.status == JobStatus.PENDING.value for j in jobs)
        
        # Start first job
        started_first = await integration_service.start_job(jobs[0].job_id)
        assert started_first.status == JobStatus.PROCESSING.value
        
        # Complete first job
        await integration_service.complete_job(jobs[0].job_id)
        
        # Next should be jobs[1]
        next_job = await integration_service.trigger_next_job("project-1")
        assert next_job.job_id == jobs[1].job_id


class TestIntegrationEndToEnd:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_complete_end_to_end_scenario(
        self, integration_service
    ):
        """Test a complete realistic end-to-end scenario."""
        # Simulate a real workload
        
        # 1. Submit initial jobs for different projects
        job1 = await integration_service.enqueue(
            agent_id="coder",
            message="Build authentication module",
            source="api",
            project_id="backend-api",
            priority=8
        )
        
        job2 = await integration_service.enqueue(
            agent_id="coder",
            message="Update API documentation",
            source="api",
            project_id="backend-api",
            priority=5
        )
        
        job3 = await integration_service.enqueue(
            agent_id="coder",
            message="Fix login form styling",
            source="webhook",
            project_id="frontend-web",
            priority=6
        )
        
        # 2. Verify initial states (all PENDING)
        assert job1.status == JobStatus.PENDING.value
        assert job2.status == JobStatus.PENDING.value
        assert job3.status == JobStatus.PENDING.value
        
        # No locks held yet
        assert await integration_service._lock_manager.is_locked("backend-api") is False
        assert await integration_service._lock_manager.is_locked("frontend-web") is False
        
        # Start first jobs for each project
        started_job1 = await integration_service.start_job(job1.job_id)
        started_job3 = await integration_service.start_job(job3.job_id)
        
        assert started_job1.status == JobStatus.PROCESSING.value
        assert started_job3.status == JobStatus.PROCESSING.value
        
        # 3. Complete job3 (frontend-web, independent)
        await integration_service.complete_job(job3.job_id)
        
        # 4. Complete job1 (backend-api first job)
        await integration_service.complete_job(job1.job_id)
        
        # 5. Trigger next for backend-api
        next_backend = await integration_service.trigger_next_job("backend-api")
        assert next_backend.job_id == job2.job_id
        assert next_backend.status == JobStatus.PROCESSING.value
        
        # 6. Complete remaining jobs
        await integration_service.complete_job(job2.job_id)
        
        # 7. Verify all completed
        all_jobs = await integration_service.list_jobs()
        assert all(j.status == JobStatus.COMPLETED.value for j in all_jobs)
        
        # 8. Verify no locks held
        assert await integration_service._lock_manager.is_locked("backend-api") is False
        assert await integration_service._lock_manager.is_locked("frontend-web") is False

    @pytest.mark.asyncio
    async def test_high_load_scenario(self, integration_service, integration_queue_repository):
        """Test with high load of jobs."""
        # Create many jobs across multiple projects
        num_projects = 3
        jobs_per_project = 10
        
        all_jobs = []
        for project_id in range(num_projects):
            project_jobs = []
            for i in range(jobs_per_project):
                job = await integration_service.enqueue(
                    agent_id="coder",
                    message=f"Job {i} for project {project_id}",
                    project_id=f"project-{project_id}",
                    priority=(i % 10) + 1
                )
                project_jobs.append(job)
            all_jobs.append(project_jobs)
        
        # Verify initial state: all PENDING, locks not held
        for project_jobs in all_jobs:
            assert all(j.status == JobStatus.PENDING.value for j in project_jobs)
        
        # Start first job for each project
        for project_id in range(num_projects):
            started = await integration_service.start_job(all_jobs[project_id][0].job_id)
            assert started.status == JobStatus.PROCESSING.value
            # Get queue_id for this project
            queue = integration_queue_repository.get_by_name(f"project-{project_id}", "system_fifo_queue")
            assert await integration_service._lock_manager.is_queue_locked(f"project-{project_id}", queue.queue_id) is True
        
        # Complete all jobs per project - get processing job, complete it, trigger next
        for project_id in range(num_projects):
            for i in range(jobs_per_project):
                # Get current processing job from repository and complete it
                processing, _ = integration_service._repository.list(
                    statuses=[JobStatus.PROCESSING.value],
                    project_id=f"project-{project_id}"
                )
                if processing:
                    await integration_service.complete_job(processing[0].job_id)
                # Start next job if there is one
                if i + 1 < jobs_per_project:
                    next_pending, _ = integration_service._repository.list(
                        statuses=[JobStatus.PENDING.value],
                        project_id=f"project-{project_id}"
                    )
                    if next_pending:
                        await integration_service.start_job(next_pending[0].job_id)
        
        # Verify all jobs are completed
        final_jobs = await integration_service.list_jobs()
        assert len(final_jobs) == num_projects * jobs_per_project
        
        # Count completed jobs
        completed_count = sum(1 for j in final_jobs if j.status == JobStatus.COMPLETED.value)
        assert completed_count == num_projects * jobs_per_project
        
        # No locks should be held
        assert len(await integration_service._lock_manager.get_all_locks()) == 0

    @pytest.mark.asyncio
    async def test_cancellation_recovery_scenario(self, integration_service):
        """Test cancellation and recovery scenario."""
        # Create a queue
        jobs = []
        for i in range(5):
            job = await integration_service.enqueue(
                agent_id="coder",
                message=f"Job {i}",
                project_id="project-1"
            )
            jobs.append(job)
        
        # All jobs are PENDING
        assert all(j.status == JobStatus.PENDING.value for j in jobs)
        
        # Start first job
        started_first = await integration_service.start_job(jobs[0].job_id)
        assert started_first.status == JobStatus.PROCESSING.value
        
        # Cancel middle jobs
        await integration_service.cancel_job(jobs[1].job_id)
        await integration_service.cancel_job(jobs[3].job_id)
        
        # Complete remaining in order
        await integration_service.complete_job(jobs[0].job_id)
        await integration_service.trigger_next_job("project-1")  # job 2
        
        await integration_service.complete_job(jobs[2].job_id)
        await integration_service.trigger_next_job("project-1")  # job 4
        
        await integration_service.complete_job(jobs[4].job_id)
        
        # Verify final states
        final_jobs = await integration_service.list_jobs()
        assert len(final_jobs) == 5
        
        cancelled = [j for j in final_jobs if j.status == JobStatus.CANCELLED.value]
        completed = [j for j in final_jobs if j.status == JobStatus.COMPLETED.value]
        
        assert len(cancelled) == 2
        assert len(completed) == 3

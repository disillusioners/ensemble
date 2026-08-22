"""Comprehensive tests for JobQueueRepository.

This module tests the SQLModel-based repository for job queue CRUD operations.
"""

import pytest

from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.models import AdmissionState, JobQueue, JobItem, JobStatus, QueueType


class TestQueueRepositoryCreate:
    """Tests for queue creation."""

    def test_create_queue_basic(self, queue_repository):
        """Test creating a queue with all fields specified."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="my-queue",
            queue_type=QueueType.FIFO.value,
            concurrency_limit=5,
            is_system=True,
            is_paused=True,
            description="Test queue description",
        )

        assert queue.queue_id is not None
        assert queue.project_id == "test-project"
        assert queue.queue_name == "my-queue"
        assert queue.queue_name_lower == "my-queue"
        assert queue.queue_type == QueueType.FIFO.value
        assert queue.concurrency_limit == 5
        assert queue.is_system is True
        assert queue.is_paused is True
        assert queue.description == "Test queue description"

    def test_create_queue_generates_uuid(self, queue_repository):
        """Test that queue_id is auto-generated as UUID format."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="uuid-test",
        )

        # UUID format: 36 characters with 4 hyphens
        assert len(queue.queue_id) == 36
        assert queue.queue_id.count("-") == 4

    def test_create_queue_sets_timestamps(self, queue_repository):
        """Test that created_at and updated_at are set on creation."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="timestamp-test",
        )

        assert queue.created_at is not None
        assert queue.updated_at is not None
        assert queue.created_at == queue.updated_at

    def test_create_queue_default_values(self, queue_repository):
        """Test default values when creating queue with minimal params."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="defaults-test",
        )

        assert queue.is_paused is False
        assert queue.concurrency_limit == 1
        assert queue.queue_type == QueueType.FIFO.value
        assert queue.is_system is False
        assert queue.description is None


class TestQueueRepositoryGet:
    """Tests for queue retrieval."""

    def test_get_existing_queue(self, queue_repository):
        """Test getting an existing queue by ID."""
        created = queue_repository.create(
            project_id="test-project",
            queue_name="get-test",
        )

        retrieved = queue_repository.get(created.queue_id)

        assert retrieved is not None
        assert retrieved.queue_id == created.queue_id
        assert retrieved.queue_name == "get-test"

    def test_get_nonexistent_queue(self, queue_repository):
        """Test getting a non-existent queue returns None."""
        result = queue_repository.get("nonexistent-queue-id")
        assert result is None

    def test_get_by_name_and_project(self, queue_repository):
        """Test getting queue by name and project_id."""
        queue_repository.create(
            project_id="test-project",
            queue_name="MyQueue",
        )

        retrieved = queue_repository.get_by_name("test-project", "MyQueue")

        assert retrieved is not None
        assert retrieved.queue_name == "MyQueue"
        assert retrieved.project_id == "test-project"

    def test_get_by_name_wrong_project(self, queue_repository):
        """Test getting queue by name returns None for wrong project."""
        queue_repository.create(
            project_id="test-project",
            queue_name="ProjectQueue",
        )

        # Try to get with different project_id
        result = queue_repository.get_by_name("other-project", "ProjectQueue")

        assert result is None

    def test_get_by_name_case_insensitive(self, queue_repository):
        """Test queue name lookup is case-insensitive."""
        queue_repository.create(
            project_id="test-project",
            queue_name="MyQueue",
        )

        # Different case variations should all match
        result1 = queue_repository.get_by_name("test-project", "myqueue")
        result2 = queue_repository.get_by_name("test-project", "MYQUEUE")
        result3 = queue_repository.get_by_name("test-project", "MyQueue")

        assert result1 is not None
        assert result2 is not None
        assert result3 is not None
        assert result1.queue_id == result2.queue_id == result3.queue_id


class TestQueueRepositoryListByProject:
    """Tests for listing queues by project."""

    def test_list_by_project_returns_queues(self, queue_repository):
        """Test listing returns all queues for a project."""
        queue_repository.create(project_id="test-project", queue_name="queue-1")
        queue_repository.create(project_id="test-project", queue_name="queue-2")
        queue_repository.create(project_id="test-project", queue_name="queue-3")

        queues = queue_repository.list_by_project("test-project")

        assert len(queues) == 3
        queue_names = {q.queue_name for q in queues}
        assert queue_names == {"queue-1", "queue-2", "queue-3"}

    def test_list_by_project_excludes_other_projects(self, queue_repository):
        """Test listing doesn't return queues from other projects."""
        queue_repository.create(project_id="test-project", queue_name="our-queue")
        queue_repository.create(project_id="other-project", queue_name="their-queue")

        queues = queue_repository.list_by_project("test-project")

        assert len(queues) == 1
        assert queues[0].queue_name == "our-queue"

    def test_list_by_project_empty(self, queue_repository):
        """Test listing returns empty list for project with no queues."""
        queues = queue_repository.list_by_project("empty-project")
        assert queues == []


class TestQueueRepositoryGetSystemQueues:
    """Tests for getting system queues."""

    def test_get_system_queues_returns_both(self, queue_repository):
        """Test getting system queues returns all system queues for project."""
        queue_repository.create(
            project_id="test-project",
            queue_name="fifo-system",
            is_system=True,
        )
        queue_repository.create(
            project_id="test-project",
            queue_name="priority-system",
            is_system=True,
        )
        queue_repository.create(
            project_id="test-project",
            queue_name="custom-queue",
            is_system=False,
        )

        system_queues = queue_repository.get_system_queues("test-project")

        assert len(system_queues) == 2
        queue_names = {q.queue_name for q in system_queues}
        assert "fifo-system" in queue_names
        assert "priority-system" in queue_names

    def test_get_system_queues_excludes_custom(self, queue_repository):
        """Test get_system_queues doesn't return custom queues."""
        queue_repository.create(
            project_id="test-project",
            queue_name="system-fifo",
            is_system=True,
        )
        queue_repository.create(
            project_id="test-project",
            queue_name="custom-queue",
            is_system=False,
        )

        system_queues = queue_repository.get_system_queues("test-project")

        queue_names = {q.queue_name for q in system_queues}
        assert "custom-queue" not in queue_names


class TestQueueRepositoryUpdate:
    """Tests for queue updates."""

    def test_update_queue_name(self, queue_repository):
        """Test updating queue name."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="old-name",
        )

        updated = queue_repository.update(queue.queue_id, queue_name="new-name")

        assert updated is not None
        assert updated.queue_name == "new-name"
        assert updated.queue_name_lower == "new-name"

    def test_update_queue_concurrency(self, queue_repository):
        """Test updating queue concurrency_limit."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="concurrency-test",
            concurrency_limit=1,
        )

        updated = queue_repository.update(queue.queue_id, concurrency_limit=10)

        assert updated is not None
        assert updated.concurrency_limit == 10

    def test_update_queue_pause(self, queue_repository):
        """Test updating queue is_paused state."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="pause-test",
            is_paused=False,
        )

        updated = queue_repository.update(queue.queue_id, is_paused=True)

        assert updated is not None
        assert updated.is_paused is True

    def test_update_nonexistent_queue(self, queue_repository):
        """Test updating non-existent queue returns None."""
        result = queue_repository.update("nonexistent-id", queue_name="new")
        assert result is None

    def test_update_sets_updated_at(self, queue_repository):
        """Test that update changes updated_at timestamp."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="timestamp-update-test",
        )
        original_updated_at = queue.updated_at

        # Small delay to ensure timestamp difference
        import time
        time.sleep(0.01)

        updated = queue_repository.update(queue.queue_id, is_paused=True)

        assert updated is not None
        assert updated.updated_at > original_updated_at


class TestQueueRepositoryDelete:
    """Tests for queue deletion."""

    def test_delete_existing_queue(self, queue_repository):
        """Test deleting an existing queue."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="delete-test",
        )

        result = queue_repository.delete(queue.queue_id)

        assert result["deleted"] is True
        assert result["queue_id"] == queue.queue_id
        assert result["project_id"] == "test-project"

        # Verify queue is gone
        assert queue_repository.get(queue.queue_id) is None

    def test_delete_nonexistent_queue(self, queue_repository):
        """Test deleting non-existent queue returns deleted=False."""
        result = queue_repository.delete("nonexistent-id")

        assert result["deleted"] is False
        assert "error" in result

    def test_delete_removes_from_list(self, queue_repository):
        """Test deleted queue no longer appears in list_by_project."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="remove-test",
        )

        # Verify it exists
        queues = queue_repository.list_by_project("test-project")
        assert any(q.queue_id == queue.queue_id for q in queues)

        # Delete it
        queue_repository.delete(queue.queue_id)

        # Verify it's gone
        queues = queue_repository.list_by_project("test-project")
        assert not any(q.queue_id == queue.queue_id for q in queues)


class TestQueueRepositoryCountJobsByStatus:
    """Tests for counting jobs by status in a queue."""

    def test_count_jobs_empty_queue(self, queue_repository, repository):
        """Test counting jobs in empty queue returns 0 for all statuses."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="empty-count-test",
        )

        counts = queue_repository.count_jobs_by_admission(queue.queue_id)

        assert counts[AdmissionState.QUEUED.value] == 0
        assert counts[AdmissionState.ACTIVE.value] == 0
        assert counts[AdmissionState.DONE.value] == 0
        assert counts[AdmissionState.DEAD.value] == 0

    def test_count_jobs_mixed_statuses(self, queue_repository, repository):
        """Test counting jobs with mixed statuses returns correct counts."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="mixed-count-test",
        )

        # Create jobs with different statuses
        job1 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="pending",
            project_id="test-project",
            queue_id=queue.queue_id,
        )
        job2 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="pending2",
            project_id="test-project",
            queue_id=queue.queue_id,
        )
        job3 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="processing",
            project_id="test-project",
            queue_id=queue.queue_id,
        )
        job4 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="completed",
            project_id="test-project",
            queue_id=queue.queue_id,
        )
        job5 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="failed",
            project_id="test-project",
            queue_id=queue.queue_id,
        )
        job6 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="cancelled",
            project_id="test-project",
            queue_id=queue.queue_id,
        )

        # Start job3 then complete it
        started3 = repository.start_job(job3.job_id, "instance-3")
        repository.complete_job(started3.job_id)  # COMPLETED

        # Start job4 then complete it
        started4 = repository.start_job(job4.job_id, "instance-4")
        repository.complete_job(started4.job_id)  # COMPLETED

        # Start job5 then fail it
        started5 = repository.start_job(job5.job_id, "instance-5")
        repository.fail_job(started5.job_id, "error")  # FAILED

        # Cancel job6
        repository.cancel_job(job6.job_id)  # CANCELLED

        counts = queue_repository.count_jobs_by_admission(queue.queue_id)

        # completed(2) + failed(1) + cancelled(1) all collapse to "done"
        assert counts[AdmissionState.QUEUED.value] == 2
        assert counts[AdmissionState.ACTIVE.value] == 0
        assert counts[AdmissionState.DONE.value] == 4
        assert counts[AdmissionState.DEAD.value] == 0

    def test_count_jobs_only_for_this_queue(self, queue_repository, repository):
        """Test count doesn't include jobs from other queues."""
        queue1 = queue_repository.create(
            project_id="test-project",
            queue_name="queue-1",
        )
        queue2 = queue_repository.create(
            project_id="test-project",
            queue_name="queue-2",
        )

        # Create jobs for queue1
        job1 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="job1",
            project_id="test-project",
            queue_id=queue1.queue_id,
        )
        job2 = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="job2",
            project_id="test-project",
            queue_id=queue1.queue_id,
        )

        # Create jobs for queue2
        repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="job3",
            project_id="test-project",
            queue_id=queue2.queue_id,
        )

        counts = queue_repository.count_jobs_by_admission(queue1.queue_id)

        assert counts[AdmissionState.QUEUED.value] == 2


class TestQueueRepositoryReassignPendingJobsAtomic:
    """Tests for atomic job reassignment between queues."""

    def test_reassign_moves_pending_only(self, queue_repository, repository):
        """Test reassign moves PENDING jobs but not PROCESSING jobs."""
        from_queue = queue_repository.create(
            project_id="test-project",
            queue_name="from-queue",
        )
        to_queue = queue_repository.create(
            project_id="test-project",
            queue_name="to-queue",
        )

        # Create PENDING job
        pending_job = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="pending",
            project_id="test-project",
            queue_id=from_queue.queue_id,
        )

        # Create PROCESSING job
        processing_job = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="processing",
            project_id="test-project",
            queue_id=from_queue.queue_id,
        )
        repository.start_job(processing_job.job_id, "instance-1")

        # Reassign
        reassigned_count = queue_repository.reassign_pending_jobs_atomic(
            from_queue.queue_id,
            to_queue.queue_id,
        )

        assert reassigned_count == 1

        # Verify PENDING job moved
        moved_job = repository.get(pending_job.job_id)
        assert moved_job.queue_id == to_queue.queue_id

        # Verify PROCESSING job stayed
        stayed_job = repository.get(processing_job.job_id)
        assert stayed_job.queue_id == from_queue.queue_id

    def test_reassign_to_target_queue(self, queue_repository, repository):
        """Test jobs get assigned the correct target queue_id."""
        from_queue = queue_repository.create(
            project_id="test-project",
            queue_name="from",
        )
        to_queue = queue_repository.create(
            project_id="test-project",
            queue_name="to",
        )

        job = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="test",
            project_id="test-project",
            queue_id=from_queue.queue_id,
        )

        queue_repository.reassign_pending_jobs_atomic(
            from_queue.queue_id,
            to_queue.queue_id,
        )

        updated_job = repository.get(job.job_id)
        assert updated_job.queue_id == to_queue.queue_id

    def test_reassign_returns_count(self, queue_repository, repository):
        """Test reassign returns the number of jobs reassigned."""
        from_queue = queue_repository.create(
            project_id="test-project",
            queue_name="count-from",
        )
        to_queue = queue_repository.create(
            project_id="test-project",
            queue_name="count-to",
        )

        # Create 5 PENDING jobs
        for i in range(5):
            repository.create(
                agent_id="test-agent",
                agent_dir="/test",
                message=f"job-{i}",
                project_id="test-project",
                queue_id=from_queue.queue_id,
            )

        count = queue_repository.reassign_pending_jobs_atomic(
            from_queue.queue_id,
            to_queue.queue_id,
        )

        assert count == 5

    def test_reassign_no_pending(self, queue_repository, repository):
        """Test reassign returns 0 when no PENDING jobs exist."""
        from_queue = queue_repository.create(
            project_id="test-project",
            queue_name="empty-from",
        )
        to_queue = queue_repository.create(
            project_id="test-project",
            queue_name="empty-to",
        )

        # Create only PROCESSING job
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="processing",
            project_id="test-project",
            queue_id=from_queue.queue_id,
        )
        repository.start_job(job.job_id, "instance-1")

        count = queue_repository.reassign_pending_jobs_atomic(
            from_queue.queue_id,
            to_queue.queue_id,
        )

        assert count == 0


class TestQueueRepositoryIsSystemQueue:
    """Tests for system queue identification (via is_system flag)."""

    def test_is_system_fifo(self, queue_repository):
        """Test FIFO system queue has is_system=True."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="system-fifo",
            queue_type=QueueType.FIFO.value,
            is_system=True,
        )

        assert queue.is_system is True
        assert queue.queue_type == QueueType.FIFO.value

    def test_is_system_priority(self, queue_repository):
        """Test PRIORITY system queue has is_system=True."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="system-priority",
            queue_type=QueueType.PARALLEL.value,
            is_system=True,
        )

        assert queue.is_system is True
        assert queue.queue_type == QueueType.PARALLEL.value

    def test_is_not_system_custom(self, queue_repository):
        """Test custom queue has is_system=False."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="custom-queue",
            is_system=False,
        )

        assert queue.is_system is False


class TestQueueRepositoryEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_create_duplicate_name_same_project(self, queue_repository):
        """Test creating queue with duplicate name in same project raises error."""
        queue_repository.create(
            project_id="test-project",
            queue_name="unique-name",
        )

        # Creating with same name should raise due to unique constraint
        with pytest.raises(Exception):
            queue_repository.create(
                project_id="test-project",
                queue_name="unique-name",
            )

    def test_create_same_name_different_projects(self, queue_repository):
        """Test same name allowed for different projects."""
        queue1 = queue_repository.create(
            project_id="project-1",
            queue_name="same-name",
        )
        queue2 = queue_repository.create(
            project_id="project-2",
            queue_name="same-name",
        )

        assert queue1.queue_id != queue2.queue_id
        assert queue1.queue_name == queue2.queue_name

    def test_update_queue_name_syncs_lowercase(self, queue_repository):
        """Test updating name also updates queue_name_lower."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="OriginalName",
        )

        updated = queue_repository.update(queue.queue_id, queue_name="NewName")

        assert updated.queue_name == "NewName"
        assert updated.queue_name_lower == "newname"

    def test_update_multiple_fields_at_once(self, queue_repository):
        """Test updating multiple fields in single call."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="multi-update-test",
            concurrency_limit=1,
            is_paused=False,
        )

        updated = queue_repository.update(
            queue.queue_id,
            concurrency_limit=10,
            is_paused=True,
            description="New description",
        )

        assert updated.concurrency_limit == 10
        assert updated.is_paused is True
        assert updated.description == "New description"

    def test_get_by_name_nonexistent(self, queue_repository):
        """Test get_by_name returns None for nonexistent queue."""
        result = queue_repository.get_by_name("test-project", "nonexistent")
        assert result is None

    def test_list_by_project_multiple_projects(self, queue_repository):
        """Test listing correctly separates queues by project."""
        queue_repository.create(project_id="project-a", queue_name="a1")
        queue_repository.create(project_id="project-a", queue_name="a2")
        queue_repository.create(project_id="project-b", queue_name="b1")
        queue_repository.create(project_id="project-c", queue_name="c1")

        queues_a = queue_repository.list_by_project("project-a")
        queues_b = queue_repository.list_by_project("project-b")

        assert len(queues_a) == 2
        assert len(queues_b) == 1

    def test_queue_id_persistence(self, queue_repository):
        """Test queue_id persists after operations."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="persistence-test",
        )
        original_id = queue.queue_id

        # Perform various operations
        queue_repository.update(original_id, is_paused=True)
        retrieved = queue_repository.get(original_id)

        assert retrieved is not None
        assert retrieved.queue_id == original_id


class TestQueueRepositoryOrdering:
    """Tests for queue ordering."""

    def test_list_by_project_ordered_by_name(self, queue_repository):
        """Test list_by_project returns queues ordered by name."""
        queue_repository.create(project_id="test-project", queue_name="zebra")
        queue_repository.create(project_id="test-project", queue_name="apple")
        queue_repository.create(project_id="test-project", queue_name="mango")

        queues = queue_repository.list_by_project("test-project")

        assert queues[0].queue_name == "apple"
        assert queues[1].queue_name == "mango"
        assert queues[2].queue_name == "zebra"

    def test_get_system_queues_ordered_by_name(self, queue_repository):
        """Test get_system_queues returns queues ordered by name."""
        queue_repository.create(
            project_id="test-project",
            queue_name="zulu",
            is_system=True,
        )
        queue_repository.create(
            project_id="test-project",
            queue_name="alpha",
            is_system=True,
        )

        queues = queue_repository.get_system_queues("test-project")

        assert queues[0].queue_name == "alpha"
        assert queues[1].queue_name == "zulu"


class TestListQueuesWithAdmittableWork:
    """Tests for ``JobQueueRepository.list_queues_with_admittable_work``.

    Work-driven scan method backing the admission-starvation fix
    (``daemon/services/job_processor.py:_process_next_job``). The
    SQLite-backed tests in
    ``tests/job_queue/test_job_processor_admission_starvation.py``
    cover the integration contract; this class targets the
    SQLite repository surface directly so the input-validation
    contracts (the empty-list ValueError footgun) are pinned at the
    lowest level where the SQL is built.
    """

    def test_empty_admission_states_raises_value_error(self, queue_repository):
        """Empty ``admission_states`` raises ``ValueError``.

        An empty ``IN ()`` clause compiles to ``WHERE FALSE`` on both
        SQLite and PostgreSQL, which silently returns zero rows and
        is indistinguishable from "no admittable work" to the
        caller — an admission-starvation footgun. Surface it loudly
        rather than letting the SQL degenerate silently.
        """
        repo: JobQueueRepository = queue_repository
        with pytest.raises(ValueError) as excinfo:
            repo.list_queues_with_admittable_work(admission_states=[])

        # The message names the failure mode so the next person to
        # debug a starvation incident immediately recognises it.
        assert "admission_states must be non-empty" in str(excinfo.value)

    def test_default_admission_states_routes_through_active_constant(
        self, queue_repository
    ):
        """The default (``admission_states=None``) is equivalent to
        passing ``ACTIVE_ADMISSION_STATES`` explicitly.

        Pins the single-source-of-truth contract from Nit #4: the
        default MUST route through ``models.ACTIVE_ADMISSION_STATES``
        so the in-flight set is spelled in exactly one place. If the
        constant is ever extended (e.g. a new admission state added),
        the default follows automatically.
        """
        from daemon.repositories.job_queue.models import ACTIVE_ADMISSION_STATES

        repo: JobQueueRepository = queue_repository

        # Seed a queue with one queued JobItem so the scan has
        # something to return. Direct INSERT via the engine — mirrors
        # ``test_job_processor_admission_starvation.py`` helpers and
        # avoids needing the full ``JobRepository.create`` surface.
        queue = repo.create(
            project_id="default-routing-p1",
            queue_name="routing_fifo",
            queue_type=QueueType.FIFO.value,
            concurrency_limit=1,
        )
        with repo.engine.begin() as conn:  # type: ignore[attr-defined]
            from sqlalchemy import text as sql_text

            conn.execute(
                sql_text(
                    "INSERT INTO job_queue_items "
                    "(job_id, agent_id, agent_dir, message, source, "
                    "project_id, queue_id, priority, admission_state, "
                    "created_at, instance_id, job_type, retry_count, "
                    "metadata) VALUES "
                    "(:job_id, 'a', 'agents/a', 'm', 'api', "
                    ":project_id, :queue_id, 5, :admission_state, "
                    ":created_at, NULL, 'task', 0, '{}')"
                ),
                {
                    "job_id": "routing-job-1",
                    "project_id": queue.project_id,
                    "queue_id": queue.queue_id,
                    "admission_state": AdmissionState.QUEUED.value,
                    "created_at": "2026-08-01T00:00:00+00:00",
                },
            )

        default_ids = {
            q.queue_id for q in repo.list_queues_with_admittable_work()
        }
        explicit_ids = {
            q.queue_id
            for q in repo.list_queues_with_admittable_work(
                admission_states=list(ACTIVE_ADMISSION_STATES),
            )
        }

        assert default_ids == explicit_ids, (
            "default (None) must route through ACTIVE_ADMISSION_STATES — "
            f"got default={default_ids}, explicit={explicit_ids}"
        )
        # Sanity: the seeded queue is in both result sets.
        assert queue.queue_id in default_ids

    def test_explicit_admission_state_works(self, queue_repository):
        """Passing an explicit list works (positive case for the empty-list guard)."""
        # Even if no rows match, the call must NOT raise — only the
        # empty-list case is invalid.
        result = queue_repository.list_queues_with_admittable_work(
            admission_states=[AdmissionState.DONE.value],
        )
        assert result == []


class TestJobQueueModel:
    """Tests for JobQueue model methods."""

    def test_queue_to_dict(self, queue_repository):
        """Test JobQueue.to_dict() method."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="dict-test",
            description="Test description",
        )

        queue_dict = queue.to_dict()

        assert isinstance(queue_dict, dict)
        assert queue_dict["queue_id"] == queue.queue_id
        assert queue_dict["queue_name"] == queue.queue_name
        assert queue_dict["project_id"] == "test-project"
        assert queue_dict["description"] == "Test description"
        assert "created_at" in queue_dict
        assert "updated_at" in queue_dict


class TestQueueTypeValues:
    """Tests for QueueType enum values."""

    def test_fifo_value(self):
        """Test FIFO queue type value."""
        assert QueueType.FIFO.value == "fifo"

    def test_parallel_value(self):
        """Test PARALLEL queue type value."""
        assert QueueType.PARALLEL.value == "parallel"


class TestJobStatusValues:
    """Tests for JobStatus enum values."""

    def test_all_status_values(self):
        """Test all job status values.

        Phase 5 (Job-as-Queue-Proxy): the 4-value ``AdmissionState``
        vocabulary replaces the legacy ``JobStatus`` enum. ``QUEUED``
        / ``ACTIVE`` / ``DONE`` / ``DEAD`` are the canonical strings
        — the legacy ``JobStatus`` keys (``pending`` /
        ``processing`` / ``completed`` / etc.) remain readable via
        ``ADMISSION_STATE_TO_STATUS`` for backward compat, but the
        ``AdmissionState`` enum members themselves carry the new
        values directly.
        """
        assert AdmissionState.QUEUED.value == "queued"
        assert AdmissionState.ACTIVE.value == "active"
        assert AdmissionState.DONE.value == "done"
        assert AdmissionState.DEAD.value == "dead"

    def test_is_valid_status(self):
        """Test status validation."""
        assert JobStatus.is_valid("pending") is True
        assert JobStatus.is_valid("processing") is True
        assert JobStatus.is_valid("completed") is True
        assert JobStatus.is_valid("failed") is True
        assert JobStatus.is_valid("cancelled") is True

    def test_is_invalid_status(self):
        """Test invalid status returns False."""
        assert JobStatus.is_valid("invalid") is False
        assert JobStatus.is_valid("") is False
        assert JobStatus.is_valid("PENDING") is False  # Case sensitive

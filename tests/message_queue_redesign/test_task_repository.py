"""Tests for TaskRepository."""

import pytest
import json

from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


class TestTaskCreation:
    """Tests for task creation."""

    def test_create_task(self, repository, sample_task_data):
        """Test creating a basic task."""
        task = repository.create(**sample_task_data)

        assert task.id is not None
        assert task.task_type == sample_task_data["task_type"]
        assert task.instance_id == sample_task_data["instance_id"]
        assert task.message_id == sample_task_data["message_id"]
        assert task.status == TaskStatus.PENDING.value
        assert task.worker_id is None
        assert task.result is None
        assert task.error is None
        assert task.started_at is None
        assert task.completed_at is None

    def test_create_task_with_defaults(self, repository):
        """Test creating a task with minimal data."""
        task = repository.create(
            task_type=TaskType.CLEANUP.value,
            instance_id="instance-1",
        )

        assert task.id is not None
        assert task.task_type == TaskType.CLEANUP.value
        assert task.status == TaskStatus.PENDING.value
        assert task.message_id is None

    def test_create_multiple_tasks_ordered(self, repository):
        """Test that multiple tasks are ordered by created_at."""
        task1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        task2 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")
        task3 = repository.create(task_type=TaskType.SEND_REPORT.value, instance_id="i3")

        assert task1.id < task2.id < task3.id
        assert task1.created_at <= task2.created_at <= task3.created_at


class TestTaskRetrieval:
    """Tests for task retrieval methods."""

    def test_get_existing_task(self, repository, sample_task_data):
        """Test getting an existing task by ID."""
        created = repository.create(**sample_task_data)

        retrieved = repository.get(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.task_type == created.task_type

    def test_get_nonexistent_task(self, repository):
        """Test getting a non-existent task returns None."""
        result = repository.get(99999)
        assert result is None

    def test_get_by_instance(self, repository):
        """Test getting all tasks for an instance."""
        instance_id = "test-instance"

        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id=instance_id)
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id=instance_id)
        repository.create(task_type=TaskType.CLEANUP.value, instance_id=instance_id)
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="other-instance")

        tasks = repository.get_by_instance(instance_id)

        assert len(tasks) == 3
        assert all(t.instance_id == instance_id for t in tasks)

    def test_get_by_message(self, repository, sample_task_data):
        """Test getting task by message ID."""
        created = repository.create(**sample_task_data)

        retrieved = repository.get_by_message(sample_task_data["message_id"])

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.message_id == sample_task_data["message_id"]

    def test_get_by_message_not_found(self, repository):
        """Test getting task by non-existent message ID."""
        result = repository.get_by_message("nonexistent-message")
        assert result is None


class TestTaskClaiming:
    """Tests for atomic task claiming."""

    def test_claim_pending_task(self, repository, sample_task_data):
        """Test claiming a pending task."""
        created_task = repository.create(**sample_task_data)
        assert created_task.status == TaskStatus.PENDING.value

        claimed_task = repository.claim_pending_task(worker_id="worker-1")

        assert claimed_task is not None
        assert claimed_task.id == created_task.id
        assert claimed_task.status == TaskStatus.RUNNING.value
        assert claimed_task.worker_id == "worker-1"
        assert claimed_task.started_at is not None

    def test_claim_pending_task_none_when_empty(self, repository):
        """Test claiming when no tasks available."""
        result = repository.claim_pending_task(worker_id="worker-1")
        assert result is None

    def test_claim_pending_task_with_type_filter(self, repository):
        """Test claiming tasks filtered by type."""
        repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="instance-1",
        )
        repository.create(
            task_type=TaskType.SEND_REPORT.value,
            instance_id="instance-1",
        )

        claimed = repository.claim_pending_task(
            worker_id="worker-1",
            task_type=TaskType.PROCESS_MESSAGE.value,
        )

        assert claimed is not None
        assert claimed.task_type == TaskType.PROCESS_MESSAGE.value

    def test_claim_only_returns_pending(self, repository):
        """Test that claiming only returns pending tasks."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="instance-1",
        )
        repository.claim_pending_task(worker_id="worker-1")

        result = repository.claim_pending_task(worker_id="worker-2")
        assert result is None

    def test_claim_fifo_ordering(self, repository):
        """Test that claiming returns oldest pending task first."""
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i3")

        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.instance_id == "i1"


class TestTaskCompletion:
    """Tests for task completion."""

    def test_complete_task(self, repository, sample_task_data):
        """Test completing a task with result."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        result_data = {"success": True, "output": "test output"}
        completed = repository.complete_task(created.id, result_data)

        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED.value
        assert completed.completed_at is not None
        assert completed.result is not None
        assert json.loads(completed.result) == result_data

    def test_complete_task_not_found(self, repository):
        """Test completing non-existent task."""
        result = repository.complete_task(99999, {"success": True})
        assert result is None

    def test_complete_already_completed_task(self, repository, sample_task_data):
        """Test completing an already completed task still works."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")
        repository.complete_task(created.id, {"first": "result"})

        second = repository.complete_task(created.id, {"second": "result"})

        assert second is not None
        assert second.status == TaskStatus.COMPLETED.value


class TestTaskFailure:
    """Tests for task failure."""

    def test_fail_task(self, repository, sample_task_data):
        """Test failing a task with error."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        failed = repository.fail_task(created.id, "Test error message")

        assert failed is not None
        assert failed.status == TaskStatus.FAILED.value
        assert failed.error == "Test error message"
        assert failed.completed_at is not None

    def test_fail_task_not_found(self, repository):
        """Test failing non-existent task."""
        result = repository.fail_task(99999, "Error")
        assert result is None

    def test_fail_task_preserves_other_fields(self, repository, sample_task_data):
        """Test that failing preserves other task fields."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        failed = repository.fail_task(created.id, "Error message")

        assert failed.id == created.id
        assert failed.task_type == created.task_type
        assert failed.instance_id == created.instance_id
        assert failed.message_id == created.message_id
        assert failed.worker_id == "worker-1"


class TestTaskRecovery:
    """Tests for task recovery (stale task handling)."""

    def test_find_stale_running_tasks_empty(self, repository, sample_task_data):
        """Test finding stale tasks when none exist."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        stale = repository.find_stale_running_tasks(threshold_minutes=15)

        assert len(stale) == 0

    def test_reset_stale_tasks_empty(self, repository, sample_task_data):
        """Test resetting stale tasks when none are stale."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        count = repository.reset_stale_tasks(threshold_minutes=15)

        assert count == 0

    def test_reset_stale_tasks_resets_tasks(self, repository, sample_task_data):
        """Test that reset_stale_tasks resets running tasks to pending."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        task = repository.get(created.id)
        assert task.status == TaskStatus.RUNNING.value

        count = repository.reset_stale_tasks(threshold_minutes=0)

        assert count == 1

        task = repository.get(created.id)
        assert task.status == TaskStatus.PENDING.value
        assert task.worker_id is None
        assert task.started_at is None


class TestTaskStats:
    """Tests for task statistics."""

    def test_get_pending_count(self, repository):
        """Test getting pending task count."""
        assert repository.get_pending_count() == 0

        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")
        repository.create(task_type=TaskType.CLEANUP.value, instance_id="i3")

        assert repository.get_pending_count() == 3

    def test_count_by_status(self, repository):
        """Test counting tasks by status."""
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")

        repository.claim_pending_task(worker_id="worker-1")

        counts = repository.count_by_status()

        assert counts["pending"] == 1
        assert counts["running"] == 1
        assert counts["completed"] == 0
        assert counts["failed"] == 0

    def test_count_by_status_after_completion(self, repository):
        """Test counts update after task completion."""
        task = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.claim_pending_task(worker_id="worker-1")
        repository.complete_task(task.id, {"result": "ok"})

        counts = repository.count_by_status()

        assert counts["pending"] == 0
        assert counts["running"] == 0
        assert counts["completed"] == 1


class TestTaskDeletion:
    """Tests for task deletion."""

    def test_delete_task(self, repository, sample_task_data):
        """Test deleting a task."""
        task = repository.create(**sample_task_data)
        task_id = task.id

        result = repository.delete(task_id)
        assert result is True

        assert repository.get(task_id) is None

    def test_delete_task_not_found(self, repository):
        """Test deleting non-existent task."""
        result = repository.delete(99999)
        assert result is False

    def test_delete_by_instance(self, repository):
        """Test deleting all tasks for an instance."""
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="instance-1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="instance-1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="instance-2")

        count = repository.delete_by_instance("instance-1")
        assert count == 2

        remaining = repository.get_by_instance("instance-2")
        assert len(remaining) == 1

        assert repository.get_pending_count() == 1

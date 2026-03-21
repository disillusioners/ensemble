"""Tests for TaskRepository.

This module tests the SQLModel-based repository for task queue CRUD operations.
"""

import pytest
import time

from daemon.repositories.task_queue import TaskRepository
from daemon.repositories.task_queue.models import TaskStatus, TaskQueueItem


class TestRepositoryCreate:
    """Tests for task creation."""

    def test_create_task_basic(self, repository, sample_task_data):
        """Test creating a basic task."""
        task = repository.create(**sample_task_data)
        
        assert task.task_id is not None
        assert task.agent_dir == sample_task_data["agent_dir"]
        assert task.message == sample_task_data["message"]
        assert task.source == sample_task_data["source"]
        assert task.project_id == sample_task_data["project_id"]
        assert task.priority == sample_task_data["priority"]
        assert task.status == TaskStatus.PENDING.value
        assert task.task_metadata == sample_task_data["task_metadata"]

    def test_create_task_without_project(self, repository, sample_task_data_no_project):
        """Test creating a task without project_id."""
        task = repository.create(**sample_task_data_no_project)
        
        assert task.task_id is not None
        assert task.project_id is None
        assert task.status == TaskStatus.PENDING.value

    def test_create_task_default_values(self, repository):
        """Test creating task with minimal parameters."""
        task = repository.create(
            agent_dir="/test/agent",
            message="Test message"
        )
        
        assert task.task_id is not None
        assert task.source == "api"  # Default value
        assert task.priority == 5  # Default value
        assert task.status == TaskStatus.PENDING.value
        assert task.task_metadata == {}  # Default empty dict

    def test_create_task_generates_timestamps(self, repository, sample_task_data):
        """Test that create generates created_at timestamp."""
        task = repository.create(**sample_task_data)
        
        assert task.created_at is not None
        assert task.started_at is None
        assert task.completed_at is None

    def test_create_task_uuid_format(self, repository, sample_task_data):
        """Test that task_id is a valid UUID."""
        task = repository.create(**sample_task_data)
        
        # Should be a valid UUID format (36 chars with hyphens)
        assert len(task.task_id) == 36
        assert task.task_id.count("-") == 4

    def test_create_multiple_tasks_unique_ids(self, repository, sample_task_data):
        """Test that multiple created tasks have unique IDs."""
        task1 = repository.create(**sample_task_data)
        task2 = repository.create(**sample_task_data)
        task3 = repository.create(**sample_task_data)
        
        assert task1.task_id != task2.task_id
        assert task2.task_id != task3.task_id
        assert task1.task_id != task3.task_id


class TestRepositoryRead:
    """Tests for task retrieval."""

    def test_get_existing_task(self, repository, sample_task_data):
        """Test getting an existing task by ID."""
        created = repository.create(**sample_task_data)
        
        retrieved = repository.get(created.task_id)
        
        assert retrieved is not None
        assert retrieved.task_id == created.task_id
        assert retrieved.message == created.message

    def test_get_nonexistent_task(self, repository):
        """Test getting a non-existent task returns None."""
        result = repository.get("nonexistent-id")
        assert result is None

    def test_get_by_session_existing(self, repository, sample_task_data):
        """Test getting task by session ID."""
        created = repository.create(**sample_task_data)
        started = repository.start_task(created.task_id, "test-session")
        
        retrieved = repository.get_by_session("test-session")
        
        assert retrieved is not None
        assert retrieved.task_id == created.task_id

    def test_get_by_session_nonexistent(self, repository):
        """Test getting by non-existent session returns None."""
        result = repository.get_by_session("nonexistent-session")
        assert result is None


class TestRepositoryList:
    """Tests for task listing."""

    def test_list_all_tasks(self, repository, sample_task_data):
        """Test listing all tasks."""
        repository.create(**sample_task_data)
        repository.create(**sample_task_data)
        repository.create(**sample_task_data)
        
        tasks, total = repository.list()
        
        assert len(tasks) == 3
        assert total == 3

    def test_list_by_status(self, repository, sample_task_data):
        """Test listing tasks filtered by status."""
        task1 = repository.create(**sample_task_data)
        task2 = repository.create(**sample_task_data)
        
        # Start task1
        repository.start_task(task1.task_id, "session-1")
        
        pending_tasks, total = repository.list(status=TaskStatus.PENDING.value)
        processing_tasks, _ = repository.list(status=TaskStatus.PROCESSING.value)
        
        assert len(pending_tasks) == 1
        assert pending_tasks[0].task_id == task2.task_id
        assert len(processing_tasks) == 1
        assert processing_tasks[0].task_id == task1.task_id

    def test_list_by_project(self, repository, sample_task_data):
        """Test listing tasks filtered by project."""
        task1 = repository.create(**sample_task_data)
        task2 = repository.create(
            **{
                **sample_task_data,
                "project_id": "other-project"
            }
        )
        
        tasks, total = repository.list(project_id="test-project")
        
        assert len(tasks) == 1
        assert tasks[0].task_id == task1.task_id

    def test_list_with_pagination(self, repository, sample_task_data):
        """Test listing with limit and offset."""
        for i in range(5):
            repository.create(**sample_task_data)
        
        # Get first page
        page1, total = repository.list(limit=2, offset=0)
        assert len(page1) == 2
        assert total == 5
        
        # Get second page
        page2, _ = repository.list(limit=2, offset=2)
        assert len(page2) == 2
        
        # Get last item
        page3, _ = repository.list(limit=2, offset=4)
        assert len(page3) == 1

    def test_list_empty_queue(self, repository):
        """Test listing when no tasks exist."""
        tasks, total = repository.list()
        
        assert tasks == []
        assert total == 0

    def test_list_pending_by_project(self, repository, sample_task_data):
        """Test listing pending tasks for a specific project."""
        # Create multiple tasks for same project
        task1 = repository.create(**sample_task_data)  # priority=5
        task2 = repository.create(**sample_task_data)  # priority=5
        
        # Create task for different project
        repository.create(**{**sample_task_data, "project_id": "other"})
        
        pending = repository.list_pending_by_project("test-project")
        
        assert len(pending) == 2
        assert all(t.status == TaskStatus.PENDING.value for t in pending)

    def test_list_pending_ordered_by_priority(self, repository):
        """Test that pending tasks are ordered by priority descending."""
        # Create tasks with different priorities
        repository.create(
            agent_dir="/test", message="low",
            project_id="test", priority=1
        )
        repository.create(
            agent_dir="/test", message="high",
            project_id="test", priority=10
        )
        repository.create(
            agent_dir="/test", message="medium",
            project_id="test", priority=5
        )
        
        pending = repository.list_pending_by_project("test")
        
        assert len(pending) == 3
        assert pending[0].message == "high"  # priority=10
        assert pending[1].message == "medium"  # priority=5
        assert pending[2].message == "low"  # priority=1

    def test_list_all_pending(self, repository, sample_task_data):
        """Test listing all pending tasks regardless of project."""
        # Create tasks for different projects
        task1 = repository.create(**sample_task_data)
        task2 = repository.create(
            **{
                **sample_task_data,
                "project_id": "other-project",
                "priority": 10  # Higher priority
            }
        )
        
        # Start task1
        repository.start_task(task1.task_id, "session-1")
        
        pending = repository.list_all_pending()
        
        # Should only return task2 (task1 is now processing)
        assert len(pending) == 1
        assert pending[0].task_id == task2.task_id


class TestRepositoryUpdate:
    """Tests for task updates."""

    def test_update_single_field(self, repository, sample_task_data):
        """Test updating a single field."""
        task = repository.create(**sample_task_data)
        
        updated = repository.update(task.task_id, priority=8)
        
        assert updated is not None
        assert updated.priority == 8
        assert updated.message == sample_task_data["message"]  # Unchanged

    def test_update_multiple_fields(self, repository, sample_task_data):
        """Test updating multiple fields."""
        task = repository.create(**sample_task_data)
        
        updated = repository.update(
            task.task_id,
            priority=3,
            message="Updated message"
        )
        
        assert updated.priority == 3
        assert updated.message == "Updated message"

    def test_update_nonexistent_task(self, repository):
        """Test updating non-existent task returns None."""
        result = repository.update("nonexistent-id", priority=10)
        assert result is None

    def test_update_invalid_status(self, repository, sample_task_data):
        """Test updating with invalid status raises ValueError."""
        task = repository.create(**sample_task_data)
        
        with pytest.raises(ValueError) as exc_info:
            repository.update(task.task_id, status="invalid-status")
        
        assert "Invalid status" in str(exc_info.value)


class TestRepositoryTaskLifecycle:
    """Tests for task lifecycle transitions."""

    def test_start_pending_task(self, repository, sample_task_data):
        """Test starting a pending task."""
        task = repository.create(**sample_task_data)
        
        started = repository.start_task(task.task_id, "session-1")
        
        assert started is not None
        assert started.status == TaskStatus.PROCESSING.value
        assert started.session_id == "session-1"
        assert started.started_at is not None

    def test_start_already_started_task_raises(self, repository, sample_task_data):
        """Test starting an already started task raises ValueError."""
        task = repository.create(**sample_task_data)
        repository.start_task(task.task_id, "session-1")
        
        with pytest.raises(ValueError) as exc_info:
            repository.start_task(task.task_id, "session-2")
        
        assert "Cannot start task" in str(exc_info.value)
        assert "processing" in str(exc_info.value)

    def test_start_completed_task_raises(self, repository, sample_task_data):
        """Test starting a completed task raises ValueError."""
        task = repository.create(**sample_task_data)
        started = repository.start_task(task.task_id, "session-1")
        repository.complete_task(started.task_id)
        
        with pytest.raises(ValueError) as exc_info:
            repository.start_task(task.task_id, "session-2")
        
        assert "Cannot start task" in str(exc_info.value)
        assert "completed" in str(exc_info.value)

    def test_complete_processing_task(self, repository, sample_task_data):
        """Test completing a processing task."""
        task = repository.create(**sample_task_data)
        started = repository.start_task(task.task_id, "session-1")
        
        completed = repository.complete_task(
            started.task_id,
            result_summary="Task completed successfully"
        )
        
        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED.value
        assert completed.completed_at is not None
        assert completed.result_summary == "Task completed successfully"

    def test_complete_pending_task_raises(self, repository, sample_task_data):
        """Test completing a pending task raises ValueError."""
        task = repository.create(**sample_task_data)
        
        with pytest.raises(ValueError) as exc_info:
            repository.complete_task(task.task_id)
        
        assert "Cannot complete task" in str(exc_info.value)
        assert "pending" in str(exc_info.value)

    def test_fail_processing_task(self, repository, sample_task_data):
        """Test failing a processing task."""
        task = repository.create(**sample_task_data)
        started = repository.start_task(task.task_id, "session-1")
        
        failed = repository.fail_task(
            started.task_id,
            error_message="Something went wrong"
        )
        
        assert failed is not None
        assert failed.status == TaskStatus.FAILED.value
        assert failed.completed_at is not None
        assert failed.error_message == "Something went wrong"

    def test_fail_pending_task_raises(self, repository, sample_task_data):
        """Test failing a pending task raises ValueError."""
        task = repository.create(**sample_task_data)
        
        with pytest.raises(ValueError) as exc_info:
            repository.fail_task(task.task_id, "Error")
        
        assert "Cannot fail task" in str(exc_info.value)
        assert "pending" in str(exc_info.value)

    def test_cancel_pending_task(self, repository, sample_task_data):
        """Test cancelling a pending task."""
        task = repository.create(**sample_task_data)
        
        cancelled = repository.cancel_task(task.task_id)
        
        assert cancelled is not None
        assert cancelled.status == TaskStatus.CANCELLED.value
        assert cancelled.cancelled_at is not None

    def test_cancel_processing_task_raises(self, repository, sample_task_data):
        """Test cancelling a processing task raises ValueError."""
        task = repository.create(**sample_task_data)
        repository.start_task(task.task_id, "session-1")
        
        with pytest.raises(ValueError) as exc_info:
            repository.cancel_task(task.task_id)
        
        assert "Cannot cancel task" in str(exc_info.value)
        assert "processing" in str(exc_info.value)


class TestRepositoryDelete:
    """Tests for task deletion."""

    def test_delete_existing_task(self, repository, sample_task_data):
        """Test deleting an existing task."""
        task = repository.create(**sample_task_data)
        
        result = repository.delete(task.task_id)
        
        assert result["deleted"] is True
        assert result["task_id"] == task.task_id
        
        # Verify task is gone
        assert repository.get(task.task_id) is None

    def test_delete_nonexistent_task(self, repository):
        """Test deleting non-existent task returns error."""
        result = repository.delete("nonexistent-id")
        
        assert result["deleted"] is False
        assert "error" in result

    def test_delete_completed_tasks(self, repository, sample_task_data):
        """Test deleting all completed tasks."""
        # Create and complete some tasks
        task1 = repository.create(**sample_task_data)
        task2 = repository.create(**sample_task_data)
        task3 = repository.create(**sample_task_data)
        
        repository.start_task(task1.task_id, "s1")
        repository.start_task(task2.task_id, "s2")
        repository.complete_task(task1.task_id)
        repository.complete_task(task2.task_id)
        # task3 remains pending
        
        deleted_count = repository.delete_completed()
        
        assert deleted_count == 2
        assert repository.get(task1.task_id) is None
        assert repository.get(task2.task_id) is None
        assert repository.get(task3.task_id) is not None

    def test_delete_by_project(self, repository, sample_task_data):
        """Test deleting all tasks for a project."""
        # Create tasks for multiple projects
        task1 = repository.create(**sample_task_data)  # test-project
        task2 = repository.create(**sample_task_data)  # test-project
        task3 = repository.create(
            **{**sample_task_data, "project_id": "other"}
        )
        
        deleted_count = repository.delete_by_project("test-project")
        
        assert deleted_count == 2
        assert repository.get(task1.task_id) is None
        assert repository.get(task2.task_id) is None
        assert repository.get(task3.task_id) is not None

    def test_delete_completed_when_none(self, repository):
        """Test delete_completed when no completed tasks exist."""
        count = repository.delete_completed()
        assert count == 0


class TestRepositoryEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_create_task_with_extreme_priority(self, repository, sample_task_data):
        """Test creating tasks with boundary priority values."""
        low_task = repository.create(**{**sample_task_data, "priority": 1})
        high_task = repository.create(**{**sample_task_data, "priority": 10})
        
        assert low_task.priority == 1
        assert high_task.priority == 10

    def test_create_task_with_metadata(self, repository):
        """Test creating task with complex metadata."""
        metadata = {
            "user_id": "user-123",
            "tags": ["urgent", "backend"],
            "config": {"timeout": 30, "retries": 3}
        }
        
        task = repository.create(
            agent_dir="/test",
            message="Test",
            task_metadata=metadata
        )
        
        assert task.task_metadata == metadata

    def test_start_task_with_empty_session(self, repository, sample_task_data):
        """Test starting task with empty session ID."""
        task = repository.create(**sample_task_data)
        
        # Empty string should be allowed
        started = repository.start_task(task.task_id, "")
        assert started is not None
        assert started.session_id == ""

    def test_update_task_metadata(self, repository, sample_task_data):
        """Test updating task metadata."""
        task = repository.create(**sample_task_data)
        
        updated = repository.update(
            task.task_id,
            task_metadata={"new_key": "new_value"}
        )
        
        assert updated.task_metadata == {"new_key": "new_value"}

    def test_list_with_filters_combined(self, repository, sample_task_data):
        """Test listing with multiple filters combined."""
        # Create task in different states
        task1 = repository.create(**sample_task_data)
        repository.create(**{**sample_task_data, "project_id": "other"})
        
        repository.start_task(task1.task_id, "session-1")
        
        # Filter by both status and project
        tasks, total = repository.list(
            status=TaskStatus.PENDING.value,
            project_id="test-project"
        )
        
        assert total == 0  # No pending tasks for test-project

    def test_get_task_idempotent(self, repository, sample_task_data):
        """Test that getting same task multiple times works."""
        task = repository.create(**sample_task_data)
        
        result1 = repository.get(task.task_id)
        result2 = repository.get(task.task_id)
        result3 = repository.get(task.task_id)
        
        assert result1.task_id == result2.task_id == result3.task_id

    def test_start_nonexistent_task(self, repository):
        """Test starting non-existent task returns None."""
        result = repository.start_task("nonexistent-id", "session")
        assert result is None

    def test_complete_nonexistent_task(self, repository):
        """Test completing non-existent task returns None."""
        result = repository.complete_task("nonexistent-id")
        assert result is None

    def test_fail_nonexistent_task(self, repository):
        """Test failing non-existent task returns None."""
        result = repository.fail_task("nonexistent-id", "error")
        assert result is None

    def test_cancel_nonexistent_task(self, repository):
        """Test cancelling non-existent task returns None."""
        result = repository.cancel_task("nonexistent-id")
        assert result is None


class TestRepositoryConcurrency:
    """Tests for concurrent access patterns."""

    def test_rapid_create_operations(self, repository, sample_task_data):
        """Test creating many tasks rapidly."""
        tasks = []
        for i in range(100):
            tasks.append(repository.create(**sample_task_data))
        
        assert len(tasks) == 100
        assert len(set(t.task_id for t in tasks)) == 100  # All unique

    def test_task_status_consistency(self, repository, sample_task_data):
        """Test that task status transitions are consistent."""
        task = repository.create(**sample_task_data)
        
        # Verify initial state
        assert task.status == TaskStatus.PENDING.value
        
        # Start task
        started = repository.start_task(task.task_id, "session-1")
        assert started.status == TaskStatus.PROCESSING.value
        
        # Complete task
        completed = repository.complete_task(task.task_id, "Done")
        assert completed.status == TaskStatus.COMPLETED.value
        
        # Verify final state persists
        final = repository.get(task.task_id)
        assert final.status == TaskStatus.COMPLETED.value
        assert final.completed_at is not None


class TestTaskStatusValidation:
    """Tests for TaskStatus enum validation."""

    def test_valid_status_values(self):
        """Test all valid status values."""
        assert TaskStatus.is_valid("pending")
        assert TaskStatus.is_valid("processing")
        assert TaskStatus.is_valid("completed")
        assert TaskStatus.is_valid("failed")
        assert TaskStatus.is_valid("cancelled")

    def test_invalid_status_values(self):
        """Test invalid status values return False."""
        assert TaskStatus.is_valid("invalid") is False
        assert TaskStatus.is_valid("") is False
        assert TaskStatus.is_valid("PENDING") is False  # Case sensitive
        assert TaskStatus.is_valid("Pending") is False  # Case sensitive


class TestTaskQueueItem:
    """Tests for TaskQueueItem model."""

    def test_to_dict(self, repository, sample_task_data):
        """Test TaskQueueItem.to_dict() method."""
        task = repository.create(**sample_task_data)
        
        task_dict = task.to_dict()
        
        assert isinstance(task_dict, dict)
        assert task_dict["task_id"] == task.task_id
        assert task_dict["message"] == task.message
        assert task_dict["status"] == task.status
        assert task_dict["metadata"] == task.task_metadata

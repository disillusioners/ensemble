"""Integration tests for Task Queue feature.

This module tests the complete task queue workflow including:
- Full workflow: enqueue -> process -> complete
- Multiple tasks with same project (serialization)
- Multiple tasks with different projects (parallel)
- Crash recovery scenario
"""

import asyncio
import pytest

from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.task_queue import TaskRepository
from daemon.repositories.task_queue.models import TaskStatus
from daemon.services.task_lock_manager import TaskLockManager
from daemon.services.task_queue_service import TaskQueueService


@pytest.fixture
def integration_engine():
    """Create fresh in-memory SQLite engine for integration tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def integration_repository(integration_engine):
    """Create repository with fresh database."""
    return TaskRepository(integration_engine)


@pytest.fixture
def integration_lock_manager():
    """Create fresh lock manager."""
    manager = TaskLockManager()
    yield manager
    manager.clear()


@pytest.fixture
def integration_service(integration_repository, integration_lock_manager):
    """Create service with fresh dependencies."""
    return TaskQueueService(integration_repository, integration_lock_manager)


class TestIntegrationBasicWorkflow:
    """Tests for basic task queue workflow."""

    @pytest.mark.asyncio
    async def test_enqueue_process_complete_workflow(
        self, integration_service
    ):
        """Test the complete enqueue -> process -> complete workflow."""
        # Step 1: Enqueue task
        task = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Process this task",
            source="test",
            project_id="project-1",
            priority=5
        )
        
        assert task is not None
        assert task.status == TaskStatus.PROCESSING.value
        assert task.session_id is not None
        
        # Step 2: Verify task is in database
        retrieved = await integration_service.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.status == TaskStatus.PROCESSING.value
        
        # Step 3: Complete the task
        completed = await integration_service.complete_task(task.task_id)
        
        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED.value
        assert completed.completed_at is not None
        
        # Step 4: Verify final state
        final = await integration_service.get_task(task.task_id)
        assert final.status == TaskStatus.COMPLETED.value
        
        # Step 5: Verify lock is released
        assert await integration_service._lock_manager.is_locked("project-1") is False

    @pytest.mark.asyncio
    async def test_enqueue_without_project_skips_queue(
        self, integration_service
    ):
        """Test that tasks without project_id skip the queue entirely."""
        task = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="No project task",
            source="test",
            project_id=None,
            priority=5
        )
        
        # Should be processing immediately
        assert task.status == TaskStatus.PROCESSING.value
        assert task.session_id is not None
        
        # Complete the task
        completed = await integration_service.complete_task(task.task_id)
        assert completed.status == TaskStatus.COMPLETED.value


class TestIntegrationSameProjectSerialization:
    """Tests for tasks with the same project (serialization)."""

    @pytest.mark.asyncio
    async def test_multiple_tasks_same_project_serialized(
        self, integration_service
    ):
        """Test that multiple tasks for the same project are serialized."""
        # Enqueue multiple tasks for the same project
        task1 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 1",
            project_id="project-1",
            priority=5
        )
        
        task2 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 2",
            project_id="project-1",
            priority=5
        )
        
        task3 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 3",
            project_id="project-1",
            priority=5
        )
        
        # Only first task should be processing
        assert task1.status == TaskStatus.PROCESSING.value
        assert task2.status == TaskStatus.PENDING.value
        assert task3.status == TaskStatus.PENDING.value
        
        # Lock should be held
        assert await integration_service._lock_manager.is_locked("project-1") is True
        
        # Complete task 1
        await integration_service.complete_task(task1.task_id)
        
        # Trigger next task (lock is released, so task2 should start)
        await integration_service.trigger_next_task("project-1")
        
        # Task 2 should now be processing
        task2_updated = await integration_service.get_task(task2.task_id)
        assert task2_updated.status == TaskStatus.PROCESSING.value
        
        # Complete task 2
        await integration_service.complete_task(task2.task_id)
        
        # Trigger next task
        await integration_service.trigger_next_task("project-1")
        
        # Task 3 should now be processing
        task3_updated = await integration_service.get_task(task3.task_id)
        assert task3_updated.status == TaskStatus.PROCESSING.value
        
        # Complete task 3
        await integration_service.complete_task(task3.task_id)
        
        # All tasks completed
        assert await integration_service._lock_manager.is_locked("project-1") is False
        
        # Verify all are completed
        all_tasks = await integration_service.list_tasks()
        assert all(t.status == TaskStatus.COMPLETED.value for t in all_tasks)

    @pytest.mark.asyncio
    async def test_priority_ordering_same_project(
        self, integration_service
    ):
        """Test that tasks are processed by priority for same project."""
        # Enqueue in reverse priority order
        task_low = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Low priority",
            project_id="project-1",
            priority=1
        )
        
        task_high = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="High priority",
            project_id="project-1",
            priority=10
        )
        
        task_medium = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Medium priority",
            project_id="project-1",
            priority=5
        )
        
        # First task (low priority) is processing
        assert task_low.status == TaskStatus.PROCESSING.value
        
        # Complete low priority task
        await integration_service.complete_task(task_low.task_id)
        
        # Trigger next task
        await integration_service.trigger_next_task("project-1")
        
        # High priority should be next
        task_high_updated = await integration_service.get_task(task_high.task_id)
        assert task_high_updated.status == TaskStatus.PROCESSING.value
        
        # Complete high priority task
        await integration_service.complete_task(task_high.task_id)
        
        # Trigger next task
        await integration_service.trigger_next_task("project-1")
        
        # Medium priority should be last
        task_medium_updated = await integration_service.get_task(task_medium.task_id)
        assert task_medium_updated.status == TaskStatus.PROCESSING.value

    @pytest.mark.asyncio
    async def test_cancel_queued_task_unblocks_next(
        self, integration_service
    ):
        """Test that cancelling a queued task allows next task to proceed."""
        task1 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 1",
            project_id="project-1"
        )
        
        task2 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 2",
            project_id="project-1"
        )
        
        task3 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 3",
            project_id="project-1"
        )
        
        # Cancel task 2
        await integration_service.cancel_task(task2.task_id)
        
        # Complete task 1
        await integration_service.complete_task(task1.task_id)
        
        # Trigger next task (task 2 was cancelled, so task 3 should start)
        await integration_service.trigger_next_task("project-1")
        
        # Task 3 should be processing (task 2 was cancelled)
        task3_updated = await integration_service.get_task(task3.task_id)
        assert task3_updated.status == TaskStatus.PROCESSING.value


class TestIntegrationDifferentProjectsParallel:
    """Tests for tasks with different projects (parallel execution)."""

    @pytest.mark.asyncio
    async def test_different_projects_run_parallel(
        self, integration_service
    ):
        """Test that tasks for different projects run in parallel."""
        task1 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task for project 1",
            project_id="project-1"
        )
        
        task2 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task for project 2",
            project_id="project-2"
        )
        
        task3 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task for project 3",
            project_id="project-3"
        )
        
        # All should be processing
        assert task1.status == TaskStatus.PROCESSING.value
        assert task2.status == TaskStatus.PROCESSING.value
        assert task3.status == TaskStatus.PROCESSING.value
        
        # All locks should be held
        assert await integration_service._lock_manager.is_locked("project-1") is True
        assert await integration_service._lock_manager.is_locked("project-2") is True
        assert await integration_service._lock_manager.is_locked("project-3") is True
        
        # Complete all
        await integration_service.complete_task(task1.task_id)
        await integration_service.complete_task(task2.task_id)
        await integration_service.complete_task(task3.task_id)
        
        # All locks released
        assert await integration_service._lock_manager.is_locked("project-1") is False
        assert await integration_service._lock_manager.is_locked("project-2") is False
        assert await integration_service._lock_manager.is_locked("project-3") is False

    @pytest.mark.asyncio
    async def test_mixed_projects_serialization_and_parallelism(
        self, integration_service
    ):
        """Test mixing serialized and parallel tasks."""
        # Project 1 gets multiple tasks (serialized)
        task1_p1 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="P1 Task 1",
            project_id="project-1"
        )
        
        task2_p1 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="P1 Task 2",
            project_id="project-1"
        )
        
        # Project 2 gets one task (parallel)
        task_p2 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="P2 Task",
            project_id="project-2"
        )
        
        # Both projects should have processing tasks
        assert task1_p1.status == TaskStatus.PROCESSING.value
        assert task_p2.status == TaskStatus.PROCESSING.value
        
        # Project 1 should have one pending
        assert task2_p1.status == TaskStatus.PENDING.value
        
        # Complete project 2 task
        await integration_service.complete_task(task_p2.task_id)
        
        # Project 1 task 2 is still pending (not unblocked, it's same project)
        task2_p1_updated = await integration_service.get_task(task2_p1.task_id)
        assert task2_p1_updated.status == TaskStatus.PENDING.value
        
        # Complete project 1 task 1
        await integration_service.complete_task(task1_p1.task_id)
        
        # Trigger next task for project 1
        await integration_service.trigger_next_task("project-1")
        
        # Now project 1 task 2 should start
        task2_p1_updated = await integration_service.get_task(task2_p1.task_id)
        assert task2_p1_updated.status == TaskStatus.PROCESSING.value


class TestIntegrationCrashRecovery:
    """Tests for crash recovery scenarios."""

    @pytest.mark.asyncio
    async def test_recovery_from_lock_manager_crash(
        self, integration_service, integration_lock_manager
    ):
        """Test recovery when lock manager state is lost (simulated crash)."""
        # Enqueue and start a task
        task = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task before crash",
            project_id="project-1"
        )
        
        assert task.status == TaskStatus.PROCESSING.value
        assert await integration_service._lock_manager.is_locked("project-1") is True
        
        # Simulate crash: clear lock manager
        integration_lock_manager.clear()
        
        # Lock should be released (simulating crash recovery)
        assert await integration_service._lock_manager.is_locked("project-1") is False
        
        # The task is still in PROCESSING state in database
        # but the lock is gone - this is crash recovery state
        
        # New task should be able to acquire lock
        new_task = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task after crash",
            project_id="project-1"
        )
        
        # Should acquire lock and start processing
        assert new_task.status == TaskStatus.PROCESSING.value
        
        # The old task is now orphaned - depends on application logic to handle

    @pytest.mark.asyncio
    async def test_recovery_completed_task_cleanup(
        self, integration_service
    ):
        """Test cleanup of completed tasks after recovery."""
        # Create and complete some tasks
        for i in range(5):
            task = await integration_service.enqueue(
                agent_dir="/test/agent",
                message=f"Task {i}",
                project_id="project-1"
            )
            await integration_service.complete_task(task.task_id)
        
        # Verify all are completed
        tasks = await integration_service.list_tasks()
        assert all(t.status == TaskStatus.COMPLETED.value for t in tasks)
        
        # Cleanup completed tasks
        deleted = integration_service._repository.delete_completed()
        
        assert deleted == 5
        
        # Verify all tasks are gone
        remaining = await integration_service.list_tasks()
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_recovery_orphaned_processing_tasks(
        self, integration_service
    ):
        """Test handling of tasks stuck in PROCESSING state."""
        # Start a task but don't complete it
        task = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Orphaned task",
            project_id="project-1"
        )
        
        # Simulate crash: clear lock but leave task in PROCESSING
        await integration_service._lock_manager.release_by_session(task.session_id)
        
        # Task is still in PROCESSING state
        assert task.status == TaskStatus.PROCESSING.value
        
        # We should be able to cancel it
        cancelled = await integration_service.cancel_task(task.task_id)
        assert cancelled is True
        
        # Or we could manually reset it
        updated = integration_service._repository.update(
            task.task_id,
            status=TaskStatus.PENDING.value,
            session_id=None  # Clear session
        )
        assert updated is not None
        assert updated.status == TaskStatus.PENDING.value
        
        # Now a new task can be enqueued
        new_task = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="New task",
            project_id="project-1"
        )
        
        assert new_task.status == TaskStatus.PROCESSING.value

    @pytest.mark.asyncio
    async def test_recovery_with_multiple_queued_tasks(
        self, integration_service
    ):
        """Test recovery when multiple tasks are queued."""
        # Create a queue
        task1 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 1",
            project_id="project-1"
        )
        
        task2 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 2",
            project_id="project-1"
        )
        
        task3 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 3",
            project_id="project-1"
        )
        
        # Simulate crash during task1 processing
        await integration_service._lock_manager.release_by_session(task1.session_id)
        
        # Cancel the orphaned task1
        await integration_service.cancel_task(task1.task_id)
        
        # Complete the recovery
        await integration_service.complete_task(task1.task_id)
        
        # Trigger next task
        next_task = await integration_service.trigger_next_task("project-1")
        
        # Should be task2
        assert next_task is not None
        assert next_task.task_id == task2.task_id
        assert next_task.status == TaskStatus.PROCESSING.value
        
        # Complete task2 and trigger task3
        await integration_service.complete_task(task2.task_id)
        
        next_task = await integration_service.trigger_next_task("project-1")
        assert next_task.task_id == task3.task_id


class TestIntegrationConcurrentOperations:
    """Tests for concurrent task operations."""

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_same_project(
        self, integration_service
    ):
        """Test concurrent enqueue operations for same project."""
        async def enqueue_task(i: int):
            return await integration_service.enqueue(
                agent_dir="/test/agent",
                message=f"Concurrent task {i}",
                project_id="project-1"
            )
        
        # Enqueue multiple tasks concurrently
        results = await asyncio.gather(*[
            enqueue_task(i) for i in range(5)
        ])
        
        # Only one should be processing
        processing_count = sum(
            1 for t in results if t.status == TaskStatus.PROCESSING.value
        )
        pending_count = sum(
            1 for t in results if t.status == TaskStatus.PENDING.value
        )
        
        assert processing_count == 1
        assert pending_count == 4
        
        # Complete all in order
        for task in results:
            await integration_service.complete_task(task.task_id)
            # After each completion, next pending becomes processing
            # Check remaining pending
            remaining_pending = await integration_service.list_tasks(
                status=TaskStatus.PENDING,
                project_id="project-1"
            )
            # Can verify ordering

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_different_projects(
        self, integration_service
    ):
        """Test concurrent enqueue operations for different projects."""
        async def enqueue_task(i: int):
            return await integration_service.enqueue(
                agent_dir="/test/agent",
                message=f"Task for project {i}",
                project_id=f"project-{i}"
            )
        
        # Enqueue tasks for different projects concurrently
        results = await asyncio.gather(*[
            enqueue_task(i) for i in range(5)
        ])
        
        # All should be processing (different projects)
        assert all(
            t.status == TaskStatus.PROCESSING.value for t in results
        )

    @pytest.mark.asyncio
    async def test_concurrent_complete_operations(
        self, integration_service
    ):
        """Test concurrent complete operations."""
        # Create multiple tasks for different projects
        tasks = []
        for i in range(5):
            task = await integration_service.enqueue(
                agent_dir="/test/agent",
                message=f"Task {i}",
                project_id=f"project-{i}"
            )
            tasks.append(task)
        
        # Complete all concurrently
        async def complete_task(task):
            return await integration_service.complete_task(task.task_id)
        
        results = await asyncio.gather(*[
            complete_task(t) for t in tasks
        ])
        
        # All should complete successfully
        assert all(r is not None for r in results)
        assert all(
            r.status == TaskStatus.COMPLETED.value for r in results if r
        )


class TestIntegrationSessionManagement:
    """Tests for session-based lock management."""

    @pytest.mark.asyncio
    async def test_release_locks_by_session(
        self, integration_service
    ):
        """Test that releasing by session releases all locks for that session."""
        # Create tasks for different projects (each gets own session)
        task1 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 1",
            project_id="project-1"
        )
        
        task2 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 2",
            project_id="project-2"
        )
        
        task3 = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task 3",
            project_id="project-3"
        )
        
        # Verify all locks are held
        assert await integration_service._lock_manager.is_locked("project-1") is True
        assert await integration_service._lock_manager.is_locked("project-2") is True
        assert await integration_service._lock_manager.is_locked("project-3") is True
        
        # Release all locks for task1's session (only project-1)
        released = await integration_service.release_lock_by_session(task1.session_id)
        assert "project-1" in released
        
        # Only project-1 lock should be released
        assert await integration_service._lock_manager.is_locked("project-1") is False
        assert await integration_service._lock_manager.is_locked("project-2") is True
        assert await integration_service._lock_manager.is_locked("project-3") is True
        
        # Release task2's session
        released = await integration_service.release_lock_by_session(task2.session_id)
        assert "project-2" in released
        
        # Release task3's session
        released = await integration_service.release_lock_by_session(task3.session_id)
        assert "project-3" in released
        
        # All locks should be released
        assert await integration_service._lock_manager.is_locked("project-1") is False
        assert await integration_service._lock_manager.is_locked("project-2") is False
        assert await integration_service._lock_manager.is_locked("project-3") is False

    @pytest.mark.asyncio
    async def test_session_cleanup_releases_project_lock(
        self, integration_service
    ):
        """Test that session cleanup releases project lock."""
        task = await integration_service.enqueue(
            agent_dir="/test/agent",
            message="Task",
            project_id="project-1"
        )
        
        assert await integration_service._lock_manager.is_locked("project-1") is True
        
        # Cleanup by session
        released = await integration_service.release_lock_by_session(task.session_id)
        
        assert "project-1" in released
        assert await integration_service._lock_manager.is_locked("project-1") is False


class TestIntegrationPriorityQueue:
    """Tests for priority-based queue ordering."""

    @pytest.mark.asyncio
    async def test_priority_queue_ordering(
        self, integration_service
    ):
        """Test that tasks are processed in priority order."""
        # Enqueue tasks with different priorities
        priorities = [5, 1, 10, 3, 8]
        tasks = []
        
        for i, priority in enumerate(priorities):
            task = await integration_service.enqueue(
                agent_dir="/test/agent",
                message=f"Priority {priority}",
                project_id="project-1",
                priority=priority
            )
            tasks.append((priority, task))
        
        # First task (priority 5) should be processing
        assert tasks[0][1].status == TaskStatus.PROCESSING.value
        
        # Get the pending tasks - should be ordered by priority (desc)
        pending = integration_service._repository.list_pending_by_project("project-1")
        pending_priorities = [t.priority for t in pending]
        
        # Pending tasks should be sorted by priority descending
        assert pending_priorities == sorted(pending_priorities, reverse=True)
        
        # Complete all tasks
        for priority, task in tasks:
            await integration_service.complete_task(task.task_id)
            await integration_service.trigger_next_task("project-1")
        
        # Verify all tasks are completed
        all_tasks = await integration_service.list_tasks()
        assert all(t.status == TaskStatus.COMPLETED.value for t in all_tasks)

    @pytest.mark.asyncio
    async def test_same_priority_fifo_ordering(
        self, integration_service
    ):
        """Test FIFO ordering for same priority tasks."""
        tasks = []
        for i in range(3):
            task = await integration_service.enqueue(
                agent_dir="/test/agent",
                message=f"Task {i}",
                project_id="project-1",
                priority=5
            )
            tasks.append(task)
            # Small delay to ensure different created_at
            await asyncio.sleep(0.01)
        
        # Complete first task
        await integration_service.complete_task(tasks[0].task_id)
        
        # Next should be task[1]
        next_task = await integration_service.trigger_next_task("project-1")
        assert next_task.task_id == tasks[1].task_id


class TestIntegrationEndToEnd:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_complete_end_to_end_scenario(
        self, integration_service
    ):
        """Test a complete realistic end-to-end scenario."""
        # Simulate a real workload
        
        # 1. Submit initial tasks for different projects
        task1 = await integration_service.enqueue(
            agent_dir="/agents/code",
            message="Build authentication module",
            source="api",
            project_id="backend-api",
            priority=8
        )
        
        task2 = await integration_service.enqueue(
            agent_dir="/agents/docs",
            message="Update API documentation",
            source="api",
            project_id="backend-api",
            priority=5
        )
        
        task3 = await integration_service.enqueue(
            agent_dir="/agents/frontend",
            message="Fix login form styling",
            source="webhook",
            project_id="frontend-web",
            priority=6
        )
        
        # 2. Verify initial states
        assert task1.status == TaskStatus.PROCESSING.value
        assert task2.status == TaskStatus.PENDING.value
        assert task3.status == TaskStatus.PROCESSING.value
        
        # 3. Complete task3 (frontend-web, independent)
        await integration_service.complete_task(task3.task_id)
        
        # 4. Complete task1 (backend-api first task)
        await integration_service.complete_task(task1.task_id)
        
        # 5. Trigger next for backend-api
        next_backend = await integration_service.trigger_next_task("backend-api")
        assert next_backend.task_id == task2.task_id
        assert next_backend.status == TaskStatus.PROCESSING.value
        
        # 6. Complete remaining tasks
        await integration_service.complete_task(task2.task_id)
        
        # 7. Verify all completed
        all_tasks = await integration_service.list_tasks()
        assert all(t.status == TaskStatus.COMPLETED.value for t in all_tasks)
        
        # 8. Verify no locks held
        assert await integration_service._lock_manager.is_locked("backend-api") is False
        assert await integration_service._lock_manager.is_locked("frontend-web") is False

    @pytest.mark.asyncio
    async def test_high_load_scenario(self, integration_service):
        """Test with high load of tasks."""
        # Create many tasks across multiple projects
        num_projects = 3
        tasks_per_project = 10
        
        all_tasks = []
        for project_id in range(num_projects):
            project_tasks = []
            for i in range(tasks_per_project):
                task = await integration_service.enqueue(
                    agent_dir="/test/agent",
                    message=f"Task {i} for project {project_id}",
                    project_id=f"project-{project_id}",
                    priority=(i % 10) + 1
                )
                project_tasks.append(task)
            all_tasks.append(project_tasks)
        
        # Verify initial state: 1 processing, rest pending per project
        for project_tasks in all_tasks:
            assert project_tasks[0].status == TaskStatus.PROCESSING.value
            for task in project_tasks[1:]:
                assert task.status == TaskStatus.PENDING.value
        
        # Complete all tasks per project
        for project_id in range(num_projects):
            project_tasks = all_tasks[project_id]
            for task in project_tasks:
                # Complete current task
                completed = await integration_service.complete_task(task.task_id)
                # Trigger next task
                await integration_service.trigger_next_task(f"project-{project_id}")
        
        # Verify all tasks are completed
        final_tasks = await integration_service.list_tasks()
        assert len(final_tasks) == num_projects * tasks_per_project
        
        # Count completed tasks
        completed_count = sum(1 for t in final_tasks if t.status == TaskStatus.COMPLETED.value)
        assert completed_count == num_projects * tasks_per_project
        
        # No locks should be held
        assert len(await integration_service._lock_manager.get_all_locks()) == 0

    @pytest.mark.asyncio
    async def test_cancellation_recovery_scenario(self, integration_service):
        """Test cancellation and recovery scenario."""
        # Create a queue
        tasks = []
        for i in range(5):
            task = await integration_service.enqueue(
                agent_dir="/test/agent",
                message=f"Task {i}",
                project_id="project-1"
            )
            tasks.append(task)
        
        # Cancel middle tasks
        await integration_service.cancel_task(tasks[1].task_id)
        await integration_service.cancel_task(tasks[3].task_id)
        
        # Complete remaining in order
        await integration_service.complete_task(tasks[0].task_id)
        await integration_service.trigger_next_task("project-1")  # task 2
        
        await integration_service.complete_task(tasks[2].task_id)
        await integration_service.trigger_next_task("project-1")  # task 4
        
        await integration_service.complete_task(tasks[4].task_id)
        
        # Verify final states
        final_tasks = await integration_service.list_tasks()
        assert len(final_tasks) == 5
        
        cancelled = [t for t in final_tasks if t.status == TaskStatus.CANCELLED.value]
        completed = [t for t in final_tasks if t.status == TaskStatus.COMPLETED.value]
        
        assert len(cancelled) == 2
        assert len(completed) == 3

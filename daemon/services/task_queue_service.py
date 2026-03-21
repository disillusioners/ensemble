"""Task Queue Service - Manages task queuing with per-project locking.

This service provides the main interface for task queue operations,
coordinating between the database repository and the lock manager.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from daemon.repositories.task_queue import TaskRepository, TaskQueueItem, TaskStatus
from daemon.services.task_lock_manager import TaskLockManager


class TaskQueueService:
    """Manages task queuing with per-project locking.
    
    Provides the main interface for submitting, tracking, and managing
    tasks in a queue with per-project serialization via locks.
    
    Attributes:
        _repository: Database repository for task persistence.
        _lock_manager: Lock manager for per-project task serialization.
    """
    
    def __init__(
        self,
        repository: TaskRepository,
        lock_manager: TaskLockManager,
    ):
        """Initialize the TaskQueueService.
        
        Args:
            repository: Task repository for database operations.
            lock_manager: Lock manager for per-project task serialization.
        """
        self._repository = repository
        self._lock_manager = lock_manager
    
    # ========== Public API ==========
    
    async def enqueue(
        self,
        agent_dir: str,
        message: str,
        source: str = "api",
        project_id: Optional[str] = None,
        priority: int = 5,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TaskQueueItem:
        """Submit a task for processing.
        
        If project_id is None: create PROCESSING task immediately.
        If project_id provided and lock available: acquire lock, create PROCESSING task.
        If project_id provided and lock held: create PENDING task with queue position.
        
        Args:
            agent_dir: Path to the agent directory.
            message: Task message/content.
            source: Source of the task ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for task serialization.
            priority: Task priority (1-10, default 5).
            metadata: Optional metadata dictionary.
            
        Returns:
            TaskQueueItem with status and (if immediate) session_id.
        """
        # Create task once (status defaults to PENDING in repository)
        task = self._repository.create(
            agent_dir=agent_dir,
            message=message,
            source=source,
            project_id=project_id,
            priority=priority,
            task_metadata=metadata,
        )
        
        # If no project_id, execute immediately without locking
        if project_id is None:
            session_id = str(uuid.uuid4())
            started_task = self._repository.start_task(task.task_id, session_id)
            assert started_task is not None, f"Failed to start task {task.task_id}"
            return started_task
        
        # Try to acquire lock for this project
        session_id = str(uuid.uuid4())
        acquired = await self._lock_manager.acquire(
            project_id=project_id,
            task_id=task.task_id,
            session_id=session_id,
        )
        
        if acquired:
            try:
                started_task = self._repository.start_task(task.task_id, session_id)
                assert started_task is not None, f"Failed to start task {task.task_id}"
                return started_task
            except Exception:
                # Release lock on error and re-raise
                await self._lock_manager.release(project_id, task.task_id)
                raise
        
        # Lock is held by another task - keep task as PENDING
        return task
    
    async def get_task(self, task_id: str) -> Optional[TaskQueueItem]:
        """Get task by ID.
        
        Args:
            task_id: Unique task identifier.
            
        Returns:
            TaskQueueItem if found, None otherwise.
        """
        return self._repository.get(task_id)
    
    async def update_task(self, task_id: str, **updates) -> Optional[TaskQueueItem]:
        """Update task fields.
        
        Args:
            task_id: Unique task identifier.
            **updates: Fields to update (e.g., status, result_summary).
            
        Returns:
            Updated TaskQueueItem if found, None otherwise.
        """
        return self._repository.update(task_id, **updates)
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending task or abort a running task.
        
        Args:
            task_id: Task identifier.
            
        Returns:
            True if cancelled successfully, False if task not found or
            not in a cancellable state.
        """
        task = self._repository.get(task_id)
        if task is None:
            return False
        
        # Can only cancel PENDING tasks
        if task.status == TaskStatus.PENDING.value:
            self._repository.cancel_task(task_id)
            return True
        
        # Can abort PROCESSING tasks (release lock)
        if task.status == TaskStatus.PROCESSING.value:
            # Release the lock held by this task's session
            if task.session_id:
                await self._lock_manager.release_by_session(task.session_id)
            # Use update() instead of cancel_task() since PROCESSING tasks
            # can't be cancelled via cancel_task() (raises ValueError)
            self._repository.update(
                task_id,
                status=TaskStatus.CANCELLED.value,
                cancelled_at=datetime.utcnow().isoformat(),
            )
            return True
        
        return False
    
    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        project_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[TaskQueueItem]:
        """List tasks with optional filters.
        
        Args:
            status: Optional status filter.
            project_id: Optional project ID filter.
            limit: Maximum number of tasks to return.
            
        Returns:
            List of TaskQueueItem objects.
        """
        status_value = status.value if status else None
        tasks, _ = self._repository.list(
            status=status_value,
            project_id=project_id,
            limit=limit,
        )
        return tasks
    
    # ========== Helper Methods ==========
    
    def _try_start_task(self, task: TaskQueueItem) -> bool:
        """Try to start a pending task.
        
        Attempts to acquire the lock for the task's project and start
        processing the task.
        
        Args:
            task: The pending task to start.
            
        Returns:
            True if task was started, False otherwise.
        """
        if task.project_id is None:
            # No project, can start immediately
            session_id = str(uuid.uuid4())
            self._repository.start_task(task.task_id, session_id)
            return True
        
        # Try to acquire lock
        session_id = str(uuid.uuid4())
        # Use synchronous acquire since we're in a sync context
        acquired = self._lock_manager.acquire_sync(
            project_id=task.project_id,
            task_id=task.task_id,
            session_id=session_id,
        )
        
        if acquired:
            self._repository.start_task(task.task_id, session_id)
            return True
        
        return False
    
    def _complete_task(self, task: TaskQueueItem, result_summary: Optional[str]) -> None:
        """Mark a task as completed and release its lock.
        
        Args:
            task: The processing task to complete.
            result_summary: Optional summary of the task result.
        """
        # Release the lock first
        if task.project_id and task.session_id:
            self._lock_manager.release_sync(task.project_id, task.task_id)
        
        # Mark task as completed
        self._repository.complete_task(task.task_id, result_summary)
    
    def _fail_task(self, task: TaskQueueItem, error_message: str) -> None:
        """Mark a task as failed and release its lock.
        
        Args:
            task: The processing task that failed.
            error_message: Error message describing the failure.
        """
        # Release the lock first
        if task.project_id and task.session_id:
            self._lock_manager.release_sync(task.project_id, task.task_id)
        
        # Mark task as failed
        self._repository.fail_task(task.task_id, error_message)
    
    def _get_next_task(self, project_id: Optional[str]) -> Optional[TaskQueueItem]:
        """Get the next pending task for a project.
        
        Args:
            project_id: Optional project ID to get next task for.
                       If None, gets next pending task regardless of project.
            
        Returns:
            Next TaskQueueItem to process, or None if no pending tasks.
        """
        if project_id:
            pending = self._repository.list_pending_by_project(project_id)
            return pending[0] if pending else None
        else:
            pending = self._repository.list_all_pending()
            return pending[0] if pending else None
    
    def _get_queue_position(self, task_id: Optional[str], project_id: str) -> int:
        """Get the queue position for a task in its project.
        
        Returns the 1-based position of the task in the pending queue
        for its project, ordered by priority (desc) then created_at (asc).
        
        Args:
            task_id: Optional task ID to find position for.
                    If None, returns count of pending tasks + 1.
            project_id: The project to get queue position for.
            
        Returns:
            1-based queue position, or None if task not found in pending queue.
        """
        pending = self._repository.list_pending_by_project(project_id)
        
        if task_id is None:
            # Return position as if this task was added to end
            return len(pending) + 1
        
        for i, task in enumerate(pending, start=1):
            if task.task_id == task_id:
                return i
        
        return len(pending) + 1

    # ========== TaskProcessor Helper Methods ==========
    
    def get_next_pending_task(self) -> Optional[TaskQueueItem]:
        """Get the next pending task (highest priority, oldest first).
        
        Returns the first pending task from all projects, ordered by
        priority (descending) then created_at (ascending).
        
        Returns:
            Next TaskQueueItem to process, or None if no pending tasks.
        """
        pending = self._repository.list_all_pending()
        return pending[0] if pending else None
    
    async def start_task(self, task_id: str) -> Optional[TaskQueueItem]:
        """Mark task as processing and acquire lock.
        
        Attempts to acquire the lock for the task's project and mark
        the task as PROCESSING.
        
        Args:
            task_id: The task ID to start.
            
        Returns:
            Updated TaskQueueItem if started successfully, None if
            task not found, cancelled, or lock acquisition failed.
        """
        task = self._repository.get(task_id)
        if task is None:
            return None
        
        # Check if task is still pending (could have been cancelled)
        if task.status != TaskStatus.PENDING.value:
            return None
        
        # Generate new session ID for this task
        session_id = str(uuid.uuid4())
        
        # If no project_id, start immediately without locking
        if task.project_id is None:
            try:
                return self._repository.start_task(task_id, session_id)
            except ValueError:
                return None
        
        # Try to acquire lock
        acquired = await self._lock_manager.acquire(
            project_id=task.project_id,
            task_id=task_id,
            session_id=session_id,
        )
        
        if not acquired:
            # Lock is held by another task
            return None
        
        try:
            return self._repository.start_task(task_id, session_id)
        except ValueError:
            # Task state changed between check and start
            await self._lock_manager.release(task.project_id, task_id)
            return None
    
    async def complete_task(
        self,
        task_id: str,
        success: bool = True,
        error: Optional[str] = None,
    ) -> Optional[TaskQueueItem]:
        """Mark task as completed or failed and release lock.
        
        Args:
            task_id: The task ID to complete.
            success: True to mark as completed, False to mark as failed.
            error: Error message if success=False.
            
        Returns:
            Updated TaskQueueItem if completed successfully, None if
            task not found or not in a processable state.
        """
        task = self._repository.get(task_id)
        if task is None:
            return None
        
        # Release the lock first
        if task.project_id:
            await self._lock_manager.release(task.project_id, task_id)
        
        # Mark task based on success/failure
        try:
            if success:
                return self._repository.complete_task(task_id, result_summary="Task queued successfully")
            else:
                return self._repository.fail_task(task_id, error_message=error or "Unknown error")
        except ValueError:
            # Task state changed (already completed/cancelled)
            return None
    
    async def trigger_next_task(self, project_id: str) -> Optional[TaskQueueItem]:
        """Trigger the next pending task for a project.
        
        Called after a task completes to process any waiting tasks
        for the same project.
        
        Args:
            project_id: The project to trigger next task for.
            
        Returns:
            The next TaskQueueItem started, or None if no pending tasks.
        """
        next_task = self._get_next_task(project_id)
        if next_task is None:
            return None
        
        return await self.start_task(next_task.task_id)
    
    async def release_lock_by_session(self, session_id: str) -> list[str]:
        """Release any locks held by a session.
        
        This method is called during session termination to clean up
        any project locks that the session's tasks were holding.
        
        Args:
            session_id: The session to release locks for.
            
        Returns:
            List of project_ids that were released.
        """
        return await self._lock_manager.release_by_session(session_id)

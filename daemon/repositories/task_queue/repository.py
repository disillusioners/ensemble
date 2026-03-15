"""SQLModel-based TaskQueue Repository implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete as sql_delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from .models import TaskQueueItem, TaskStatus


class TaskRepository:
    """SQLModel-based Task Queue repository for CRUD operations.
    
    Provides persistence for task queue items with support for
    project-based task serialization.
    """
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        agent_dir: str,
        message: str,
        source: str = "api",
        project_id: Optional[str] = None,
        priority: int = 5,
        task_metadata: Optional[dict[str, Any]] = None,
    ) -> TaskQueueItem:
        """Create a new task queue item.
        
        Args:
            agent_dir: Path to the agent directory.
            message: Task message/content.
            source: Source of the task ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for task serialization.
            priority: Task priority (1-10, default 5).
            task_metadata: Optional metadata dictionary.
            
        Returns:
            Created TaskQueueItem object.
        """
        with SQLModelSession(self.engine) as db_session:
            task = TaskQueueItem(
                agent_dir=agent_dir,
                message=message,
                source=source,
                project_id=project_id,
                priority=priority,
                status=TaskStatus.PENDING.value,
                task_metadata=task_metadata or {},
            )

            db_session.add(task)
            db_session.commit()
            db_session.refresh(task)

            return task

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, task_id: str) -> Optional[TaskQueueItem]:
        """Get a task by ID.
        
        Args:
            task_id: Unique task identifier.
            
        Returns:
            TaskQueueItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            task = db_session.get(TaskQueueItem, task_id)
            return task

    def get_by_session(self, session_id: str) -> Optional[TaskQueueItem]:
        """Get a task by session ID.
        
        Args:
            session_id: Session identifier.
            
        Returns:
            TaskQueueItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(TaskQueueItem).where(TaskQueueItem.session_id == session_id)
            task = db_session.exec(stmt).first()
            return task

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        status: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskQueueItem], int]:
        """List tasks with optional filters and pagination.
        
        Args:
            status: Optional status filter.
            project_id: Optional project ID filter.
            limit: Maximum number of tasks to return.
            offset: Number of tasks to skip.
            
        Returns:
            Tuple of (list of tasks, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Build count query
            count_stmt = select(func.count()).select_from(TaskQueueItem)
            if status:
                count_stmt = count_stmt.where(TaskQueueItem.status == status)
            if project_id:
                count_stmt = count_stmt.where(TaskQueueItem.project_id == project_id)
            total = db_session.exec(count_stmt).one()

            # Build list query with filters
            stmt = select(TaskQueueItem)
            if status:
                stmt = stmt.where(TaskQueueItem.status == status)
            if project_id:
                stmt = stmt.where(TaskQueueItem.project_id == project_id)
            
            stmt = stmt.order_by(
                col(TaskQueueItem.priority).desc(),
                col(TaskQueueItem.created_at).asc()
            ).offset(offset).limit(limit)
            
            tasks = list(db_session.exec(stmt))
            
            return tasks, total

    def list_pending_by_project(self, project_id: str) -> list[TaskQueueItem]:
        """List pending tasks for a specific project, ordered by priority.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of pending TaskQueueItem objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(TaskQueueItem)
                .where(TaskQueueItem.project_id == project_id)
                .where(TaskQueueItem.status == TaskStatus.PENDING.value)
                .order_by(col(TaskQueueItem.priority).desc())
            )
            tasks = list(db_session.exec(stmt))
            return tasks

    def list_all_pending(self) -> list[TaskQueueItem]:
        """List all pending tasks (for tasks without project_id).
        
        Returns:
            List of all pending TaskQueueItem objects.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(TaskQueueItem)
                .where(TaskQueueItem.status == TaskStatus.PENDING.value)
                .order_by(col(TaskQueueItem.priority).desc())
            )
            tasks = list(db_session.exec(stmt))
            return tasks

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, task_id: str, **updates) -> Optional[TaskQueueItem]:
        """Update a task's fields.
        
        Args:
            task_id: Task identifier.
            **updates: Fields to update.
            
        Returns:
            Updated TaskQueueItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            task = db_session.get(TaskQueueItem, task_id)
            if task is None:
                return None

            if 'status' in updates and not TaskStatus.is_valid(updates['status']):
                raise ValueError(f"Invalid status: {updates['status']}")

            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)

            db_session.commit()
            db_session.refresh(task)

            return task

    def start_task(
        self,
        task_id: str,
        session_id: str,
    ) -> Optional[TaskQueueItem]:
        """Mark a task as processing (started).
        
        Can only be called on PENDING tasks.
        
        Args:
            task_id: Task identifier.
            session_id: Session ID that is processing this task.
            
        Returns:
            Updated TaskQueueItem if found, None otherwise.
            
        Raises:
            ValueError: If task is not in PENDING state.
        """
        task = self.get(task_id)
        if task is None:
            return None
        if task.status != TaskStatus.PENDING.value:
            raise ValueError(
                f"Cannot start task in '{task.status}' state, must be PENDING"
            )
        return self.update(
            task_id,
            status=TaskStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            session_id=session_id,
        )

    def complete_task(
        self,
        task_id: str,
        result_summary: Optional[str] = None,
    ) -> Optional[TaskQueueItem]:
        """Mark a task as completed.
        
        Can only be called on PROCESSING tasks.
        
        Args:
            task_id: Task identifier.
            result_summary: Optional summary of the task result.
            
        Returns:
            Updated TaskQueueItem if found, None otherwise.
            
        Raises:
            ValueError: If task is not in PROCESSING state.
        """
        task = self.get(task_id)
        if task is None:
            return None
        if task.status != TaskStatus.PROCESSING.value:
            raise ValueError(
                f"Cannot complete task in '{task.status}' state, must be PROCESSING"
            )
        return self.update(
            task_id,
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.utcnow().isoformat(),
            result_summary=result_summary,
        )

    def fail_task(
        self,
        task_id: str,
        error_message: str,
    ) -> Optional[TaskQueueItem]:
        """Mark a task as failed.
        
        Can only be called on PROCESSING tasks.
        
        Args:
            task_id: Task identifier.
            error_message: Error message describing the failure.
            
        Returns:
            Updated TaskQueueItem if found, None otherwise.
            
        Raises:
            ValueError: If task is not in PROCESSING state.
        """
        task = self.get(task_id)
        if task is None:
            return None
        if task.status != TaskStatus.PROCESSING.value:
            raise ValueError(
                f"Cannot fail task in '{task.status}' state, must be PROCESSING"
            )
        return self.update(
            task_id,
            status=TaskStatus.FAILED.value,
            completed_at=datetime.utcnow().isoformat(),
            error_message=error_message,
        )

    def cancel_task(self, task_id: str) -> Optional[TaskQueueItem]:
        """Mark a task as cancelled.
        
        Can only be called on PENDING tasks.
        
        Args:
            task_id: Task identifier.
            
        Returns:
            Updated TaskQueueItem if found, None otherwise.
            
        Raises:
            ValueError: If task is not in PENDING state.
        """
        task = self.get(task_id)
        if task is None:
            return None
        if task.status != TaskStatus.PENDING.value:
            raise ValueError(
                f"Cannot cancel task in '{task.status}' state, must be PENDING"
            )
        return self.update(
            task_id,
            status=TaskStatus.CANCELLED.value,
            cancelled_at=datetime.utcnow().isoformat(),
        )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, task_id: str) -> dict[str, Any]:
        """Delete a task by ID.
        
        Args:
            task_id: Task identifier.
            
        Returns:
            Dictionary with deletion status.
        """
        with SQLModelSession(self.engine) as db_session:
            task = db_session.get(TaskQueueItem, task_id)
            if task is None:
                return {"deleted": False, "task_id": task_id, "error": "Not found"}

            db_session.delete(task)
            db_session.commit()

            return {
                "deleted": True,
                "task_id": task_id,
                "agent_dir": task.agent_dir,
            }

    def delete_completed(self) -> int:
        """Delete all completed tasks.
        
        Returns:
            Number of tasks deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(TaskQueueItem).where(
                TaskQueueItem.status == TaskStatus.COMPLETED.value
            )
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def delete_by_project(self, project_id: str) -> int:
        """Delete all tasks for a specific project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Number of tasks deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(TaskQueueItem).where(
                TaskQueueItem.project_id == project_id
            )
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

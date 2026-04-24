"""Dead Letter Queue Service - Handles moving failed jobs to DLQ and replaying them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, List

from daemon.repositories.job_queue import DeadLetterItem, JobRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.services.job_state_machine import InvalidTransitionError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as SQLAlchemySession
    from sqlmodel import Session as SQLModelSession

logger = logging.getLogger(__name__)


class DeadLetterServiceError(Exception):
    """Base exception for DeadLetterService errors."""
    pass


class JobNotInFailedStateError(DeadLetterServiceError):
    """Raised when trying to move a non-FAILED job to DLQ."""
    
    def __init__(self, job_id: str, current_status: str) -> None:
        self.job_id = job_id
        self.current_status = current_status
        super().__init__(
            f"Job {job_id} is in '{current_status}' state, must be FAILED to move to DLQ"
        )


class DLQItemNotFoundError(DeadLetterServiceError):
    """Raised when DLQ item is not found."""
    
    def __init__(self, dlq_id: str) -> None:
        self.dlq_id = dlq_id
        super().__init__(f"DLQ item {dlq_id} not found")


class DeadLetterService:
    """Service for managing the dead letter queue.
    
    Handles atomic operations for:
    - Moving failed jobs to the dead letter queue
    - Replaying jobs from the dead letter queue
    - Listing and managing DLQ items
    """
    
    def __init__(
        self,
        job_repository: JobRepository,
        dlq_repository: DeadLetterRepository,
        job_queue_service: Any = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        """Initialize the DeadLetterService.
        
        Args:
            job_repository: Repository for job persistence.
            dlq_repository: Repository for DLQ item persistence.
            job_queue_service: Optional JobQueueService for watcher notifications.
            loop: Optional event loop for async notifications.
        """
        self._job_repo = job_repository
        self._dlq_repo = dlq_repository
        self._job_queue_service = job_queue_service
        self._loop = loop
    
    def move_to_dlq(
        self,
        session: "SQLModelSession",
        job_id: str,
        reason: str = "MAX_RETRIES",
    ) -> DeadLetterItem:
        """Move a failed job to the dead-letter queue atomically.
        
        MUST be called within an existing session/transaction.
        This method does NOT create its own session.
        
        Uses pessimistic locking (FOR UPDATE) to prevent TOCTOU race conditions
        when multiple processes try to move the same job to DLQ.
        
        Args:
            session: An existing SQLModel Session (shared transaction).
            job_id: The job to move.
            reason: "MAX_RETRIES" or "MANUAL".
        
        Returns:
            The created DeadLetterItem.
        
        Raises:
            ValueError: If job not found or not in FAILED state.
        """
        from sqlalchemy.exc import IntegrityError
        from daemon.repositories.job_queue.models import JobItem
        
        # Use FOR UPDATE to acquire pessimistic row lock, preventing TOCTOU race
        job = session.get(JobItem, job_id, with_for_update=True)
        
        if job is None:
            raise DLQItemNotFoundError(job_id)
        
        # Verify job is in FAILED state (now safe under lock)
        if job.status != "failed":
            raise JobNotInFailedStateError(job_id, job.status)
        
        # Create DLQ item from job data
        dlq_item = DeadLetterItem(
            job_id=job.job_id,
            agent_id=job.agent_id,
            agent_dir=job.agent_dir,
            message=job.message,
            source=job.source,
            project_id=job.project_id or "",
            queue_id=job.queue_id or "",
            priority=job.priority,
            error_message=job.error_message or "",
            retry_count=job.retry_count,
            failed_at=job.failed_at or datetime.utcnow().isoformat(),
            reason=reason,
            metadata_json=job.job_metadata,
        )
        
        try:
            # Add DLQ item to session
            session.add(dlq_item)
            
            # Update job status to dead_letter
            job.status = "dead_letter"
            
            # Let the caller commit the session
            return dlq_item
        except IntegrityError:
            # Concurrent process already moved this job to DLQ
            session.rollback()
            raise JobNotInFailedStateError(job_id, job.status)
    
    def move_to_dlq_standalone(
        self,
        job_id: str,
        reason: str = "MAX_RETRIES",
    ) -> DeadLetterItem:
        """Atomically move a FAILED job to the dead letter queue.
        
        This is a standalone version that creates its own session.
        Use move_to_dlq() when you need to participate in a shared transaction.
        
        Both the job status transition AND DLQ item creation happen in a
        single transaction - either both succeed or both fail.
        
        Uses pessimistic locking (FOR UPDATE) to prevent TOCTOU race conditions
        when multiple processes try to move the same job to DLQ.
        
        Args:
            job_id: The job to move to DLQ.
            reason: Reason for moving to DLQ (e.g., "MAX_RETRIES", "MANUAL").
            
        Returns:
            The created DeadLetterItem.
            
        Raises:
            JobNotInFailedStateError: If job is not in FAILED state (including concurrent modification).
        """
        from sqlalchemy.exc import IntegrityError
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem
        
        with SQLModelSession(self._job_repo.engine) as session:
            # Use FOR UPDATE to acquire pessimistic row lock, preventing TOCTOU race
            job = session.get(JobItem, job_id, with_for_update=True)
            if job is None:
                raise DLQItemNotFoundError(job_id)
            
            # Validate job is in FAILED state (now safe under lock)
            if job.status != "failed":
                raise JobNotInFailedStateError(job_id, job.status)
            
            # Create DLQ item from job data
            dlq_item = DeadLetterItem(
                job_id=job.job_id,
                agent_id=job.agent_id,
                agent_dir=job.agent_dir,
                message=job.message,
                source=job.source,
                project_id=job.project_id or "",
                queue_id=job.queue_id or "",
                priority=job.priority,
                error_message=job.error_message or "",
                retry_count=job.retry_count,
                failed_at=job.failed_at or datetime.utcnow().isoformat(),
                reason=reason,
                metadata_json=job.job_metadata,
            )
            
            try:
                # Add DLQ item to session
                session.add(dlq_item)
                
                # Update job status to dead_letter
                job.status = "dead_letter"
                
                # Commit both operations atomically
                session.commit()
                session.refresh(dlq_item)
                
                # Notify watchers after successful commit
                if self._job_queue_service and self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._job_queue_service.notify_watchers(job_id, "dead_letter", job.error_message),
                        self._loop,
                    )
                
                return dlq_item
            except IntegrityError:
                # Concurrent process already moved this job to DLQ
                session.rollback()
                raise JobNotInFailedStateError(job_id, job.status)
    
    def replay_from_dlq(self, dlq_id: str) -> Any:
        """Atomically replay a job from the dead letter queue.
        
        This operation in a SINGLE transaction:
        1. Fetches the DLQ item (with FOR UPDATE lock)
        2. Updates the job to PENDING status (resetting retry_count)
        3. Deletes the DLQ item
        
        Either all operations succeed or none do.
        
        Uses pessimistic locking (FOR UPDATE) on the DLQ item to prevent
        concurrent replays of the same DLQ item.
        
        Args:
            dlq_id: The DLQ item to replay.
            
        Returns:
            The updated JobItem.
            
        Raises:
            DLQItemNotFoundError: If DLQ item not found.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem, DeadLetterItem
        
        with SQLModelSession(self._job_repo.engine) as session:
            # Fetch DLQ item with FOR UPDATE lock to prevent concurrent replay
            dlq_item = session.get(DeadLetterItem, dlq_id, with_for_update=True)
            if dlq_item is None:
                raise DLQItemNotFoundError(dlq_id)
            
            job_id = dlq_item.job_id
            
            # Fetch the job from the same session (also lock to prevent concurrent modifications)
            job = session.get(JobItem, job_id, with_for_update=True)
            if job is None:
                raise DLQItemNotFoundError(dlq_id)
            
            # Verify job is in dead_letter state
            if job.status != "dead_letter":
                from daemon.services.job_state_machine import InvalidTransitionError
                raise InvalidTransitionError(
                    job_id=job_id,
                    from_status=job.status,
                    to_status="pending",
                )
            
            # Update job status to pending (reset retry fields)
            job.status = "pending"
            job.retry_count = 0
            job.failed_at = None
            job.error_message = None
            job.started_at = None
            job.completed_at = None
            job.instance_id = None
            
            # Delete the DLQ item
            session.delete(dlq_item)
            
            # Commit both operations atomically
            session.commit()
            session.refresh(job)
            
            return job
    
    def list_dlq(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
        reason: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[DeadLetterItem], int]:
        """List dead letter queue items with optional filters and pagination.
        
        Args:
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            reason: Optional reason filter.
            limit: Maximum number of items to return.
            offset: Number of items to skip for pagination.
            
        Returns:
            Tuple of (list of matching items, total count BEFORE pagination).
        """
        return self._dlq_repo.list(
            project_id=project_id,
            queue_id=queue_id,
            reason=reason,
            limit=limit,
            offset=offset,
        )
    
    def get_dlq(self, dlq_id: str) -> DeadLetterItem | None:
        """Get a dead letter item by DLQ ID.
        
        Args:
            dlq_id: The DLQ identifier.
            
        Returns:
            DeadLetterItem if found, None otherwise.
        """
        return self._dlq_repo.get(dlq_id)
    
    def get_dlq_by_job_id(self, job_id: str) -> DeadLetterItem | None:
        """Get a dead letter item by original job ID.
        
        Args:
            job_id: The original job identifier.
            
        Returns:
            DeadLetterItem if found, None otherwise.
        """
        return self._dlq_repo.get_by_job_id(job_id)
    
    def delete_dlq(self, dlq_id: str) -> bool:
        """Delete a dead letter item by DLQ ID.
        
        Args:
            dlq_id: The DLQ identifier.
            
        Returns:
            True if deleted, False if not found.
        """
        return self._dlq_repo.delete(dlq_id)
    
    def cleanup_dlq(
        self,
        max_age_days: int,
        reason: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Delete dead letter items older than max_age_days.
        
        Args:
            max_age_days: Maximum age in days for items to keep.
            reason: Optional reason filter to only delete items with specific reason.
            project_id: Optional project ID filter to only delete items for a specific project.
            
        Returns:
            Number of items deleted.
        """
        return self._dlq_repo.cleanup_by_age(
            max_age_days * 24,
            reason=reason,
            project_id=project_id,
        )
    
    def count_dlq(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
    ) -> int:
        """Count dead letter queue items with optional filters.
        
        Args:
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            
        Returns:
            Count of matching items.
        """
        return self._dlq_repo.count(
            project_id=project_id,
            queue_id=queue_id,
        )


# Module-level singleton for dependency injection
_service: DeadLetterService | None = None


def get_dead_letter_service() -> DeadLetterService:
    """Get the module-level DeadLetterService instance.
    
    Returns:
        The singleton DeadLetterService instance.
        
    Raises:
        RuntimeError: If service has not been initialized.
    """
    if _service is None:
        raise RuntimeError("DeadLetterService has not been initialized")
    return _service


def set_dead_letter_service(service: DeadLetterService) -> None:
    """Set the module-level DeadLetterService instance.
    
    Args:
        service: The service instance to use.
    """
    global _service
    _service = service

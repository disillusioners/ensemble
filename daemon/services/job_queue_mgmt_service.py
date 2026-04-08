"""Job Queue Management Service - Queue CRUD operations.

This service wraps JobQueueRepository to provide queue management operations
with IDOR protection and validation.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.models import JobQueue, JobStatus


# Reserved system queue names that cannot be created or deleted
RESERVED_QUEUE_NAMES = {"system_fifo_queue", "system_parallel_queue"}


class JobQueueMgmtService:
    """Service for managing job queues with CRUD operations.
    
    Provides queue lifecycle management including creation, updates,
    deletion with job reassignment, and pause/resume functionality.
    
    Attributes:
        _queue_repo: Repository for queue persistence.
        _job_repo: Repository for job persistence.
    """
    
    def __init__(
        self,
        queue_repo: JobQueueRepository,
        job_repo: JobRepository,
    ):
        """Initialize the JobQueueMgmtService.
        
        Args:
            queue_repo: Repository for queue database operations.
            job_repo: Repository for job database operations.
        """
        self._queue_repo = queue_repo
        self._job_repo = job_repo
    
    # ========== System Queue Provisioning ==========
    
    async def auto_provision_system_queues(self, project_id: str) -> list[JobQueue]:
        """Create system queues for a project if they don't exist.
        
        Creates two system queues:
        - system_fifo_queue: FIFO queue with concurrency_limit=1
        - system_parallel_queue: Parallel queue with concurrency_limit=5
        
        Idempotent: skips creation if queue already exists.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of created or existing system JobQueue objects.
        """
        system_queues = []
        
        # Check and create system FIFO queue
        fifo_queue = await asyncio.to_thread(
            self._queue_repo.get_by_name,
            project_id,
            "system_fifo_queue",
        )
        if fifo_queue is None:
            fifo_queue = await asyncio.to_thread(
                self._queue_repo.create,
                project_id=project_id,
                queue_name="system_fifo_queue",
                queue_type="fifo",
                concurrency_limit=1,
                is_system=True,
            )
        system_queues.append(fifo_queue)
        
        # Check and create system parallel queue
        parallel_queue = await asyncio.to_thread(
            self._queue_repo.get_by_name,
            project_id,
            "system_parallel_queue",
        )
        if parallel_queue is None:
            parallel_queue = await asyncio.to_thread(
                self._queue_repo.create,
                project_id=project_id,
                queue_name="system_parallel_queue",
                queue_type="parallel",
                concurrency_limit=5,
                is_system=True,
            )
        system_queues.append(parallel_queue)
        
        return system_queues
    
    # ========== Queue CRUD ==========
    
    async def create_queue(
        self,
        project_id: str,
        queue_name: str,
        queue_type: str = "fifo",
        concurrency_limit: int = 1,
        description: str | None = None,
    ) -> JobQueue:
        """Create a new queue for a project.
        
        Args:
            project_id: Project identifier.
            queue_name: Unique queue name within the project.
            queue_type: Queue type ("fifo" or "parallel").
            concurrency_limit: Max concurrent jobs (default 1).
            description: Optional queue description.
            
        Returns:
            Created JobQueue object.
            
        Raises:
            ValueError: If queue_name is reserved, FIFO has concurrency > 1,
                        or queue with same name exists in project.
        """
        # Validate reserved names
        if queue_name.lower() in RESERVED_QUEUE_NAMES:
            raise ValueError(f"Cannot use reserved queue name: {queue_name}")
        
        # Validate FIFO concurrency
        if queue_type == "fifo" and concurrency_limit != 1:
            raise ValueError("FIFO queue must have concurrency_limit=1")
        
        # Check uniqueness within project
        existing = await asyncio.to_thread(
            self._queue_repo.get_by_name,
            project_id,
            queue_name,
        )
        if existing is not None:
            raise ValueError(f"Queue '{queue_name}' already exists in project")
        
        # Create queue
        queue = await asyncio.to_thread(
            self._queue_repo.create,
            project_id=project_id,
            queue_name=queue_name,
            queue_type=queue_type,
            concurrency_limit=concurrency_limit,
            is_system=False,
            description=description,
        )
        
        return queue
    
    async def get_queue_with_counts(
        self,
        project_id: str,
        queue_id: str,
    ) -> Optional[dict[str, Any]]:
        """Get a queue by ID with actual job counts.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            
        Returns:
            Dictionary with queue data and job counts, or None if not found.
        """
        queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
        
        # IDOR protection: verify ownership
        if queue is None or queue.project_id != project_id:
            return None
        
        # Get job counts for this queue
        counts = await asyncio.to_thread(
            self._queue_repo.count_jobs_by_status,
            queue.queue_id,
        )
        
        queue_dict = queue.to_dict()
        queue_dict["active_jobs"] = counts.get(JobStatus.PROCESSING.value, 0)
        queue_dict["pending_jobs"] = counts.get(JobStatus.PENDING.value, 0)
        
        return queue_dict
    
    async def get_queue(
        self,
        project_id: str,
        queue_id: str,
    ) -> Optional[JobQueue]:
        """Get a queue by ID with IDOR protection.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            
        Returns:
            JobQueue if found and owned by project, None otherwise.
        """
        queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
        
        # IDOR protection: verify ownership
        if queue is None or queue.project_id != project_id:
            return None
        
        return queue
    
    async def list_queues(self, project_id: str) -> list[dict[str, Any]]:
        """List all queues for a project with job counts.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of queue dictionaries with job statistics.
        """
        queues = await asyncio.to_thread(
            self._queue_repo.list_by_project,
            project_id,
        )
        
        result = []
        for queue in queues:
            # Get job counts for this queue
            counts = await asyncio.to_thread(
                self._queue_repo.count_jobs_by_status,
                queue.queue_id,
            )
            
            queue_dict = queue.to_dict()
            queue_dict["active_jobs"] = counts.get(JobStatus.PROCESSING.value, 0)
            queue_dict["pending_jobs"] = counts.get(JobStatus.PENDING.value, 0)
            result.append(queue_dict)
        
        return result
    
    async def update_queue(
        self,
        project_id: str,
        queue_id: str,
        **updates: Any,
    ) -> Optional[JobQueue]:
        """Update a queue's fields with validation.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            **updates: Fields to update (queue_name, queue_type, concurrency_limit,
                       is_paused, description).
            
        Returns:
            Updated JobQueue if successful, None if not found or not owned.
            
        Raises:
            ValueError: If updating to reserved name, changing FIFO concurrency,
                        or queue name conflicts with existing queue.
        """
        # Get queue with IDOR protection
        queue = await self.get_queue(project_id, queue_id)
        if queue is None:
            return None
        
        # Validate queue_name update
        if "queue_name" in updates:
            new_name = updates["queue_name"].lower()
            if new_name in RESERVED_QUEUE_NAMES:
                raise ValueError(f"Cannot use reserved queue name: {updates['queue_name']}")
            
            # Check uniqueness within project
            existing = await asyncio.to_thread(
                self._queue_repo.get_by_name,
                project_id,
                updates["queue_name"],
            )
            if existing is not None and existing.queue_id != queue_id:
                raise ValueError(f"Queue '{updates['queue_name']}' already exists in project")
        
        # Validate queue_type update
        new_queue_type = updates.get("queue_type", queue.queue_type)
        new_concurrency = updates.get("concurrency_limit", queue.concurrency_limit)
        if new_queue_type == "fifo" and new_concurrency != 1:
            raise ValueError("FIFO queue must have concurrency_limit=1")
        
        # Apply updates
        updated = await asyncio.to_thread(
            self._queue_repo.update,
            queue_id,
            **updates,
        )
        
        return updated
    
    async def delete_queue(
        self,
        project_id: str,
        queue_id: str,
    ) -> dict[str, Any]:
        """Delete a queue with job reassignment.
        
        Reassigns PENDING jobs to system FIFO queue before deletion.
        PROCESSING jobs block deletion.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            
        Returns:
            Dictionary with deletion status and reassigned job count.
            
        Raises:
            ValueError: If queue is a system queue (403),
                        has PROCESSING jobs (409), or queue not found.
        """
        # Get queue with IDOR protection
        queue = await self.get_queue(project_id, queue_id)
        if queue is None:
            raise ValueError("Queue not found")
        
        # Cannot delete system queues
        if queue.is_system:
            raise ValueError("Cannot delete system queue")
        
        # Get system FIFO queue for reassignment
        system_fifo = await asyncio.to_thread(
            self._queue_repo.get_by_name,
            project_id,
            "system_fifo_queue",
        )
        if system_fifo is None:
            raise ValueError("System FIFO queue not found")
        
        # Reassign PENDING jobs first (atomic operation only affects PENDING jobs)
        reassigned_count = await asyncio.to_thread(
            self._queue_repo.reassign_pending_jobs_atomic,
            queue_id,
            system_fifo.queue_id,
            [JobStatus.PENDING.value],
        )
        
        # Check for PROCESSING jobs AFTER reassignment
        # This prevents TOCTOU: jobs transitioning PENDING→PROCESSING during
        # reassignment are caught here and block deletion
        counts = await asyncio.to_thread(
            self._queue_repo.count_jobs_by_status,
            queue_id,
        )
        if counts.get(JobStatus.PROCESSING.value, 0) > 0:
            raise ValueError("Queue has processing jobs")
        
        # Delete the queue
        await asyncio.to_thread(self._queue_repo.delete, queue_id)
        
        return {
            "deleted": True,
            "queue_id": queue_id,
            "reassigned_jobs": reassigned_count,
        }
    
    # ========== Queue State Management ==========
    
    async def start_queue(
        self,
        project_id: str,
        queue_id: str,
    ) -> Optional[JobQueue]:
        """Resume a paused queue.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            
        Returns:
            Updated JobQueue if successful, None if not found or not owned.
        """
        return await self.update_queue(project_id, queue_id, is_paused=False)
    
    async def stop_queue(
        self,
        project_id: str,
        queue_id: str,
    ) -> Optional[JobQueue]:
        """Pause a queue.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            
        Returns:
            Updated JobQueue if successful, None if not found or not owned.
        """
        return await self.update_queue(project_id, queue_id, is_paused=True)

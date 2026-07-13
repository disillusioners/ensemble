"""Job Queue Management Service - Queue CRUD operations.

This service wraps JobQueueRepository to provide queue management operations
with IDOR protection and validation.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.services.dispatch_event_bus import DispatchEventBus

from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobQueue


# Reserved system queue names that cannot be created or deleted
RESERVED_QUEUE_NAMES = {"system_fifo_queue", "system_parallel_queue", "system_kb_fifo_queue", "system_defer_queue", "system_background_queue"}


class JobQueueMgmtService:
    """Service for managing job queues with CRUD operations.
    
    Provides queue lifecycle management including creation, updates,
    deletion with job reassignment, and pause/resume functionality.
    
    Attributes:
        _queue_repo: Repository for queue persistence.
        _job_repo: Repository for job persistence.
        _dispatch_bus: Optional DispatchEventBus for resume notifications.
    """
    
    def __init__(
        self,
        queue_repo: JobQueueRepository,
        job_repo: JobRepository,
        dispatch_bus: "DispatchEventBus" | None = None,
    ):
        """Initialize the JobQueueMgmtService.
        
        Args:
            queue_repo: Repository for queue database operations.
            job_repo: Repository for job database operations.
            dispatch_bus: Optional DispatchEventBus for resume notifications.
        """
        self._queue_repo = queue_repo
        self._job_repo = job_repo
        self._dispatch_bus = dispatch_bus
    
    # ========== System Queue Provisioning ==========
    
    async def auto_provision_system_queues(self, project_id: str) -> list[JobQueue]:
        """Create system queues for a project if they don't exist.
        
        Creates five system queues:
        - system_fifo_queue: FIFO queue with concurrency_limit=1
        - system_parallel_queue: Parallel queue with concurrency_limit=5
        - system_kb_fifo_queue: FIFO queue for KB import jobs with concurrency_limit=1
        - system_defer_queue: Defer queue with concurrency_limit=1
        - system_background_queue: Background queue with concurrency_limit=1
          (only processes when ALL projects are idle)
        
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
        
        # Check and create system KB FIFO queue (for knowledge base import jobs)
        kb_fifo_queue = await asyncio.to_thread(
            self._queue_repo.get_by_name,
            project_id,
            "system_kb_fifo_queue",
        )
        if kb_fifo_queue is None:
            kb_fifo_queue = await asyncio.to_thread(
                self._queue_repo.create,
                project_id=project_id,
                queue_name="system_kb_fifo_queue",
                queue_type="fifo",
                concurrency_limit=1,
                is_system=True,
                description="System FIFO queue for Knowledge Base import jobs",
            )
        system_queues.append(kb_fifo_queue)
        
        # Check and create system defer queue
        defer_queue = await asyncio.to_thread(
            self._queue_repo.get_by_name, project_id, "system_defer_queue",
        )
        if defer_queue is None:
            defer_queue = await asyncio.to_thread(
                self._queue_repo.create,
                project_id=project_id, queue_name="system_defer_queue",
                queue_type="defer", concurrency_limit=1, is_system=True,
                description="System defer queue - only processes when project is idle",
            )
        system_queues.append(defer_queue)
        
        # Check and create system background queue
        background_queue = await asyncio.to_thread(
            self._queue_repo.get_by_name, project_id, "system_background_queue",
        )
        if background_queue is None:
            background_queue = await asyncio.to_thread(
                self._queue_repo.create,
                project_id=project_id, queue_name="system_background_queue",
                queue_type="background", concurrency_limit=1, is_system=True,
                description="System background queue - only processes when ALL projects are idle",
            )
        system_queues.append(background_queue)
        
        return system_queues
    
    async def ensure_system_queues(self, project_id: str) -> dict[str, Any]:
        """Ensure system queues exist for a project, tracking existing vs created.
        
        Reuses auto_provision_system_queues() and tracks which queues already
        existed vs which were newly created.
        
        Idempotent: safe to call multiple times.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Dictionary with lists of existing and created queue names,
            plus total count.
        """
        # 1. Get existing system queue names BEFORE provisioning
        all_queues_before = await asyncio.to_thread(
            self._queue_repo.list_by_project, project_id
        )
        existing_system_queues = [
            q.queue_name for q in all_queues_before
            if q.queue_name in RESERVED_QUEUE_NAMES
        ]
        
        # 2. Call the existing function to create any missing queues
        await self.auto_provision_system_queues(project_id)
        
        # 3. Determine which were created (by comparing before vs after)
        all_queues_after = await asyncio.to_thread(
            self._queue_repo.list_by_project, project_id
        )
        all_system_queues = [
            q.queue_name for q in all_queues_after
            if q.queue_name in RESERVED_QUEUE_NAMES
        ]
        created_queues = [
            name for name in all_system_queues
            if name not in existing_system_queues
        ]
        
        return {
            "existing_queues": existing_system_queues,
            "created_queues": created_queues,
            "total_system_queues": len(all_system_queues),
        }
    
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
        
        # Validate FIFO/defer/background concurrency (mirror
        # ``update_queue``'s rule: these queue types must be serialized
        # so defer-idle and background-idle semantics stay well-defined).
        if queue_type in ("fifo", "defer", "background") and concurrency_limit != 1:
            raise ValueError(f"{queue_type} queue must have concurrency_limit=1")
        
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
    ) -> dict[str, Any | None]:
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
        queue_dict["active_jobs"] = counts.get(AdmissionState.ACTIVE.value, 0)
        queue_dict["pending_jobs"] = counts.get(AdmissionState.QUEUED.value, 0)

        return queue_dict
    
    async def get_queue(
        self,
        project_id: str,
        queue_id: str,
    ) -> JobQueue | None:
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
            queue_dict["active_jobs"] = counts.get(AdmissionState.ACTIVE.value, 0)
            queue_dict["pending_jobs"] = counts.get(AdmissionState.QUEUED.value, 0)
            result.append(queue_dict)
        
        return result
    
    async def update_queue(
        self,
        project_id: str,
        queue_id: str,
        **updates: Any,
    ) -> JobQueue | None:
        """Update a queue's fields with validation.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            **updates: Fields to update (queue_name, queue_type, concurrency_limit,
                       is_paused, description).
            
        Returns:
            Updated JobQueue if successful, None if not found or not owned.
            
        Raises:
            ValueError: If updating to reserved name, changing FIFO/defer/background
                        concurrency, or queue name conflicts with existing queue.
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
        if new_queue_type in ("fifo", "defer", "background") and new_concurrency != 1:
            raise ValueError(f"{new_queue_type} queue must have concurrency_limit=1")
        
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
        
        # Reassign QUEUED jobs first (atomic operation only affects QUEUED jobs)
        reassigned_count = await asyncio.to_thread(
            self._queue_repo.reassign_pending_jobs_atomic,
            queue_id,
            system_fifo.queue_id,
            [AdmissionState.QUEUED.value],
        )
        
        # Check for ACTIVE jobs AFTER reassignment
        # This prevents TOCTOU: jobs transitioning QUEUED→ACTIVE during
        # reassignment are caught here and block deletion
        counts = await asyncio.to_thread(
            self._queue_repo.count_jobs_by_admission,
            queue_id,
        )
        if counts.get(AdmissionState.ACTIVE.value, 0) > 0:
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
    ) -> JobQueue | None:
        """Resume a paused queue.
        
        Notifies the dispatch bus to wake up the job processor after resume.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            
        Returns:
            Updated JobQueue if successful, None if not found or not owned.
        """
        result = await self.update_queue(project_id, queue_id, is_paused=False)
        
        # Notify dispatch bus of queue resume for immediate job processing
        if result is not None and self._dispatch_bus is not None:
            self._dispatch_bus.notify_new_job(project_id)
        
        return result
    
    async def stop_queue(
        self,
        project_id: str,
        queue_id: str,
    ) -> JobQueue | None:
        """Pause a queue.
        
        Args:
            project_id: Project identifier for ownership validation.
            queue_id: Queue identifier.
            
        Returns:
            Updated JobQueue if successful, None if not found or not owned.
        """
        return await self.update_queue(project_id, queue_id, is_paused=True)

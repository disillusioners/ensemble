"""Job Lock Manager - Per-queue job serialization using database as single source of truth.

This module provides the lock management layer that controls per-queue job
serialization with concurrency support. All lock state is persisted in the
database for crash recovery - no in-memory caching needed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from daemon.repositories.job_queue.models import JobLock, JobLockInfo

if TYPE_CHECKING:
    from daemon.repositories.job_queue.lock_repository import LockRepository


class JobLockManager:
    """Manages per-queue locks for job execution using database as single source of truth.
    
    Provides per-queue job serialization with concurrency support. All lock state
    is stored in the database for durability - no in-memory cache needed.
    
    Attributes:
        _lock_repo: Repository for database lock persistence (required).
    """
    
    def __init__(self, lock_repo: "LockRepository") -> None:
        """Initialize the JobLockManager.
        
        Args:
            lock_repo: Required LockRepository for database persistence.
        """
        if lock_repo is None:
            raise ValueError("lock_repo is required - database is single source of truth")
        self._lock_repo = lock_repo
        self._lock = asyncio.Lock()
    
    async def acquire_queue_lock(
        self,
        project_id: str,
        queue_id: str,
        job_id: str,
        instance_id: str,
        concurrency_limit: int,
    ) -> bool:
        """Try to acquire lock for queue with concurrency check.
        
        Atomically checks capacity and acquires lock in database.
        
        Args:
            project_id: The project owning the queue
            queue_id: The queue to lock
            job_id: The job acquiring the lock
            instance_id: The instance running the job
            concurrency_limit: Maximum concurrent jobs allowed for this queue
            
        Returns:
            True if lock acquired, False if at capacity
        """
        async with self._lock:
            # Check current lock count
            current_count = await asyncio.to_thread(
                self._lock_repo.get_lock_count, project_id, queue_id
            )
            
            if current_count >= concurrency_limit:
                return False
            
            # Acquire lock in database
            db_lock = JobLock(
                project_id=project_id,
                queue_id=queue_id,
                job_id=job_id,
                instance_id=instance_id,
            )
            await asyncio.to_thread(self._lock_repo.acquire, db_lock)
            return True
    
    async def release_queue_lock(
        self,
        project_id: str,
        queue_id: str,
        job_id: str,
    ) -> bool:
        """Release lock for queue if held by specified job.
        
        Args:
            project_id: The project owning the queue
            queue_id: The queue to unlock
            job_id: The job that holds the lock
            
        Returns:
            True if released, False if not held by this job
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._lock_repo.release_by_job, project_id, queue_id, job_id
            )
    
    async def is_queue_locked(self, project_id: str, queue_id: str) -> bool:
        """Check if queue has any active locks.
        
        Args:
            project_id: The project owning the queue
            queue_id: The queue to check
            
        Returns:
            True if any locks exist, False otherwise
        """
        async with self._lock:
            count = await asyncio.to_thread(
                self._lock_repo.get_lock_count, project_id, queue_id
            )
            return count > 0
    
    async def get_queue_lock_count(self, project_id: str, queue_id: str) -> int:
        """Get number of active locks for queue.
        
        Args:
            project_id: The project owning the queue
            queue_id: The queue to check
            
        Returns:
            Number of active locks
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._lock_repo.get_lock_count, project_id, queue_id
            )
    
    async def release_by_instance(self, instance_id: str) -> list[tuple[str, str]]:
        """Release any locks held by an instance.
        
        Args:
            instance_id: The instance to release locks for
            
        Returns:
            List of (project_id, queue_id) tuples that were released
        """
        async with self._lock:
            # Get locks before releasing to return what was released
            released_locks = await asyncio.to_thread(
                self._lock_repo.get_locks_by_instance, instance_id
            )
            released_keys = [(lock.project_id, lock.queue_id) for lock in released_locks]
            
            # Release all locks for this instance
            await asyncio.to_thread(
                self._lock_repo.release_by_instance, instance_id
            )
            
            return released_keys
    
    async def get_all_locks(self) -> dict[str, list[JobLockInfo]]:
        """Get all current locks grouped by queue.
        
        Returns:
            Dictionary mapping "project_id:queue_id" to list of JobLockInfo
        """
        async with self._lock:
            all_locks = await asyncio.to_thread(self._lock_repo.get_all_locks)
            
            result: dict[str, list[JobLockInfo]] = {}
            for lock in all_locks:
                key = f"{lock.project_id}:{lock.queue_id}"
                if key not in result:
                    result[key] = []
                result[key].append(JobLockInfo(
                    job_id=lock.job_id,
                    project_id=lock.project_id,
                    queue_id=lock.queue_id,
                    instance_id=lock.instance_id,
                    locked_at=datetime.fromisoformat(lock.acquired_at) if isinstance(lock.acquired_at, str) else lock.acquired_at,
                ))
            return result
    
    def clear_all_locks(self) -> None:
        """Clear all locks from database.
        
        Warning: This should only be used for testing or cleanup.
        """
        raise NotImplementedError("Use release_by_instance() for specific cleanup by instance")
    
    # ========== Backward Compatibility Methods ==========
    # These methods provide backward compatibility with project-based locking
    # for jobs that don't have a queue_id assigned.
    
    def _get_default_queue_id(self, project_id: str) -> str:
        """Get the default queue ID for a project.
        
        Used by backward compatibility methods to handle jobs without explicit queue_id.
        
        Args:
            project_id: The project ID.
            
        Returns:
            Default queue ID in format "project:{project_id}"
        """
        return f"project:{project_id}"
    
    async def acquire(
        self,
        project_id: str,
        job_id: str,
        instance_id: str,
    ) -> bool:
        """Acquire lock for project (backward compatibility).
        
        This method is for backward compatibility with project-based locking.
        Uses a default queue derived from the project_id.
        
        Args:
            project_id: The project to lock.
            job_id: The job acquiring the lock.
            instance_id: The instance running the job.
            
        Returns:
            True if lock acquired, False if at capacity (default: 1)
        """
        queue_id = self._get_default_queue_id(project_id)
        return await self.acquire_queue_lock(
            project_id=project_id,
            queue_id=queue_id,
            job_id=job_id,
            instance_id=instance_id,
            concurrency_limit=1,  # Default concurrency for project-based locks
        )
    
    async def release(self, project_id: str, job_id: str) -> bool:
        """Release lock for project (backward compatibility).
        
        This method is for backward compatibility with project-based locking.
        
        Args:
            project_id: The project owning the lock.
            job_id: The job that holds the lock.
            
        Returns:
            True if released, False if not held by this job
        """
        queue_id = self._get_default_queue_id(project_id)
        return await self.release_queue_lock(project_id, queue_id, job_id)
    
    async def is_locked(self, project_id: str) -> bool:
        """Check if project has any active locks (backward compatibility).
        
        Args:
            project_id: The project to check.
            
        Returns:
            True if any locks exist for the project, False otherwise
        """
        queue_id = self._get_default_queue_id(project_id)
        return await self.is_queue_locked(project_id, queue_id)
    
    async def get_lock_info(self, project_id: str) -> JobLockInfo | None:
        """Get lock info for project (backward compatibility).
        
        Args:
            project_id: The project to get lock info for.
            
        Returns:
            JobLockInfo if a lock exists, None otherwise
        """
        queue_id = self._get_default_queue_id(project_id)
        
        async with self._lock:
            locks = await asyncio.to_thread(
                self._lock_repo.get_active_locks, project_id, queue_id
            )
            if locks:
                lock = locks[0]
                return JobLockInfo(
                    job_id=lock.job_id,
                    project_id=lock.project_id,
                    queue_id=lock.queue_id,
                    instance_id=lock.instance_id,
                    locked_at=datetime.fromisoformat(lock.acquired_at) if isinstance(lock.acquired_at, str) else lock.acquired_at,
                )
            return None
    
    async def get_waiter_count(self, queue_id: str) -> int:
        """Get count of waiters for a queue.
        
        Args:
            queue_id: The queue to check. If starts with "project:", uses project-based lock.
            
        Returns:
            Number of active locks
        """
        # Extract project_id if queue_id is in project: format
        if queue_id.startswith("project:"):
            project_id = queue_id[len("project:"):]
            return await self.get_queue_lock_count(project_id, queue_id)
        
        # For other queue_ids, we'd need the project_id
        # This is a limitation - return 0 as fallback
        return 0


# Backward compatibility alias
TaskLockManager = JobLockManager

"""Job Lock Manager - Per-queue job serialization using in-memory + database locks.

This module provides the lock management layer that controls per-queue job
serialization with concurrency support using in-memory locks with optional
database persistence for crash recovery.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Optional

from daemon.repositories.job_queue.models import JobLockInfo

if TYPE_CHECKING:
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.models import JobLock


class LockInfo:
    """Information about a held lock.
    
    Internal representation used by JobLockManager.
    """
    job_id: str
    project_id: str
    queue_id: str
    instance_id: str
    locked_at: datetime
    
    def __init__(
        self,
        job_id: str,
        project_id: str,
        queue_id: str,
        instance_id: str,
        locked_at: datetime | None = None
    ) -> None:
        self.job_id = job_id
        self.project_id = project_id
        self.queue_id = queue_id
        self.instance_id = instance_id
        self.locked_at = locked_at or datetime.now(UTC)
    
    def to_lock_info(self) -> JobLockInfo:
        """Convert to JobLockInfo model for external use."""
        return JobLockInfo(
            job_id=self.job_id,
            project_id=self.project_id,
            queue_id=self.queue_id,
            instance_id=self.instance_id,
            locked_at=self.locked_at,
        )


class JobLockManager:
    """Manages per-queue locks for job execution.
    
    Provides in-memory lock management for per-queue job serialization with
    concurrency support. Multiple jobs can run concurrently on a queue up to
    the configured concurrency_limit.
    
    Attributes:
        _queue_locks: Dictionary mapping (project_id, queue_id) to list of LockInfo
        _lock: asyncio.Lock for thread-safe operations on internal state
    """
    
    def __init__(self, lock_repo: Optional["LockRepository"] = None) -> None:
        """Initialize the JobLockManager.
        
        Args:
            lock_repo: Optional LockRepository for database persistence.
        """
        self._queue_locks: dict[tuple[str, str], list[LockInfo]] = {}
        self._lock = asyncio.Lock()
        self._lock_repo = lock_repo
    
    async def acquire_queue_lock(
        self,
        project_id: str,
        queue_id: str,
        job_id: str,
        instance_id: str,
        concurrency_limit: int,
    ) -> bool:
        """Try to acquire lock for queue with concurrency check.
        
        Capacity check and lock acquisition happen atomically under asyncio.Lock.
        
        Args:
            project_id: The project owning the queue
            queue_id: The queue to lock
            job_id: The job acquiring the lock
            instance_id: The instance running the job
            concurrency_limit: Maximum concurrent jobs allowed for this queue
            
        Returns:
            True if lock acquired, False if at capacity
        """
        key = (project_id, queue_id)
        
        async with self._lock:
            current_locks = self._queue_locks.get(key, [])
            
            if len(current_locks) >= concurrency_limit:
                return False
            
            lock_info = LockInfo(
                job_id=job_id,
                project_id=project_id,
                queue_id=queue_id,
                instance_id=instance_id,
                locked_at=datetime.now(UTC)
            )
            
            if key not in self._queue_locks:
                self._queue_locks[key] = []
            
            self._queue_locks[key].append(lock_info)
            
            # Persist to database if repository is available
            if self._lock_repo:
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
        key = (project_id, queue_id)
        
        async with self._lock:
            if key not in self._queue_locks:
                return False
            
            current_locks = self._queue_locks[key]
            
            for i, lock_info in enumerate(current_locks):
                if lock_info.job_id == job_id:
                    current_locks.pop(i)
                    
                    # Clean up empty queue lock entries
                    if not current_locks:
                        del self._queue_locks[key]
                    
                    # Release from database if repository is available
                    if self._lock_repo:
                        await asyncio.to_thread(
                            self._lock_repo.release_by_job, project_id, queue_id, job_id
                        )
                    
                    return True
            
            return False
    
    async def is_queue_locked(self, project_id: str, queue_id: str) -> bool:
        """Check if queue has any active locks.
        
        Args:
            project_id: The project owning the queue
            queue_id: The queue to check
            
        Returns:
            True if any locks exist, False otherwise
        """
        key = (project_id, queue_id)
        
        async with self._lock:
            return key in self._queue_locks and len(self._queue_locks[key]) > 0
    
    async def get_queue_lock_count(self, project_id: str, queue_id: str) -> int:
        """Get number of active locks for queue.
        
        Args:
            project_id: The project owning the queue
            queue_id: The queue to check
            
        Returns:
            Number of active locks
        """
        key = (project_id, queue_id)
        
        # Prefer database count for accuracy if available
        if self._lock_repo:
            return await asyncio.to_thread(
                self._lock_repo.get_lock_count, project_id, queue_id
            )
        
        # Fallback to in-memory count
        async with self._lock:
            if key not in self._queue_locks:
                return 0
            return len(self._queue_locks[key])
    
    async def release_by_instance(self, instance_id: str) -> list[tuple[str, str]]:
        """Release any locks held by an instance.
        
        Args:
            instance_id: The instance to release locks for
            
        Returns:
            List of (project_id, queue_id) tuples that were released
        """
        released: list[tuple[str, str]] = []
        
        async with self._lock:
            keys_to_check = list(self._queue_locks.keys())
            
            for key in keys_to_check:
                project_id, queue_id = key
                current_locks = self._queue_locks[key]
                
                # Filter out locks for this instance
                remaining = [
                    lock for lock in current_locks
                    if lock.instance_id != instance_id
                ]
                
                if len(remaining) != len(current_locks):
                    if remaining:
                        self._queue_locks[key] = remaining
                    else:
                        del self._queue_locks[key]
                    released.append(key)
        
        # Release all locks from database if repository is available
        if self._lock_repo and released:
            await asyncio.to_thread(self._lock_repo.release_by_instance, instance_id)
        
        return released
    
    async def get_all_locks(self) -> dict[str, list[JobLockInfo]]:
        """Get all current locks grouped by queue.
        
        Returns:
            Dictionary mapping "project_id:queue_id" to list of JobLockInfo
        """
        async with self._lock:
            result: dict[str, list[JobLockInfo]] = {}
            
            for (project_id, queue_id), locks in self._queue_locks.items():
                key = f"{project_id}:{queue_id}"
                result[key] = [
                    lock.to_lock_info() for lock in locks
                ]
            
            return result
    
    def clear(self) -> None:
        """Clear all locks.
        
        Warning: This should only be used for testing or cleanup.
        """
        self._queue_locks.clear()
    
    async def reconcile_locks(
        self,
        get_instance_status: Optional[callable] = None
    ) -> int:
        """Reconcile in-memory locks with database state.
        
        Called on startup to clean up orphaned locks and rebuild
        in-memory state from persistent storage.
        
        Args:
            get_instance_status: Optional callable(instance_id) -> Optional[str]
                Returns instance status or None if instance doesn't exist.
                If not provided, all DB locks are loaded to memory only.
        
        Returns:
            Number of orphaned locks cleaned up.
        """
        if not self._lock_repo:
            return 0
        
        # Load all DB locks
        db_locks = await asyncio.to_thread(self._lock_repo.get_all_locks)
        
        reconciled = 0
        for db_lock in db_locks:
            # If we have a status checker, verify the instance is still active
            if get_instance_status:
                status = await get_instance_status(db_lock.instance_id)
                if status is None or status in ("completed", "terminated", "error", "cancelled"):
                    # Instance is gone or completed — release orphaned lock
                    await asyncio.to_thread(self._lock_repo.release, db_lock.lock_id)
                    reconciled += 1
                    continue
            
            # Rebuild in-memory state from DB locks
            key = (db_lock.project_id, db_lock.queue_id)
            if key not in self._queue_locks:
                self._queue_locks[key] = []
            
            # Check if already in memory
            existing_job_ids = {l.job_id for l in self._queue_locks[key]}
            if db_lock.job_id not in existing_job_ids:
                # Parse acquired_at - may be string or datetime
                acquired_at = db_lock.acquired_at
                if isinstance(acquired_at, str):
                    acquired_at = datetime.fromisoformat(acquired_at)
                
                self._queue_locks[key].append(LockInfo(
                    job_id=db_lock.job_id,
                    project_id=db_lock.project_id,
                    queue_id=db_lock.queue_id,
                    instance_id=db_lock.instance_id or "",
                    locked_at=acquired_at,
                ))
        
        return reconciled
    
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
    
    def _acquire_sync_internal(
        self,
        project_id: str,
        job_id: str,
        instance_id: str,
    ) -> bool:
        """Internal synchronous lock acquisition without async context.
        
        This modifies internal state directly without async operations.
        Used by acquire_sync() when asyncio.run() is not available.
        
        Args:
            project_id: The project to lock.
            job_id: The job acquiring the lock.
            instance_id: The instance running the job.
            
        Returns:
            True if lock acquired, False if at capacity
        """
        queue_id = self._get_default_queue_id(project_id)
        key = (project_id, queue_id)
        
        current_locks = self._queue_locks.get(key, [])
        
        # Default concurrency of 1 for project-based locks
        if len(current_locks) >= 1:
            return False
        
        lock_info = LockInfo(
            job_id=job_id,
            project_id=project_id,
            queue_id=queue_id,
            instance_id=instance_id,
            locked_at=datetime.now(UTC)
        )
        
        if key not in self._queue_locks:
            self._queue_locks[key] = []
        
        self._queue_locks[key].append(lock_info)
        return True
    
    def _release_sync_internal(self, project_id: str, job_id: str) -> bool:
        """Internal synchronous lock release without async context.
        
        This modifies internal state directly without async operations.
        Used by release_sync() when asyncio.run() is not available.
        
        Args:
            project_id: The project owning the lock.
            job_id: The job that holds the lock.
            
        Returns:
            True if released, False if not held by this job
        """
        queue_id = self._get_default_queue_id(project_id)
        key = (project_id, queue_id)
        
        if key not in self._queue_locks:
            return False
        
        current_locks = self._queue_locks[key]
        
        for i, lock_info in enumerate(current_locks):
            if lock_info.job_id == job_id:
                current_locks.pop(i)
                
                if not current_locks:
                    del self._queue_locks[key]
                
                return True
        
        return False
    
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
    
    def acquire_sync(
        self,
        project_id: str,
        job_id: str,
        instance_id: str,
    ) -> bool:
        """Acquire lock for project synchronously (backward compatibility).
        
        This method modifies internal state directly to work from any context.
        Note: Not fully thread-safe. Use async acquire() when possible.
        
        Args:
            project_id: The project to lock.
            job_id: The job acquiring the lock.
            instance_id: The instance running the job.
            
        Returns:
            True if lock acquired, False if at capacity
        """
        return self._acquire_sync_internal(project_id, job_id, instance_id)
    
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
    
    def release_sync(self, project_id: str, job_id: str) -> bool:
        """Release lock for project synchronously (backward compatibility).
        
        This method modifies internal state directly to work from any context.
        Note: Not fully thread-safe. Use async release() when possible.
        
        Args:
            project_id: The project owning the lock.
            job_id: The job that holds the lock.
            
        Returns:
            True if released, False if not held by this job
        """
        return self._release_sync_internal(project_id, job_id)
    
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
        key = (project_id, queue_id)
        
        async with self._lock:
            locks = self._queue_locks.get(key, [])
            if locks:
                return locks[0].to_lock_info()
            return None
    
    async def get_waiter_count(self, queue_id: str) -> int:
        """Get count of waiters for a queue.
        
        This is an approximation - counts active locks which represents
        slots in use, not actual waiters.
        
        Args:
            queue_id: The queue to check. If starts with "project:", uses project-based lock.
            
        Returns:
            Number of waiters (approximated by active locks)
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

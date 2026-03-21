"""Job Lock Manager - Per-project job serialization using in-memory locks.

This module provides the lock management layer that controls per-project job
serialization using in-memory locks with waiter notification.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Optional

from daemon.repositories.job_queue.models import JobLockInfo


class LockInfo:
    """Information about a held lock.
    
    Internal representation used by JobLockManager.
    """
    job_id: str
    project_id: str
    session_id: str
    locked_at: datetime
    
    def __init__(
        self,
        job_id: str,
        project_id: str,
        session_id: str,
        locked_at: datetime | None = None
    ) -> None:
        self.job_id = job_id
        self.project_id = project_id
        self.session_id = session_id
        self.locked_at = locked_at or datetime.utcnow()
    
    def to_lock_info(self) -> JobLockInfo:
        """Convert to JobLockInfo model for external use."""
        return JobLockInfo(
            job_id=self.job_id,
            project_id=self.project_id,
            session_id=self.session_id,
            locked_at=self.locked_at
        )


class JobLockManager:
    """Manages per-project locks for job execution.
    
    Provides in-memory lock management with waiter notification for
    per-project job serialization. This ensures only one job runs
    per project at a time.
    
    Attributes:
        _locks: Dictionary mapping project_id to LockInfo
        _waiters: Dictionary mapping project_id to list of (job_id, event) tuples
        _lock: asyncio.Lock for thread-safe operations on internal state
        _max_waiters: Maximum number of waiters per project (0 = unlimited)
    """
    
    def __init__(self, max_waiters: int = 0) -> None:
        """Initialize the JobLockManager.
        
        Args:
            max_waiters: Maximum number of waiters per project. 
                        0 means unlimited (default).
        """
        self._locks: dict[str, LockInfo] = {}
        # Using list instead of Queue for O(n) removal on timeout
        self._waiters: dict[str, list[tuple[str, asyncio.Event]]] = {}
        self._lock = asyncio.Lock()
        self._max_waiters = max_waiters
    
    async def acquire(
        self,
        project_id: str,
        job_id: str,
        session_id: str
    ) -> bool:
        """Try to acquire lock for project.
        
        Args:
            project_id: The project to lock
            job_id: The job acquiring the lock
            session_id: The session running the job
            
        Returns:
            True if lock acquired, False if already held
        """
        async with self._lock:
            if project_id in self._locks:
                return False
            
            self._locks[project_id] = LockInfo(
                job_id=job_id,
                project_id=project_id,
                session_id=session_id,
                locked_at=datetime.now(UTC)
            )
            return True
    
    def acquire_sync(
        self,
        project_id: str,
        job_id: str,
        session_id: str
    ) -> bool:
        """Synchronous version of acquire for non-async contexts.
        
        Note: Not thread-safe for concurrent access. Use async acquire()
        in async contexts.
        """
        if project_id in self._locks:
            return False
        
        self._locks[project_id] = LockInfo(
            job_id=job_id,
            project_id=project_id,
            session_id=session_id,
            locked_at=datetime.now(UTC)
        )
        return True
    
    async def release(self, project_id: str, job_id: str) -> bool:
        """Release lock if held by specified job.
        
        Args:
            project_id: The project to unlock
            job_id: The job that holds the lock
            
        Returns:
            True if released, False if not held by this job
        """
        async with self._lock:
            if project_id not in self._locks:
                return False
            
            if self._locks[project_id].job_id != job_id:
                return False
            
            del self._locks[project_id]
        
        # Notify next waiter (outside the lock to avoid deadlock)
        await self._notify_waiter(project_id)
        return True
    
    def release_sync(self, project_id: str, job_id: str) -> bool:
        """Synchronous version of release for non-async contexts.
        
        Note: Not thread-safe. Use async release() in async contexts.
        """
        if project_id not in self._locks:
            return False
        
        if self._locks[project_id].job_id != job_id:
            return False
        
        del self._locks[project_id]
        
        # Note: Can't notify waiters synchronously
        # This should be called in async context
        return True
    
    async def release_by_session(self, session_id: str) -> list[str]:
        """Release any locks held by a session.
        
        Args:
            session_id: The session to release locks for
            
        Returns:
            List of project_ids that were released
        """
        async with self._lock:
            released = []
            for project_id, info in list(self._locks.items()):
                if info.session_id == session_id:
                    del self._locks[project_id]
                    released.append(project_id)
        
        # Notify waiters outside the lock
        for project_id in released:
            await self._notify_waiter(project_id)
        
        return released
    
    def release_by_session_sync(self, session_id: str) -> list[str]:
        """Synchronous version of release_by_session.
        
        Note: Not thread-safe. Use async release_by_session() in async contexts.
        """
        released = []
        for project_id, info in list(self._locks.items()):
            if info.session_id == session_id:
                del self._locks[project_id]
                released.append(project_id)
        
        return released
    
    async def is_locked(self, project_id: str) -> bool:
        """Check if project is currently locked.
        
        Args:
            project_id: The project to check
            
        Returns:
            True if locked, False otherwise
        """
        async with self._lock:
            return project_id in self._locks
    
    async def get_lock_info(self, project_id: str) -> Optional[JobLockInfo]:
        """Get lock info for project.
        
        Args:
            project_id: The project to get info for
            
        Returns:
            JobLockInfo if locked, None otherwise
        """
        async with self._lock:
            lock_info = self._locks.get(project_id)
            return lock_info.to_lock_info() if lock_info else None
    
    async def get_all_locks(self) -> dict[str, JobLockInfo]:
        """Get all current locks.
        
        Returns:
            Dictionary mapping project_id to JobLockInfo
        """
        async with self._lock:
            return {
                project_id: info.to_lock_info()
                for project_id, info in self._locks.items()
            }
    
    async def wait_for_lock(
        self,
        project_id: str,
        job_id: str,
        session_id: str,
        timeout: Optional[float] = None
    ) -> bool:
        """Wait for lock to become available and acquire it.
        
        This method atomically checks if the lock is free and if not,
        adds itself to the waiter queue to avoid race conditions.
        
        Args:
            project_id: The project to lock
            job_id: The job that will acquire the lock
            session_id: The session running the job
            timeout: Maximum time to wait in seconds. None means wait forever.
            
        Returns:
            True if lock acquired (either immediately or after waiting),
            False if timeout occurred or max waiters reached
        """
        event = asyncio.Event()
        
        # Atomic: check if lock is free, if not add to waiter queue
        async with self._lock:
            if project_id not in self._locks:
                # Lock is free, acquire it immediately
                self._locks[project_id] = LockInfo(
                    job_id=job_id,
                    project_id=project_id,
                    session_id=session_id,
                    locked_at=datetime.now(UTC)
                )
                return True
            
            # Lock is held, need to wait
            # Check max waiters limit
            if self._max_waiters > 0:
                current_waiters = len(self._waiters.get(project_id, []))
                if current_waiters >= self._max_waiters:
                    return False
            
            # Ensure waiter list exists and add ourselves
            if project_id not in self._waiters:
                self._waiters[project_id] = []
            self._waiters[project_id].append((job_id, event))
        
        # Wait for notification (outside the lock)
        try:
            if timeout is not None:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            else:
                await event.wait()
        except asyncio.TimeoutError:
            # Remove ourselves from the waiter list
            async with self._lock:
                if project_id in self._waiters:
                    self._waiters[project_id] = [
                        (jid, evt) for jid, evt in self._waiters[project_id]
                        if jid != job_id
                    ]
                    # Clean up empty waiter lists
                    if not self._waiters[project_id]:
                        del self._waiters[project_id]
            return False
        
        # We were notified - try to acquire the lock
        # Note: There's a small race here where another job could acquire
        # before us. This is acceptable behavior - we'll return False.
        async with self._lock:
            if project_id not in self._locks:
                self._locks[project_id] = LockInfo(
                    job_id=job_id,
                    project_id=project_id,
                    session_id=session_id,
                    locked_at=datetime.now(UTC)
                )
                return True
            
            # Lock was grabbed by someone else
            return False
    
    async def _notify_waiter(self, project_id: str) -> None:
        """Notify next waiting job that lock is available.
        
        Args:
            project_id: The project whose lock was released
        """
        async with self._lock:
            if project_id not in self._waiters:
                return
            
            if not self._waiters[project_id]:
                # Empty waiter list, clean up
                del self._waiters[project_id]
                return
            
            # Pop the first waiter (FIFO order)
            _, event = self._waiters[project_id].pop(0)
            
            # Clean up empty waiter lists
            if not self._waiters[project_id]:
                del self._waiters[project_id]
        
        # Set event outside the lock
        event.set()
    
    async def get_waiter_count(self, project_id: str) -> int:
        """Get the number of waiters for a project.
        
        Args:
            project_id: The project to check
            
        Returns:
            Number of waiting jobs
        """
        async with self._lock:
            return len(self._waiters.get(project_id, []))
    
    @asynccontextmanager
    async def lock_context(
        self,
        project_id: str,
        job_id: str,
        session_id: str,
        timeout: Optional[float] = None
    ):
        """Context manager for automatic lock acquisition and release.
        
        Args:
            project_id: The project to lock
            job_id: The job acquiring the lock
            session_id: The session running the job
            timeout: Maximum time to wait for lock
            
        Yields:
            True if lock acquired, False if not (timeout or failed)
            
        Example:
            async with manager.lock_context(project_id, job_id, session_id) as acquired:
                if acquired:
                    # Do work
                    pass
        """
        acquired = await self.wait_for_lock(project_id, job_id, session_id, timeout)
        try:
            yield acquired
        finally:
            if acquired:
                await self.release(project_id, job_id)
    
    def clear(self) -> None:
        """Clear all locks and waiters.
        
        Warning: This should only be used for testing or cleanup.
        """
        self._locks.clear()
        self._waiters.clear()


# Backward compatibility alias
TaskLockManager = JobLockManager

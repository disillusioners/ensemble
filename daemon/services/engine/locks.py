"""Single-flight job locks (Engine Arc P2 — T2.2).

``JobLockTable`` keeps one ``asyncio.Lock`` per ``job_id``. ``try_acquire``
returns ``False`` immediately when held (no await-queueing) so an overlap
is counted, never queued (D2.3). Release is idempotent; every holder
releases in ``finally`` so a leak is impossible by construction.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional


class JobLockTable:
    """Per-job single-flight locks."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        # D2.7: an in-flight dispatch that has hit the extend-timeout fires
        # marks itself here so subsequent ticks stamp the SPECIFIC
        # TIMEOUT_PENDING_EXTEND member instead of the generic OVERLAP.
        self._extended: dict[str, bool] = {}

    def _lock_for(self, job_id: str) -> asyncio.Lock:
        lock = self._locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[job_id] = lock
        return lock

    async def try_acquire(self, job_id: str) -> bool:
        """Try to acquire the lock for ``job_id``. Returns False if held."""
        lock = self._lock_for(job_id)
        if lock.locked():
            return False
        await lock.acquire()  # uncontended fast path — no queueing
        return True

    def release(self, job_id: str) -> None:
        """Release the lock for ``job_id`` (idempotent).

        Clears the extend flag on every release so the next dispatch starts
        from a clean state (a leaked TPE flag would mislabel future overlaps).
        """
        lock = self._locks.get(job_id)
        if lock is not None and lock.locked():
            lock.release()
        self._extended.pop(job_id, None)

    def mark_extended(self, job_id: str, extended: bool) -> None:
        """Mark the held dispatch for ``job_id`` as extend-timeout pending."""
        self._extended[job_id] = bool(extended)

    def is_extended(self, job_id: str) -> bool:
        """Whether the in-flight dispatch for ``job_id`` is in extended state."""
        return bool(self._extended.get(job_id, False))

    def locked(self, job_id: str) -> bool:
        lock = self._locks.get(job_id)
        return bool(lock is not None and lock.locked())

    @asynccontextmanager
    async def hold(self, job_id: str) -> AsyncIterator[bool]:
        """Context manager: yields True if acquired, False if overlap.

        Releases in ``finally`` so a leak is impossible by construction.
        """
        acquired = await self.try_acquire(job_id)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(job_id)

    def clear(self) -> None:
        self._locks.clear()
        self._extended.clear()


__all__ = ["JobLockTable"]

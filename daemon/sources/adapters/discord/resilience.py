"""Discord outbound resilience primitives.

This module provides `DiscordSendSemaphore`, a thin concurrency cap for
outbound REST sends. discord.py handles 429 Too Many Requests and dynamic
per-route buckets internally (it inspects ``X-RateLimit-*`` headers and the
``Retry-After`` body field and retries transparently). As a result, the
adapter does NOT implement any tiered bucket logic — the only resilience
this module adds is a local cap to prevent the adapter from flooding
discord.py's HTTP handler with more in-flight send calls than it can
absorb.

The class is named ``DiscordSendSemaphore`` (not ``DiscordRateLimiter``)
to make the semantics explicit at every call site. See the leader
decisions in ``.agents/shared/planning/discord-source/plan-overview.md``
for the rationale.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class DiscordSendSemaphore:
    """Async context-manager that caps concurrent Discord REST sends.

    Wraps :class:`asyncio.Semaphore` with lightweight metrics:

    * ``active_sends`` — number of sends currently holding the semaphore
    * ``total_sends`` — total number of times the semaphore was acquired
    * ``rate_limit_waits`` — number of acquisitions that had to wait for a
      free slot. ``asyncio.Semaphore`` blocks instead of rejecting, so a
      "wait" is the right semantic here (no caller is ever dropped).

    The semaphore is intentionally NOT coupled to the circuit breaker or
    the per-channel ordering locks — those are owned by the adapter and
    applied independently on the send path.

    Args:
        max_concurrent_sends: Maximum number of concurrent in-flight
            Discord sends. Defaults to 5, which is comfortably below
            discord.py's internal pool sizing and matches the leader
            decision in ``plan-overview.md``.
    """

    def __init__(self, max_concurrent_sends: int = 5) -> None:
        if max_concurrent_sends < 1:
            raise ValueError(
                f"max_concurrent_sends must be >= 1, got {max_concurrent_sends}"
            )
        self._semaphore = asyncio.Semaphore(max_concurrent_sends)
        self._max_concurrent = max_concurrent_sends
        self._active_sends = 0
        self._total_sends = 0
        self._rate_limit_waits = 0

    @property
    def active_sends(self) -> int:
        """Current in-flight send count."""
        return self._active_sends

    @property
    def total_sends(self) -> int:
        """Lifetime acquired count."""
        return self._total_sends

    @property
    def rate_limit_waits(self) -> int:
        """Lifetime count of acquires that blocked waiting for a slot."""
        return self._rate_limit_waits

    @property
    def max_concurrent(self) -> int:
        """Configured concurrency cap."""
        return self._max_concurrent

    async def acquire(self) -> None:
        """Acquire a send slot, blocking if at capacity.

        Records a "wait" only if the semaphore had to be awaited (i.e.
        the caller was the N+1th concurrent send). Plain immediate
        acquires do not increment ``rate_limit_waits``.
        """
        # If a slot is free we want a fast path so metrics reflect the
        # 'waited vs. immediate' distinction. ``locked()`` is non-blocking
        # and only requires the lock be held while we check it; in
        # practice ``asyncio.Semaphore.locked()`` does not actually
        # contend so this is safe to call concurrently.
        if self._semaphore.locked():
            self._rate_limit_waits += 1
        await self._semaphore.acquire()
        self._active_sends += 1
        self._total_sends += 1

    async def release(self) -> None:
        """Release a previously acquired send slot."""
        if self._active_sends <= 0:
            logger.warning(
                "DiscordSendSemaphore.release() called with no active sends"
            )
            return
        self._active_sends -= 1
        self._semaphore.release()

    async def __aenter__(self) -> "DiscordSendSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.release()

    def get_stats(self) -> dict:
        """Return a snapshot of the semaphore's metrics for observability."""
        return {
            "active_sends": self._active_sends,
            "total_sends": self._total_sends,
            "rate_limit_waits": self._rate_limit_waits,
            "max_concurrent": self._max_concurrent,
        }

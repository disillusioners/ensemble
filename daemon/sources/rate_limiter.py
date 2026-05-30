"""Token bucket rate limiter for message source throttling."""

import asyncio
import time
from dataclasses import dataclass



@dataclass
class RateLimit:
    """Rate limit configuration."""
    messages_per_second: float
    burst_size: int


# Platform-specific defaults
DEFAULT_RATE_LIMITS = {
    "telegram": RateLimit(messages_per_second=30, burst_size=30),
    "webhook": RateLimit(messages_per_second=100, burst_size=100),
    "whatsapp": RateLimit(messages_per_second=10, burst_size=20),
    "slack": RateLimit(messages_per_second=50, burst_size=50),
}


class TokenBucketLimiter:
    """Token bucket rate limiter for per-source throttling.
    
    Implements token bucket algorithm to control message throughput
    and prevent overwhelming external APIs.
    """
    
    def __init__(self, rate: RateLimit):
        self._rate = rate
        self._tokens = float(rate.burst_size)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> bool:
        """Try to acquire a token. Returns True if allowed."""
        async with self._lock:
            return self._try_acquire_unlocked()
    
    def _try_acquire_unlocked(self) -> bool:
        """Non-blocking acquire (must hold lock)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        
        # Refill tokens
        self._tokens = min(
            self._rate.burst_size,
            self._tokens + elapsed * self._rate.messages_per_second
        )
        self._last_refill = now
        
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False
    
    async def wait_and_acquire(self, max_wait: float = 5.0) -> bool:
        """Wait up to max_wait for a token.
        
        Args:
            max_wait: Maximum seconds to wait.
            
        Returns:
            True if token acquired, False if timeout.
        """
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if await self.acquire():
                return True
            await asyncio.sleep(0.1)
        return False
    
    @property
    def available_tokens(self) -> float:
        """Get current available tokens (for monitoring).
        
        Note: This is a snapshot and may be slightly stale due to race
        with concurrent acquire() calls. For precise control, use acquire().
        The GIL provides some protection for simple float reads.
        """
        now = time.monotonic()
        tokens = self._tokens  # Read once
        last_refill = self._last_refill  # Read once
        elapsed = now - last_refill
        return min(
            self._rate.burst_size,
            tokens + elapsed * self._rate.messages_per_second
        )

"""Circuit breaker pattern for protecting against cascading failures."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"         # Failing, reject all calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures.
    
    Prevents repeated calls to failing external services by tracking
    failures and temporarily blocking requests.
    """
    failure_threshold: int = 5
    recovery_timeout: float = 60.0  # seconds
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    
    async def can_execute(self) -> bool:
        """Check if call should be allowed."""
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                # Check if recovery timeout passed
                if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    logger.info("Circuit entering HALF_OPEN state")
                    return True
                return False
            # HALF_OPEN: allow one test call
            return True
    
    async def record_success(self) -> None:
        """Record successful call."""
        async with self._lock:
            self.failure_count = 0
            if self.state == CircuitState.HALF_OPEN:
                logger.info("Circuit returning to CLOSED state")
            self.state = CircuitState.CLOSED
    
    async def record_failure(self) -> None:
        """Record failed call."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(f"Circuit OPEN after {self.failure_count} failures")
    
    def get_state(self) -> str:
        """Get current state as string."""
        return self.state.value

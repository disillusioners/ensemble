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
    # CR-4: single-probe-in-flight flag. When the OPEN→HALF_OPEN
    # transition happens, exactly ONE caller gets ``True`` from
    # ``can_execute()`` and is the probe; subsequent concurrent callers
    # see ``False`` until the probe reports success (CLOSED) or failure
    # (OPEN again). Without this, every concurrent caller in HALF_OPEN
    # races through the gate and re-trips the failing server.
    _probe_in_flight: bool = field(default=False, init=False, repr=False)

    async def can_execute(self) -> bool:
        """Check if call should be allowed.

        Atomicity: every read and write of ``_probe_in_flight`` is
        under ``self._lock`` so concurrent callers cannot both see
        ``_probe_in_flight == False`` and both flip it to ``True``
        (the thundering-herd bug CR-4 fixes).

        State transitions:
          - CLOSED → CLOSED + return True (normal traffic)
          - OPEN + recovery_timeout elapsed → HALF_OPEN, claim the
            probe slot, return True (this caller IS the probe)
          - OPEN + recovery_timeout NOT elapsed → return False
          - HALF_OPEN + probe slot free → claim it, return True (a new
            probe is allowed after the previous one completed)
          - HALF_OPEN + probe slot already taken → return False
        """
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                # Check if recovery timeout passed
                if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self._probe_in_flight = True
                    logger.info(
                        "Circuit entering HALF_OPEN state (probe claimed)"
                    )
                    return True
                return False
            # HALF_OPEN: at most one probe in flight at a time.
            if self._probe_in_flight:
                # Another caller is already probing — degrade.
                return False
            # Probe slot is free — claim it for this caller.
            self._probe_in_flight = True
            return True

    async def record_success(self) -> None:
        """Record successful call.

        Releases the probe-in-flight slot (set by ``can_execute`` when
        transitioning OPEN→HALF_OPEN or when granting a fresh probe
        after a previous probe completed). Safe under the lock with the
        rest of the state-machine invariants.
        """
        async with self._lock:
            self.failure_count = 0
            self._probe_in_flight = False
            if self.state == CircuitState.HALF_OPEN:
                logger.info("Circuit returning to CLOSED state")
            self.state = CircuitState.CLOSED

    async def record_failure(self) -> None:
        """Record failed call.

        Releases the probe-in-flight slot (the probe just failed) so a
        future ``can_execute`` after the recovery timeout can re-enter
        HALF_OPEN cleanly. Without this reset, the second probe after
        re-OPEN would see ``_probe_in_flight == True`` and degrade
        even though the probe slot is logically free.
        """
        async with self._lock:
            self._probe_in_flight = False
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(f"Circuit OPEN after {self.failure_count} failures")

    def get_state(self) -> str:
        """Get current state as string."""
        return self.state.value

    def reset(self) -> None:
        """Reset the circuit breaker to initial closed state.

        This clears all failure tracking and allows fresh start.
        Used when restarting an adapter to ensure circuit breaker
        state doesn't persist from previous failed attempts.
        """
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._probe_in_flight = False

"""Blueprinter rate limiter and circuit breaker.

Prevents runaway blueprint maintenance: caps revisions per hour per
project, and trips a circuit breaker after N consecutive failures.

In-process only — no persistence. State resets on daemon restart, which
is acceptable since the blueprinter rebuilds its state naturally on the
next run.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _ProjectState:
    """Per-project rate-limit state."""
    # Sliding window of revision timestamps (epoch seconds)
    revision_timestamps: list[float] = field(default_factory=list)
    # Consecutive failure count
    consecutive_failures: int = 0
    # Circuit breaker tripped until this epoch (0 = not tripped)
    cooldown_until: float = 0.0


class BlueprintRateLimiter:
    """Windowed counter + circuit breaker for blueprint revisions.

    Configuration:
        max_revisions_per_hour: Hard cap on revisions per project per hour.
        failure_threshold: Consecutive failures before circuit breaker trips.
        cooldown_seconds: How long the breaker stays tripped.

    Defaults are initial values — calibrate in Phase 6 (open item O4).
    """

    def __init__(
        self,
        max_revisions_per_hour: int = 5,
        failure_threshold: int = 3,
        cooldown_seconds: int = 600,  # 10 minutes
    ):
        self._max_per_hour = max_revisions_per_hour
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._state: dict[str, _ProjectState] = defaultdict(_ProjectState)
        self._lock = Lock()

    def can_proceed(self, project_id: str) -> bool:
        """Check if a revision is allowed for this project.

        Returns False if:
        - Rate limit exceeded (more than max_revisions_per_hour in the last hour)
        - Circuit breaker is tripped (cooldown period not elapsed)
        """
        with self._lock:
            state = self._state[project_id]
            now = time.time()

            # Check circuit breaker
            if state.cooldown_until > now:
                return False

            # Prune old timestamps (older than 1 hour)
            cutoff = now - 3600
            state.revision_timestamps = [
                ts for ts in state.revision_timestamps if ts > cutoff
            ]

            # Check rate limit
            if len(state.revision_timestamps) >= self._max_per_hour:
                return False

            return True

    def record_success(self, project_id: str) -> None:
        """Record a successful revision. Resets consecutive failure counter."""
        with self._lock:
            state = self._state[project_id]
            state.consecutive_failures = 0
            state.revision_timestamps.append(time.time())

    def record_failure(self, project_id: str) -> None:
        """Record a failed revision. Trips circuit breaker after threshold."""
        with self._lock:
            state = self._state[project_id]
            state.consecutive_failures += 1
            if state.consecutive_failures >= self._failure_threshold:
                state.cooldown_until = time.time() + self._cooldown_seconds

    def is_tripped(self, project_id: str) -> bool:
        """Check if the circuit breaker is currently tripped for this project."""
        with self._lock:
            state = self._state[project_id]
            return state.cooldown_until > time.time()


__all__ = ["BlueprintRateLimiter"]

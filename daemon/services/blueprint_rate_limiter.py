"""Blueprinter rate limiter and circuit breaker.

Prevents runaway blueprint maintenance: caps revisions per hour per
project, and trips a circuit breaker after N consecutive failures.

In-process only — no persistence. State resets on daemon restart, which
is acceptable since the blueprinter rebuilds its state naturally on the
next run.

Security notes
--------------
* **Bounded state (C2/C7):** ``_state`` is an ``OrderedDict`` capped at
  ``_MAX_STATE_ENTRIES`` (1000). When the cap is reached, the
  least-recently-used entry is evicted. This prevents an attacker (or
  buggy caller) flooding unique ``project_id`` strings from growing
  memory without bound. Additionally, the router validates
  ``project_id`` is UUID-shaped (C2 fix e) so arbitrary strings never
  reach the limiter.
* **Atomic reservation (C2 fix a):** :meth:`reserve` collapses the
  check + record into ONE operation under a single lock hold, closing
  the TOCTOU window that ``can_proceed`` + ``record_success`` had.
"""

import time
from collections import OrderedDict
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

    #: Maximum number of per-project state entries kept in memory (LRU cap).
    #: Prevents unbounded growth from attacker-controlled project_id values.
    _MAX_STATE_ENTRIES = 1000

    def __init__(
        self,
        max_revisions_per_hour: int = 10,
        failure_threshold: int = 3,
        cooldown_seconds: int = 600,  # 10 minutes
    ):
        self._max_per_hour = max_revisions_per_hour
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        # OrderedDict for O(1) LRU eviction (move_to_end on access).
        self._state: OrderedDict[str, _ProjectState] = OrderedDict()
        self._lock = Lock()

    # ── Internal helper ────────────────────────────────────────────────

    def _get_state(self, project_id: str) -> _ProjectState:
        """Get-or-create state for a project, applying LRU eviction.

        Marks the entry as most-recently-used (move_to_end). If the cap
        is reached on a NEW entry, the least-recently-used entry is
        evicted first.
        """
        state = self._state.get(project_id)
        if state is not None:
            self._state.move_to_end(project_id)
            return state
        # New entry — evict LRU if at cap.
        if len(self._state) >= self._MAX_STATE_ENTRIES:
            self._state.popitem(last=False)  # pop oldest (LRU)
        state = _ProjectState()
        self._state[project_id] = state
        return state

    def reserve(self, project_id: str) -> bool:
        """Atomically check AND reserve a write slot (C2 fix a).

        Collapses the check+record into ONE atomic operation under a
        single lock hold, closing the TOCTOU window that
        ``can_proceed`` + ``record_success`` had.

        Returns True if the write is allowed (and the slot is now
        consumed). Returns False if rate-limited or circuit breaker
        tripped. On success, the timestamp is appended and
        ``consecutive_failures`` is reset.

        The caller does NOT call ``record_success()`` afterward — the
        reserve already recorded the slot and reset the failure counter.
        Only call ``record_failure()`` if the reserved write later fails
        at the repo level.
        """
        with self._lock:
            state = self._get_state(project_id)
            now = time.time()
            # Prune old timestamps (older than 1 hour).
            cutoff = now - 3600
            state.revision_timestamps = [
                ts for ts in state.revision_timestamps if ts > cutoff
            ]
            # Check circuit breaker.
            if state.cooldown_until > now:
                return False
            # Check rate limit.
            if len(state.revision_timestamps) >= self._max_per_hour:
                return False
            # RESERVE: consume the slot now (atomic with the check).
            state.revision_timestamps.append(now)
            state.consecutive_failures = 0
            return True

    def can_proceed(self, project_id: str) -> bool:
        """Check if a revision is allowed for this project (NON-atomic).

        .. warning::
            This method has a TOCTOU race with :meth:`record_success`.
            Prefer :meth:`reserve` which is atomic. Kept for backward
            compatibility with existing tests and callers.

        Returns False if:
        - Rate limit exceeded (more than max_revisions_per_hour in the last hour)
        - Circuit breaker is tripped (cooldown period not elapsed)
        """
        with self._lock:
            state = self._get_state(project_id)
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
        """Record a successful revision. Resets consecutive failure counter.

        .. note::
            When using :meth:`reserve`, do NOT call this — the reserve
            already recorded the timestamp and reset failures. This
            method is kept for callers using the legacy
            ``can_proceed`` + ``record_success`` pair.
        """
        with self._lock:
            state = self._get_state(project_id)
            now = time.time()
            # Prune old timestamps (older than 1 hour) — C2 fix c.
            cutoff = now - 3600
            state.revision_timestamps = [
                ts for ts in state.revision_timestamps if ts > cutoff
            ]
            state.consecutive_failures = 0
            state.revision_timestamps.append(now)

    def record_failure(self, project_id: str) -> None:
        """Record a failed revision. Trips circuit breaker after threshold."""
        with self._lock:
            state = self._get_state(project_id)
            state.consecutive_failures += 1
            if state.consecutive_failures >= self._failure_threshold:
                state.cooldown_until = time.time() + self._cooldown_seconds

    def is_tripped(self, project_id: str) -> bool:
        """Check if the circuit breaker is currently tripped for this project."""
        with self._lock:
            state = self._get_state(project_id)
            return state.cooldown_until > time.time()


__all__ = ["BlueprintRateLimiter"]

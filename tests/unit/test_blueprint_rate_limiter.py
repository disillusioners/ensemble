"""Unit tests for the Blueprinter rate limiter + circuit breaker.

Covers :class:`daemon.services.blueprint_rate_limiter.BlueprintRateLimiter`:

* **Windowed rate-limit** — ``max_revisions_per_hour`` caps revisions per
  project within a sliding 1-hour window.
* **Circuit breaker** — after ``failure_threshold`` consecutive failures the
  breaker trips for ``cooldown_seconds``.
* **Per-project isolation** — state is keyed by ``project_id``.

The limiter is pure-Python (``threading.Lock`` + ``time.time()``); we drive it
directly.  Time-dependent assertions manipulate the internal ``_state``
dict (``cooldown_until``) rather than monkeypatching the clock — this exercises
the real comparison logic.
"""

from __future__ import annotations

from daemon.services.blueprint_rate_limiter import BlueprintRateLimiter


# ─── Basic allowance ──────────────────────────────────────────────────────────


class TestCanProceed:
    """``can_proceed`` gating logic."""

    def test_can_proceed_initially_allows(self) -> None:
        """A fresh limiter with no history allows any project."""
        rl = BlueprintRateLimiter()
        assert rl.can_proceed("proj-1") is True
        assert rl.can_proceed("proj-2") is True


# ─── Rate-limit window ────────────────────────────────────────────────────────


class TestRateLimit:
    """Sliding-window revision counter."""

    def test_rate_limit_allows_n_then_blocks(self) -> None:
        """With max=3, three successes fill the window; the 4th is blocked."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=3)
        rl.record_success("proj-1")
        assert rl.can_proceed("proj-1")  # 1 used, under cap
        rl.record_success("proj-1")
        assert rl.can_proceed("proj-1")  # 2 used, under cap
        rl.record_success("proj-1")
        # 3 timestamps now recorded — at or above cap
        assert rl.can_proceed("proj-1") is False


# ─── Circuit breaker ──────────────────────────────────────────────────────────


class TestCircuitBreaker:
    """Consecutive-failure breaker and cooldown."""

    def test_circuit_breaker_trips_at_threshold(self) -> None:
        """At threshold (3) failures the breaker trips and blocks proceed."""
        rl = BlueprintRateLimiter(failure_threshold=3, cooldown_seconds=600)
        rl.record_failure("proj-1")
        rl.record_failure("proj-1")
        rl.record_failure("proj-1")  # hits threshold
        assert rl.is_tripped("proj-1") is True
        # Breaker check is independent of rate count
        assert rl.can_proceed("proj-1") is False

    def test_circuit_breaker_below_threshold_not_tripped(self) -> None:
        """Two failures (below threshold 3) do not trip the breaker."""
        rl = BlueprintRateLimiter(failure_threshold=3)
        rl.record_failure("proj-1")
        rl.record_failure("proj-1")
        assert rl.is_tripped("proj-1") is False
        assert rl.can_proceed("proj-1") is True


# ─── record_success resets failures ──────────────────────────────────────────


class TestSuccessResetsFailures:
    """``record_success`` resets the consecutive-failure counter."""

    def test_record_success_resets_failures(self) -> None:
        """After a success, 3 MORE failures are needed to trip — not 1."""
        rl = BlueprintRateLimiter(failure_threshold=3)
        rl.record_failure("proj-1")
        rl.record_failure("proj-1")
        assert not rl.is_tripped("proj-1")
        rl.record_success("proj-1")  # reset to 0
        # Two more failures must NOT trip (counter restarted from 0)
        rl.record_failure("proj-1")
        rl.record_failure("proj-1")
        assert not rl.is_tripped("proj-1")
        # Third failure since reset reaches threshold
        rl.record_failure("proj-1")
        assert rl.is_tripped("proj-1")


# ─── Per-project isolation ────────────────────────────────────────────────────


class TestPerProjectIsolation:
    """Rate-limit state is independent per project."""

    def test_isolated_per_project(self) -> None:
        """Saturating project A does not affect project B."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=2)
        rl.record_success("proj-A")
        rl.record_success("proj-A")
        # proj-A now at cap
        assert rl.can_proceed("proj-A") is False
        # proj-B untouched
        assert rl.can_proceed("proj-B") is True

    def test_isolated_failure_state(self) -> None:
        """Failures on project A do not trip the breaker for project B."""
        rl = BlueprintRateLimiter(failure_threshold=3)
        rl.record_failure("proj-A")
        rl.record_failure("proj-A")
        rl.record_failure("proj-A")
        assert rl.is_tripped("proj-A")
        assert not rl.is_tripped("proj-B")
        assert rl.can_proceed("proj-B")


# ─── Cooldown expiry & manual reset ───────────────────────────────────────────


class TestCooldownAndReset:
    """Breaker cooldown expiry and manual reset."""

    def test_cooldown_expires(self) -> None:
        """After the cooldown timestamp passes, the breaker releases."""
        rl = BlueprintRateLimiter(failure_threshold=3, cooldown_seconds=600)
        for _ in range(3):
            rl.record_failure("proj-1")
        assert rl.is_tripped("proj-1")
        # Simulate time passing: set cooldown_until into the past
        rl._state["proj-1"].cooldown_until = 0.0
        assert rl.is_tripped("proj-1") is False
        assert rl.can_proceed("proj-1") is True

    def test_manual_reset(self) -> None:
        """Manually clearing cooldown_until un-trips the breaker."""
        rl = BlueprintRateLimiter(failure_threshold=3, cooldown_seconds=600)
        for _ in range(3):
            rl.record_failure("proj-1")
        assert rl.is_tripped("proj-1")
        # Manual reset
        rl._state["proj-1"].cooldown_until = 0.0
        assert rl.is_tripped("proj-1") is False

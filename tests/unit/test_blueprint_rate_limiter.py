"""Unit tests for the Blueprinter rate limiter + circuit breaker.

Covers :class:`daemon.services.blueprint_rate_limiter.BlueprintRateLimiter`:

* **Windowed rate-limit** — ``max_revisions_per_hour`` caps revisions per
  project within a sliding 1-hour window.
* **Circuit breaker** — after ``failure_threshold`` consecutive failures the
  breaker trips for ``cooldown_seconds``.
* **Per-project isolation** — state is keyed by ``project_id``.
* **Atomic reserve** (C2 fix a) — ``reserve()`` collapses check+record.
* **Bounded LRU** (C2/C7 fix b) — ``_state`` capped at 1000 entries.
* **Timestamp pruning** (C2 fix c) — old timestamps pruned on access.

The limiter is pure-Python (``threading.Lock`` + ``time.time()``); we drive it
directly.  Time-dependent assertions manipulate the internal ``_state``
dict (``cooldown_until``) rather than monkeypatching the clock — this exercises
the real comparison logic.
"""

from __future__ import annotations

import threading
import time as _time_mod

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


# ─── C2 fix a: Atomic reserve ───────────────────────────────────────────────


class TestReserve:
    """``reserve()`` atomically checks AND consumes a write slot."""

    def test_reserve_allows_until_cap(self) -> None:
        """With max=3, three reserves succeed; the 4th fails."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=3)
        assert rl.reserve("proj-1") is True
        assert rl.reserve("proj-1") is True
        assert rl.reserve("proj-1") is True
        assert rl.reserve("proj-1") is False  # cap reached

    def test_reserve_consumes_slot_atomically(self) -> None:
        """After reserve returns True, can_proceed returns False (slot gone)."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=1)
        assert rl.reserve("proj-1") is True
        # The slot is consumed — can_proceed now returns False.
        assert rl.can_proceed("proj-1") is False

    def test_reserve_resets_failures(self) -> None:
        """reserve() resets consecutive_failures on success."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=5, failure_threshold=3)
        rl.record_failure("proj-1")
        rl.record_failure("proj-1")
        assert rl.reserve("proj-1") is True
        # Failures reset — two more won't trip.
        rl.record_failure("proj-1")
        rl.record_failure("proj-1")
        assert not rl.is_tripped("proj-1")

    def test_reserve_atomic_under_concurrency(self) -> None:
        """20 threads reserve simultaneously with max=5 → exactly 5 succeed.

        Proves the TOCTOU fix: check+record is one atomic operation.
        With the old can_proceed+record_success split, a burst of threads
        could all pass the check before any recorded a success.
        """
        rl = BlueprintRateLimiter(max_revisions_per_hour=5)
        n_threads = 20
        barrier = threading.Barrier(n_threads)
        results: list[bool] = []
        results_lock = threading.Lock()

        def _worker() -> None:
            barrier.wait()  # all threads start simultaneously
            ok = rl.reserve("proj-1")
            with results_lock:
                results.append(ok)

        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        trues = sum(1 for r in results if r)
        falses = sum(1 for r in results if not r)
        assert trues == 5, f"Expected exactly 5 successes, got {trues}"
        assert falses == 15, f"Expected exactly 15 rejections, got {falses}"


# ─── C2/C7 fix b: Bounded LRU ──────────────────────────────────────────────


class TestBoundedLRU:
    """``_state`` is capped at ``_MAX_STATE_ENTRIES`` (1000)."""

    def test_state_dict_bounded_lru(self) -> None:
        """Inserting 1001+ unique project_ids keeps the dict ≤ 1000."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=10000)
        for i in range(1001):
            rl.reserve(f"proj-{i}")
        assert len(rl._state) <= BlueprintRateLimiter._MAX_STATE_ENTRIES
        assert len(rl._state) == 1000  # exactly at cap

    def test_lru_evicts_oldest_not_newest(self) -> None:
        """The oldest entry is evicted, not the newest."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=10000)
        # Override cap for a faster test
        rl._MAX_STATE_ENTRIES = 3
        rl.reserve("a")
        rl.reserve("b")
        rl.reserve("c")
        assert len(rl._state) == 3
        # Inserting d evicts a (the LRU).
        rl.reserve("d")
        assert len(rl._state) == 3
        assert "a" not in rl._state
        assert "d" in rl._state


# ─── C2 fix c: Timestamp pruning ────────────────────────────────────────────


class TestTimestampPruning:
    """Old timestamps (older than 1 hour) are pruned on access."""

    def test_reserve_prunes_old_timestamps(self) -> None:
        """Pre-fill timestamps older than 1 hour; reserve prunes them."""
        rl = BlueprintRateLimiter(max_revisions_per_hour=2)
        now = _time_mod.time()
        # Insert two timestamps older than 1 hour.
        rl._get_state("proj-1").revision_timestamps = [
            now - 3700,
            now - 3660,
        ]
        # reserve should prune the old ones and succeed.
        assert rl.reserve("proj-1") is True
        # Only the new timestamp remains.
        state = rl._get_state("proj-1")
        assert len(state.revision_timestamps) == 1
        assert state.revision_timestamps[0] > now - 10

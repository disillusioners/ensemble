"""Regression tests for the adapter-supervisor backoff-reset defect.

Context: critical note c6ae6582 / commission item O4 Part A
(agents-ensemble ``resource-cost-control``).

Defect: ``SourceRegistry._run_adapter_safe`` reset ``backoff`` to its 2.0s
base **unconditionally** on every successful ``adapter.start()``. During a
sustained outage ``start()`` succeeds almost instantly (the adapter merely
reports RUNNING); the child then fails its health check seconds later and
crashes. Because every successful START zeroed ``backoff``, the crash
handler's own conditional reset (which needs a >=60s run) could never
accumulate a growing delay — the retry cadence stayed pinned at ~2-5s
(the live plane/computer-use restart storm).

Fix: the successful-start reset fires ONLY when the PRIOR run lived
>= ``success_threshold`` (60s).

These tests drive the real ``_run_adapter_safe`` loop with a fake adapter,
a controllable monotonic clock, and a recording sleep — so no real time
passes, and no subprocess, network, filesystem, or HOME-derived /
live-shaped path is ever touched.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daemon.sources.registry as registry_module
from daemon.sources.base import MessageSourceAdapter, SourceConfig
from daemon.sources.registry import SourceRegistry


class _FakeClock:
    """Controllable monotonic clock.

    Patched over the registry MODULE's ``time`` name only
    (``daemon.sources.registry.time``) — the stdlib ``time`` module object
    itself is never mutated, so the event loop's own clock and any asyncio
    timers keep working normally.
    """

    def __init__(self, start: float = 10_000.0) -> None:
        self.t = start

    def monotonic(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _CrashAfterRunAdapter(MessageSourceAdapter):
    """Adapter whose ``start()`` ALWAYS succeeds but whose health check then
    fails — the sustained-outage shape.

    ``run_durations`` supplies the simulated lifetime of each successive
    run: before failing the health check the adapter advances the fake clock
    by that amount, so the crash handler observes the intended run duration
    without sleeping for real.
    """

    def __init__(self, clock: _FakeClock, run_durations: list[float]) -> None:
        super().__init__(
            SourceConfig(
                source_id="plane-test",
                source_type="plane",
                name="plane-test",
                config={},
                credentials={},
            ),
            on_message=MagicMock(),
        )
        self._clock = clock
        self._run_durations = list(run_durations)
        self._iteration = 0
        self.start_count = 0
        self.stop_count = 0

    async def start(self) -> None:
        self.start_count += 1

    async def stop(self) -> None:
        self.stop_count += 1

    async def send(self, message) -> bool:  # pragma: no cover - unused here
        return True

    async def health_check(self) -> bool:
        # Simulate this run's lifetime, then fail. A False health check is
        # re-raised as _HealthCheckFailed and routed to the crash handler
        # (outer except), exactly like a real adapter death.
        idx = min(self._iteration, len(self._run_durations) - 1)
        self._clock.advance(self._run_durations[idx])
        self._iteration += 1
        return False


def _drive_supervisor(
    adapter: _CrashAfterRunAdapter,
    clock: _FakeClock,
    cycles: int,
    monkeypatch,
    advance_clock_on_sleep: bool = False,
) -> list[float]:
    """Run the REAL supervisor loop for exactly ``cycles`` crash cycles.

    Returns the recorded backoff sleep durations (one per cycle). Jitter is
    pinned to 1.0 so ``sleep_time == backoff`` and the cadence is exact.

    ``advance_clock_on_sleep`` controls whether the fake monotonic clock
    advances by each backoff sleep's duration — i.e. whether time actually
    passes while the supervisor waits to restart. Real waiting does advance
    the clock; leaving it frozen makes the measured interval
    ``now - last_success_time`` equal the prior run's lifetime alone, which
    is what masks the interval-measurement defect (see the MUTATION-SENSITIVE
    storm test below).
    """
    sleeps: list[float] = []

    async def _recording_sleep(delay, *args, **kwargs):
        sleeps.append(float(delay))
        if advance_clock_on_sleep:
            clock.advance(float(delay))
        if len(sleeps) >= cycles:
            # Signal the supervisor to exit on its next loop-turn check.
            registry_obj._running[adapter.source_id] = False

    # Patch the registry module's own names only (never the stdlib modules).
    monkeypatch.setattr(
        registry_module, "time", SimpleNamespace(monotonic=clock.monotonic)
    )
    monkeypatch.setattr(
        registry_module, "random", SimpleNamespace(uniform=lambda a, b: 1.0)
    )
    # ``asyncio.sleep`` is the module attribute the loop awaits; the fake
    # records the cadence and returns immediately (monkeypatch restores it).
    monkeypatch.setattr(asyncio, "sleep", _recording_sleep)

    registry_obj = SourceRegistry(MagicMock(), MagicMock())
    registry_obj._running[adapter.source_id] = True

    asyncio.run(registry_obj._run_adapter_safe(adapter))

    assert len(sleeps) == cycles, (
        f"expected {cycles} backoff sleeps, recorded {len(sleeps)}: {sleeps}"
    )
    return sleeps


def test_sustained_outage_grows_backoff_across_cycles(monkeypatch):
    """Storm reproducer: start() succeeds -> health check fails, repeatedly.

    The retry cadence must GROW across cycles (2 -> 5 -> 12.5 -> 31.25 with
    unit jitter) instead of being pinned at ~2-5s.
    """
    clock = _FakeClock()
    adapter = _CrashAfterRunAdapter(clock, run_durations=[1.0] * 6)

    sleeps = _drive_supervisor(adapter, clock, cycles=4, monkeypatch=monkeypatch)

    assert sleeps == pytest.approx([2.0, 5.0, 12.5, 31.25])
    # Explicit anti-storm assertion: strictly growing, never back to base.
    assert sleeps[1] > sleeps[0]
    assert sleeps[2] > sleeps[1]
    assert sleeps[3] > sleeps[2]


def test_sustained_outage_reaches_max_backoff_with_advancing_clock(monkeypatch):
    """Storm reproducer WITH time actually passing during the backoff sleeps.

    MUTATION-SENSITIVE companion to the frozen-clock test above. That sibling
    never advances the clock across ``asyncio.sleep`` calls, so
    ``now - last_success_time`` collapses to the prior run's lifetime alone
    and the measurement defect stays invisible. Here the fake clock advances
    by each backoff sleep — the real-world shape — so
    ``now - last_success_time`` also carries the ACCUMULATED inter-run sleep.

    Correct semantics: across >=8 short-death cycles (start succeeds ->
    health check fails) the cadence must climb monotonically to
    ``max_backoff`` (300s) and STAY there — a young-dying run must never earn
    the reset. The defect instead sawtooths (2 -> 5 -> 12.5 -> 31.25 ->
    78.125 -> ~2 -> repeat) once the accumulated sleep crosses 60s.
    """
    clock = _FakeClock()
    adapter = _CrashAfterRunAdapter(clock, run_durations=[1.0] * 16)

    sleeps = _drive_supervisor(
        adapter,
        clock,
        cycles=8,
        monkeypatch=monkeypatch,
        advance_clock_on_sleep=True,
    )

    # Exact cadence: 2 * 2.5^k, clamped at 300, once each cycle.
    assert sleeps == pytest.approx(
        [2.0, 5.0, 12.5, 31.25, 78.125, 195.3125, 300.0, 300.0]
    )
    # Strictly growing up to the clamp ...
    assert all(b > a for a, b in zip(sleeps[:6], sleeps[1:7]))
    # ... then flat at max_backoff — no reset to base while runs die young.
    assert sleeps[6:] == pytest.approx([300.0, 300.0])
    assert 2.0 not in sleeps[1:]


def test_short_lived_crash_does_not_reset_backoff(monkeypatch):
    """The direct gate pin: a *successful start* that is followed by a
    short-lived (<60s) crash must NOT zero the accumulated backoff."""
    clock = _FakeClock()
    adapter = _CrashAfterRunAdapter(clock, run_durations=[1.0, 1.0, 1.0])

    sleeps = _drive_supervisor(adapter, clock, cycles=2, monkeypatch=monkeypatch)

    # First crash: initial base backoff.
    assert sleeps[0] == pytest.approx(2.0)
    # Second cycle's start() succeeds, but the prior run lived only ~1s, so
    # the backoff is NOT reset -> the cadence grows to 5.0, not back to 2.0.
    assert sleeps[1] == pytest.approx(5.0)


def test_backoff_reset_after_long_lived_run(monkeypatch):
    """The healthy side: a run that actually lived >=60s still earns the
    reset, and growth resumes afterwards if subsequent runs are short."""
    clock = _FakeClock()
    adapter = _CrashAfterRunAdapter(clock, run_durations=[120.0, 1.0, 1.0])

    sleeps = _drive_supervisor(adapter, clock, cycles=3, monkeypatch=monkeypatch)

    # Run 1 lived 120s (>=60s): its crash handler resets -> 2.0s cadence.
    assert sleeps[0] == pytest.approx(2.0)
    # Run 2 starts right after a >=60s run: the successful-start reset fires
    # (no-op, already at base) -> still a 2.0s cadence, not 5.0s.
    assert sleeps[1] == pytest.approx(2.0)
    # Run 3 starts after a short-lived run: no reset -> growth resumes.
    assert sleeps[2] == pytest.approx(5.0)

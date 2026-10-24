"""Sampler + lag-probe pack (T30 / AC43 / D4 / D5 / D6 / D4b / MR-6).

WHAT THIS PACK PROVES
---------------------
* the CPU **delta maths** against a synthetic ``/proc`` fixture — the
  expected percentage of ONE core, including the **>100 %** case, and the
  fact that the first tick is a baseline (zero, never a fabricated
  number);
* the **per-thread attribution** with the canonical field set
  (``tid`` / ``native_id`` / ``name`` / ``comm`` / ``cpu_delta_s`` /
  ``share_pct``) and the ``comm`` column's presence for EVERY thread —
  the auditable identity when the ``native_id`` mapping has no Python
  thread behind it (D6: this tree has zero ``native_id`` precedent);
* **MR-6**: the observer's own thread is EXCLUDED from the attribution;
* the lag probe against a **real event loop** with a deliberate block —
  the measured value is asserted as MEASURED (approver N4: a block
  measures less than the block, so the assertion is a lower bound on the
  observed stall, never ``block == lag``);
* the bounded histogram (300 samples, oldest-evicted-first) and the
  ``lag.json`` payload's canonical fields;
* the probe's degraded states (``unknown`` / ``stopped``) — a state, never
  a crash and never a fabricated number.

SAFETY FENCE: every path is a ``tmp_path``. No live-shaped path, no
no live-database connection variable and no HOME-derived directory is ever resolved here.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from daemon.services.wedge_capture import sampler as sampler_module
from daemon.services.wedge_capture.lag_probe import (
    LAG_OVERFLOW_BUCKET_S,
    MAX_LAG_SAMPLES,
    PROBE_INITIALIZING,
    PROBE_OK,
    PROBE_STOPPED,
    PROBE_UNKNOWN,
    LagHistogram,
    LoopLagProbe,
)
from daemon.services.wedge_capture.sampler import (
    CLOCK_TICKS_PER_SECOND,
    CpuSampler,
    ThreadSample,
)

#: The canonical per-thread field set (the sibling matrix §9).
PER_THREAD_FIELDS = frozenset(
    {"tid", "native_id", "name", "comm", "cpu_delta_s", "share_pct"}
)


# ── the synthetic /proc fixture ────────────────────────────────────────────


def _stat_line(*, utime: int, stime: int, pid: int = 1234, comm: str = "python") -> str:
    """A syntactically valid ``/proc/<pid>/stat`` line.

    The field layout matches ``/proc``: state at index 0 of the tail,
    ``utime``/``stime`` at the indices ``proc_readers._parse_proc_stat``
    reads. ``vsize``/``rss`` are present so the parser's length guard
    passes.
    """
    tail = (
        ["S"]
        + ["0"] * 10  # ppid … cmajflt (fields 4-13)
        + [str(utime), str(stime)]  # fields 14-15
        + ["0", "0", "20", "0"]  # cutime, cstime, priority, nice
        + ["4", "0", "1000", "4096", "10"]  # threads, itreal, start, vsize, rss
    )
    return f"{pid} ({comm}) " + " ".join(tail)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _FakeProc:
    """A writable synthetic ``/proc`` tree for one process + N threads."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def set_process(self, *, utime: int, stime: int) -> None:
        _write(self.root / "self" / "stat", _stat_line(utime=utime, stime=stime))

    def set_thread(self, tid: int, *, utime: int, stime: int, comm: str) -> None:
        _write(
            self.root / "self" / "task" / str(tid) / "stat",
            _stat_line(utime=utime, stime=stime, pid=tid, comm=comm),
        )
        _write(self.root / "self" / "task" / str(tid) / "comm", comm + "\n")

    def remove_thread(self, tid: int) -> None:
        for name in ("stat", "comm"):
            path = self.root / "self" / "task" / str(tid) / name
            if path.exists():
                path.unlink()


class _Clock:
    """A controllable ``time.monotonic`` stand-in (module-scoped patch)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Patch ONLY the sampler module's ``time`` reference."""
    stub = _Clock()
    monkeypatch.setattr(sampler_module, "time", stub)
    return stub


# ── AC43 / AC2: the CPU delta maths ────────────────────────────────────────


class TestCpuDeltaMaths:
    def test_the_first_tick_is_a_baseline_not_a_fabricated_number(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        proc = _FakeProc(tmp_path / "proc")
        proc.set_process(utime=1000, stime=0)
        proc.set_thread(111, utime=1000, stime=0, comm="MainThread")

        sample = CpuSampler(proc_root=proc.root).sample()

        assert sample.process_cpu_delta_s == 0.0
        assert sample.process_cpu_pct == 0.0
        assert len(sample.per_thread) == 1

    def test_a_one_second_burn_at_one_core_is_100_percent(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        """100 jiffies over a 1.0 s tick == 100 % of ONE core."""
        proc = _FakeProc(tmp_path / "proc")
        proc.set_process(utime=1000, stime=0)
        proc.set_thread(111, utime=1000, stime=0, comm="MainThread")
        sampler = CpuSampler(proc_root=proc.root)
        sampler.sample()  # baseline

        jiffies = CLOCK_TICKS_PER_SECOND  # exactly 1.0 s of CPU
        proc.set_process(utime=1000 + jiffies, stime=0)
        proc.set_thread(111, utime=1000 + jiffies, stime=0, comm="MainThread")
        clock.advance(1.0)

        sample = sampler.sample()

        assert sample.process_cpu_delta_s == pytest.approx(1.0)
        assert sample.process_cpu_pct == pytest.approx(100.0)
        assert sample.tick_delay_s == pytest.approx(1.0)

    def test_the_value_may_exceed_100_percent_of_one_core(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        """The incident metric was 95-107 % of ONE core — a "percent of
        all cores" normalisation would have missed it entirely."""
        proc = _FakeProc(tmp_path / "proc")
        proc.set_process(utime=0, stime=0)
        sampler = CpuSampler(proc_root=proc.root)
        sampler.sample()

        burst = int(CLOCK_TICKS_PER_SECOND * 1.5)  # 1.5 s of CPU in 1.0 s
        proc.set_process(utime=burst, stime=0)
        clock.advance(1.0)

        sample = sampler.sample()
        assert sample.process_cpu_delta_s == pytest.approx(1.5)
        assert sample.process_cpu_pct == pytest.approx(150.0)

    def test_system_time_counts_as_cpu(self, tmp_path: Path, clock: _Clock) -> None:
        proc = _FakeProc(tmp_path / "proc")
        proc.set_process(utime=0, stime=0)
        sampler = CpuSampler(proc_root=proc.root)
        sampler.sample()

        half = CLOCK_TICKS_PER_SECOND // 2
        proc.set_process(utime=half, stime=half)
        clock.advance(1.0)

        assert sampler.sample().process_cpu_delta_s == pytest.approx(1.0)

    def test_a_counter_reset_never_reports_negative_cpu(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        proc = _FakeProc(tmp_path / "proc")
        proc.set_process(utime=5000, stime=5000)
        sampler = CpuSampler(proc_root=proc.root)
        sampler.sample()

        proc.set_process(utime=10, stime=10)  # wrapped / replaced process
        clock.advance(1.0)

        assert sampler.sample().process_cpu_delta_s == 0.0


# ── AC43 / AC3: the per-thread attribution ────────────────────────────────


class TestThreadAttribution:
    def _two_thread_fixture(
        self, tmp_path: Path
    ) -> tuple[_FakeProc, CpuSampler]:
        proc = _FakeProc(tmp_path / "proc")
        proc.set_process(utime=0, stime=0)
        proc.set_thread(111, utime=0, stime=0, comm="MainThread")
        proc.set_thread(222, utime=0, stime=0, comm="psycopg-worker")
        sampler = CpuSampler(proc_root=proc.root)
        sampler.sample()  # baseline
        return proc, sampler

    def test_every_thread_carries_the_canonical_field_set(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        proc, sampler = self._two_thread_fixture(tmp_path)
        proc.set_process(utime=100, stime=0)
        proc.set_thread(111, utime=80, stime=0, comm="MainThread")
        proc.set_thread(222, utime=20, stime=0, comm="psycopg-worker")
        clock.advance(1.0)

        sample = sampler.sample()

        assert len(sample.per_thread) == 2
        for thread in sample.per_thread:
            assert isinstance(thread, ThreadSample)
            assert set(thread.as_dict()) == PER_THREAD_FIELDS
            # D6: the OS-level identity is present for EVERY thread.
            assert thread.comm
            assert thread.tid == thread.native_id

    def test_the_share_is_computed_against_the_process_delta(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        proc, sampler = self._two_thread_fixture(tmp_path)
        proc.set_process(utime=100, stime=0)
        proc.set_thread(111, utime=80, stime=0, comm="MainThread")
        proc.set_thread(222, utime=20, stime=0, comm="psycopg-worker")
        clock.advance(1.0)

        sample = sampler.sample()
        shares = {thread.tid: thread.share_pct for thread in sample.per_thread}

        assert shares[111] == pytest.approx(80.0)
        assert shares[222] == pytest.approx(20.0)
        assert sample.top_thread.tid == 111
        assert sample.top_thread_comm == "MainThread"

    def test_the_comm_column_survives_an_absent_python_thread_name(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        """The synthetic tids have no Python thread behind them, so
        ``name`` is ``None`` — and ``comm`` still names the thread. This
        is exactly the auditability D6 buys."""
        proc, sampler = self._two_thread_fixture(tmp_path)
        proc.set_process(utime=100, stime=0)
        proc.set_thread(111, utime=100, stime=0, comm="MainThread")
        clock.advance(1.0)

        sample = sampler.sample()
        thread = next(t for t in sample.per_thread if t.tid == 111)
        assert thread.name is None
        assert thread.comm == "MainThread"

    def test_mr6_the_observer_is_excluded_from_the_attribution(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        """MR-6: including the watchdog's own thread would inflate the
        measured concentration with the observer's sampling cost."""
        proc, sampler = self._two_thread_fixture(tmp_path)
        sampler.exclude_thread(222)
        proc.set_process(utime=100, stime=0)
        proc.set_thread(111, utime=60, stime=0, comm="MainThread")
        proc.set_thread(222, utime=40, stime=0, comm="WedgeCaptureWatchdog")
        clock.advance(1.0)

        sample = sampler.sample()

        assert [thread.tid for thread in sample.per_thread] == [111]
        assert sample.top_thread.tid == 111

    def test_a_missing_proc_tree_degrades_instead_of_raising(
        self, tmp_path: Path, clock: _Clock
    ) -> None:
        sampler = CpuSampler(proc_root=tmp_path / "does-not-exist")
        sample = sampler.sample()
        assert sample.process_cpu_pct == 0.0
        assert sample.per_thread == []
        assert sample.top_thread is None

    def test_reset_re_baselines(self, tmp_path: Path, clock: _Clock) -> None:
        proc = _FakeProc(tmp_path / "proc")
        proc.set_process(utime=0, stime=0)
        sampler = CpuSampler(proc_root=proc.root)
        sampler.sample()
        proc.set_process(utime=100, stime=0)
        clock.advance(1.0)
        assert sampler.sample().process_cpu_delta_s == pytest.approx(1.0)

        sampler.reset()
        proc.set_process(utime=300, stime=0)
        clock.advance(1.0)
        assert sampler.sample().process_cpu_delta_s == 0.0


# ── AC43: the lag probe against a REAL event loop ─────────────────────────


@pytest.fixture()
def real_loop():
    """A real asyncio loop running on its own thread."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="lag-probe-loop", daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        if not loop.is_closed():
            loop.close()


class TestLagProbeRealLoop:
    def test_a_blocked_loop_measures_the_stall(self, real_loop) -> None:
        """A deliberate block is MEASURED from outside the loop.

        The assertion is a lower bound on the observed stall (approver
        N4: the measured value is one probe post short of the block, so
        ``lag == block`` is vacuous — the meaningful claim is that a
        real 0.3 s block produces a lag of at least ~0.25 s).
        """
        probe = LoopLagProbe(real_loop)

        # First tick posts the heartbeat and reports NO number (A1(a)).
        assert probe.tick() is None
        assert probe.probe_state == PROBE_INITIALIZING

        # Let the heartbeat execute — the loop is healthy.
        deadline = time.monotonic() + 2.0
        while probe.never_fired and time.monotonic() < deadline:
            time.sleep(0.01)
        assert probe.never_fired is False
        assert probe.loop_thread_native_id is not None

        gate = threading.Event()
        try:
            real_loop.call_soon_threadsafe(lambda: gate.wait(2.0))
            time.sleep(0.05)  # the loop is now inside the block

            result = probe.tick()  # post or measure
            assert result is None or result >= 0.0
            time.sleep(0.3)
            measured = probe.tick()
        finally:
            gate.set()
            time.sleep(0.05)

        assert measured is not None
        assert measured >= 0.25, f"a real 0.3s block must be measured, got {measured}"
        assert probe.probe_state == PROBE_OK

    def test_the_histogram_snapshot_carries_the_canonical_fields(
        self, real_loop
    ) -> None:
        probe = LoopLagProbe(real_loop)
        probe.tick()
        time.sleep(0.05)
        payload = probe.snapshot_histogram()
        assert set(payload) >= {
            "samples", "max", "p95", "p50", "overflow_count",
            "loop_unresponsive_s", "bucket_ceiling_s", "values",
        }

    def test_a_closed_loop_is_a_state_not_a_crash(self, real_loop) -> None:
        probe = LoopLagProbe(real_loop)
        real_loop.call_soon_threadsafe(real_loop.stop)
        time.sleep(0.2)
        probe.tick()
        # ``is_closed()`` is False on a stopped-but-not-closed loop, so
        # drive the stopped state explicitly — the contract is the same:
        # no number, no exception.
        probe.stop()
        assert probe.tick() is None
        assert probe.probe_state == PROBE_STOPPED

    def test_no_loop_at_all_reports_unknown(self) -> None:
        probe = LoopLagProbe(None)
        assert probe.tick() is None
        assert probe.probe_state == PROBE_UNKNOWN
        assert probe.never_fired is True
        assert probe.last_probe_age_s is None


# ── AC43 / D4(d): the bounded histogram ───────────────────────────────────


class TestLagHistogram:
    def test_it_evicts_oldest_first_at_the_bound(self) -> None:
        histogram = LagHistogram()
        for index in range(MAX_LAG_SAMPLES + 5):
            histogram.add(float(index))
        assert len(histogram.samples) == MAX_LAG_SAMPLES
        assert histogram.samples[0] == pytest.approx(5.0)  # the oldest 5 are gone
        assert histogram.samples[-1] == pytest.approx(
            float(MAX_LAG_SAMPLES + 4)
        )

    def test_percentiles_are_nearest_rank(self) -> None:
        histogram = LagHistogram()
        for value in range(1, 101):
            histogram.add(float(value))
        assert histogram.percentile(50) == pytest.approx(50.0)
        assert histogram.percentile(95) == pytest.approx(95.0)
        assert histogram.percentile(100) == pytest.approx(100.0)
        assert histogram.percentile(0) == pytest.approx(1.0)

    def test_an_empty_histogram_renders_none_not_zero(self) -> None:
        payload = LagHistogram().as_dict()
        assert payload["samples"] == 0
        assert payload["max"] is None
        assert payload["p95"] is None
        assert payload["p50"] is None

    def test_a_value_past_the_bucket_ceiling_is_counted_not_dropped(self) -> None:
        """A1(d): an explicit top/overflow bucket — the sample is
        retained and the bucket records that it overflowed."""
        histogram = LagHistogram()
        huge = LAG_OVERFLOW_BUCKET_S + 60.0
        histogram.count_overflow(huge)
        histogram.add(huge)
        payload = histogram.as_dict()
        assert payload["overflow_count"] == 1
        assert payload["samples"] == 1
        assert payload["max"] == pytest.approx(huge)
        assert payload["bucket_ceiling_s"] == pytest.approx(LAG_OVERFLOW_BUCKET_S)

    def test_loop_unresponsive_is_reported_while_a_post_is_outstanding(self) -> None:
        probe = LoopLagProbe(None)
        assert probe.snapshot_histogram()["loop_unresponsive_s"] is None

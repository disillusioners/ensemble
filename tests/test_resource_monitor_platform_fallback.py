"""Regression pack — the resource monitor's PORTABLE HOST FALLBACK.

Incident (2026-10-19 → 2026-10-29): the resource-telemetry tier was
DARK in production. The flusher was NOT at fault — it committed every
window (``flush.batches`` climbing, ``flush.errors == 0``) and wrote
the T3 ``resource_pid_map`` rows normally. The break was ONE link
upstream:

    the deployment host is macOS — it has NO ``/proc`` and NO cgroup
    v2 tree. ``read_proc_snapshot`` therefore returned ``None`` on
    EVERY tick, the sampler wrote NO ring row (and NO instance-ring
    row), and the flusher's bounded buffer stayed empty — so the T1
    ``resource_daemon_bucket`` and T2 ``resource_instance_sample``
    tables stayed at 0 rows for the whole life of the subsystem.

The silent part was the BLINDNESS: ``sampler.counters.ticks`` climbed
(so ``/api/resources/self-cost`` reported a healthy-looking
``samples`` count) even though every single sample was dropped, and
``SamplerCounters.missed_samples`` — documented as "bumped every time
we skip a tick for overrun or for any structural read failure" — was
NEVER bumped for a read failure.

These tests pin the fix on two seams:

  * ``proc_readers`` falls back to a portable self-snapshot on a host
    with no ``/proc`` (and ONLY there — an existing proc root keeps
    the historical ``None`` contract so the injected-fixture tests stay
    hermetic; a Linux host without ``/proc`` is a broken host, not a
    platform difference, and must stay visible).
  * the sampler counts a failed platform read as a SKIPPED sample
    (no tick, no ring row) instead of silently reporting health.

FENCE DISCIPLINE: no test touches the real ``/proc`` or the real
process state. The platform reader is exercised through a MONKEYPATCHED
``_run_ps`` (canned output) and a stubbed ``sys.platform``; the
sampler's own read is stubbed outright for the counter test. The
synthetic roots live under pytest ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.services.resource_monitor import Sampler, TierRing
from daemon.services.resource_monitor import proc_readers as proc_readers_mod
from daemon.services.resource_monitor import sampler as sampler_mod
from daemon.services.resource_monitor.proc_readers import (
    DaemonProcSnapshot,
    ProcSnapshot,
)

# A proc root that certainly does not exist — stands in for a host
# with no /proc at all (macOS).
_ABSENT_PROC_ROOT = Path("/nonexistent-proc-root-for-resource-monitor-tests")

#: A minimal WELL-FORMED snapshot — used where a test needs the read
#: seam to SUCCEED (a recovery tick) without depending on the host.
_GOOD_SNAPSHOT = ProcSnapshot(
    daemon=DaemonProcSnapshot(
        pid=4242,
        rss_kb=177520,
        vsize_kb=411300784,
        anon_kb=177520,
        hw_peak_kb=200000,
        threads=2,
        fds=64,
        cpu_user_usec=1000,
        cpu_sys_usec=1000,
        pss_kb=None,
    ),
    cgroup=None,
)


class _StubSys:
    """Stand-in for the ``sys`` module with a forged ``platform``."""

    def __init__(self, platform: str) -> None:
        self.platform = platform


def _fake_ps(memory: str, threads: str):
    """Build a ``_run_ps`` replacement returning canned output.

    ``memory`` is the ``ps -o rss=,vsz=`` payload; ``threads`` is the
    ``ps -M`` payload (a header line + one line per thread).
    """

    def _run(args):
        if list(args[:1]) == ["-o"]:
            return memory
        if list(args[:1]) == ["-M"]:
            return threads
        return None

    return _run


@pytest.fixture
def darwin_host(monkeypatch):
    """A host that looks like macOS to ``proc_readers``.

    Two monkeypatches, both LOCAL to the module under test:

    * ``proc_readers.sys`` — a stub whose ``platform`` is ``"darwin"``
      (the real ``sys`` module is never mutated).
    * ``proc_readers._run_ps`` — canned ``ps`` output; the real ``ps``
      binary is never spawned, so the test is hermetic AND identical
      on a Linux CI host.
    """
    monkeypatch.setattr(
        proc_readers_mod, "sys", _StubSys("darwin"), raising=False
    )
    monkeypatch.setattr(
        proc_readers_mod,
        "_run_ps",
        _fake_ps(
            memory="177520 411300784",
            threads=(
                "USER            PID   TT   %CPU STAT PRI     STIME"
                "     UTIME COMMAND\n"
                "nguyenminhkha 44004   ??    4.3 S    31T   0:00.02"
                "   0:00.02 /path/to/python\n"
                "nguyenminhkha 44004   ??    0.0 S    31T   0:00.02"
                "   0:00.02 /path/to/python\n"
            ),
        ),
        raising=False,
    )
    return True


# ──────────────────────────────────────────────────────────────────────────
# proc_readers — the portable fallback
# ──────────────────────────────────────────────────────────────────────────


class TestPortableFallback:
    def test_proc_less_host_yields_a_snapshot(self, darwin_host):
        """A host with NO /proc still produces a daemon snapshot.

        Pre-fix this returned ``None`` — the sampler then dropped
        every tick and the flusher wrote nothing.
        """
        snap = proc_readers_mod.read_daemon_snapshot(_ABSENT_PROC_ROOT)
        assert snap is not None, (
            "a /proc-less host must degrade to the portable reader, not "
            "to a silent no-snapshot"
        )
        assert snap.rss_kb == 177520
        assert snap.vsize_kb == 411300784
        # ``ps -M`` prints a header + one line per thread ⇒ 2 threads.
        assert snap.threads == 2
        assert snap.anon_kb == snap.rss_kb
        assert snap.pss_kb is None
        assert snap.cpu_user_usec >= 0
        assert snap.cpu_sys_usec >= 0

    def test_proc_snapshot_uses_the_fallback(self, darwin_host):
        """``read_proc_snapshot`` — the sampler's public seam — works."""
        snap = proc_readers_mod.read_proc_snapshot(
            _ABSENT_PROC_ROOT, Path("/nonexistent-cgroup-root-for-tests")
        )
        assert snap is not None
        # No cgroup v2 tree ⇒ the payload degrades to daemon-only.
        assert snap.cgroup is None
        assert snap.daemon.rss_kb == 177520

    def test_existing_proc_root_never_uses_the_fallback(self, darwin_host, tmp_path):
        """An EXISTING proc root keeps the historical ``None`` contract.

        This is what keeps the injected-fixture tests hermetic: they
        build a real directory tree, so the fallback must never reach
        for the REAL host process.
        """
        proc_root = tmp_path / "proc"
        proc_root.mkdir()
        assert proc_readers_mod.read_daemon_snapshot(proc_root) is None

    def test_linux_host_without_proc_stays_none(self, monkeypatch, tmp_path):
        """On Linux a missing /proc is a BROKEN host, not a platform.

        The fallback must not paper over it — the sampler counter
        contract (below) is what makes the breakage visible.
        """
        monkeypatch.setattr(
            proc_readers_mod, "sys", _StubSys("linux"), raising=False
        )
        monkeypatch.setattr(
            proc_readers_mod,
            "_run_ps",
            _fake_ps(memory="177520 411300784", threads="HEADER\nrow\n"),
            raising=False,
        )
        assert (
            proc_readers_mod.read_daemon_snapshot(tmp_path / "absent-proc")
            is None
        )


# ──────────────────────────────────────────────────────────────────────────
# sampler — the ring row + the counter contract
# ──────────────────────────────────────────────────────────────────────────


class TestSamplerOnAProcLessHost:
    def _sampler(self, proc_root: Path):
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        sampler = Sampler(
            sample_seconds=30,
            self_cpu_budget_pct=1.0,
            self_rss_budget_mb=50,
            detail_level="auto",
            proc_root=proc_root,
            cgroup_root=Path("/nonexistent-cgroup-root-for-tests"),
            ring=ring,
            instance_ring=instance_ring,
        )
        return sampler, ring, instance_ring

    def test_tick_writes_a_ring_row(self, darwin_host):
        """The tick produces a row — the precondition for ANY DB row.

        Pre-fix the ring stayed empty, so the flusher's buffer stayed
        empty and T1/T2 stayed at 0 rows.
        """
        sampler, ring, _instance_ring = self._sampler(_ABSENT_PROC_ROOT)
        snap = sampler.run_once()
        assert snap is not None
        assert len(ring) == 1
        row = ring.snapshot()[0]
        assert row["rss_kb"] == 177520
        assert row["threads"] == 2
        assert sampler.counters.ticks == 1
        assert sampler.counters.ticks_skipped == 0
        assert sampler.counters.missed_samples == 0

    def test_failed_read_is_counted_as_a_skipped_sample(self, monkeypatch):
        """A failed platform read is a SKIPPED sample, not a healthy tick.

        Pre-fix ``counters.ticks`` climbed anyway (so ``/self-cost``
        reported a healthy ``samples`` count) and ``missed_samples``
        stayed 0 — the exact blindness that let the dark tier survive
        for days with ``flush.errors == 0``.

        B1 (revision round): the ring now carries ONE DEGRADED
        SENTINEL row for the failed tick — the DB evidence path. It
        is NOT a sample: it never becomes a ``counters.ticks`` tick,
        it carries ``interval_s == 0`` (the degraded marker), and
        every daemon metric on it is NULL.
        """
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        sampler, ring, _instance_ring = self._sampler(_ABSENT_PROC_ROOT)
        assert sampler.run_once() is None
        # ONE sentinel row — the drop leaves DB evidence.
        assert len(ring) == 1
        sentinel = ring.snapshot()[0]
        assert sentinel["interval_s"] == sampler_mod.DEGRADED_SENTINEL_INTERVAL_S
        assert sentinel["rss_kb"] is None
        assert sentinel["anon_kb"] is None
        assert sentinel["threads"] is None
        assert sentinel["missed_samples"] == 1
        # ... and it is NOT counted as a sample.
        assert sampler.counters.ticks == 0
        assert sampler.counters.ticks_skipped == 1
        assert sampler.counters.missed_samples == 1

    def test_failed_read_warns_once(self, monkeypatch, caplog):
        """One clear line ties the degradation to its cause (F3)."""
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        sampler, _ring, _instance_ring = self._sampler(_ABSENT_PROC_ROOT)
        with caplog.at_level("WARNING"):
            sampler.run_once()
            sampler.run_once()
        warnings = [
            rec
            for rec in caplog.records
            if "no daemon snapshot" in rec.getMessage()
        ]
        assert len(warnings) == 1, (
            "the structural read failure must be logged ONCE, not per tick"
        )


# ──────────────────────────────────────────────────────────────────────────
# B4 — the degradation warning must NAME A CAUSE and must RE-ARM
# ──────────────────────────────────────────────────────────────────────────


class TestDegradationWarningDiagnosability:
    """B4(i)+(ii) — the warning is diagnosable and it re-arms.

    B4(ii): the old warning said only "(platform read failed)" — a
    missing binary, a non-zero exit, a timeout and a parse failure
    are four DIFFERENT operator problems, and with a broken PATH the
    old code produced the SAME silent ``None`` as every other failure
    (the original dark-tier bug, re-armed silently). The reader now
    records a REASON TOKEN and the warning names it.

    B4(i): the old one-shot flag was set on the first failure and
    NEVER reset — a daemon that recovered and later degraded again
    stayed silent for the rest of its life. The flag is now reset on a
    clean tick (with a RECOVERY line), the consecutive-failure count
    is tracked, and a long streak re-warns every N failures.
    """

    def _sampler(self, proc_root: Path):
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        sampler = Sampler(
            sample_seconds=30,
            self_cpu_budget_pct=1.0,
            self_rss_budget_mb=50,
            detail_level="auto",
            proc_root=proc_root,
            cgroup_root=Path("/nonexistent-cgroup-root-for-tests"),
            ring=ring,
            instance_ring=instance_ring,
        )
        return sampler, ring, instance_ring

    def test_the_warning_names_the_read_failure_cause(self, tmp_path, caplog):
        """The warning carries the reader's REASON token.

        The proc root EXISTS but carries no ``self/stat`` — the real
        reader path, on Linux and on macOS alike (the portable
        fallback only engages for an ABSENT proc root). The reader
        therefore reports ``proc-root-unreadable`` and the sampler
        warning must name it.
        """
        broken_proc = tmp_path / "proc"
        broken_proc.mkdir()
        sampler, _ring, _instance_ring = self._sampler(broken_proc)
        with caplog.at_level("WARNING"):
            assert sampler.run_once() is None
        warnings = [
            rec
            for rec in caplog.records
            if "no daemon snapshot" in rec.getMessage()
        ]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "proc-root-unreadable" in message, (
            f"the degradation warning must name the CAUSE: {message!r}"
        )

    def test_the_warning_rearms_after_a_recovery(self, monkeypatch, caplog):
        """Fail → clean → fail warns TWICE (the old flag stayed silent).

        The old one-shot guard made a SECOND degradation invisible for
        the life of the process — exactly the class of blindness this
        whole pack exists to close.
        """
        state = {"fail": True}

        def _read(*a, **k):
            return None if state["fail"] else _GOOD_SNAPSHOT

        monkeypatch.setattr(sampler_mod, "read_proc_snapshot", _read)
        sampler, _ring, _instance_ring = self._sampler(_ABSENT_PROC_ROOT)
        with caplog.at_level("INFO"):
            sampler.run_once()          # failure #1 → warns
            state["fail"] = False
            sampler.run_once()          # clean → recovery + re-arm
            state["fail"] = True
            sampler.run_once()          # failure #2 → MUST warn again
        warnings = [
            rec
            for rec in caplog.records
            if "no daemon snapshot" in rec.getMessage()
        ]
        assert len(warnings) == 2, (
            "a second degradation must warn again after a recovery"
        )
        recoveries = [
            rec
            for rec in caplog.records
            if "RECOVERED" in rec.getMessage()
        ]
        assert len(recoveries) == 1, "the recovery must be logged once"

    def test_a_long_streak_rewarns_every_n_failures(self, monkeypatch, caplog):
        """A permanently dark host stays visible (re-warn every N=50)."""
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        sampler, _ring, _instance_ring = self._sampler(_ABSENT_PROC_ROOT)
        every = sampler_mod.READ_FAILURE_REWARN_EVERY
        with caplog.at_level("WARNING"):
            for _ in range(every * 2):
                sampler.run_once()
        warnings = [
            rec
            for rec in caplog.records
            if "no daemon snapshot" in rec.getMessage()
        ]
        # n=1 warns, n=50 warns, n=100 warns → three warnings in 100
        # consecutive failures (NOT one, NOT one per tick).
        assert len(warnings) == 3, (
            f"expected 3 warnings in {every * 2} consecutive failures, "
            f"got {len(warnings)}"
        )


# ──────────────────────────────────────────────────────────────────────────
# B5(i) — the self-throttle latch must be RELEASABLE
# ──────────────────────────────────────────────────────────────────────────


class TestSelfThrottleLatch:
    """B5(i) — one transient hang must not degrade sampling forever.

    ``counters.self_throttled`` was set once and NEVER reset: a single
    hung ``ps`` (5 s timeout x 2 calls = 10 s blocked tick against a
    ~300 ms budget) latched the throttle for the LIFETIME of the
    process — the doubled interval and the disabled deep scan never
    came back. The latch is now released once the tick-cost EMA has
    recovered under the BASE budget.
    """

    def test_the_throttle_latch_is_released_when_the_ema_recovers(self):
        """A throttled sampler recovers its interval and deep scan."""
        clock = {"t": 0.0}

        def fake_clock() -> float:
            clock["t"] += 0.5
            return clock["t"]

        ring = TierRing(tier="raw", maxlen=16)
        sampler = Sampler(
            sample_seconds=30,
            self_cpu_budget_pct=1.0,
            self_rss_budget_mb=50,
            detail_level="auto",
            proc_root=_ABSENT_PROC_ROOT,
            cgroup_root=Path("/nonexistent-cgroup-root-for-tests"),
            ring=ring,
            wall_clock=fake_clock,
        )
        # Each tick costs 1.0 s (two clock calls) against a 0.3 s
        # budget → the EMA climbs above the budget and the throttle
        # fires.
        for _ in range(6):
            sampler.run_once()
        assert sampler.counters.self_throttled is True
        assert sampler._effective_interval_s > 30.0
        assert sampler._deep_scan_enabled is False

        # Now the ticks become CHEAP (0.002 s per tick): the EMA
        # decays under the base budget and the latch must release.
        cheap = {"t": clock["t"]}

        def cheap_clock() -> float:
            cheap["t"] += 0.001
            return cheap["t"]

        sampler._wall_clock = cheap_clock
        for _ in range(80):
            sampler.run_once()
        assert sampler.counters.self_throttled is False, (
            "the self-throttle latch was never released — one transient "
            "hang degrades the sampling cadence for the process lifetime"
        )
        assert sampler._effective_interval_s == 30.0
        assert sampler._deep_scan_enabled is True

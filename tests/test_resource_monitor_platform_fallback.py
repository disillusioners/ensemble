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

# A proc root that certainly does not exist — stands in for a host
# with no /proc at all (macOS).
_ABSENT_PROC_ROOT = Path("/nonexistent-proc-root-for-resource-monitor-tests")


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
        """
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        sampler, ring, _instance_ring = self._sampler(_ABSENT_PROC_ROOT)
        assert sampler.run_once() is None
        assert len(ring) == 0
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

"""LINUX-FACING LADDER SEMANTICS — a failed platform read does NOT
demote the sampling ladder.

This pack pins ONE behaviour change that the macOS dark-tier repair
introduced, on the seam that matters most on LINUX (where ``/proc`` IS
authoritative and a read failure means a genuinely broken host, not a
platform difference):

    A failed platform read NO LONGER DEMOTES the sampling ladder.

History (A/B-verified by the reviewer against the base commit):

  * BASE: a failed platform read walked the ladder down
    ``full -> reduced -> minimal``. On a host whose ``/proc`` was
    broken the sampler therefore degraded to the CHEAPEST sampling
    tier and STAYED there — the ladder never came back, so the
    recovery was permanent blindness.
  * HEAD: only an OVERRUN demotes the ladder. A failed READ leaves
    the ladder untouched.

Why the asymmetry is correct (the reviewer's adjudication): the
ladder's demotion is driven by the tick COST, and a failed read
carries NO cost signal — demoting on a read failure is a SYMMETRIC
rule that would stick the ladder at ``minimal`` forever (the same
"latch for the lifetime of the process" class the self-throttle latch
had). The cost-driven demotion (overrun) is unchanged and still
walks the ladder down.

The test is deliberately PLATFORM-NEUTRAL: the read failure is
MOCKED (the ``read_proc_snapshot`` seam is replaced), so it runs
green — and asserts the same thing — on macOS (the deployment host)
and on Linux (the platform whose semantics it pins). The docstring
and the module name say so explicitly: this is LINUX-FACING ladder
semantics, verified on any host.

FENCE DISCIPLINE: no test touches the real ``/proc``, the real
process state or the DB. The synthetic ``/proc`` tree lives under
pytest ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.services.resource_monitor import Sampler, TierRing
from daemon.services.resource_monitor import sampler as sampler_mod
from daemon.services.resource_monitor.sampler import LADDER_DEFAULT

_ABSENT_PROC_ROOT = Path("/nonexistent-proc-root-for-resource-monitor-tests")


def _build_tree(root: Path) -> tuple[Path, Path]:
    """A synthetic ``/proc`` + cgroup v2 tree (Linux-shaped)."""
    proc = root / "proc"
    cgroup_root = root / "sys" / "fs" / "cgroup"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "cgroup").write_text("0::/system.slice/test.service\n")
    (proc / "self" / "stat").write_text(
        "1234 (python) R 1 1234 1234 0 -1 4194304 100 0 0 0 50 25 0 0 20 0 "
        "1 0 9999 1000000 134 18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 "
        "17 0 0 0 0 0 0 0 0 0 0 0 0 0"
    )
    (proc / "self" / "status").write_text(
        "Name:\tpython\nVmRSS:\t131072 kB\nVmHWM:\t262144 kB\n"
        "VmSize:\t4096000 kB\nVmData:\t65536 kB\nThreads:\t4\n"
    )
    (proc / "self" / "statm").write_text("1024000 131072 100 0 0 0 0")
    fd = proc / "self" / "fd"
    fd.mkdir()
    for name in ("0", "2", "5"):
        (fd / name).touch()
    cg = cgroup_root / "system.slice" / "test.service"
    cg.mkdir(parents=True)
    (cg / "memory.current").write_text("5368709120")
    (cg / "memory.peak").write_text("7516192768")
    (cg / "memory.stat").write_text(
        "anon 2147483648\nfile 1073741824\nslab 268435456\n"
    )
    return proc, cgroup_root


@pytest.fixture
def tree(tmp_path):
    return _build_tree(tmp_path)


class TestLinuxFacingLadderSemantics:
    """The ladder demotes on COST (overrun), never on a READ failure."""

    def _sampler(self, tree, *, clock=None, interval_override=None):
        proc, cg_root = tree
        ring = TierRing(tier="raw", maxlen=64)
        kwargs = {}
        if clock is not None:
            kwargs["wall_clock"] = clock
        s = Sampler(
            sample_seconds=30,
            self_cpu_budget_pct=1.0,
            self_rss_budget_mb=50,
            detail_level="auto",
            proc_root=proc,
            cgroup_root=cg_root,
            ring=ring,
            **kwargs,
        )
        if interval_override is not None:
            s._effective_interval_s = interval_override
        return s, ring

    def test_a_failed_platform_read_does_not_demote_the_ladder(
        self, tree, monkeypatch
    ):
        """LINUX-FACING LADDER SEMANTICS: a failed READ never demotes.

        On Linux a failed read means a broken ``/proc``. The BASE
        implementation walked the ladder down to ``minimal`` and left
        it there — the degradation was permanent even after the host
        recovered. The ladder must stay at its default level across
        an arbitrarily long streak of failed reads.
        """
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        s, _ring = self._sampler(tree)
        for _ in range(12):
            assert s.run_once() is None
        assert s.counters.ladder_level == LADDER_DEFAULT, (
            "a failed platform read must NOT demote the ladder "
            f"(got {s.counters.ladder_level!r})"
        )
        assert s.counters.ticks_skipped == 12
        assert s.counters.missed_samples == 12
        # The drops are still RECORDED — the ladder just does not move.
        assert s.counters.ticks == 0

    def test_an_overrun_still_demotes_the_ladder(self, tree):
        """The contrast: the COST-driven demotion is unchanged.

        Without this leg the test above could pass on a sampler whose
        ladder never moves at all — the demotion must still happen on
        the overrun path (that is the whole point of the ladder).
        """
        clock = {"t": 0.0}

        def slow_clock():
            clock["t"] += 10.0
            return clock["t"]

        s, _ring = self._sampler(
            tree, clock=slow_clock, interval_override=0.02
        )
        assert s.run_once() is None
        assert s.counters.ladder_level == "reduced"
        s.run_once()
        assert s.counters.ladder_level == "minimal"

    def test_the_ladder_recovers_to_the_default_after_clean_ticks(self, tree):
        """A demoted ladder is re-promoted by clean ticks (hysteresis)."""
        clock = {"t": 0.0}

        def slow_clock():
            clock["t"] += 10.0
            return clock["t"]

        s, _ring = self._sampler(
            tree, clock=slow_clock, interval_override=0.02
        )
        s.run_once()
        assert s.counters.ladder_level == "reduced"

        cheap = {"t": clock["t"]}

        def cheap_clock():
            cheap["t"] += 0.001
            return cheap["t"]

        s._wall_clock = cheap_clock
        s._effective_interval_s = 30.0
        for _ in range(10):
            s.run_once()
        assert s.counters.ladder_level == LADDER_DEFAULT

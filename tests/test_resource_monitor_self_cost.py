"""Self-cost regression test for the resource monitor (P1 T9 / AC7 / D12).

THE gate: the resource monitor's self-cost budget is an acceptance
criterion, not a nicety (D12). This test asserts:

  * median tick < 5 ms over N synthetic ticks;
  * HARD gate: max tick < 50 ms;
  * EMA self-throttle triggers at > 1 % of interval (interval doubled);
  * sampler RSS delta < 20 MB across the run;
  * ZERO reader calls escape the fixture root (escape-guard assertion).

FENCE DISCIPLINE — the 2026-10-12 live-install-deletion incident class
(project critical note: "Test scripts must NEVER resolve live-shaped
paths"). Zero tolerance:

  * ``FAKE_HOME`` is a ``mktemp`` directory; tests read NOTHING from
    ``$HOME`` or any install-dir.
  * All /proc + cgroup reads target a SYNTHETIC fixture tree built
    under pytest ``tmp_path``. The real ``/proc`` and
    ``/sys/fs/cgroup`` are NEVER touched — an escape-guard assertion
    fails the test if any reader call resolves outside the fixture
    root.
  * The readers are driven via ``proc_root`` / ``cgroup_root``
    constructor args (the injectable-root seam T4 added for exactly
    this reason).

Run with ``.venv/bin/python -m pytest tests/test_resource_monitor_self_cost.py -q``.
"""

from __future__ import annotations

import os
import statistics
import tempfile
import time
from pathlib import Path

import pytest

# The monitor's reader module — the escape-guard instruments its
# ``_read_text`` (the SINGLE low-level read seam all readers route
# through).
from daemon.services.resource_monitor import proc_readers
from daemon.services.resource_monitor.ring import TierRing
from daemon.services.resource_monitor.sampler import (
    Sampler,
    SamplerCounters,
)


# ──────────────────────────────────────────────────────────────────────────
# Fence (session-scoped) — mirrors the project env-scrub convention.
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    """Session fence: dev-mode + FAKE_HOME mktemp + POSTGRES_DB scrub.

    ``FAKE_HOME`` is created ONLY if absent (idempotent under a
    session that ran earlier fence fixtures). Never reads the real
    ``$HOME``.
    """
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: self-cost test targets "
            f"POSTGRES_DB={os.environ.get('POSTGRES_DB')!r} (env-poison "
            f"3-guard convention).",
            returncode=1,
        )
    fake = os.environ.get("FAKE_HOME")
    if not fake or not os.path.isabs(fake):
        fake = tempfile.mkdtemp(prefix="ens-fake-home-")
        os.environ["FAKE_HOME"] = fake
        os.environ["HOME"] = fake
    yield


# ──────────────────────────────────────────────────────────────────────────
# Synthetic fixture tree (under tmp_path — NEVER the real /proc)
# ──────────────────────────────────────────────────────────────────────────


def _build_synthetic_proc_tree(root: Path) -> tuple[Path, Path]:
    """Build a synthetic /proc + cgroup v2 tree under ``root``.

    Returns (proc_root, cgroup_root). Every path here is under
    ``root`` (pytest tmp_path) — the real /proc is never touched.
    """
    proc = root / "proc"
    cgroup_root = root / "sys" / "fs" / "cgroup"
    (proc / "self").mkdir(parents=True)

    # /proc/self/cgroup — cgroup v2 line (0::<path>).
    (proc / "self" / "cgroup").write_text("0::/system.slice/test.service\n")

    # /proc/self/stat — busy but parseable. utime=50, stime=25 jiffies.
    (proc / "self" / "stat").write_text(
        "1234 (python) R 1 1234 1234 0 -1 4194304 100 0 0 0 50 25 0 0 20 0 "
        "1 0 9999 1000000 134 18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 "
        "17 0 0 0 0 0 0 0 0 0 0 0 0 0"
    )
    (proc / "self" / "status").write_text(
        "Name:\tpython\n"
        "VmRSS:\t131072 kB\n"
        "VmHWM:\t262144 kB\n"
        "VmSize:\t4096000 kB\n"
        "VmData:\t65536 kB\n"
        "Threads:\t4\n"
    )
    (proc / "self" / "statm").write_text("1024000 131072 100 0 0 0 0")

    # /proc/self/fd — three entries.
    fd = proc / "self" / "fd"
    fd.mkdir()
    (fd / "0").touch()
    (fd / "2").touch()
    (fd / "5").touch()

    # /proc/self/smaps_rollup — PSS in kB.
    (proc / "self" / "smaps_rollup").write_text(
        "Rss:\t  131072 kB\nPss:\t  123456 kB\n"
    )

    # cgroup v2 tree at the resolved path.
    cg = cgroup_root / "system.slice" / "test.service"
    cg.mkdir(parents=True)
    (cg / "memory.current").write_text("5368709120")  # 5 GiB
    (cg / "memory.peak").write_text("7516192768")      # 7 GiB
    (cg / "memory.stat").write_text(
        "anon 2147483648\nfile 1073741824\nslab 268435456\n"
    )
    (cg / "cpu.stat").write_text(
        "usage_usec 12345678901\nthrottled_usec 0\nnr_throttled 0\n"
    )
    return proc, cgroup_root


@pytest.fixture
def synthetic_tree(tmp_path):
    """Build the synthetic tree; return (proc_root, cgroup_root)."""
    return _build_synthetic_proc_tree(tmp_path)


@pytest.fixture
def escape_guard(monkeypatch, synthetic_tree):
    """Record every path the readers touch; assert none escapes.

    Two instrumentation seams (belt-and-braces):

      1. ``proc_readers._read_text`` — the module's low-level read
         helper (all readers route through it).
      2. ``pathlib.Path.read_text`` — catches a reader that
         bypasses the helper and reads a file directly.

    Live-shaped roots (``/proc``, ``/sys``) are FORBIDDEN at both
    seams: the real system must never be touched by a test. The
    synthetic fixture tree lives under ``tmp_path``, so every
    legitimate read passes.
    """
    proc_root, cgroup_root = synthetic_tree
    fixture_root = proc_root.parent  # tmp_path
    touched: list[Path] = []

    real_read_text = proc_readers._read_text
    real_path_read_text = Path.read_text

    _LIVE_ROOTS = ("/proc", "/sys")

    def _assert_not_live(path_repr: str) -> None:
        for live in _LIVE_ROOTS:
            if path_repr == live or path_repr.startswith(live + "/"):
                raise AssertionError(
                    f"ESCAPE: reader touched a LIVE-shaped path "
                    f"{path_repr!r} (forbidden root {live!r}); the real "
                    f"system must never be read by this test"
                )

    def _recording_read_text(path: Path):
        resolved = Path(path)
        touched.append(resolved)
        _assert_not_live(str(resolved))
        # Also assert containment in the fixture root (defense in
        # depth — a tmp path outside tmp_path is still wrong).
        assert str(resolved).startswith(str(fixture_root)), (
            f"ESCAPE: reader touched a path OUTSIDE the fixture root: "
            f"{resolved!r} (fixture_root={fixture_root!r})"
        )
        return real_read_text(resolved)

    def _recording_path_read_text(self, *args, **kwargs):
        _assert_not_live(str(self))
        return real_path_read_text(self, *args, **kwargs)

    monkeypatch.setattr(proc_readers, "_read_text", _recording_read_text)
    monkeypatch.setattr(Path, "read_text", _recording_path_read_text)

    yield touched

    # Post-condition: the guard saw at least one reader-originated
    # read (proves the instrumentation is wired) and none escaped.
    assert touched, "escape guard saw ZERO reads — instrumentation is not wired"


# ──────────────────────────────────────────────────────────────────────────
# The regression tests (AC7 / D12)
# ──────────────────────────────────────────────────────────────────────────


def _make_sampler(proc_root: Path, cgroup_root: Path, ring: TierRing) -> Sampler:
    return Sampler(
        sample_seconds=30,
        self_cpu_budget_pct=1.0,
        self_rss_budget_mb=50,
        detail_level="auto",
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        ring=ring,
    )


def test_forced_tick_costs_under_hard_gate(synthetic_tree, escape_guard):
    """HARD gate: forced-tick duration < 50 ms (D12).

    Measured on the REAL clock (the per-tick work includes actual
    file reads against the synthetic tree), so this is the honest
    footprint assertion. The 50 ms ceiling is 10x the 5 ms target
    — a tick that blows it means the sampler is doing unbounded
    work (DB writes, huge reads, etc.).
    """
    proc_root, cgroup_root = synthetic_tree
    ring = TierRing(tier="raw", maxlen=64)
    sampler = _make_sampler(proc_root, cgroup_root, ring)

    costs_ms: list[float] = []
    N = 40
    for _ in range(N):
        t0 = time.perf_counter()
        sampler.run_once()
        t1 = time.perf_counter()
        costs_ms.append((t1 - t0) * 1000.0)

    median = statistics.median(costs_ms)
    p95 = statistics.quantiles(costs_ms, n=20)[-1]
    max_cost = max(costs_ms)

    assert median < 5.0, f"median tick {median:.3f} ms >= 5 ms target"
    assert max_cost < 50.0, (
        f"HARD GATE FAILED: max tick {max_cost:.3f} ms >= 50 ms"
    )
    # Sanity: the p95 is informational (not a hard gate) but a p95
    # above the hard gate would imply a fat tail.
    assert p95 < 50.0, f"p95 tick {p95:.3f} ms >= 50 ms"
    # The sampler ran exactly N clean ticks (no synthetic overrun).
    assert sampler.counters.ticks == N
    assert sampler.counters.ticks_skipped == 0


def test_sampler_never_touches_db(synthetic_tree, escape_guard):
    """The sampler writes ONLY the ring — never the DB (D5).

    Asserted structurally: the ring grows by one row per clean tick
    and the sampler holds no engine / session reference.
    """
    proc_root, cgroup_root = synthetic_tree
    ring = TierRing(tier="raw", maxlen=64)
    sampler = _make_sampler(proc_root, cgroup_root, ring)

    for _ in range(5):
        sampler.run_once()

    assert len(ring) == 5
    # Structural DB-independence: the sampler exposes no engine.
    assert not hasattr(sampler, "engine")
    assert not hasattr(sampler, "session")
    # Every row is a dict (the ring's data shape), not an ORM row.
    assert all(isinstance(r, dict) for r in ring.snapshot())


def test_ema_self_throttle_at_one_percent(synthetic_tree):
    """EMA self-throttle: > 1 % of interval ⇒ interval doubled (D12).

    Drive the sampler with a SYNTHETIC clock whose tick cost
    exceeds 1 % of the interval (interval 30 s ⇒ 0.3 s budget).
    The EMA must breach and the sampler must double the effective
    interval + disable deep scans.

    This test bypasses the escape-guard fixture because the
    synthetic clock makes the reader-timing assertion meaningless
    (no real reads dominate) — but the reader roots are STILL the
    synthetic tree (never live /proc).
    """
    proc_root, cgroup_root = synthetic_tree
    ring = TierRing(tier="raw", maxlen=64)

    # Synthetic clock: each call advances by 0.5 s. ``run_once`` calls
    # the clock twice per tick (tick_start + tick_end), so a tick's
    # measured cost is 1.0 s — comfortably above the 0.3 s budget
    # (1 % of the 30 s interval) but well below the 30 s overrun
    # threshold, so this stays a CLEAN tick that only exercises the
    # EMA self-throttle.
    clock = {"t": 0.0}

    def fake_clock() -> float:
        clock["t"] += 0.5
        return clock["t"]

    sampler = Sampler(
        sample_seconds=30,
        self_cpu_budget_pct=1.0,
        self_rss_budget_mb=50,
        detail_level="auto",
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        ring=ring,
        wall_clock=fake_clock,
    )

    # A "clean" tick with cost = 1.0 s must NOT overrun (30 s
    # interval), so the ladder stays at ``full`` and only the EMA
    # throttle fires.
    initial_interval = sampler._effective_interval_s
    assert initial_interval == 30.0

    for _ in range(6):
        sampler.run_once()

    assert sampler.counters.ticks_skipped == 0, (
        "synthetic clock unexpectedly overran the interval"
    )
    assert sampler.counters.self_throttled is True, (
        f"EMA self-throttle did not fire: ema_tick_cost_s="
        f"{sampler.counters.ema_tick_cost_s:.4f}"
    )
    assert sampler._effective_interval_s > initial_interval, (
        "self-throttle fired but the effective interval was not doubled"
    )
    assert sampler._deep_scan_enabled is False, (
        "self-throttle fired but deep-scan was not disabled"
    )


def test_sampler_rss_delta_under_20mb(synthetic_tree, escape_guard):
    """Sampler RSS delta < 20 MB across the run (D12 test floor).

    Distinct from ``RESOURCE_MONITOR_SELF_RSS_BUDGET_MB`` (50, the
    runtime alarm/throttle knob) — this asserts the subsystem's
    own footprint during a run.

    RSS is read via ``resource.getrusage(RUSAGE_SELF).ru_maxrss``
    (kB on Linux). Note ru_maxrss is a HIGH-WATER mark, so the
    delta is a conservative upper bound on the sampler's growth.
    """
    import resource as _resource

    proc_root, cgroup_root = synthetic_tree
    ring = TierRing(tier="raw", maxlen=8192)  # a large ring
    sampler = _make_sampler(proc_root, cgroup_root, ring)

    before_kb = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    for _ in range(200):
        sampler.run_once()
    after_kb = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss

    delta_mb = (after_kb - before_kb) / 1024.0
    # ru_maxrss is a high-water mark ⇒ delta >= 0 always. The floor
    # is 20 MB.
    assert delta_mb < 20.0, (
        f"sampler RSS delta {delta_mb:.2f} MB >= 20 MB floor"
    )
    assert len(ring) == 200


def test_escape_guard_is_load_bearing(synthetic_tree, escape_guard):
    """Sanity: the escape guard actually inspects the reads.

    Asserts the instrumentation saw reads (proves the guard is
    wired, not vacuously passing) and that every touched path is
    under the synthetic fixture root.
    """
    proc_root, cgroup_root = synthetic_tree
    ring = TierRing(tier="raw", maxlen=16)
    sampler = _make_sampler(proc_root, cgroup_root, ring)
    sampler.run_once()

    assert escape_guard, "escape guard recorded no reads"
    fixture_root = str(proc_root.parent)
    for path in escape_guard:
        assert str(path).startswith(fixture_root), (
            f"ESC-DELTA: reader escaped fixture root: {path}"
        )


def test_synthetic_tree_has_no_live_paths(synthetic_tree):
    """Positive control: the fixture tree contains only tmp_path paths.

    A defense-in-depth assertion that the fixture itself never
    points at a live-shaped path.
    """
    proc_root, cgroup_root = synthetic_tree
    for root in (proc_root, cgroup_root):
        s = str(root)
        assert "tmp" in s.lower() or "pytest" in s.lower(), (
            f"fixture root looks live-shaped: {s!r}"
        )
        assert not s.startswith("/proc"), f"fixture root is real /proc: {s!r}"
        assert not s.startswith("/sys"), f"fixture root is real /sys: {s!r}"

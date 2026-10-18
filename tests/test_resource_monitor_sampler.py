"""Sampler test pack (P1 T10 / AC1, AC2, AC5, AC6).

Covers:

  * AC1 — ``ResourceMonitorConfig`` accepts all 12 §2.9 keys via env
    + yaml; bound validation (``sample_seconds`` 15/60,
    ``profile_timebox_s`` 10/120); ``enabled=false`` disables
    construction.
  * AC2 — the ``resource_monitor:`` yaml block is a peer of
    ``limits:``; root Settings exposes ``.resource_monitor``.
  * AC5 — readers parse synthetic cgroup / /proc fixtures; a
    missing / degenerate cgroup degrades to daemon-only.
  * AC6 — tick loop: overrun ⇒ skip + ``missed_samples++`` +
    ladder demote; N clean ticks ⇒ re-promote; no backlog.

FENCE DISCIPLINE (2026-10-12 critical note): the synthetic tree is
built under pytest ``tmp_path``; the real ``/proc`` and
``/sys/fs/cgroup`` are NEVER resolved. ``FAKE_HOME`` is mktemp.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from daemon.services.resource_monitor import (
    LADDER_DEFAULT,
    Sampler,
    SamplerCounters,
    TierRing,
    read_proc_snapshot,
)
from daemon.services.resource_monitor import charge as charge_mod
from daemon.services.resource_monitor.pid_registry import (
    reset_pid_registry_for_tests,
)
from daemon.services.resource_monitor.sampler import (
    ALLOWED_T2_ROW_KEYS,
    OVERFLOW_SENTINEL,
    RE_PROMOTE_CLEAN_TICKS,
)


# ──────────────────────────────────────────────────────────────────────────
# Fence
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}",
            returncode=1,
        )
    fake = os.environ.get("FAKE_HOME")
    if not fake or not os.path.isabs(fake):
        fake = tempfile.mkdtemp(prefix="ens-fake-home-")
        os.environ["FAKE_HOME"] = fake
        os.environ["HOME"] = fake
    yield


# ──────────────────────────────────────────────────────────────────────────
# Synthetic fixtures
# ──────────────────────────────────────────────────────────────────────────


def _build_tree(root: Path) -> tuple[Path, Path]:
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
    (cg / "cpu.stat").write_text(
        "usage_usec 12345678901\nthrottled_usec 0\nnr_throttled 0\n"
    )
    return proc, cgroup_root


@pytest.fixture
def tree(tmp_path):
    return _build_tree(tmp_path)


# ──────────────────────────────────────────────────────────────────────────
# AC1 / AC2 — config
# ──────────────────────────────────────────────────────────────────────────


class TestResourceMonitorConfig:
    def test_defaults_all_12_keys(self):
        from daemon.config import ResourceMonitorConfig

        c = ResourceMonitorConfig()
        assert c.enabled is True
        assert c.sample_seconds == 30
        assert c.retention_raw_minutes == 2880
        assert c.retention_minute_hours == 336
        assert c.retention_hour_days == 180
        assert c.detail_level == "auto"
        assert c.instance_cap == 50
        assert c.self_cpu_budget_pct == 1.0
        assert c.self_rss_budget_mb == 50
        assert c.profiling_enabled is False
        assert c.profile_timebox_s == 30
        assert c.flush_batch_seconds == 60

    def test_env_prefix_is_resource_monitor(self):
        from daemon.config import ResourceMonitorConfig

        assert (
            ResourceMonitorConfig.model_config.get("env_prefix")
            == "RESOURCE_MONITOR_"
        )

    def test_env_override_all_keys(self, monkeypatch):
        from daemon.config import ResourceMonitorConfig

        monkeypatch.setenv("RESOURCE_MONITOR_ENABLED", "false")
        monkeypatch.setenv("RESOURCE_MONITOR_SAMPLE_SECONDS", "45")
        monkeypatch.setenv("RESOURCE_MONITOR_RETENTION_RAW_MINUTES", "1440")
        monkeypatch.setenv("RESOURCE_MONITOR_RETENTION_MINUTE_HOURS", "168")
        monkeypatch.setenv("RESOURCE_MONITOR_RETENTION_HOUR_DAYS", "90")
        monkeypatch.setenv("RESOURCE_MONITOR_DETAIL_LEVEL", "minimal")
        monkeypatch.setenv("RESOURCE_MONITOR_INSTANCE_CAP", "12")
        monkeypatch.setenv("RESOURCE_MONITOR_SELF_CPU_BUDGET_PCT", "2.5")
        monkeypatch.setenv("RESOURCE_MONITOR_SELF_RSS_BUDGET_MB", "80")
        monkeypatch.setenv("RESOURCE_MONITOR_PROFILING_ENABLED", "true")
        monkeypatch.setenv("RESOURCE_MONITOR_PROFILE_TIMEBOX_S", "45")
        monkeypatch.setenv("RESOURCE_MONITOR_FLUSH_BATCH_SECONDS", "30")

        c = ResourceMonitorConfig()
        assert c.enabled is False
        assert c.sample_seconds == 45
        assert c.retention_raw_minutes == 1440
        assert c.retention_minute_hours == 168
        assert c.retention_hour_days == 90
        assert c.detail_level == "minimal"
        assert c.instance_cap == 12
        assert c.self_cpu_budget_pct == 2.5
        assert c.self_rss_budget_mb == 80
        assert c.profiling_enabled is True
        assert c.profile_timebox_s == 45
        assert c.flush_batch_seconds == 30

    def test_sample_seconds_bound_low(self):
        from daemon.config import ResourceMonitorConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ResourceMonitorConfig(sample_seconds=10)

    def test_sample_seconds_bound_high(self):
        from daemon.config import ResourceMonitorConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ResourceMonitorConfig(sample_seconds=61)

    def test_profile_timebox_bounds(self):
        from daemon.config import ResourceMonitorConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ResourceMonitorConfig(profile_timebox_s=5)
        with pytest.raises(ValidationError):
            ResourceMonitorConfig(profile_timebox_s=121)

    def test_root_settings_exposes_resource_monitor(self):
        from daemon.config import Config

        cfg = Config()
        assert hasattr(cfg, "resource_monitor")
        assert cfg.resource_monitor.sample_seconds == 30

    def test_yaml_block_is_comment_only(self):
        """The config.yaml resource_monitor: block MUST carry no literal
        values (the LIMITS_LLM_CONCURRENCY trap: yaml init-kwargs beat
        env vars in pydantic-settings).
        """
        import yaml

        repo = Path(__file__).resolve().parent.parent
        cfg_path = repo / "config.yaml"
        raw = yaml.safe_load(cfg_path.read_text())
        # The block is either absent entirely or comment-only (which
        # means safe_load sees NO `resource_monitor` key at all).
        assert "resource_monitor" not in (raw or {}), (
            "config.yaml has a literal resource_monitor: block — this "
            "silently kills the RESOURCE_MONITOR_* env tier"
        )

    def test_enabled_false_short_circuits_build(self):
        """enabled=false ⇒ the facade is built disabled (AC1)."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from daemon.config import ResourceMonitorConfig
        from daemon.services.resource_monitor import build_resource_monitor

        tmp = tempfile.mkdtemp(prefix="rm-facade-off-")
        engine = create_engine(
            f"sqlite:///{tmp}/t.db",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        mon = build_resource_monitor(
            config_resource_monitor=ResourceMonitorConfig(enabled=False),
            engine=engine,
        )
        assert mon.enabled is False
        assert mon.boot_error


# ──────────────────────────────────────────────────────────────────────────
# AC5 — readers
# ──────────────────────────────────────────────────────────────────────────


class TestReaders:
    def test_parses_synthetic_fixture(self, tree):
        proc, cg_root = tree
        snap = read_proc_snapshot(proc, cg_root)
        assert snap is not None
        assert snap.daemon.rss_kb == 131072
        assert snap.daemon.hw_peak_kb == 262144
        assert snap.daemon.threads == 4
        assert snap.daemon.fds == 3
        assert snap.daemon.cpu_user_usec == 50 * 10_000
        assert snap.daemon.cpu_sys_usec == 25 * 10_000
        assert snap.cgroup is not None
        assert snap.cgroup.memory.current_mb == 5120
        assert snap.cgroup.memory.peak_mb == 7168
        assert snap.cgroup.memory.anon_mb == 2048
        assert snap.cgroup.memory.file_mb == 1024
        assert snap.cgroup.memory.slab_mb == 256
        assert snap.cgroup.cpu.usage_usec == 12345678901

    def test_missing_cgroup_degrades(self, tmp_path):
        """No /proc/self/cgroup ⇒ cgroup None (daemon-only sampling)."""
        proc = tmp_path / "proc"
        (proc / "self").mkdir(parents=True)
        (proc / "self" / "stat").write_text(
            "1 (python) R 1 1 1 0 -1 0 1 1 1 1 1 1 0 0 20 0 1 0 1 1 1 0 0 0 0 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        snap = read_proc_snapshot(proc, tmp_path / "cgroup")
        assert snap is not None
        assert snap.cgroup is None

    def test_cgroup_v1_format_degrades(self, tmp_path):
        """A cgroup v1 line (no ``0::``) ⇒ cgroup None."""
        proc = tmp_path / "proc"
        (proc / "self").mkdir(parents=True)
        (proc / "self" / "cgroup").write_text("12:cpuset:/\n11:memory:/\n")
        (proc / "self" / "stat").write_text(
            "1 (python) R 1 1 1 0 -1 0 1 1 1 1 1 1 0 0 20 0 1 0 1 1 1 0 0 0 0 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        snap = read_proc_snapshot(proc, tmp_path / "cgroup")
        assert snap is not None
        assert snap.cgroup is None

    def test_missing_stat_returns_none(self, tmp_path):
        """No /proc/self/stat ⇒ None (the sampler skips, never crashes)."""
        proc = tmp_path / "proc"
        (proc / "self").mkdir(parents=True)
        assert read_proc_snapshot(proc, tmp_path / "cgroup") is None

    def test_child_read(self, tmp_path):
        proc = tmp_path / "proc"
        (proc / "self").mkdir(parents=True)
        (proc / "self" / "stat").write_text(
            "1 (python) R 1 1 1 0 -1 0 1 1 1 1 1 1 0 0 20 0 1 0 1 1 1 0 0 0 0 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        child = proc / "5678"
        child.mkdir()
        child.joinpath("stat").write_text(
            "5678 (bash) R 1 5678 5678 0 -1 0 100 0 0 0 10 5 0 0 20 0 1 0 "
            "8888 500000 16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        (child / "status").write_text("Name:\tbash\nVmRSS:\t8192 kB\n")
        snap = read_proc_snapshot(proc, tmp_path / "cgroup", child_pids=[5678])
        assert snap is not None
        assert len(snap.children) == 1
        assert snap.children[0].pid == 5678
        assert snap.children[0].cpu_user_usec == 10 * 10_000

    def test_comm_with_spaces_and_parens(self, tmp_path):
        """The ``(comm)`` field may contain spaces + parens — the
        parser locates the LAST ``)``.
        """
        proc = tmp_path / "proc"
        (proc / "self").mkdir(parents=True)
        (proc / "self" / "stat").write_text(
            "1 (weird (name) here) R 1 1 1 0 -1 0 1 1 1 1 7 3 0 0 20 0 2 0 "
            "999 1000 10 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        snap = read_proc_snapshot(proc, tmp_path / "cgroup")
        assert snap is not None
        # utime=7, stime=3
        assert snap.daemon.cpu_user_usec == 7 * 10_000
        assert snap.daemon.cpu_sys_usec == 3 * 10_000


# ──────────────────────────────────────────────────────────────────────────
# AC6 — tick / ladder
# ──────────────────────────────────────────────────────────────────────────


class TestTickLadder:
    def _sampler(self, tree, *, clock=None, interval_override=None):
        proc, cg_root = tree
        ring = TierRing(tier="raw", maxlen=200)
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

    def test_clean_ticks_stack_rows(self, tree):
        s, ring = self._sampler(tree)
        for _ in range(5):
            s.run_once()
        assert len(ring) == 5
        assert s.counters.ticks == 5
        assert s.counters.ticks_skipped == 0
        assert s.counters.ladder_level == LADDER_DEFAULT

    def test_overrun_skips_and_demotes(self, tree):
        """AC6: overrun ⇒ skip + missed_samples++ + demote one level."""
        clock = {"t": 0.0}

        def slow_clock():
            clock["t"] += 10.0  # 10 s/call ⇒ 20 s/tick > 0.02 s interval
            return clock["t"]

        s, ring = self._sampler(
            tree, clock=slow_clock, interval_override=0.02
        )
        snap = s.run_once()
        assert snap is None  # skipped
        assert len(ring) == 0  # no row written
        assert s.counters.ticks_skipped == 1
        assert s.counters.missed_samples == 1
        assert s.counters.ladder_level == "reduced"  # demoted from full

        s.run_once()
        assert s.counters.ladder_level == "minimal"

        # At the bottom — further overruns don't demote past minimal.
        s.run_once()
        assert s.counters.ladder_level == "minimal"
        assert s.counters.ticks_skipped == 3

    def test_re_promote_after_clean_ticks(self, tree):
        """AC6: N clean ticks ⇒ re-promote (hysteresis)."""
        clock = {"t": 0.0}

        def fake_clock():
            clock["t"] += 0.001
            return clock["t"]

        s, ring = self._sampler(tree, clock=fake_clock)
        # Force the ladder to minimal, then drive clean ticks.
        s.counters.ladder_level = "minimal"
        for _ in range(RE_PROMOTE_CLEAN_TICKS):
            s.run_once()
        assert s.counters.ladder_level == "reduced"

        for _ in range(RE_PROMOTE_CLEAN_TICKS):
            s.run_once()
        assert s.counters.ladder_level == "full"

    def test_no_backlog_queue(self, tree):
        """A skipped tick writes NOTHING — no backlog accumulation."""
        clock = {"t": 0.0}

        def slow_clock():
            clock["t"] += 10.0
            return clock["t"]

        s, ring = self._sampler(
            tree, clock=slow_clock, interval_override=0.02
        )
        for _ in range(10):
            s.run_once()
        assert len(ring) == 0
        assert s.counters.ticks_skipped == 10
        assert s.counters.ticks == 0

    def test_ring_is_bounded_drop_oldest(self):
        """The ring drops the oldest row at maxlen (bounded memory)."""
        ring = TierRing(tier="raw", maxlen=3)
        for i in range(5):
            ring.append({"i": i})
        assert len(ring) == 3
        assert [r["i"] for r in ring.snapshot()] == [2, 3, 4]


# ──────────────────────────────────────────────────────────────────────────
# P2 T10/T5 — attribution integration (AC7, AC11, D12/D13/D14)
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def charge_env(tmp_path):
    """Reset the charge + pid-registry singletons around each test.

    The sampler consumes the PROCESS-WIDE charge registry — without
    this fixture a test that enables charging would leak records into
    every later pack in the session.
    """
    charge_mod.reset()
    charge_mod.set_enabled(False)
    reg = reset_pid_registry_for_tests(proc_root=tmp_path / "proc")
    yield reg
    charge_mod.reset()
    charge_mod.set_enabled(False)
    reset_pid_registry_for_tests()


class TestAttributionIntegration:
    """AC7 (cardinality + honesty) / T5 (cheap counts) / AC11 (D14)."""

    def _sampler(
        self,
        tree,
        *,
        instance_cap=64,
        graph_cache=None,
        graph_tasks=None,
        state_counts=None,
        ring_maxlen=256,
        pid_registry=None,
    ):
        proc, cg_root = tree
        ring = TierRing(tier="raw", maxlen=ring_maxlen)
        instance_ring = TierRing(tier="instance", maxlen=512)
        s = Sampler(
            sample_seconds=30,
            self_cpu_budget_pct=1.0,
            self_rss_budget_mb=50,
            detail_level="auto",
            proc_root=proc,
            cgroup_root=cg_root,
            ring=ring,
            instance_ring=instance_ring,
            instance_cap=instance_cap,
            graph_cache_provider=(
                (lambda: graph_cache) if graph_cache is not None else None
            ),
            graph_tasks_provider=(
                (lambda: graph_tasks) if graph_tasks is not None else None
            ),
            state_message_counts_provider=(
                (lambda: state_counts) if state_counts is not None else None
            ),
            pid_registry=pid_registry,
        )
        return s, ring, instance_ring

    # ── AC7 — cardinality cap + overflow sentinel ──

    def test_n70_instances_give_64_rows_plus_overflow(self, tree, charge_env):
        """AC7: N=70 instances ⇒ 64 top-N rows + ONE ``__overflow__``."""
        graph = {f"inst-{i:02d}": (object(), f"/agents/agent-{i:02d}") for i in range(70)}
        charge_mod.set_enabled(True)
        # Give 68 instances distinct charged bytes (inst-00 smallest).
        for i in range(68):
            charge_mod.note_message_append(
                f"inst-{i:02d}", {"payload": "x" * (10 * (i + 1))}
            )
        # 2 instances in flight (NOT among the top-charged).
        graph_tasks = {"inst-00": object(), "inst-01": object()}
        s, _ring, inst_ring = self._sampler(
            tree,
            instance_cap=64,
            graph_cache=graph,
            graph_tasks=graph_tasks,
        )
        s.run_once()
        rows = inst_ring.snapshot()
        assert len(rows) == 65  # 64 + 1 overflow sentinel
        non_sentinel = [r for r in rows if r["instance_id"] != OVERFLOW_SENTINEL]
        sentinels = [r for r in rows if r["instance_id"] == OVERFLOW_SENTINEL]
        assert len(non_sentinel) == 64
        assert len(sentinels) == 1
        # Both in-flight instances survive the cap (in-flight ranks first).
        kept_ids = {r["instance_id"] for r in non_sentinel}
        assert {"inst-00", "inst-01"} <= kept_ids
        # The sentinel aggregates what collapsed: 70 − 64 = 6 instances.
        ov = sentinels[0]
        assert ov["estimated"] is True
        assert ov["scan_kind"] == "counter"
        assert ov["in_flight_tasks"] == 0  # both in-flight were kept
        collapsed_charged = sum(
            (charge_mod.snapshot_instance(f"inst-{i:02d}") or {}).get(
                "charged_bytes", 0
            )
            for i in range(2, 8)  # the 6 smallest charged (inst-02..07)
            if f"inst-{i:02d}" not in kept_ids
        )
        assert ov["charged_bytes"] == collapsed_charged

    def test_cap_respected_and_no_sentinel_under_cap(self, tree, charge_env):
        """Under the cap there is NO sentinel row."""
        graph = {f"i{i}": (object(), f"/agents/a{i}") for i in range(5)}
        charge_mod.set_enabled(True)
        for i in range(5):
            charge_mod.note_message_append(f"i{i}", {"x": "y"})
        s, _ring, inst_ring = self._sampler(
            tree, instance_cap=64, graph_cache=graph
        )
        s.run_once()
        rows = inst_ring.snapshot()
        assert len(rows) == 5
        assert all(r["instance_id"] != OVERFLOW_SENTINEL for r in rows)

    # ── AC7 — coverage math (D13 blend rule) ──

    def test_coverage_math_unattributed_clamped_and_bounded(self, tree, charge_env):
        """coverage_pct ∈ [0,1]; unattributed_kb = anon − attributed."""
        from daemon.services.resource_monitor.charge import bounded_deep_size

        charge_mod.set_enabled(True)
        msg = {"blob": "x" * 1_000_000}  # 1 MB payload
        charged, _nodes, _partial = bounded_deep_size(msg)
        charge_mod.note_message_append("inst-a", msg)
        s, ring, _inst = self._sampler(tree)
        s.run_once()
        row = ring.snapshot()[-1]
        anon_bytes = 65536 * 1024  # VmData in the synthetic fixture
        assert row["attributed_bytes"] == charged
        assert row["unattributed_kb"] == (anon_bytes - charged) // 1024
        assert 0.0 <= row["coverage_pct"] <= 1.0
        assert row["coverage_pct"] == pytest.approx(
            min(1.0, charged / anon_bytes), abs=1e-9
        )

    def test_coverage_never_exceeds_one_and_unattributed_never_negative(
        self, tmp_path, charge_env
    ):
        """Charged stock over anon ⇒ clamp: unattributed 0, coverage 1."""
        # Tiny-anon daemon fixture (VmData 100 kB) so a modest charge
        # exceeds the whole anon pool.
        proc = tmp_path / "proc"
        cg_root = tmp_path / "sys" / "fs" / "cgroup"
        (proc / "self").mkdir(parents=True)
        (proc / "self" / "cgroup").write_text("0::/system.slice/t.service\n")
        (proc / "self" / "stat").write_text(
            "1 (python) R 1 1 1 0 -1 0 1 1 1 1 1 1 0 0 20 0 1 0 1 1 1 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        (proc / "self" / "status").write_text(
            "Name:\tpython\nVmRSS:\t100 kB\nVmHWM:\t100 kB\n"
            "VmSize:\t100 kB\nVmData:\t100 kB\nThreads:\t1\n"
        )
        (proc / "self" / "statm").write_text("25 25 0 0 0 0 0")
        (proc / "self" / "fd").mkdir()
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-big", {"blob": "x" * 1_000_000})
        s, ring, _inst = self._sampler((proc, cg_root))
        s.run_once()
        row = ring.snapshot()[-1]
        assert row["attributed_bytes"] > 100 * 1024  # charged > anon
        assert row["unattributed_kb"] == 0
        assert row["coverage_pct"] == 1.0

    def test_anon_zero_gives_coverage_zero(self, tmp_path, charge_env):
        """anon=0 ⇒ coverage 0 (div-by-zero guard), never a crash."""
        proc = tmp_path / "proc"
        cg_root = tmp_path / "sys" / "fs" / "cgroup"
        (proc / "self").mkdir(parents=True)
        (proc / "self" / "cgroup").write_text("0::/system.slice/t.service\n")
        (proc / "self" / "stat").write_text(
            "1 (python) R 1 1 1 0 -1 0 1 1 1 1 1 1 0 0 20 0 1 0 1 1 1 "
            "0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        (proc / "self" / "status").write_text(
            "Name:\tpython\nVmRSS:\t0 kB\nVmHWM:\t0 kB\n"
            "VmSize:\t0 kB\nVmData:\t0 kB\nThreads:\t1\n"
        )
        (proc / "self" / "statm").write_text("0 0 0 0 0 0 0")
        (proc / "self" / "fd").mkdir()
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-z", {"blob": "x" * 12345})
        s, ring, _inst = self._sampler(
            (proc, cg_root),
        )
        s.run_once()
        row = ring.snapshot()[-1]
        assert row["attributed_bytes"] > 0
        assert row["unattributed_kb"] == 0
        assert row["coverage_pct"] == 0.0

    # ── deep-scan reconcile on the tick (scan_kind / estimated) ──

    def test_deep_scanned_row_marked_and_others_estimated(
        self, tree, charge_env
    ):
        """scan_kind='deep' + estimated=False ONLY on the fresh
        complete deep scan; every other row is counter/True (D13)."""
        from daemon.services.resource_monitor.deep_scan import DeepScanner

        graph = {f"inst-{i}": ({"node": "x" * 50}, f"/agents/a{i}") for i in range(4)}
        charge_mod.set_enabled(True)
        for i in range(4):
            charge_mod.note_message_append(f"inst-{i}", {"p": "y"})
        s, _ring, inst_ring = self._sampler(
            tree, graph_cache=graph, instance_cap=64
        )
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        assert set(rows) == {f"inst-{i}" for i in range(4)}
        # Cursor starts at 0 ⇒ inst-0 scanned this tick (complete walk —
        # tiny graph, far under the node cap ⇒ estimated False).
        assert rows["inst-0"]["scan_kind"] == "deep"
        assert rows["inst-0"]["estimated"] is False
        assert rows["inst-0"]["deep_bytes"] == charge_mod.snapshot_instance(
            "inst-0"
        )["deep_bytes"]
        assert rows["inst-0"]["deep_bytes"] > 0
        for i in (1, 2, 3):
            assert rows[f"inst-{i}"]["scan_kind"] == "counter"
            assert rows[f"inst-{i}"]["estimated"] is True
            assert rows[f"inst-{i}"]["deep_bytes"] is None
        # estimated is NEVER None anywhere (NOT NULL contract).
        assert all(r["estimated"] is not None for r in rows.values())

    # ── T5 — cheap counts match driven state ──

    def test_cheap_counts_match_driven_state(self, tree, charge_env):
        """T5/AC3: graph_cache_present / in_flight_tasks /
        all_state_messages_len match the driven providers."""
        graph = {
            "inst-cache": (object(), "/agents/cached"),
            "inst-idle": (object(), "/agents/idle"),
        }
        graph_tasks = {"inst-cache": object()}
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-cache", {"m": "z"})
        # 3 appends ⇒ charge-record counter == 3 for inst-cache.
        for _ in range(2):
            charge_mod.note_message_append("inst-cache", {"m": "z"})
        # inst-nothing: charged (1 append) but absent from the
        # graph cache / provider maps — the fallback counter must
        # still surface its cheap count.
        charge_mod.note_message_append("inst-nothing", {"m": "q"})
        s, _ring, inst_ring = self._sampler(
            tree,
            graph_cache=graph,
            graph_tasks=graph_tasks,
            state_counts={"inst-cache": 7},
        )
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        cache_row = rows["inst-cache"]
        assert cache_row["graph_cache_present"] is True
        assert cache_row["in_flight_tasks"] == 1
        # Provider map wins when present; the charge-record counter is
        # the fallback source (3 appends observed).
        assert cache_row["all_state_messages_len"] == 7
        idle_row = rows["inst-idle"]
        assert idle_row["graph_cache_present"] is True
        assert idle_row["in_flight_tasks"] == 0
        assert idle_row["all_state_messages_len"] is None
        nothing_row = rows["inst-nothing"]
        assert nothing_row["graph_cache_present"] is False
        assert nothing_row["all_state_messages_len"] == 1

    def test_all_state_messages_len_falls_back_to_charge_counter(
        self, tree, charge_env
    ):
        """Without the state-counts provider, the charge append-count
        is the T5 source (cheap len — no deep walk)."""
        charge_mod.set_enabled(True)
        for _ in range(3):
            charge_mod.note_message_append("inst-c", {"m": "z"})
        s, _ring, inst_ring = self._sampler(tree)
        s.run_once()
        row = inst_ring.snapshot()[0]
        assert row["all_state_messages_len"] == 3

    # ── T10 — registry children charged to the OWNING instance ──

    def _register_child(self, registry, proc: Path, *, pid, start_time,
                        kind, instance_id, command="bash -c x"):
        child = proc / str(pid)
        child.mkdir(parents=True)
        # Field 4=ppid, 5=pgid, 22=starttime (jiffies); utime=10, stime=5.
        # Field 22 MUST equal the registered start_time — the sampler's
        # recycle defense reads it back (parts[19] after the comm strip).
        (child / "stat").write_text(
            f"{pid} (bash) R 1 {pid} {pid} 0 -1 0 100 0 0 0 10 5 0 0 20 0 "
            f"1 0 {start_time} 500000 16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        (child / "status").write_text(
            "Name:\tbash\nVmRSS:\t8192 kB\nVmHWM:\t9216 kB\n"
        )
        return registry.register(
            pid=pid,
            start_time=start_time,
            kind=kind,
            instance_id=instance_id,
            command=command,
            pgid=pid,
        )

    def test_child_exact_reads_charged_to_owning_instance(
        self, tree, charge_env
    ):
        proc = tree[0]
        self._register_child(
            charge_env, proc, pid=5678, start_time=8888,
            kind="bash", instance_id="inst-owner",
        )
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-owner", {"m": "z"})
        charge_mod.note_message_append("inst-other", {"m": "z"})
        s, ring, inst_ring = self._sampler(
            tree, graph_cache={"inst-owner": (object(), "/agents/o"),
                               "inst-other": (object(), "/agents/x")},
        )
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        # EXACT child read charged to the OWNER only.
        assert rows["inst-owner"]["child_rss_kb"] == 8192
        assert rows["inst-owner"]["child_cpu_user_usec"] == 10 * 10_000
        assert rows["inst-other"]["child_rss_kb"] == 0
        # T1 child_rss_total_kb covers the registry child too.
        assert ring.snapshot()[-1]["child_rss_total_kb"] == 8192

    def test_recycled_pid_never_charged(self, tree, charge_env):
        """D8 at the sampler: /proc starttime mismatch ⇒ skip."""
        proc = tree[0]
        self._register_child(
            charge_env, proc, pid=4242, start_time=111111,
            kind="bash", instance_id="inst-owner",
        )
        # Rewrite the stat with a DIFFERENT starttime (recycled pid).
        (proc / "4242" / "stat").write_text(
            "4242 (evil) R 1 4242 4242 0 -1 0 100 0 0 0 99 5 0 0 20 0 "
            "1 0 9999 500000 16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-owner", {"m": "z"})
        s, ring, inst_ring = self._sampler(
            tree, graph_cache={"inst-owner": (object(), "/agents/o")}
        )
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        assert rows["inst-owner"]["child_rss_kb"] == 0
        assert ring.snapshot()[-1]["child_rss_total_kb"] == 0

    def test_mcp_pool_child_stays_server_scoped(self, tree, charge_env):
        """instance_id NULL (mcp pool) ⇒ NO instance gets the bytes;
        the child still shows in the §2.8 children view (D11)."""
        proc = tree[0]
        self._register_child(
            charge_env, proc, pid=7171, start_time=31337,
            kind="mcp_child", instance_id=None,
            command="node server.js",
        )
        # Server-scoped identity rides the entry's detail (the seam
        # caller stamps the MCP server name there).
        charge_env._live[7171].detail = "context7"
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-a", {"m": "z"})
        s, ring, inst_ring = self._sampler(
            tree, graph_cache={"inst-a": (object(), "/agents/a")}
        )
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        assert rows["inst-a"]["child_rss_kb"] == 0
        assert ring.snapshot()[-1]["child_rss_total_kb"] == 8192
        view = s.last_children_view
        assert len(view) == 1
        assert view[0] == {
            "pid": 7171,
            "kind": "mcp_child",
            "name": "context7",
            "cpu_pct": None,  # first sight — honest None
            "rss_mb": 8.0,
        }

    def test_children_cpu_pct_second_tick(self, tree, charge_env):
        """cpu_pct is a delta — None on first sight, a number on the
        second (never invented)."""
        proc = tree[0]
        self._register_child(
            charge_env, proc, pid=5150, start_time=777,
            kind="bash", instance_id="inst-owner",
        )
        charge_mod.set_enabled(True)
        s, _ring, _inst = self._sampler(
            tree, graph_cache={"inst-owner": (object(), "/agents/o")}
        )
        s.run_once()
        assert s.last_children_view[0]["cpu_pct"] is None
        # Bump the child's utime (10 → 20 jiffies), SAME starttime —
        # tick again.
        (proc / "5150" / "stat").write_text(
            "5150 (bash) R 1 5150 5150 0 -1 0 100 0 0 0 20 5 0 0 20 0 "
            "1 0 777 500000 16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        s.run_once()
        pct = s.last_children_view[0]["cpu_pct"]
        assert pct is not None and pct > 0.0

    # ── AC11 — D14 no-raw-args guard (schema + canary) ──

    def test_t2_row_schema_scalars_only(self, tree, charge_env):
        """AC11: the T2 row carries ONLY the allowed scalar keys."""
        graph = {"inst-a": (object(), "/agents/a")}
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-a", {"m": "z"})
        s, _ring, inst_ring = self._sampler(
            tree, graph_cache=graph,
            graph_tasks={"inst-a": object()},
            state_counts={"inst-a": 1},
        )
        s.run_once()
        for row in inst_ring.snapshot():
            extra = set(row) - ALLOWED_T2_ROW_KEYS
            assert not extra, extra
            missing = {
                "instance_id", "scan_kind", "charged_bytes",
                "child_rss_kb", "child_cpu_user_usec", "estimated",
            } - set(row)
            assert not missing, missing

    def test_canary_secret_never_reaches_rows_or_logs(
        self, tree, charge_env, tmp_path, caplog, monkeypatch
    ):
        """AC11 canary: a secret planted in a synthetic tool-arg /
        message must not appear in ANY resource_* row nor in any
        resource_monitor log line across a full tick+flush cycle."""
        import logging

        CANARY = "SECRET_CANARY__ENS__pb-f1-plaintext"

        # Apply the 4 migrations to a file SQLite.
        engine = create_engine(
            f"sqlite:///{tmp_path / 'canary.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        migrations = (
            Path(__file__).resolve().parent.parent
            / "daemon" / "migrations" / "versions"
        )

        def split_sql(sql_text):
            if "-- DOWN" in sql_text:
                sql_text = sql_text.split("-- DOWN")[0]
            code = "\n".join(
                ln for ln in sql_text.splitlines()
                if not ln.lstrip().startswith("--")
            )
            return [s.strip() for s in code.split(";") if s.strip()]

        from daemon.services.resource_monitor import Flusher
        from daemon.services.resource_monitor.rollup import RetentionPolicy

        for fn in [
            "20261018_090001_create_resource_daemon_bucket.sql",
            "20261018_090002_create_resource_instance_sample.sql",
            "20261018_090003_create_resource_pid_map.sql",
            "20261018_090004_create_resource_limit_policy.sql",
        ]:
            sql = (migrations / fn).read_text()
            with engine.begin() as conn:
                for stmt in split_sql(sql):
                    conn.execute(text(stmt))

        # Plant the canary EVERYWHERE the seams could conceivably leak:
        # a message object (append), a checkpoint blob, and a graph
        # node the deep scan walks.
        charge_mod.set_enabled(True)
        charge_mod.note_message_append(
            "inst-canary", {"tool_args": {"cmd": CANARY}, "content": CANARY}
        )
        charge_mod.note_checkpoint_read("inst-canary", {"blob": CANARY})
        graph = {"inst-canary": ({"secret": CANARY}, "/agents/canary")}

        # A registry child whose (sanitized-shape) command carries the
        # canary — even a BAD seam caller must not leak through us.
        proc = tree[0]
        self._register_child(
            charge_env, proc, pid=9090, start_time=424242,
            kind="bash", instance_id="inst-canary",
            command=f"bash -c {CANARY}",
        )

        s, ring, inst_ring = self._sampler(
            tree, graph_cache=graph,
        )
        flusher = Flusher(
            engine=engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            instance_ring=inst_ring,
            pid_registry=charge_env,
        )

        with caplog.at_level(
            logging.DEBUG, logger="daemon.services.resource_monitor"
        ):
            s.run_once()
            s.run_once()  # 2 ticks — cpu_pct delta + a second deep scan
            flusher.run_once()

        # 1. Every persisted resource_* row is canary-free.
        with engine.connect() as conn:
            for table in (
                "resource_daemon_bucket",
                "resource_instance_sample",
                "resource_pid_map",
                "resource_limit_policy",
            ):
                rows = conn.execute(
                    text(f"SELECT * FROM {table}")
                ).fetchall()
                for row in rows:
                    rendered = json.dumps(
                        [str(c) for c in row], default=str
                    )
                    assert CANARY not in rendered, (
                        f"CANARY LEAKED into {table}: {rendered[:200]}"
                    )
        # 2. The in-RAM T2 rows are canary-free too.
        for row in inst_ring.snapshot():
            assert CANARY not in json.dumps(row, default=str)
        # 3. No resource_monitor log line carried the canary.
        for rec in caplog.records:
            assert CANARY not in rec.getMessage()
            assert CANARY not in str(rec.exc_info)
        # 4. And the rows EXIST (the cycle was real, not vacuous).
        with engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM resource_instance_sample")
            ).scalar()
            assert n >= 2


# ──────────────────────────────────────────────────────────────────────────
# P3 — T2 ``resource_instance_sample.status`` seam (sample-time snapshot)
# ──────────────────────────────────────────────────────────────────────────


class TestT2StatusSeam:
    """The sampler writes the instance's status AT SAMPLE TIME (§2.6).

    Two honest derivations over the maps the tick already holds (no DB
    read on the tick — D5): ``running`` = active graph task, ``idle`` =
    cached graph without an active task, ``None`` = charge-only
    (status genuinely unknown — never guessed). The overflow sentinel
    row is an aggregate pseudo-instance: ``None``.
    """

    def _sampler(self, tree, *, graph_cache=None, graph_tasks=None):
        proc, cg_root = tree
        ring = TierRing(tier="raw", maxlen=64)
        instance_ring = TierRing(tier="instance", maxlen=128)
        s = Sampler(
            sample_seconds=30,
            self_cpu_budget_pct=1.0,
            self_rss_budget_mb=50,
            detail_level="auto",
            proc_root=proc,
            cgroup_root=cg_root,
            ring=ring,
            instance_ring=instance_ring,
            instance_cap=64,
            graph_cache_provider=(
                (lambda: graph_cache) if graph_cache is not None else None
            ),
            graph_tasks_provider=(
                (lambda: graph_tasks) if graph_tasks is not None else None
            ),
        )
        return s, instance_ring

    def test_running_when_active_graph_task(self, tree, charge_env):
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("i-run", {"payload": "x" * 32})
        s, inst_ring = self._sampler(
            tree,
            graph_cache={"i-run": (object(), "/agents/dev")},
            graph_tasks={"i-run": object()},
        )
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        assert rows["i-run"]["status"] == "running"

    def test_idle_when_cached_without_task(self, tree, charge_env):
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("i-idle", {"payload": "x" * 32})
        s, inst_ring = self._sampler(
            tree,
            graph_cache={"i-idle": (object(), "/agents/dev")},
            graph_tasks={},
        )
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        assert rows["i-idle"]["status"] == "idle"

    def test_none_for_charge_only_unknown(self, tree, charge_env):
        """Charge-only presence ⇒ status genuinely unknown ⇒ None."""
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("i-ghost", {"payload": "x" * 32})
        s, inst_ring = self._sampler(tree)  # no providers injected
        s.run_once()
        rows = {r["instance_id"]: r for r in inst_ring.snapshot()}
        assert rows["i-ghost"]["status"] is None

    def test_overflow_sentinel_status_none(self, tree, charge_env):
        charge_mod.set_enabled(True)
        for n in range(70):
            charge_mod.note_message_append(f"i-{n:02d}", {"payload": "x" * (10 * n)})
        s, inst_ring = self._sampler(
            tree,
            graph_cache={f"i-{n:02d}": (object(), "/agents/dev") for n in range(70)},
        )
        s.run_once()
        rows = inst_ring.snapshot()
        sentinels = [r for r in rows if r["instance_id"] == OVERFLOW_SENTINEL]
        assert len(sentinels) == 1 and sentinels[0]["status"] is None

    def test_status_key_within_allowed_t2_keys(self, tree, charge_env):
        """The seam rides the EXISTING D14 allow-list (``status`` was
        reserved at P1); the belt-and-braces tripwire must stay quiet."""
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("i-run", {"payload": "x" * 32})
        s, inst_ring = self._sampler(
            tree,
            graph_cache={"i-run": (object(), "/agents/dev")},
            graph_tasks={"i-run": object()},
        )
        s.run_once()  # raises inside if a row carried a non-allowed key
        assert "status" in ALLOWED_T2_ROW_KEYS
        rows = inst_ring.snapshot()
        assert all(set(r) <= ALLOWED_T2_ROW_KEYS for r in rows)

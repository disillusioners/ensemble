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

import os
import tempfile
from pathlib import Path

import pytest

from daemon.services.resource_monitor import (
    LADDER_DEFAULT,
    Sampler,
    SamplerCounters,
    TierRing,
    read_proc_snapshot,
)
from daemon.services.resource_monitor.sampler import RE_PROMOTE_CLEAN_TICKS


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

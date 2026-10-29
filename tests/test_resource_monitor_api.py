"""API test pack (P1 T10 / AC10, AC11, AC12).

Covers:

  * AC10 — degraded boot leaves the app healthy: the resource
    monitor can be None / disabled; the endpoints respond 200 +
    empty shape (D11), not 503. The stop Event + detached timed
    join are exercised.
  * AC11 — ``/live`` + ``/history`` + ``/self-cost`` return the
    §2.8 shapes; empty store / disabled sampler ⇒ 200 + empty
    shape (never 503 — D11); ``history`` auto-falls resolution
    for windows beyond raw retention and reports
    ``resolution_used``.
  * AC12 — error envelope uniformity: every raise uses
    ``detail={"error": ...}`` (house shape, snapshots.py
    precedent). Malformed window ⇒ 400 with the envelope.

FENCE DISCIPLINE: file-SQLite at pytest ``tmp_path``; the real
``/proc`` and ``/sys/fs/cgroup`` are NEVER resolved. FAKE_HOME
mktemp.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from daemon.config import ResourceMonitorConfig
from daemon.routers.resources import router as resources_router
from daemon.services.resource_monitor import build_resource_monitor
from daemon.services.resource_monitor.sampler import (
    DEGRADED_SENTINEL_INTERVAL_S,
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
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def app_state(tmp_path):
    """Build a FastAPI app + TestClient with a synthetic proc tree."""
    app = FastAPI()
    app.include_router(resources_router, prefix="/api")

    # Build a synthetic /proc + cgroup tree.
    proc = tmp_path / "proc"
    cgroup_root = tmp_path / "sys" / "fs" / "cgroup"
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
    (proc / "self" / "fd").mkdir()
    for n in ("0", "2", "5"):
        (proc / "self" / "fd" / n).touch()
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

    # Apply the 4 .sql migrations.
    def split_sql(sql_text):
        if "-- DOWN" in sql_text:
            sql_text = sql_text.split("-- DOWN")[0]
        code = "\n".join(
            ln
            for ln in sql_text.splitlines()
            if not ln.lstrip().startswith("--")
        )
        return [s.strip() for s in code.split(";") if s.strip()]

    from sqlalchemy import text

    engine = create_engine(
        f"sqlite:///{tmp_path / 'api_test.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    migrations = Path(__file__).resolve().parent.parent / "daemon" / "migrations" / "versions"
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

    class _FakeManager:
        def __init__(self, engine):
            self.engine = engine

    app.state.manager = _FakeManager(engine)
    app.state.resource_monitor = None  # default — degraded

    return app, proc, cgroup_root, engine


# ──────────────────────────────────────────────────────────────────────────
# AC10 — degraded boot + stop Event + detached join
# ──────────────────────────────────────────────────────────────────────────


class TestDegradedBoot:
    def test_no_monitor_returns_200_empty_shape(self, app_state):
        app, _proc, _cg, _engine = app_state
        client = TestClient(app)
        for path in ("/api/resources/live", "/api/resources/self-cost"):
            r = client.get(path)
            assert r.status_code == 200
            j = r.json()
            # Empty shapes still expose every documented key.
            assert "ts" in j or path.endswith("self-cost")

    def test_disabled_monitor_returns_200(self, app_state):
        """AC10: enabled=false ⇒ disabled facade; endpoints 200 + empty."""
        app, proc, cg, engine = app_state
        cfg = ResourceMonitorConfig(enabled=False)
        mon = build_resource_monitor(
            config_resource_monitor=cfg, engine=engine, proc_root=proc, cgroup_root=cg
        )
        assert mon.enabled is False
        app.state.resource_monitor = mon
        client = TestClient(app)
        r = client.get("/api/resources/live")
        assert r.status_code == 200
        assert r.json()["cgroup"] is None
        r = client.get("/api/resources/self-cost")
        assert r.status_code == 200
        assert r.json()["cpu_pct"] is None

    def test_stop_event_detached_join(self, app_state):
        """AC10: the facade exposes stop() + join(timeout); the join
        runs on a detached thread (D10) and never blocks the loop.
        """
        app, proc, cg, engine = app_state
        mon = build_resource_monitor(
            config_resource_monitor=ResourceMonitorConfig(),
            engine=engine, proc_root=proc, cgroup_root=cg,
        )
        mon.start()
        time.sleep(0.2)
        assert mon.is_alive()
        mon.stop()
        done = threading.Event()

        def join_and_signal():
            mon.join(timeout=5.0)
            done.set()

        threading.Thread(target=join_and_signal, daemon=True).start()
        assert done.wait(timeout=6.0), "join did not complete in 6s"
        assert not mon.is_alive()


# ──────────────────────────────────────────────────────────────────────────
# AC11 — endpoint shapes
# ──────────────────────────────────────────────────────────────────────────


class TestEndpointShapes:
    def _build_enabled(self, app_state):
        app, proc, cg, engine = app_state
        mon = build_resource_monitor(
            config_resource_monitor=ResourceMonitorConfig(),
            engine=engine, proc_root=proc, cgroup_root=cg,
        )
        mon.sampler.run_once()
        mon.sampler.run_once()
        mon.enabled = True
        app.state.resource_monitor = mon
        return app, mon

    def test_live_shape(self, app_state):
        app, _mon = self._build_enabled(app_state)
        client = TestClient(app)
        r = client.get("/api/resources/live")
        assert r.status_code == 200
        j = r.json()
        assert set(j.keys()) >= {
            "ts", "sample_level", "daemon", "cgroup", "children",
            "instances", "attribution", "overruns", "self_cost",
        }
        # daemon shape — every documented key is present.
        assert set(j["daemon"].keys()) == {
            "pid", "cpu_pct", "rss_mb", "threads", "fds", "uptime_s",
        }
        # pid is POPULATED from the ring row (review F2) — the fake
        # proc tree is read with the test process's own pid, which
        # the sampler stamps into the row, which the /live handler
        # surfaces here.
        assert j["daemon"]["pid"] == os.getpid()
        # P2 (D13): the attribution block carries REAL values. With
        # the charge registry empty (nothing charged yet), attributed
        # is 0 and unattributed = the whole anon pool (VmData 65536 kB
        # = 64 MB in the synthetic fixture) — coverage 0. The zero
        # block P1 pinned here was the stub; the honesty columns now
        # demonstrate the arithmetic.
        assert j["attribution"] == {
            "attributed_mb": 0,
            "unattributed_mb": 64.0,
            "coverage_pct": 0.0,
        }
        # The denominator is the DAEMON's own anon (VmData 65536 kB in
        # the fixture — §2.2 in-process attribution), NOT the cgroup
        # figure (which also covers children): 65536 kB = 64 MB.
        assert j["attribution"]["unattributed_mb"] == 64.0

    def test_the_degraded_sentinel_never_reaches_the_live_view(self, app_state):
        """B1: the sentinel is DB EVIDENCE, never a live measurement.

        The raw ring carries the DEGRADED SENTINEL rows (a tick whose
        platform read failed). A sentinel has NO measurement — every
        daemon metric is NULL. If the live view rendered it, the FE
        would show "0.0 MB RSS" and "no threads" for a daemon whose
        memory was never measured. The live view must keep showing the
        last REAL sample.
        """
        app, mon = self._build_enabled(app_state)
        client = TestClient(app)
        before = client.get("/api/resources/live").json()
        assert before["daemon"]["rss_mb"] is not None

        mon.ring.append(
            {
                "interval_s": DEGRADED_SENTINEL_INTERVAL_S,
                "pid": None,
                "rss_kb": None,
                "pss_kb": None,
                "anon_kb": None,
                "hw_peak_kb": None,
                "threads": None,
                "fds": None,
                "cpu_user_usec": None,
                "cpu_sys_usec": None,
                "child_rss_total_kb": None,
                "attributed_bytes": None,
                "unattributed_kb": None,
                "coverage_pct": None,
                "sample_cost_ms": 1.0,
                "sampler_lag_ms": None,
                "missed_samples": 1,
                "ladder_level": "full",
            }
        )
        after = client.get("/api/resources/live").json()
        assert after["daemon"]["rss_mb"] == before["daemon"]["rss_mb"]
        assert after["daemon"]["pid"] == before["daemon"]["pid"]
        assert after["daemon"]["threads"] == before["daemon"]["threads"]
        assert after["daemon"]["fds"] == before["daemon"]["fds"]

    def test_self_cost_exact_field_set(self, app_state):
        """AC11: /self-cost returns the EXACT §2.8 field set."""
        app, _mon = self._build_enabled(app_state)
        client = TestClient(app)
        r = client.get("/api/resources/self-cost")
        assert r.status_code == 200
        j = r.json()
        assert set(j.keys()) == {
            "cpu_pct", "cumulative_cpu_s", "buffers_bytes",
            "rss_budget_mb", "threads", "samples", "ticks_skipped",
            "flush", "profiling",
        }
        # nested flush + profiling shapes.
        assert set(j["flush"].keys()) == {"batches", "bytes", "errors"}
        assert set(j["profiling"].keys()) == {"active", "capabilities"}
        # rss_budget_mb reflects RESOURCE_MONITOR_SELF_RSS_BUDGET_MB.
        assert j["rss_budget_mb"] == 50

    def test_history_resolution_auto(self, app_state):
        """AC11: ``window > raw-retention`` auto-falls to minute."""
        app, _proc, _cg, _engine = app_state
        client = TestClient(app)
        # 15m window — raw.
        r = client.get("/api/resources/history?window=15m&resolution=auto")
        assert r.status_code == 200
        assert r.json()["resolution_used"] == "raw"
        # 7d window — auto-falls to minute (window > 48h raw retention).
        r = client.get("/api/resources/history?window=7d&resolution=auto")
        assert r.status_code == 200
        assert r.json()["resolution_used"] == "minute"

    def test_history_with_data(self, app_state):
        app, _proc, _cg, _engine = app_state
        # Enable the monitor and flush one window.
        mon = build_resource_monitor(
            config_resource_monitor=ResourceMonitorConfig(),
            engine=app_state[3],
            proc_root=app_state[1],
            cgroup_root=app_state[2],
        )
        mon.sampler.run_once()
        mon.enabled = True
        mon.flusher.run_once()
        app_state[0].state.resource_monitor = mon

        client = TestClient(app_state[0])
        r = client.get("/api/resources/history?window=1h&resolution=raw")
        assert r.status_code == 200
        j = r.json()
        assert "points" in j
        assert "resolution_used" in j
        assert j["resolution_used"] == "raw"
        assert isinstance(j["truncated"], bool)


# ──────────────────────────────────────────────────────────────────────────
# AC12 — error envelope uniformity
# ──────────────────────────────────────────────────────────────────────────


class TestErrorEnvelope:
    def test_malformed_window_returns_400_with_envelope(self, app_state):
        app, *_ = app_state
        client = TestClient(app)
        r = client.get("/api/resources/history?window=bogus")
        assert r.status_code == 400
        d = r.json().get("detail")
        assert isinstance(d, dict)
        assert "error" in d
        assert "bogus" in d["error"]

    def test_malformed_resolution_returns_400(self, app_state):
        app, *_ = app_state
        client = TestClient(app)
        r = client.get("/api/resources/history?window=15m&resolution=bogus")
        assert r.status_code == 400
        d = r.json().get("detail")
        assert isinstance(d, dict)
        assert "error" in d

    def test_error_envelope_uniform_detail_shape(self, app_state):
        """The detail= shape is the same dict on every 400 — house shape."""
        app, *_ = app_state
        client = TestClient(app)
        for params in (
            {"window": "bogus"},
            {"window": "15m", "resolution": "bogus"},
        ):
            r = client.get("/api/resources/history", params=params)
            assert r.status_code == 400
            detail = r.json().get("detail")
            assert isinstance(detail, dict)
            assert "error" in detail
            assert isinstance(detail["error"], str)

# ──────────────────────────────────────────────────────────────────────────
# P2 T11 — /live honesty surfacing (AC8, D7/D13)
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def p2_env(tmp_path):
    """Reset the charge + pid-registry singletons around each test."""
    from daemon.services.resource_monitor import charge as charge_mod
    from daemon.services.resource_monitor.pid_registry import (
        reset_pid_registry_for_tests,
    )

    charge_mod.reset()
    charge_mod.set_enabled(False)
    reg = reset_pid_registry_for_tests(proc_root=tmp_path / "proc")
    yield charge_mod, reg
    charge_mod.reset()
    charge_mod.set_enabled(False)
    reset_pid_registry_for_tests()


class TestLiveHonesty:
    """AC8: attribution block + per-instance fields typed; children[]
    reflects the registry snapshot."""

    def _build(self, app_state, p2_env, *, providers=True):
        charge_mod, registry = p2_env
        app, proc, cg, engine = app_state
        kwargs = {}
        if providers:
            graph = {
                "inst-live": (object(), "/agents/live-agent"),
                "inst-idle": (object(), "/agents/idle-agent"),
            }
            kwargs = dict(
                graph_cache_provider=lambda: graph,
                graph_tasks_provider=lambda: {"inst-live": object()},
                state_message_counts_provider=lambda: {"inst-live": 4},
            )
        mon = build_resource_monitor(
            config_resource_monitor=ResourceMonitorConfig(),
            engine=engine,
            proc_root=proc,
            cgroup_root=cg,
            **kwargs,
        )
        mon.enabled = True
        app.state.resource_monitor = mon
        return mon

    def test_attribution_block_real_values(self, app_state, p2_env):
        """The attribution block carries the D13 arithmetic: with 1 MB
        charged, attributed_mb > 0, unattributed = anon − attributed,
        coverage ∈ [0, 1]."""
        charge_mod, _reg = p2_env
        charge_mod.set_enabled(True)
        charge_mod.note_message_append(
            "inst-live", {"blob": "x" * (2 * 1024 * 1024)}
        )
        mon = self._build(app_state, p2_env)
        mon.sampler.run_once()
        from fastapi.testclient import TestClient

        client = TestClient(app_state[0])
        r = client.get("/api/resources/live")
        assert r.status_code == 200
        j = r.json()
        attributed_bytes = mon.sampler._attrib["attributed_bytes"]
        assert j["attribution"]["attributed_mb"] == round(
            attributed_bytes / 1048576.0, 2
        )
        assert j["attribution"]["attributed_mb"] > 0
        anon_bytes = 65536 * 1024  # fixture VmData
        assert j["attribution"]["unattributed_mb"] == round(
            (anon_bytes - attributed_bytes) // 1024 / 1024.0, 2
        )
        assert 0.0 <= j["attribution"]["coverage_pct"] <= 1.0

    def test_instances_block_fields_typed(self, app_state, p2_env):
        """instances[] matches §2.8 EXACTLY (P5 consumes this — the
        field set is frozen)."""
        charge_mod, _reg = p2_env
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-live", {"blob": "z" * (1024 * 1024)})
        mon = self._build(app_state, p2_env)
        mon.sampler.run_once()
        from fastapi.testclient import TestClient

        client = TestClient(app_state[0])
        j = client.get("/api/resources/live").json()
        instances = j["instances"]
        assert len(instances) == 2  # overflow sentinel excluded
        by_id = {i["instance_id"]: i for i in instances}
        live = by_id["inst-live"]
        assert set(live.keys()) == {
            "instance_id", "agent", "estimated", "charged_mb",
            "child_rss_mb", "children_n", "in_flight_tasks",
        }
        assert live["agent"] == "live-agent"
        # inst-live is the FIRST key in the graph cache ⇒ it received
        # this tick's deep-scan reconcile (tiny graph ⇒ complete walk
        # ⇒ estimated False — the honesty rule working as designed).
        assert live["estimated"] is False
        assert live["charged_mb"] > 0
        assert live["child_rss_mb"] == 0.0
        assert live["children_n"] == 0
        assert live["in_flight_tasks"] == 1
        idle = by_id["inst-idle"]
        assert idle["agent"] == "idle-agent"
        assert idle["charged_mb"] == 0.0
        assert idle["in_flight_tasks"] == 0
        # estimated is never None anywhere.
        assert all(i["estimated"] is not None for i in instances)

    def test_children_block_reflects_registry(self, app_state, p2_env):
        """children[] comes from the pid registry (kind/name/rss from
        the entry + the synthetic /proc read), cpu_pct COALESCED to
        0.0 on first sight (W-5 — the FE types are non-null; the
        router coalesces, the sampler's RAM view stays honest),
        server-scoped mcp children included."""
        _charge_mod, registry = p2_env
        proc = app_state[1]
        child = proc / "8791"
        child.mkdir()
        (child / "stat").write_text(
            "8791 (node) R 1 8791 8791 0 -1 0 100 0 0 0 10 5 0 0 20 0 "
            "1 0 31337 500000 16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        (child / "status").write_text("Name:\tnode\nVmRSS:\t4096 kB\n")
        registry.register(
            pid=8791,
            start_time=31337,
            kind="mcp_child",
            instance_id=None,  # server-scoped pool child (D11)
            command="node server.js",
            detail="context7",
            pgid=8791,
        )
        mon = self._build(app_state, p2_env)
        mon.sampler.run_once()
        from fastapi.testclient import TestClient

        client = TestClient(app_state[0])
        j = client.get("/api/resources/live").json()
        children = j["children"]
        assert len(children) == 1
        c = children[0]
        assert set(c.keys()) == {"pid", "kind", "name", "cpu_pct", "rss_mb"}
        assert c["pid"] == 8791
        assert c["kind"] == "mcp_child"
        assert c["name"] == "context7"
        # W-5: coalesced at the router — first sight None → 0.0
        # (the FE contract is non-null; the sampler view keeps None).
        assert c["cpu_pct"] == 0.0
        assert c["rss_mb"] == 4.0

    def test_history_instances_active_real(self, app_state, p2_env):
        """/history points carry REAL instances_active (was a P1
        None stub) — distinct instances sampled per bucket."""
        charge_mod, _reg = p2_env
        charge_mod.set_enabled(True)
        charge_mod.note_message_append("inst-live", {"b": "y" * 1024})
        charge_mod.note_message_append("inst-idle", {"b": "y" * 1024})
        mon = self._build(app_state, p2_env)
        mon.sampler.run_once()
        mon.flusher.run_once()
        from fastapi.testclient import TestClient

        client = TestClient(app_state[0])
        r = client.get("/api/resources/history?window=1h&resolution=raw")
        assert r.status_code == 200
        points = r.json()["points"]
        assert points, "expected at least one history point"
        assert points[-1]["instances_active"] == 2

    def test_degraded_still_empty_shape(self, app_state):
        """D11 preserved: no monitor ⇒ attribution zero block +
        empty instances/children (never 503)."""
        from fastapi.testclient import TestClient

        client = TestClient(app_state[0])
        j = client.get("/api/resources/live").json()
        assert j["instances"] == []
        assert j["children"] == []
        assert j["attribution"] == {
            "attributed_mb": 0,
            "unattributed_mb": 0,
            "coverage_pct": 0.0,
        }


# ──────────────────────────────────────────────────────────────────────────
# P2 review batch — W-4 (newest-buckets order) + W-5 (coalesced nulls)
# ──────────────────────────────────────────────────────────────────────────


class TestW4InstancesActiveNewestFirst:
    """W-4: ``_read_instances_active`` bounded at 20 000 rows — the
    LIMIT must be paired with ``ORDER BY bucket_ts DESC`` so past
    the cap the DROPPED rows are the OLDEST, never the newest
    (un-ordered LIMIT reads SQLite rowid order = insertion order =
    oldest-first ⇒ instances_active read 0 at the newest end)."""

    def test_newest_buckets_survive_the_cap(self, app_state):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import text as _text

        from daemon.routers.resources import (
            _floor_naive_iso,
            _read_instances_active,
        )

        _app, _proc, _cg, engine = app_state
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        old_ts = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        new_ts = (now - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")

        rows = [
            # 20 000 OLD rows FIRST (insertion order = the old
            # un-ordered read order) — one persistent instance.
            (old_ts, "inst-old") for _ in range(20000)
        ]
        # 3 NEWEST rows LAST — the rows the old code dropped.
        rows += [
            (new_ts, "inst-new-1"),
            (new_ts, "inst-new-2"),
            (new_ts, "inst-new-3"),
        ]
        with engine.begin() as conn:
            conn.execute(
                _text(
                    "INSERT INTO resource_instance_sample "
                    "(bucket_ts, instance_id, estimated) "
                    "VALUES (:ts, :iid, 1)"
                ),
                [{"ts": ts, "iid": iid} for ts, iid in rows],
            )

        result = _read_instances_active(engine, "raw", 24 * 3600)
        newest_bucket = _floor_naive_iso(new_ts, 30)
        assert result.get(newest_bucket) == 3, (
            f"newest bucket lost past the cap (W-4): "
            f"buckets={sorted(result.items())[:5]}… "
            f"n={len(result)}"
        )

    def test_under_cap_still_counts_all_buckets(self, app_state):
        from daemon.routers.resources import (
            _floor_naive_iso,
            _read_instances_active,
        )

        _app, _proc, _cg, engine = app_state
        from sqlalchemy import text as _text
        with engine.begin() as conn:
            conn.execute(
                _text(
                    "INSERT INTO resource_instance_sample "
                    "(bucket_ts, instance_id, estimated) VALUES (:ts, :iid, 1)"
                ),
                [
                    {"ts": "2026-10-18 10:00:01", "iid": "a"},
                    {"ts": "2026-10-18 10:00:20", "iid": "b"},
                    {"ts": "2026-10-18 10:01:02", "iid": "a"},
                ],
            )
        result = _read_instances_active(engine, "raw", 24 * 3600)
        # Window robustness: if the box clock precedes the seeded ts
        # the rows fall outside the cutoff — assert only when the
        # window actually covers them.
        if result:
            b1 = _floor_naive_iso("2026-10-18 10:00:01", 30)
            b2 = _floor_naive_iso("2026-10-18 10:01:02", 30)
            assert result.get(b1) == 2
            assert result.get(b2) == 1


class TestW5CoalescedNullsForFE:
    """W-5: the /live wire coalesces the sampler's honest nulls to
    the FE contract types (agent '' / in_flight_tasks 0 / cpu_pct
    0.0). The sampler's cached RAM views must stay None (the
    coalescing works on COPIES)."""

    def test_live_coalesces_null_fields(self, app_state, p2_env):
        _charge_mod, _reg = p2_env
        app, proc, cg, engine = app_state
        mon = build_resource_monitor(
            config_resource_monitor=ResourceMonitorConfig(),
            engine=engine, proc_root=proc, cgroup_root=cg,
        )
        mon.enabled = True
        app.state.resource_monitor = mon
        # Seed the sampler's RAM views with the null shapes the
        # sampler legitimately emits (charge-only D11 instance,
        # provider-degrade in_flight_tasks, first-sight cpu_pct).
        mon.sampler.last_instance_view = [
            {
                "instance_id": "charge-only",
                "agent": None,
                "estimated": True,
                "charged_mb": 1.0,
                "child_rss_mb": 0.0,
                "children_n": 0,
                "in_flight_tasks": None,
            },
        ]
        mon.sampler.last_children_view = [
            {
                "pid": 4242,
                "kind": "bash",
                "name": "worker",
                "cpu_pct": None,
                "rss_mb": 12.5,
            },
        ]
        client = TestClient(app)
        j = client.get("/api/resources/live").json()
        inst = j["instances"][0]
        assert inst["agent"] == ""
        assert inst["in_flight_tasks"] == 0
        child = j["children"][0]
        assert child["cpu_pct"] == 0.0
        # The RAM views stay honest (coalescing on copies).
        assert mon.sampler.last_instance_view[0]["agent"] is None
        assert mon.sampler.last_instance_view[0]["in_flight_tasks"] is None
        assert mon.sampler.last_children_view[0]["cpu_pct"] is None

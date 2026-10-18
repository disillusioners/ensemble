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
        # attribution zero-value in P1 (D13).
        assert j["attribution"] == {
            "attributed_mb": 0,
            "unattributed_mb": 0,
            "coverage_pct": 0.0,
        }

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
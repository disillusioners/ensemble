"""P3 traceback join layer — unit pack (``tests/unit/services``, D4).

Covers, per phase3-plan task breakdown:

* the 🔴 mandated TZ equivalence contract (§TZ / task 1) — an event
  row stamped aware ``09:00+07:00`` and a task row stamped naive
  ``02:00`` (the SAME instant) are both seen at ``T=02:00Z``, and the
  same window queried as naive ``02:00`` vs aware ``09:00+07:00``
  produces IDENTICAL serialized JSON;
* the 7 §2.6 join questions J1-J7 (tasks 2-5) — each with ≥1 positive
  and ≥1 boundary/negative case;
* the LIVE structural block (task 6) — present / raising / absent
  providers degrade to ``null``, never raise;
* pin test (a) — the excluded-spine AST scan of
  ``daemon/services/resource_traceback.py`` (task 8): no import of or
  attribute reference to the boot-wiped queue spine or any
  checkpoint-store module (docstring MENTIONS are deliberate — the
  module must self-document its exclusions — so the pin walks the AST,
  not raw grep);
* pin test (b) — SQLite ``EXPLAIN QUERY PLAN`` assertions that the
  layer's actual SQL uses ``idx_event_instance_created`` (J1),
  ``idx_task_status_created`` (J2), the ``(instance_id, bucket_ts)``
  index (J5) and ``(kind, spawned_at)`` (J6) — a flip to TABLE SCAN
  fails the gate.

FENCE (2026-10-12 convention): synthetic fixtures only — tmp_path
SQLite file, synthetic harness tables created from the SQLModel
metadata, FAKE_HOME escape guard; never live-shaped ``/proc`` or
HOME-derived paths; no live-DB dependency; no new runtime deps.
"""

from __future__ import annotations

import ast
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.models.resource_monitor import (
    ResourceDaemonBucket,
    ResourceInstanceSample,
    ResourcePidMap,
)
from daemon.repositories.event.models import Event as EventModel
from daemon.repositories.instance.models import Instance as InstanceModel
from daemon.repositories.message_metadata.models import (
    MessageMetadata as MessageMetadataModel,
)
from daemon.repositories.service_tool.models import (
    ServiceTracking as ServiceTrackingModel,
)
from daemon.repositories.task.models import Task as TaskModel
from daemon.services import resource_traceback as rt


# ──────────────────────────────────────────────────────────────────────────
# Fence — never resolve live-shaped paths (incident class 2026-10-12)
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
# Fixtures + seed helpers (synthetic harness tables, tmp_path SQLite)
# ──────────────────────────────────────────────────────────────────────────

_SPINE_TABLES = [
    TaskModel.__table__,
    EventModel.__table__,
    MessageMetadataModel.__table__,
    InstanceModel.__table__,
    ServiceTrackingModel.__table__,
    ResourceInstanceSample.__table__,
    ResourcePidMap.__table__,
    ResourceDaemonBucket.__table__,
]


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'traceback_test.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng, tables=_SPINE_TABLES)
    return eng


def _nn_defaults(eng, table):
    """NOT NULL columns without a SQL default — supply inert values."""
    out = {}
    for col in inspect(eng).get_columns(table):
        if not col["nullable"] and col["default"] is None and col["name"] != "id":
            py = col["type"].python_type
            out[col["name"]] = {int: 0, bool: 0, str: "", float: 0.0}.get(py, "")
    return out


def _insert(eng, table, cols):
    full = {**_nn_defaults(eng, table), **cols}
    names = ",".join(full)
    ph = ",".join(f":{k}" for k in full)
    with eng.begin() as conn:
        conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({ph})"), full)


def seed_instance(eng, iid, status="running", created_at="2026-10-18T00:30:00+00:00",
                  agent_name="dev"):
    _insert(eng, "instances", {
        "instance_id": iid, "agent_id": agent_name, "agent_dir": f"/agents/{agent_name}",
        "agent_name": agent_name, "status": status, "created_at": created_at,
        "updated_at": created_at, "version": 1,
    })


def seed_event(eng, iid, status, created_at_str):
    # RAW insert on purpose — the offset must survive SQLite TEXT
    # storage (the ORM DATETIME bind would strip it).
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO event (instance_id, kind, data, created_at) "
                 "VALUES (:iid, 'instance_lifecycle', :data, :ts)"),
            {"iid": iid, "data": json.dumps({"status": status}), "ts": created_at_str},
        )


def seed_task(eng, tid, iid, created_at, completed_at=None, message_id=None,
              work_id=None, status="running"):
    _insert(eng, "task", {
        "id": tid, "work_id": work_id or f"w-{tid}", "instance_id": iid,
        "message_id": message_id, "status": status, "created_at": created_at,
        "completed_at": completed_at, "task_type": "process_message",
    })


def seed_meta(eng, iid, mid, created_at="2026-10-18T01:00:00+00:00"):
    _insert(eng, "message_metadata", {
        "thread_id": iid, "message_id": mid, "created_at": created_at,
    })


def seed_sample(eng, iid, bucket_ts, agent_id="dev", charged=1048576,
                status="running", peak_child_kb=None):
    _insert(eng, "resource_instance_sample", {
        "bucket_ts": bucket_ts, "instance_id": iid, "agent_id": agent_id,
        "status": status, "charged_bytes": charged, "estimated": 1,
        "child_rss_kb": peak_child_kb,
    })


def seed_child(eng, pid, kind, spawned_at, exited_at=None, iid="i-alive",
               command="chromium --headless --render", peak=262144, start_time=None):
    _insert(eng, "resource_pid_map", {
        "pid": pid, "kind": kind, "command": command, "instance_id": iid,
        "spawned_at": spawned_at, "exited_at": exited_at,
        "peak_rss_kb": peak, "start_time": start_time,
    })


def seed_service(eng, pid, iid, start_time=777):
    _insert(eng, "service_tracking", {
        "name": f"svc-{pid}", "pid": pid, "start_time": start_time,
        "started_by_instance_id": iid, "status": "running",
        "command": "svc",
    })


def T(hour=2, minute=0, second=0):
    """The canonical at-instant: 2026-10-18 02:00Z."""
    return datetime(2026, 10, 18, hour, minute, second, tzinfo=timezone.utc)


# ──────────────────────────────────────────────────────────────────────────
# Task 1 — the mandated TZ seam tests
# ──────────────────────────────────────────────────────────────────────────


class TestTzSeam:
    def test_mandated_event_and_task_same_instant_both_seen(self, engine):
        """event aware 09:00+07:00 ≡ task naive 02:00 — BOTH seen at 02:00Z."""
        seed_instance(engine, "i-tz")
        seed_event(engine, "i-tz", "running", "2026-10-18 09:00:00.000000+07:00")
        seed_task(engine, 1, "i-tz", "2026-10-18 02:00:00.000000")

        alive = rt.instances_alive_at(engine, T())
        assert [r["instance_id"] for r in alive] == ["i-tz"]
        assert alive[0]["source"] == "event"

        in_flight = rt.in_flight_tasks_at(engine, T())
        assert [r["task_id"] for r in in_flight] == ["1"]

    def test_naive_window_vs_plus07_window_identical_json(self, engine):
        """The same instant queried naive vs +07:00 ⇒ identical JSON."""
        seed_instance(engine, "i-tz")
        seed_event(engine, "i-tz", "running", "2026-10-18 09:00:00.000000+07:00")
        seed_task(engine, 1, "i-tz", "2026-10-18 02:00:00.000000", message_id="m-1")
        seed_meta(engine, "i-tz", "m-1")
        seed_sample(engine, "i-tz", "2026-10-18 01:59:35")
        seed_child(engine, 111, "chart_render", "2026-10-18 01:59:00", iid="i-tz")

        v_naive = rt.build_window_view(engine, "2026-10-18T02:00:00", "2026-10-18T02:00:00")
        v_plus7 = rt.build_window_view(
            engine, "2026-10-18T09:00:00+07:00", "2026-10-18T09:00:00+07:00"
        )
        assert json.dumps(v_naive, sort_keys=True) == json.dumps(v_plus7, sort_keys=True)
        assert v_naive["window"]["t0"] == "2026-10-18T02:00:00+00:00"

    def test_z_suffix_param_accepted(self):
        assert rt.parse_window_param("t1", "2026-10-18T02:00:00Z") == T()

    def test_naive_param_assumes_utc(self):
        assert rt.parse_window_param("t0", "2026-10-18T02:00:00") == T()

    def test_offset_param_converts(self):
        assert rt.parse_window_param("t0", "2026-10-18T09:00:00+07:00") == T()

    def test_event_offset_survives_raw_read(self, engine):
        """Rule 3: the spine read path keeps the stored offset."""
        seed_event(engine, "i-tz", "running", "2026-10-18 09:00:00.000000+07:00")
        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT created_at FROM event WHERE instance_id='i-tz'")
            ).scalar()
        assert isinstance(raw, str) and raw.endswith("+07:00")
        assert rt._read_dt(raw) == T(hour=2)

    def test_naive_digits_derived_from_coerce(self):
        # The companion must funnel through coerce_to_aware_utc — a
        # +07:00 input normalizes before digit-stripping.
        assert rt._naive_digits(datetime(2026, 10, 18, 9, 0, tzinfo=timezone(timedelta(hours=7)))) == (
            "2026-10-18 02:00:00.000000"
        )


# ──────────────────────────────────────────────────────────────────────────
# Task 2 — J1 alive-at-T / J2 in-flight-at-T
# ──────────────────────────────────────────────────────────────────────────


class TestJ1AliveAtT:
    def test_alive_via_lifecycle_event(self, engine):
        seed_instance(engine, "i-a", status="running")
        seed_event(engine, "i-a", "running", "2026-10-18 01:00:00.000000+00:00")
        alive = rt.instances_alive_at(engine, T())
        assert [(r["instance_id"], r["source"]) for r in alive] == [("i-a", "event")]

    def test_alive_via_current_status_fallback(self, engine):
        """No event coverage ≤ T ⇒ CURRENT snapshot decides (labelled)."""
        seed_instance(engine, "i-b", status="running")
        alive = rt.instances_alive_at(engine, T())
        assert [(r["instance_id"], r["source"]) for r in alive] == [
            ("i-b", "status-snapshot")
        ]

    def test_terminated_before_T_excluded_event_source(self, engine):
        seed_instance(engine, "i-c", status="terminated")
        seed_event(engine, "i-c", "terminated", "2026-10-18 01:30:00.000000+00:00")
        assert rt.instances_alive_at(engine, T()) == []

    def test_terminated_before_T_excluded_snapshot_source(self, engine):
        seed_instance(engine, "i-c2", status="failed")
        assert rt.instances_alive_at(engine, T()) == []

    def test_terminal_after_T_stays_alive(self, engine):
        """Running at T (event 01:00), terminated at 02:30 (after T)
        ⇒ still alive AT T. A now-terminal instance with NO event ≤ T
        stays excluded — without at-T evidence the snapshot union is
        scoped to still-running instances only (§2.6)."""
        seed_instance(engine, "i-d", status="terminated")
        seed_event(engine, "i-d", "running", "2026-10-18 01:00:00.000000+00:00")
        seed_event(engine, "i-d", "terminated", "2026-10-18 02:30:00.000000+00:00")
        alive = rt.instances_alive_at(engine, T())
        assert [r["instance_id"] for r in alive] == ["i-d"]
        assert alive[0]["status"] == "running"  # the AT-T status, not the current one

    def test_terminal_now_without_preT_event_excluded(self, engine):
        """No lifecycle evidence ≤ T + terminal now ⇒ not claimed alive
        (cannot distinguish terminated-before-T from after-T)."""
        seed_instance(engine, "i-d2", status="terminated")
        seed_event(engine, "i-d2", "terminated", "2026-10-18 02:30:00.000000+00:00")
        assert rt.instances_alive_at(engine, T()) == []

    def test_created_after_T_excluded(self, engine):
        seed_instance(engine, "i-e", status="running",
                      created_at="2026-10-18T05:00:00+00:00")
        assert rt.instances_alive_at(engine, T()) == []

    def test_latest_event_wins_per_instance(self, engine):
        seed_instance(engine, "i-f", status="running")
        seed_event(engine, "i-f", "running", "2026-10-18 00:30:00.000000+00:00")
        seed_event(engine, "i-f", "paused", "2026-10-18 01:45:00.000000+00:00")
        alive = rt.instances_alive_at(engine, T())
        assert len(alive) == 1 and alive[0]["status"] == "paused"

    def test_empty_registry_returns_empty(self, engine):
        assert rt.instances_alive_at(engine, T()) == []

    def test_limit_bounds_list(self, engine):
        for n in range(5):
            seed_instance(engine, f"i-l{n}")
        assert len(rt.instances_alive_at(engine, T(), limit=3)) == 3


class TestJ2InFlightAtT:
    def test_in_flight_null_completed(self, engine):
        seed_task(engine, 1, "i-x", "2026-10-18 01:00:00.000000", completed_at=None)
        rows = rt.in_flight_tasks_at(engine, T())
        assert [r["task_id"] for r in rows] == ["1"]

    def test_excluded_once_completed_before_T(self, engine):
        seed_task(engine, 1, "i-x", "2026-10-18 00:30:00.000000",
                  completed_at="2026-10-18 01:15:00.000000", status="failed")
        assert rt.in_flight_tasks_at(engine, T()) == []

    def test_terminal_now_completed_after_T_included(self, engine):
        """Finished at 02:30 (after T) ⇒ WAS in flight at T."""
        seed_task(engine, 2, "i-x", "2026-10-18 00:00:00.000000",
                  completed_at="2026-10-18 02:30:00.000000", status="completed")
        rows = rt.in_flight_tasks_at(engine, T())
        assert [r["task_id"] for r in rows] == ["2"]
        assert rows[0]["status"] == "completed"  # CURRENT status is the honest label

    def test_created_after_T_excluded(self, engine):
        seed_task(engine, 3, "i-x", "2026-10-18 03:00:00.000000")
        assert rt.in_flight_tasks_at(engine, T()) == []

    def test_newest_first_and_limit(self, engine):
        for n in range(4):
            seed_task(engine, n, "i-x", f"2026-10-18 00:0{n}:00.000000")
        rows = rt.in_flight_tasks_at(engine, T(), limit=2)
        assert [r["task_id"] for r in rows] == ["3", "2"]


# ──────────────────────────────────────────────────────────────────────────
# Task 3 — J3 work/agent enrichment + J4 wallclock bridge
# ──────────────────────────────────────────────────────────────────────────


class TestJ3J4Enrichment:
    def test_work_id_and_agent_hop(self, engine):
        seed_task(engine, 1, "i-y", "2026-10-18 01:00:00.000000", work_id="w-unique",
                  message_id="m-1")
        seed_sample(engine, "i-y", "2026-10-18 01:59:35", agent_id="developer")
        rows = rt.in_flight_tasks_at(engine, T())
        assert rows[0]["work_id"] == "w-unique"
        assert rows[0]["agent_id"] == "developer"  # J3: one hop via the P2 sample

    def test_bridge_resolves_via_pk(self, engine):
        seed_task(engine, 1, "i-y", "2026-10-18 01:00:00.000000", message_id="m-1")
        seed_meta(engine, "i-y", "m-1", created_at="2026-10-18T01:00:00+00:00")
        rows = rt.in_flight_tasks_at(engine, T())
        assert rows[0]["bridge"] == "2026-10-18T01:00:00+00:00"

    def test_missing_metadata_row_degrades_to_none(self, engine):
        seed_task(engine, 1, "i-y", "2026-10-18 01:00:00.000000", message_id="m-missing")
        rows = rt.in_flight_tasks_at(engine, T())  # must not raise
        assert rows[0]["bridge"] is None

    def test_missing_message_id_degrades_to_none(self, engine):
        seed_task(engine, 1, "i-y", "2026-10-18 01:00:00.000000", message_id=None)
        rows = rt.in_flight_tasks_at(engine, T())
        assert rows[0]["bridge"] is None


# ──────────────────────────────────────────────────────────────────────────
# Task 4 — J5 nearest-bucket resource lookup
# ──────────────────────────────────────────────────────────────────────────


class TestJ5NearestSample:
    def _seed_near(self, engine, bucket="2026-10-18 01:59:35"):
        seed_sample(engine, "i-s", bucket, charged=4096)

    def test_exact_tick_hit(self, engine):
        self._seed_near(engine, "2026-10-18 02:00:00")
        row = rt.nearest_sample_at(engine, "i-s", T())
        assert row is not None and row["charged_bytes"] == 4096
        assert row["sampled_at"] == T()

    def test_29s_old_bucket_accepted(self, engine):
        self._seed_near(engine, "2026-10-18 01:59:31")  # 29s before T
        assert rt.nearest_sample_at(engine, "i-s", T()) is not None

    def test_30s_boundary_accepted(self, engine):
        self._seed_near(engine, "2026-10-18 01:59:30")
        assert rt.nearest_sample_at(engine, "i-s", T()) is not None

    def test_stale_bucket_returns_none(self, engine):
        """>interval-stale ⇒ honest gap (None), never interpolated."""
        self._seed_near(engine, "2026-10-18 01:59:29")  # 31s before T
        assert rt.nearest_sample_at(engine, "i-s", T()) is None

    def test_unknown_instance_none(self, engine):
        assert rt.nearest_sample_at(engine, "i-ghost", T()) is None

    def test_tolerance_none_disables_freshness(self, engine):
        self._seed_near(engine, "2026-10-17 02:00:00")  # a day old
        row = rt.nearest_sample_at(engine, "i-s", T(), tolerance_s=None)
        assert row is not None

    def test_tolerance_configurable(self, engine):
        self._seed_near(engine, "2026-10-18 01:59:29")
        assert rt.nearest_sample_at(engine, "i-s", T(), tolerance_s=60) is not None


# ──────────────────────────────────────────────────────────────────────────
# Task 5 — J6 children-at-T + J7 historical pid→instance
# ──────────────────────────────────────────────────────────────────────────


class TestJ6ChildrenAtT:
    def test_live_child_exited_null(self, engine):
        seed_child(engine, 111, "chart_render", "2026-10-18 01:00:00", exited_at=None)
        rows = rt.children_at_t(engine, T())
        assert len(rows) == 1
        assert rows[0]["exited_at"] is None
        assert rows[0]["name"] == "chromium"  # basename of the sanitized command

    def test_exited_child_excluded_at_T(self, engine):
        seed_child(engine, 111, "bash", "2026-10-18 00:30:00",
                   exited_at="2026-10-18 01:00:00")
        assert rt.children_at_t(engine, T()) == []

    def test_exited_after_T_included(self, engine):
        seed_child(engine, 111, "bash", "2026-10-18 00:30:00",
                   exited_at="2026-10-18 02:30:00")
        rows = rt.children_at_t(engine, T())
        assert [r["pid"] for r in rows] == [111]

    def test_spawned_after_T_excluded(self, engine):
        seed_child(engine, 111, "bash", "2026-10-18 03:00:00")
        assert rt.children_at_t(engine, T()) == []

    def test_kind_filter_chart_render(self, engine):
        seed_child(engine, 111, "chart_render", "2026-10-18 00:59:00", peak=262144)
        seed_child(engine, 222, "bash", "2026-10-18 00:58:00")
        assert [r["pid"] for r in rt.children_at_t(engine, T(), kind="chart_render")] == [111]
        assert len(rt.children_at_t(engine, T())) == 2  # unfiltered = both

    def test_unknown_kind_filter_empty(self, engine):
        seed_child(engine, 111, "bash", "2026-10-18 00:58:00")
        assert rt.children_at_t(engine, T(), kind="chart_render") == []

    def test_chart_render_aggregate(self, engine):
        seed_child(engine, 111, "chart_render", "2026-10-18 00:59:00", peak=262144)
        seed_child(engine, 112, "chart_render", "2026-10-18 00:30:00",
                   exited_at="2026-10-18 01:30:00", peak=131072)  # gone before T
        seed_child(engine, 113, "chart_render", "2026-10-18 00:20:00", peak=65536)
        agg = rt.chart_render_aggregate(engine, T())
        assert agg == {"kind": "chart_render", "count": 2, "total_peak_rss_kb": 327680}

    def test_chart_render_aggregate_empty(self, engine):
        assert rt.chart_render_aggregate(engine, T()) == {
            "kind": "chart_render", "count": 0, "total_peak_rss_kb": 0
        }


class TestJ7PidResolve:
    def test_map_row_resolves(self, engine):
        seed_child(engine, 111, "bash", "2026-10-18 00:58:00", iid="i-p", start_time=555)
        row = rt.resolve_pid_instance(engine, 111)
        assert row["instance_id"] == "i-p" and row["source"] == "resource_pid_map"

    def test_pid_recycle_start_time_match_wins(self, engine):
        """Two rows, same pid, different start_time ⇒ correct era wins.
        The recycled-OUT era is EXITED (the live-pid partial UNIQUE
        forbids two live rows for one pid)."""
        seed_child(engine, 111, "bash", "2026-10-17 00:58:00", iid="i-old",
                   exited_at="2026-10-17 12:00:00", start_time=100)
        seed_child(engine, 111, "bash", "2026-10-18 01:58:00", iid="i-new",
                   start_time=900)
        row = rt.resolve_pid_instance(engine, 111, start_time=900)
        assert row["instance_id"] == "i-new"
        row = rt.resolve_pid_instance(engine, 111, start_time=100)
        assert row["instance_id"] == "i-old"

    def test_pid_recycle_no_match_falls_to_latest(self, engine):
        seed_child(engine, 111, "bash", "2026-10-17 00:58:00", iid="i-old",
                   exited_at="2026-10-17 12:00:00", start_time=100)
        seed_child(engine, 111, "bash", "2026-10-18 01:58:00", iid="i-new",
                   start_time=900)
        row = rt.resolve_pid_instance(engine, 111, start_time=424242)
        assert row["instance_id"] == "i-new"  # newest spawned_at row

    def test_service_tracking_fallback(self, engine):
        seed_service(engine, 333, "i-live", start_time=777)
        row = rt.resolve_pid_instance(engine, 333)
        assert row["source"] == "service_tracking"
        assert row["instance_id"] == "i-live"

    def test_neither_spine_none(self, engine):
        assert rt.resolve_pid_instance(engine, 999999) is None


# ──────────────────────────────────────────────────────────────────────────
# Task 6 — structural block (LIVE at query time, injected providers)
# ──────────────────────────────────────────────────────────────────────────


class TestStructural:
    def test_providers_present(self):
        out = rt.collect_structural({
            "queue_depth": lambda: 3, "workers": lambda: 8,
            "checkpoints": lambda: 4242, "gc_events": lambda: 99,
        })
        assert out == {"queue_depth": 3, "workers": 8, "checkpoints": 4242,
                       "gc_events": 99}

    def test_raising_provider_degrades_to_none(self):
        out = rt.collect_structural({
            "queue_depth": lambda: 1 / 0, "workers": lambda: 8,
        })
        assert out == {"queue_depth": None, "workers": 8, "checkpoints": None,
                       "gc_events": None}

    def test_absent_providers_all_none(self):
        assert rt.collect_structural(None) == {
            "queue_depth": None, "workers": None, "checkpoints": None,
            "gc_events": None,
        }
        assert rt.collect_structural({}) == {
            "queue_depth": None, "workers": None, "checkpoints": None,
            "gc_events": None,
        }

    def test_non_int_value_degrades_to_none(self):
        assert rt.collect_structural({"workers": lambda: "many"})["workers"] is None


# ──────────────────────────────────────────────────────────────────────────
# Assembly — build_window_view
# ──────────────────────────────────────────────────────────────────────────


class TestBuildWindowView:
    def test_happy_shape_and_findings_reserved(self, engine):
        seed_instance(engine, "i-v")
        seed_child(engine, 111, "chart_render", "2026-10-18 01:59:00", iid="i-v")
        view = rt.build_window_view(engine, "2026-10-18T02:00:00", "2026-10-18T02:00:10")
        assert set(view) == {
            "window", "instances_alive", "in_flight_tasks", "spawned_children",
            "structural", "findings", "_degraded",
        }
        assert view["findings"] == []  # key reserved for P4
        assert view["_degraded"] == []
        assert view["window"] == {
            "t0": "2026-10-18T02:00:00+00:00", "t1": "2026-10-18T02:00:10+00:00"
        }
        assert view["structural"].keys() == {
            "queue_depth", "workers", "checkpoints", "gc_events"
        }

    def test_wire_rows_carry_ids_and_timestamps_only(self, engine):
        """D14: no message bodies / tool args / raw commands on the view."""
        seed_instance(engine, "i-v")
        seed_task(engine, 1, "i-v", "2026-10-18 01:00:00.000000", message_id="m-1")
        seed_child(engine, 111, "bash", "2026-10-18 01:00:00",
                   command="bash -c SECRET_TOKEN_XYZ", iid="i-v")
        view = rt.build_window_view(engine, "2026-10-18T02:00:00", "2026-10-18T02:00:10")
        blob = json.dumps(view)
        assert "SECRET_TOKEN_XYZ" not in blob
        child = view["spawned_children"][0]
        assert set(child) == {"pid", "kind", "name", "spawned_at", "exited_at",
                              "instance_id"}

    def test_empty_window_valid_empty_shape(self, engine):
        view = rt.build_window_view(engine, "2026-10-18T02:00:00", "2026-10-18T02:00:10")
        assert view["instances_alive"] == []
        assert view["in_flight_tasks"] == []
        assert view["spawned_children"] == []
        assert view["findings"] == []
        assert view["_degraded"] == []

    def test_unknown_instance_ids_absent_not_error(self, engine):
        seed_instance(engine, "i-real")
        view = rt.build_window_view(engine, "2026-10-18T02:00:00", "2026-10-18T02:00:10")
        ids = [r["instance_id"] for r in view["instances_alive"]]
        assert "i-ghost" not in ids and ids == ["i-real"]

    def test_span_over_168h_raises(self, engine):
        with pytest.raises(ValueError, match="exceeds"):
            rt.build_window_view(engine, "2026-10-01T02:00:00", "2026-10-18T02:00:01")

    def test_t1_before_t0_raises(self, engine):
        with pytest.raises(ValueError, match="precede"):
            rt.build_window_view(engine, "2026-10-18T02:00:01", "2026-10-18T02:00:00")

    def test_bad_iso_raises(self, engine):
        with pytest.raises(ValueError, match="ISO-8601"):
            rt.build_window_view(engine, "not-a-time", "2026-10-18T02:00:00")

    def test_missing_param_raises(self, engine):
        with pytest.raises(ValueError, match="required"):
            rt.build_window_view(engine, "", "2026-10-18T02:00:00")

    def test_engine_none_degrades_not_raises(self):
        view = rt.build_window_view(None, "2026-10-18T02:00:00", "2026-10-18T02:00:10")
        assert view["instances_alive"] == []
        assert view["_degraded"] == ["engine-unavailable"]

    def test_broken_engine_degrades_sections(self):
        class _Broken:
            def connect(self):
                raise RuntimeError("boom")

        view = rt.build_window_view(
            _Broken(), "2026-10-18T02:00:00", "2026-10-18T02:00:10"
        )
        assert view["instances_alive"] == []
        assert view["in_flight_tasks"] == []
        assert view["spawned_children"] == []
        assert len(view["_degraded"]) == 3  # each section reported honestly

    def test_limit_clamped_into_range(self, engine):
        seed_instance(engine, "i-v")
        view = rt.build_window_view(engine, "2026-10-18T02:00:00",
                                    "2026-10-18T02:00:10", limit=100000)
        assert view["_degraded"] == []  # clamp, no crash
        assert len(view["instances_alive"]) == 1


# ──────────────────────────────────────────────────────────────────────────
# Task 8a — excluded-spine pin (AST scan — docstring mentions are allowed,
# code references are not)
# ──────────────────────────────────────────────────────────────────────────


class TestExcludedSpinePin:
    _MODULE = Path(rt.__file__)

    def _tree(self):
        return ast.parse(self._MODULE.read_text(encoding="utf-8"))

    def test_no_import_of_excluded_spines(self):
        """No import may touch the boot-wiped queue or a checkpoint store."""
        banned_module_fragments = ("message_queue", "checkpoint")
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for frag in banned_module_fragments:
                        assert frag not in alias.name, (
                            f"excluded-spine import: {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for frag in banned_module_fragments:
                    assert frag not in mod, f"excluded-spine import-from: {mod}"

    def test_no_attribute_or_name_reference_to_queue_spine(self):
        """No code reference to the boot-wiped queue spine (any casing)."""
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Attribute):
                assert "message_queue" not in node.attr.lower()
            elif isinstance(node, ast.Name):
                assert "message_queue" not in node.id.lower()

    def test_module_does_use_the_timestamps_seam(self):
        """Positive control: the TZ seam import contract (rule 1) holds."""
        src = self._MODULE.read_text(encoding="utf-8")
        assert "from daemon.services.timestamps import" in src
        assert "coerce_to_aware_utc" in src and "to_utc_iso" in src

    def test_docstring_names_exclusions(self):
        """Task 9: the module self-documents the closed spine set."""
        doc = ast.get_docstring(self._tree()) or ""
        for spine in ("task", "message_metadata", "event", "instances",
                      "service_tracking", "resource_"):
            assert spine in doc, f"docstring missing legal spine: {spine}"
        assert "message_queue" in doc and "checkpoint" in doc


# ──────────────────────────────────────────────────────────────────────────
# Task 8b — EXPLAIN QUERY PLAN index pins (SQLite; the layer's ACTUAL SQL)
# ──────────────────────────────────────────────────────────────────────────


def _plan(eng, compiled_sql, params):
    with eng.connect() as conn:
        rows = conn.execute(text(f"EXPLAIN QUERY PLAN {compiled_sql}"), params).fetchall()
    return [r[3] for r in rows]


def _compile(engine, stmt, params):
    return str(stmt.compile(engine, compile_kwargs={"literal_binds": True}))


class TestExplainIndexPins:
    """A flip to TABLE SCAN on the big predicates must FAIL this gate."""

    def test_j1_uses_idx_event_instance_created(self, engine):
        sql = _compile(
            engine, rt._LIFECYCLE_SQL,
            {"ids": ["i-a", "i-b"], "kind": "instance_lifecycle",
             "hi": "2026-10-18 02:00:00.000000+00:00",
             "lo": "2026-10-04 12:00:00.000000+00:00"},
        )
        details = _plan(engine, sql, {})
        assert any("idx_event_instance_created" in d for d in details), details
        assert not any(("SCAN " in d) and ("USING " not in d) for d in details), details

    def test_j2_uses_idx_task_status_created_both_arms(self, engine):
        params = {"t": "2026-10-18 02:00:00.000000", "cap": 100}
        for statuses in (list(rt._TASK_LIVE_STATUSES), list(rt._TASK_TERMINAL_STATUSES)):
            sql = _compile(
                engine, rt._INFLIGHT_SQL,
                {"statuses": statuses, **params},
            )
            details = _plan(engine, sql, {})
            assert any("idx_task_status_created" in d for d in details), details
            assert not any(("SCAN " in d) and ("USING " not in d) for d in details), details

    def test_j5_uses_instance_bucket_index(self, engine):
        sql = _compile(
            engine, rt._NEAREST_SAMPLE_SQL,
            {"iid": "i-a", "t": "2026-10-18 02:00:00.000000"},
        )
        details = _plan(engine, sql, {})
        assert any(
            "idx_resource_instance_sample_instance_bucket" in d for d in details
        ), details
        assert not any(("SCAN " in d) and ("USING " not in d) for d in details), details

    def test_j6_uses_kind_spawned_index(self, engine):
        sql = _compile(
            engine, rt._CHILDREN_SQL,
            {"kind": "chart_render", "t": "2026-10-18 02:00:00.000000", "limit": 100},
        )
        details = _plan(engine, sql, {})
        assert any("idx_resource_pid_map_kind_spawned" in d for d in details), details
        assert not any(("SCAN " in d) and ("USING " not in d) for d in details), details

    def test_j6_aggregate_uses_kind_spawned_index(self, engine):
        sql = _compile(
            engine, rt._CHART_AGG_SQL, {"t": "2026-10-18 02:00:00.000000"}
        )
        details = _plan(engine, sql, {})
        assert any("idx_resource_pid_map_kind_spawned" in d for d in details), details

    def test_no_filter_children_fan_keeps_index(self, engine):
        """The unfiltered fan (DISTINCT kinds + per-kind seeks) never
        degrades to a bare table scan on the fan query."""
        sql = _compile(
            engine, rt._DISTINCT_KINDS_SQL, {"t": "2026-10-18 02:00:00.000000"}
        )
        details = _plan(engine, sql, {})
        assert any("idx_resource_pid_map_kind_spawned" in d for d in details), details

    def test_j7_pid_lookups_use_pid_indexes(self, engine):
        for stmt in (rt._PID_MAP_SQL, rt._SERVICE_TRACKING_SQL):
            sql = _compile(engine, stmt, {"pid": 111})
            details = _plan(engine, sql, {})
            assert any("USING INDEX idx_" in d for d in details), details

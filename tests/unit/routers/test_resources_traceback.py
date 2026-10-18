"""``GET /api/resources/traceback`` — HTTP contract pack (D5).

Covers the phase3-plan task 7 router acceptance:

* happy path — FE-frozen shape (``TracebackPayload``), ``findings``
  reserved-empty, ``_degraded`` stripped from the wire;
* param validation — missing ``t0``/``t1`` ⇒ 422, unparseable ISO ⇒
  422, span > 168 h ⇒ 422, ``t1 < t0`` ⇒ 422, Z-suffixed params
  accepted, ``limit`` bounds 1..500 (422 outside);
* degrade-not-503 (D11) — a forced INTERNAL failure returns the
  empty-but-valid shape with HTTP 200 + WARN log + the
  ``X-Resources-Traceback-Degraded`` header (reason), so "join
  yielded nothing" stays distinguishable from "query failed" without
  breaking the frozen body contract; manager/monitor absence degrades
  the same way;
* LIVE structural wiring over the fake manager — gc counter real,
  absent queue/checkpoint tables and pools degrade to null;
* FE-contract parity pin — the response models' field names must
  match ``frontend/src/app/models/resources.models.ts``
  (``TracebackPayload`` + children, :202-248 at HEAD) exactly.

FENCE (2026-10-12 convention): tmp_path SQLite + synthetic tables,
FAKE_HOME escape guard; never live-shaped paths; no live DB.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
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
from daemon.routers.resources import (
    TracebackInFlightTask,
    TracebackInstanceAlive,
    TracebackResponse,
    TracebackSpawnedChild,
    TracebackStructural,
    TracebackWindow,
    router as resources_router,
)
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
# App harness
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


class _FakeManager:
    def __init__(self, engine):
        self.engine = engine


def _seed_minimal(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO instances (instance_id, agent_id, agent_dir, "
                "agent_name, status, created_at, updated_at, version) VALUES "
                "('i-live','dev','/agents/dev','dev','running',"
                "'2026-10-18T00:30:00+00:00','2026-10-18T00:30:00+00:00',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO event (instance_id, kind, data, created_at) VALUES "
                "('i-live','instance_lifecycle','{\"status\": \"running\"}',"
                "'2026-10-18 01:00:00.000000+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO resource_pid_map (pid, kind, command, instance_id, "
                "spawned_at, peak_rss_kb) VALUES "
                "(111,'chart_render','chromium --headless','i-live',"
                "'2026-10-18 01:59:00',262144)"
            )
        )


@pytest.fixture
def app_state(tmp_path):
    app = FastAPI()
    app.include_router(resources_router, prefix="/api")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'traceback_router_test.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_SPINE_TABLES)
    _seed_minimal(engine)
    app.state.manager = _FakeManager(engine)
    app.state.resource_monitor = None  # monitor absence must not matter here
    return app


# ──────────────────────────────────────────────────────────────────────────
# Happy path + FE-frozen shape
# ──────────────────────────────────────────────────────────────────────────


class TestTracebackHappyPath:
    def test_200_shape_findings_reserved(self, app_state):
        client = TestClient(app_state)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        assert r.status_code == 200
        body = r.json()
        # The wire carries EXACTLY the FE-frozen keys — the internal
        # ``_degraded`` channel is stripped by the response_model.
        assert set(body) == {
            "window", "instances_alive", "in_flight_tasks",
            "spawned_children", "structural", "findings",
        }
        assert body["findings"] == []  # key reserved for P4
        assert body["window"] == {
            "t0": "2026-10-18T02:00:00+00:00",  # Z-suffix echoed as aware ISO
            "t1": "2026-10-18T02:00:10+00:00",
        }

    def test_alive_and_children_populated(self, app_state):
        client = TestClient(app_state)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        body = r.json()
        assert [i["instance_id"] for i in body["instances_alive"]] == ["i-live"]
        assert body["instances_alive"][0]["agent"] == "dev"
        kids = body["spawned_children"]
        assert [k["pid"] for k in kids] == [111]
        assert kids[0]["kind"] == "chart_render"
        assert kids[0]["exited_at"] is None

    def test_kind_filter_param(self, app_state):
        client = TestClient(app_state)
        r = client.get(
            "/api/resources/traceback",
            params={
                "t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z",
                "kind": "chart_render",
            },
        )
        assert r.status_code == 200
        assert len(r.json()["spawned_children"]) == 1
        r = client.get(
            "/api/resources/traceback",
            params={
                "t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z",
                "kind": "bash",
            },
        )
        assert r.status_code == 200
        assert r.json()["spawned_children"] == []

    def test_structural_live_wiring_over_fake_manager(self, app_state):
        """gc counter is real; absent queue/checkpoint tables and pools
        degrade to null (never raise)."""
        client = TestClient(app_state)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        structural = r.json()["structural"]
        assert set(structural) == {"queue_depth", "workers", "checkpoints", "gc_events"}
        assert isinstance(structural["gc_events"], int) and structural["gc_events"] >= 0
        assert structural["queue_depth"] is None   # no message_queue table → probe raises → null
        assert structural["workers"] is None       # no pools on the fake manager
        assert structural["checkpoints"] is None   # no checkpoints table
        assert r.headers.get("X-Resources-Traceback-Degraded") is None

    def test_empty_window_still_valid(self, tmp_path):
        app = FastAPI()
        app.include_router(resources_router, prefix="/api")
        engine = create_engine(
            f"sqlite:///{tmp_path / 'empty.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine, tables=_SPINE_TABLES)
        app.state.manager = _FakeManager(engine)
        client = TestClient(app)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["instances_alive"] == []
        assert body["findings"] == []


# ──────────────────────────────────────────────────────────────────────────
# Param validation — 422s
# ──────────────────────────────────────────────────────────────────────────


class TestTracebackParamValidation:
    def _get(self, app_state, **params):
        return TestClient(app_state).get("/api/resources/traceback", params=params)

    def test_missing_t0_422(self, app_state):
        r = self._get(app_state, t1="2026-10-18T02:00:00Z")
        assert r.status_code == 422

    def test_missing_t1_422(self, app_state):
        r = self._get(app_state, t0="2026-10-18T02:00:00Z")
        assert r.status_code == 422

    def test_bad_iso_422(self, app_state):
        r = self._get(app_state, t0="not-a-time", t1="2026-10-18T02:00:00Z")
        assert r.status_code == 422

    def test_span_over_168h_422(self, app_state):
        r = self._get(app_state, t0="2026-10-01T00:00:00Z", t1="2026-10-18T02:00:00Z")
        assert r.status_code == 422

    def test_t1_before_t0_422(self, app_state):
        r = self._get(app_state, t0="2026-10-18T02:00:01Z", t1="2026-10-18T02:00:00Z")
        assert r.status_code == 422

    def test_limit_over_cap_422(self, app_state):
        r = self._get(
            app_state, t0="2026-10-18T02:00:00Z", t1="2026-10-18T02:00:10Z",
            limit=501,
        )
        assert r.status_code == 422

    def test_limit_zero_422(self, app_state):
        r = self._get(
            app_state, t0="2026-10-18T02:00:00Z", t1="2026-10-18T02:00:10Z",
            limit=0,
        )
        assert r.status_code == 422

    def test_max_span_168h_accepted(self, app_state):
        r = self._get(app_state, t0="2026-10-11T02:00:00Z", t1="2026-10-18T02:00:00Z")
        assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────────────
# Degrade-not-503 (D11)
# ──────────────────────────────────────────────────────────────────────────


class TestTracebackDegrade:
    def test_forced_internal_failure_200_empty_shape(self, app_state, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("join exploded")

        monkeypatch.setattr(rt, "build_window_view", _boom)
        client = TestClient(app_state)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        assert r.status_code == 200  # NEVER 503
        body = r.json()
        assert set(body) == {
            "window", "instances_alive", "in_flight_tasks",
            "spawned_children", "structural", "findings",
        }
        assert body["instances_alive"] == []
        assert body["findings"] == []
        assert r.headers.get("X-Resources-Traceback-Degraded") == "internal-error"

    def test_partial_degrade_surfaces_header_only(self, app_state, monkeypatch):
        """A section-level degrade keeps the payload, adds the header,
        and the ``_degraded`` key never reaches the wire."""

        def _partial(engine, t0, t1, **k):
            return {
                "window": {"t0": "2026-10-18T02:00:00+00:00",
                           "t1": "2026-10-18T02:00:10+00:00"},
                "instances_alive": [],
                "in_flight_tasks": [],
                "spawned_children": [],
                "structural": {"queue_depth": None, "workers": None,
                               "checkpoints": None, "gc_events": None},
                "findings": [],
                "_degraded": ["spawned_children: probe lost"],
            }

        monkeypatch.setattr(rt, "build_window_view", _partial)
        client = TestClient(app_state)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        assert r.status_code == 200
        assert "spawned_children" in r.headers.get(
            "X-Resources-Traceback-Degraded", ""
        )
        assert "_degraded" not in r.json()

    def test_default_substituted_manager_degrades_200(self, tmp_path):
        """No manager set on a bare app — the ROOT tests/conftest.py
        autouse fixture (``_ensure_app_state_manager``) substitutes a
        default manager, so the observable contract here is: still 200,
        still the valid empty-ish shape, never 503."""
        app = FastAPI()
        app.include_router(resources_router, prefix="/api")
        client = TestClient(app)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {
            "window", "instances_alive", "in_flight_tasks",
            "spawned_children", "structural", "findings",
        }
        assert body["findings"] == []

    def test_engine_none_degrades_200(self, tmp_path):
        app = FastAPI()
        app.include_router(resources_router, prefix="/api")
        app.state.manager = _FakeManager(None)
        client = TestClient(app)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        assert r.status_code == 200
        assert r.json()["instances_alive"] == []
        assert "engine-unavailable" in r.headers.get(
            "X-Resources-Traceback-Degraded", ""
        )


# ──────────────────────────────────────────────────────────────────────────
# FE-contract parity pin (byte-for-byte field names)
# ──────────────────────────────────────────────────────────────────────────


class TestFEContractParity:
    """Cross-checked against
    ``frontend/src/app/models/resources.models.ts`` (:202-248 at HEAD
    80c248ed1) — ``TracebackPayload`` and children. A rename on either
    side must fail one of these pins."""

    def test_payload_keys(self):
        assert list(TracebackResponse.model_fields) == [
            "window", "instances_alive", "in_flight_tasks",
            "spawned_children", "structural", "findings",
        ]

    def test_child_model_fields(self):
        assert list(TracebackWindow.model_fields) == ["t0", "t1"]
        assert list(TracebackInstanceAlive.model_fields) == [
            "instance_id", "agent", "status",
        ]
        assert list(TracebackInFlightTask.model_fields) == [
            "task_id", "work_id", "instance_id", "status", "created_at",
        ]
        assert list(TracebackSpawnedChild.model_fields) == [
            "pid", "kind", "name", "spawned_at", "exited_at", "instance_id",
        ]
        assert list(TracebackStructural.model_fields) == [
            "queue_depth", "workers", "checkpoints", "gc_events",
        ]

    def test_required_vs_optional_shape(self):
        # kind is the ONLY required child field (FE: `kind: string`);
        # everything else carries a default (FE `?` optionals).
        assert TracebackSpawnedChild.model_fields["kind"].is_required()
        assert not TracebackSpawnedChild.model_fields["pid"].is_required()
        assert TracebackInstanceAlive.model_fields["instance_id"].is_required()
        assert not TracebackInstanceAlive.model_fields["agent"].is_required()
        assert TracebackWindow.model_fields["t0"].is_required()
        assert TracebackResponse.model_fields["structural"].is_required()

    def test_wire_json_matches_ts_example_shape(self, app_state):
        client = TestClient(app_state)
        r = client.get(
            "/api/resources/traceback",
            params={"t0": "2026-10-18T02:00:00Z", "t1": "2026-10-18T02:00:10Z"},
        )
        alive = r.json()["instances_alive"][0]
        assert set(alive) <= {"instance_id", "agent", "status"}
        child = r.json()["spawned_children"][0]
        assert set(child) <= {"pid", "kind", "name", "spawned_at", "exited_at",
                              "instance_id"}
        # Timestamps on the wire are aware ISO (to_utc_iso contract).
        assert child["spawned_at"].endswith("+00:00")

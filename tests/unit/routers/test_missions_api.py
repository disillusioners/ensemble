"""Tests for the M2-API mission HTTP surface (M4-i pull-forward).

``GET /api/missions`` + ``GET /api/missions/{mission_id}`` — the HTTP
debut of the mission read-model projection (docs/job-task-system.md
§8.4). These tests pin the BINDING (HTTP wire), not just the resolver:

* **Kill-switch matrix** — ``ENSEMBLE_MISSION_PROJECTION_ENABLED`` OFF
  (default) ⇒ 404 on BOTH routes (fail-closed, routes still
  registered); ON ⇒ the normal contract.
* **List contract** — liveness single + comma-multi filters, agent_id,
  pagination bounds/offset/cap, SQL ordering
  (``last_activity_at DESC NULLS LAST`` + ``mission_id ASC`` tiebreak).
* **Detail contract** — unknown id ⇒ 404; full record incl.
  ``epoch=1`` + W4-aware ``terminal_reason``.
* **W4 ``dead_letter`` pin at the HTTP binding** — the S4 lesson: the
  dead-letter assertion runs through the ROUTES (list AND detail), not
  merely the resolver, so a regression that routes the detail path
  through ``project()`` (the ``dead_linked=False`` hazard) fails here.
* **Degradation — NO 500 anywhere** — real failing engines (dropped
  tables, not mocks): count/page leg ⇒ 200 empty page +
  ``total=null`` + ``degraded=true`` + exactly-one-warning; jobs leg ⇒
  200 rows with ``linked_jobs=[]``; detail instance leg ⇒ 200
  None-fields.
* **ENGINE-BOUND batched-query bound** — a ``before_cursor_execute``
  listener counts real SELECTs per request (the M1-gate pattern, NOT
  mock counting): the mission list issues exactly 3 SELECTs per page
  (count + page + batched JobItem IN-clause), FLAT as the page
  doubles — zero per-row lookups.

Harness notes
-------------

File-backed SQLite at ``tmp_path`` with ``NullPool`` + WAL +
``busy_timeout`` (the Testing & QC conventions recipe; the
QUARANTINE.md ``StaticPool + WriteGuardSession`` trap is not used).
Real repositories wired into the real ``MissionResolver`` — the SQL
level is genuinely exercised.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.services.mission_resolver as mr_mod
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401  (transitive dep)

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.routers.missions import (
    router as missions_router,
    set_missions_resolver,
)
from daemon.services.mission_resolver import (
    MissionResolver,
    _reset_mission_projection_for_tests,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    The conventions recipe: a real file (schema persists across
    NullPool connections), no shared connection, WAL + a generous
    busy timeout. Each test gets its own ``tmp_path`` file.
    """
    db_path = tmp_path / "missions-api-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def resolver(instance_repo, job_repo) -> MissionResolver:
    return MissionResolver(
        instance_repo=instance_repo,
        job_repo=job_repo,
    )


@pytest.fixture
def client(resolver: MissionResolver) -> TestClient:
    """TestClient with the missions router mounted under /api.

    Mirrors the api.py registration (``/api`` parent prefix) and the
    lifespan wiring (``set_missions_resolver``). The kill-switch is
    consulted per-request by the routes, so ON/OFF tests flip the env
    and reset the module cache via the autouse fixture below.
    """
    set_missions_resolver(resolver)
    app = FastAPI()
    app.include_router(missions_router, prefix="/api")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_mission_kill_switch():
    """Per-test: reset the kill-switch so the OFF default is fresh.

    Module-level cached state must never leak between tests (the same
    shape as ``tests/unit/services/test_mission_resolver.py``).
    """
    _reset_mission_projection_for_tests()
    os.environ.pop("ENSEMBLE_MISSION_PROJECTION_ENABLED", None)
    yield
    _reset_mission_projection_for_tests()
    os.environ.pop("ENSEMBLE_MISSION_PROJECTION_ENABLED", None)


@pytest.fixture
def flip_on():
    """Flip the kill-switch ON for the calling test (restart-read is
    bypassed in tests via the reset hook — production reads it once)."""
    os.environ["ENSEMBLE_MISSION_PROJECTION_ENABLED"] = "1"
    _reset_mission_projection_for_tests()
    yield
    # restore handled by the autouse fixture


# ─── Seed helpers (mirror test_mission_resolver.py) ────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    last_activity_at: datetime | None = None,
    created_at: str | None = None,
) -> str:
    """Insert a populated ``Instance`` row; return its id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=created_at or iso_now,
            updated_at=iso_now,
            last_activity_at=last_activity_at,
            paused_at=None,
            parent_id=parent_id,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_type: str = "task",
    deleted_at: str | None = None,
) -> str:
    """Insert a ``JobItem`` row; return its id."""
    jid = job_id or str(uuid.uuid4())
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="M2-API test job",
            source="api",
            project_id="test-project",
            priority=5,
            admission_state=admission_state,
            terminal_reason=None,
            instance_id=instance_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            deleted_at=deleted_at,
            job_metadata={},
            job_type=job_type,
        )
        s.add(job)
        s.commit()
    return jid


def _seed_mission(
    engine: Engine,
    *,
    instance_id: str,
    agent_id: str = "developer",
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    last_activity_at: datetime | None = None,
) -> str:
    """Seed a mission = an Instance row (identity == instance_id)."""
    return _seed_instance(
        engine,
        instance_id=instance_id,
        agent_id=agent_id,
        status=status,
        parent_id=parent_id,
        last_activity_at=last_activity_at,
    )


# ─── Kill-switch matrix ─────────────────────────────────────────────────────


class TestKillSwitchMatrix:
    """OFF ⇒ 404 on BOTH routes (fail-closed); routes stay registered."""

    def test_off_list_returns_404(self, client):
        """Default OFF: GET /missions ⇒ 404, not 200/503."""
        resp = client.get("/api/missions")
        assert resp.status_code == 404
        body = resp.json()
        assert "ENSEMBLE_MISSION_PROJECTION_ENABLED" in body["detail"]["error"]

    def test_off_detail_returns_404(self, client, engine):
        """Default OFF: GET /missions/{id} ⇒ 404 even for a REAL id."""
        iid = _seed_mission(engine, instance_id="inst-off-detail")
        resp = client.get(f"/api/missions/{iid}")
        assert resp.status_code == 404

    def test_off_routes_still_registered_in_openapi(self, client):
        """Routes stay registered so OpenAPI documents them (the OFF
        gate is in-handler, not in-registration)."""
        schema = client.get("/openapi.json").json()
        paths = set(schema["paths"].keys())
        assert "/api/missions" in paths
        assert "/api/missions/{mission_id}" in paths

    def test_on_list_returns_200(self, client, engine, flip_on):
        """ON: GET /missions ⇒ 200 with an (empty) page envelope."""
        resp = client.get("/api/missions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["missions"] == []
        assert body["total"] == 0
        assert body["degraded"] is False

    def test_on_detail_returns_200(self, client, engine, flip_on):
        """ON: GET /missions/{id} for a real id ⇒ 200 full record."""
        iid = _seed_mission(
            engine,
            instance_id="inst-on-detail",
            status=InstanceStatus.RUNNING.value,
            last_activity_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )
        resp = client.get(f"/api/missions/{iid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mission_id"] == iid
        assert body["agent_id"] == "developer"
        assert body["liveness"] == "processing"
        assert body["epoch"] == 1
        assert body["terminal_reason"] is None


# ─── List contract ──────────────────────────────────────────────────────────


class TestListContract:
    """GET /missions — resolve_many's production debut (paged shape)."""

    def test_identity_and_fields(self, client, engine, flip_on):
        """mission_id == instance_id; parent_mission_id == parent_id."""
        parent = _seed_mission(engine, instance_id="mission-parent")
        child = _seed_mission(
            engine,
            instance_id="mission-child",
            parent_id=parent,
            status=InstanceStatus.PAUSED.value,
        )
        resp = client.get("/api/missions")
        assert resp.status_code == 200
        by_id = {m["mission_id"]: m for m in resp.json()["missions"]}
        assert by_id[child]["parent_mission_id"] == parent
        assert by_id[child]["liveness"] == "paused"
        assert by_id[parent]["parent_mission_id"] is None

    def test_list_scope_all_instances_no_implicit_filter(
        self, client, engine, flip_on
    ):
        """Scope is ALL instances — terminals included, no non-terminal
        default, no root filtering (FLAGGED spec-silent choice §8.4)."""
        _seed_mission(
            engine,
            instance_id="m-live",
            status=InstanceStatus.RUNNING.value,
        )
        _seed_mission(
            engine,
            instance_id="m-done",
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_mission(
            engine,
            instance_id="m-cancelled",
            status=InstanceStatus.TERMINATED.value,
        )
        root = _seed_mission(engine, instance_id="m-root")
        _seed_mission(
            engine,
            instance_id="m-child",
            parent_id=root,
            status=InstanceStatus.IDLE.value,
        )
        body = client.get("/api/missions").json()
        ids = {m["mission_id"] for m in body["missions"]}
        assert ids == {"m-live", "m-done", "m-cancelled", "m-root", "m-child"}
        assert body["total"] == 5

    def test_liveness_filter_single(self, client, engine, flip_on):
        """liveness=processing matches the active cluster only."""
        _seed_mission(
            engine, instance_id="f-run", status=InstanceStatus.RUNNING.value
        )
        _seed_mission(
            engine, instance_id="f-idle", status=InstanceStatus.IDLE.value
        )
        _seed_mission(
            engine,
            instance_id="f-waiting",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _seed_mission(
            engine,
            instance_id="f-done",
            status=InstanceStatus.COMPLETED.value,
        )
        body = client.get(
            "/api/missions", params={"liveness": "processing"}
        ).json()
        ids = {m["mission_id"] for m in body["missions"]}
        assert ids == {"f-run", "f-idle", "f-waiting"}
        assert body["total"] == 3

    def test_liveness_filter_multi_comma(self, client, engine, flip_on):
        """Comma-multi (OR): completed,failed matches both terminals."""
        _seed_mission(
            engine,
            instance_id="mf-done",
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_mission(
            engine, instance_id="mf-err", status=InstanceStatus.ERROR.value
        )
        _seed_mission(
            engine,
            instance_id="mf-term",
            status=InstanceStatus.TERMINATED.value,
        )
        _seed_mission(
            engine,
            instance_id="mf-live",
            status=InstanceStatus.RUNNING.value,
        )
        resp = client.get(
            "/api/missions", params={"liveness": "completed,failed"}
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = {m["mission_id"] for m in body["missions"]}
        assert ids == {"mf-done", "mf-err"}
        # ERROR → failed keeps its liveness; terminal_reason = failed.
        by_id = {m["mission_id"]: m for m in body["missions"]}
        assert by_id["mf-err"]["liveness"] == "failed"
        assert by_id["mf-err"]["terminal_reason"] == "failed"

    def test_liveness_filter_whitespace_and_case_tolerant(
        self, client, engine, flip_on
    ):
        """Trim + lowercase each comma segment before validation."""
        _seed_mission(
            engine,
            instance_id="ws-done",
            status=InstanceStatus.COMPLETED.value,
        )
        resp = client.get(
            "/api/missions", params={"liveness": " Completed , "}
        )
        assert resp.status_code == 200
        ids = {m["mission_id"] for m in resp.json()["missions"]}
        assert ids == {"ws-done"}

    def test_liveness_filter_unknown_value_400(self, client, engine, flip_on):
        """A typo must 400, not silently return an empty list."""
        resp = client.get("/api/missions", params={"liveness": "runing"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "runing" in detail["error"]
        assert "processing" in detail["accepted"]

    def test_liveness_filter_dead_letter_rejected(self, client, engine, flip_on):
        """dead_letter is a terminal_reason, never a liveness (§8.2) —
        it is NOT in the accepted filter vocabulary."""
        resp = client.get("/api/missions", params={"liveness": "dead_letter"})
        assert resp.status_code == 400

    def test_liveness_pending_sourceless_matches_nothing(
        self, client, engine, flip_on
    ):
        """pending is in the ratified value space but has NO
        InstanceStatus source today (§8.2) — the filter honestly
        matches nothing (total=0, not degraded)."""
        _seed_mission(
            engine, instance_id="pg-run", status=InstanceStatus.RUNNING.value
        )
        body = client.get(
            "/api/missions", params={"liveness": "pending"}
        ).json()
        assert body["missions"] == []
        assert body["total"] == 0
        assert body["degraded"] is False

    def test_agent_id_filter(self, client, engine, flip_on):
        """agent_id filters in SQL (exact match)."""
        _seed_mission(
            engine, instance_id="a-dev", agent_id="developer"
        )
        _seed_mission(
            engine, instance_id="a-test", agent_id="tester"
        )
        body = client.get(
            "/api/missions", params={"agent_id": "tester"}
        ).json()
        ids = {m["mission_id"] for m in body["missions"]}
        assert ids == {"a-test"}

    def test_agent_and_liveness_compose(self, client, engine, flip_on):
        """Filters compose with AND semantics."""
        _seed_mission(
            engine,
            instance_id="c-dev-run",
            agent_id="developer",
            status=InstanceStatus.RUNNING.value,
        )
        _seed_mission(
            engine,
            instance_id="c-dev-done",
            agent_id="developer",
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_mission(
            engine,
            instance_id="c-test-run",
            agent_id="tester",
            status=InstanceStatus.RUNNING.value,
        )
        body = client.get(
            "/api/missions",
            params={"agent_id": "developer", "liveness": "processing"},
        ).json()
        ids = {m["mission_id"] for m in body["missions"]}
        assert ids == {"c-dev-run"}


# ─── Ordering + pagination ──────────────────────────────────────────────────


class TestOrderingAndPagination:
    """SQL ordering (NULLS LAST + tiebreak) and bounded pagination."""

    def test_ordering_desc_with_nulls_last_and_tiebreak(
        self, client, engine, flip_on
    ):
        """last_activity_at DESC, NULLs LAST, mission_id ASC tiebreak.

        Three dated rows + two NULL rows + two equal-timestamp rows:
        NULLs sort after every dated row; equal timestamps tiebreak on
        mission_id ascending.
        """
        t1 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
        _seed_mission(
            engine, instance_id="ord-old", last_activity_at=t1
        )
        _seed_mission(
            engine, instance_id="ord-new", last_activity_at=t2
        )
        _seed_mission(
            engine, instance_id="ord-null-a", last_activity_at=None
        )
        _seed_mission(
            engine, instance_id="ord-null-b", last_activity_at=None
        )
        _seed_mission(
            engine, instance_id="ord-tie-z", last_activity_at=t2
        )
        _seed_mission(
            engine, instance_id="ord-tie-a", last_activity_at=t2
        )

        body = client.get("/api/missions", params={"limit": 10}).json()
        order = [m["mission_id"] for m in body["missions"]]
        # DESC: newest (t2) first with id tiebreak, then t1, then NULLs last.
        assert order == [
            "ord-new",      # t2, id asc wins tie
            "ord-tie-a",    # t2 tiebreak
            "ord-tie-z",    # t2 tiebreak
            "ord-old",      # t1
            "ord-null-a",   # NULLS LAST
            "ord-null-b",
        ]

    def test_pagination_limit_offset_has_more(self, client, engine, flip_on):
        """limit/offset page through; has_more tracks the remainder."""
        for i in range(5):
            t = datetime(2026, 9, 1, 10, i, tzinfo=timezone.utc)
            _seed_mission(
                engine,
                instance_id=f"pg-{i}",
                last_activity_at=t,
            )
        page1 = client.get("/api/missions", params={"limit": 2, "offset": 0}).json()
        page2 = client.get("/api/missions", params={"limit": 2, "offset": 2}).json()
        page3 = client.get("/api/missions", params={"limit": 2, "offset": 4}).json()

        assert page1["total"] == 5
        assert [m["mission_id"] for m in page1["missions"]] == ["pg-4", "pg-3"]
        assert page1["has_more"] is True
        assert [m["mission_id"] for m in page2["missions"]] == ["pg-2", "pg-1"]
        assert page2["has_more"] is True
        assert [m["mission_id"] for m in page3["missions"]] == ["pg-0"]
        assert page3["has_more"] is False

    def test_limit_clamped_to_max_page_limit(self, client, engine, flip_on):
        """limit=1000 clamps to MAX_PAGE_LIMIT=100 (repo convention)."""
        body = client.get("/api/missions", params={"limit": 1000}).json()
        assert body["limit"] == 100

    def test_limit_clamped_to_minimum_one(self, client, engine, flip_on):
        """limit=0 / negative clamp to 1 (repo convention)."""
        for params in ({"limit": 0}, {"limit": -5}):
            resp = client.get("/api/missions", params=params)
            assert resp.status_code == 200
            assert resp.json()["limit"] == 1

    def test_negative_offset_clamped_to_zero(self, client, engine, flip_on):
        """Negative offset clamps to 0."""
        _seed_mission(engine, instance_id="neg-off")
        resp = client.get("/api/missions", params={"offset": -10})
        assert resp.status_code == 200
        assert resp.json()["offset"] == 0
        assert resp.json()["total"] == 1

    def test_default_limit_is_ten(self, client, engine, flip_on):
        """Default page size = DEFAULT_PAGE_LIMIT (10)."""
        body = client.get("/api/missions").json()
        assert body["limit"] == 10


# ─── Detail contract ────────────────────────────────────────────────────────


class TestDetailContract:
    """GET /missions/{mission_id} — full record via resolve()."""

    def test_unknown_id_404(self, client, engine, flip_on):
        """Unknown id ⇒ 404 (the only true-miss shape)."""
        resp = client.get("/api/missions/inst-does-not-exist")
        assert resp.status_code == 404
        assert "Mission not found" in resp.json()["detail"]["error"]

    def test_completed_mission_terminal_fields(self, client, engine, flip_on):
        """Terminal mission: terminal_reason == liveness == completed;
        epoch stays 1 (§8.3: NOT None when terminal)."""
        _seed_mission(
            engine,
            instance_id="d-done",
            status=InstanceStatus.COMPLETED.value,
            last_activity_at=datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc),
        )
        body = client.get("/api/missions/d-done").json()
        assert body["liveness"] == "completed"
        assert body["terminal_reason"] == "completed"
        assert body["epoch"] == 1
        assert body["last_activity_at"] is not None
        assert body["started_at"] is not None

    def test_terminated_mission_maps_cancelled(self, client, engine, flip_on):
        """TERMINATED → liveness cancelled → terminal_reason cancelled."""
        _seed_mission(
            engine,
            instance_id="d-term",
            status=InstanceStatus.TERMINATED.value,
        )
        body = client.get("/api/missions/d-term").json()
        assert body["liveness"] == "cancelled"
        assert body["terminal_reason"] == "cancelled"

    def test_mission_without_activity(self, client, engine, flip_on):
        """No last_activity_at ⇒ started_at falls back to created_at,
        last_activity_at null."""
        _seed_mission(
            engine,
            instance_id="d-noact",
            status=InstanceStatus.QUEUED.value,
            last_activity_at=None,
        )
        body = client.get("/api/missions/d-noact").json()
        assert body["last_activity_at"] is None
        assert body["started_at"] is not None  # created_at fallback

    def test_linked_jobs_surfaced(self, client, engine, flip_on):
        """linked_jobs lists the linked JobItem ids (non-deleted)."""
        iid = _seed_mission(engine, instance_id="d-jobs")
        jid = _seed_job(engine, instance_id=iid)
        body = client.get(f"/api/missions/{iid}").json()
        assert body["linked_jobs"] == [jid]


# ─── W4 dead_letter pin at the HTTP binding (S4 lesson) ────────────────────


class TestW4DeadLetterBinding:
    """The S4 lesson pinned at the WIRE: list AND detail must both
    surface ``dead_letter`` when a linked JobItem is DEAD — regardless
    of a since-revived (RUNNING) instance. Routing the detail path
    through ``project()`` (dead_linked=False hazard) fails HERE."""

    def test_detail_dead_letter(self, client, engine, flip_on):
        """Detail: RUNNING instance + DEAD linked job ⇒ dead_letter."""
        iid = _seed_mission(
            engine,
            instance_id="w4-detail",
            status=InstanceStatus.RUNNING.value,
        )
        _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
        )
        resp = client.get(f"/api/missions/{iid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["terminal_reason"] == "dead_letter"
        # W4 override: liveness stays instance-derived...
        assert body["liveness"] == "processing"
        # ...but the terminal cause is the dead-letter truth.

    def test_list_dead_letter(self, client, engine, flip_on):
        """List: the same W4 hazard must hold on the batched path."""
        iid = _seed_mission(
            engine,
            instance_id="w4-list",
            status=InstanceStatus.RUNNING.value,
        )
        _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
        )
        resp = client.get("/api/missions")
        assert resp.status_code == 200
        rows = {
            m["mission_id"]: m for m in resp.json()["missions"]
        }
        assert rows[iid]["terminal_reason"] == "dead_letter"

    def test_soft_deleted_dead_job_does_not_trigger_w4(
        self, client, engine, flip_on
    ):
        """Soft-deleted JobItems are excluded from the W4 lookup
        (deleted_at IS NOT NULL filter in the batched SELECT)."""
        iid = _seed_mission(engine, instance_id="w4-deleted")
        _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )
        body = client.get(f"/api/missions/{iid}").json()
        assert body["terminal_reason"] is None  # live mission
        assert body["linked_jobs"] == []


# ─── Degradation contract (NO 500 anywhere) ────────────────────────────────


class TestDegradationBinding:
    """Real failing engines (dropped tables — not mocks): every
    projection-path failure degrades to 200 + honest shape, never a
    500; exactly ONE server-side warning per degraded request."""

    @pytest.fixture
    def broken_resolver_engine(self, tmp_path):
        """A file-backed engine whose tables exist but will be dropped
        after seeding — real SQLAlchemyError territory."""
        db_path = tmp_path / "missions-broken.sqlite"
        eng = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _configure(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

        SQLModel.metadata.create_all(eng)
        try:
            yield eng
        finally:
            eng.dispose()

    def _drop(self, engine: Engine, table: str) -> None:
        with engine.connect() as conn:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
            conn.commit()

    def test_list_count_page_leg_degrades_not_500(
        self, client, broken_resolver_engine, caplog, flip_on
    ):
        """Instances table gone ⇒ 200 empty page + total null +
        degraded true + exactly ONE warning (whole-page degrade)."""
        set_missions_resolver(
            MissionResolver(
                instance_repo=SQLModelInstanceRepository(
                    broken_resolver_engine
                ),
                job_repo=JobRepository(broken_resolver_engine),
            )
        )
        self._drop(broken_resolver_engine, "instances")

        with caplog.at_level("WARNING", logger="daemon.services.mission_resolver"):
            resp = client.get("/api/missions")
        assert resp.status_code == 200  # NEVER a 500
        body = resp.json()
        assert body["missions"] == []
        assert body["total"] is None  # "count unavailable", not 0
        assert body["has_more"] is None
        assert body["degraded"] is True
        warnings = [
            r for r in caplog.records if r.levelname == "WARNING"
        ]
        assert len(warnings) == 1, (
            "exactly-one-warning: the whole-page degrade must emit a "
            f"single warning; got {len(warnings)}"
        )

    def test_list_jobs_leg_degrades_rows_survive(
        self, client, broken_resolver_engine, caplog, flip_on
    ):
        """job_queue_items gone ⇒ 200 rows with linked_jobs=[] (the
        liveness fields stay populated; the W4 sub-check falls back to
        the liveness answer per §8.2 indistinguishable-by-design)."""
        set_missions_resolver(
            MissionResolver(
                instance_repo=SQLModelInstanceRepository(
                    broken_resolver_engine
                ),
                job_repo=JobRepository(broken_resolver_engine),
            )
        )
        iid = _seed_instance(
            broken_resolver_engine,
            instance_id="deg-jobs",
            status=InstanceStatus.COMPLETED.value,
        )
        self._drop(broken_resolver_engine, "job_queue_items")

        with caplog.at_level("WARNING", logger="daemon.services.mission_resolver"):
            resp = client.get("/api/missions")
        assert resp.status_code == 200  # NEVER a 500
        body = resp.json()
        assert body["degraded"] is False  # rows were served
        assert body["total"] == 1
        row = body["missions"][0]
        assert row["mission_id"] == iid
        assert row["liveness"] == "completed"
        assert row["terminal_reason"] == "completed"  # liveness fallback
        assert row["linked_jobs"] == []
        warnings = [
            r for r in caplog.records if r.levelname == "WARNING"
        ]
        assert len(warnings) == 1

    def test_detail_instance_leg_degrades_200_none_fields(
        self, client, broken_resolver_engine, caplog, flip_on
    ):
        """Detail with a failing instance lookup ⇒ 200 + the degraded
        None-fields shape (NOT 404 — the id cannot be proven missing;
        NOT 500)."""
        set_missions_resolver(
            MissionResolver(
                instance_repo=SQLModelInstanceRepository(
                    broken_resolver_engine
                ),
                job_repo=JobRepository(broken_resolver_engine),
            )
        )
        self._drop(broken_resolver_engine, "instances")

        with caplog.at_level("WARNING", logger="daemon.services.mission_resolver"):
            resp = client.get("/api/missions/inst-any-id")
        assert resp.status_code == 200  # NEVER a 500
        body = resp.json()
        assert body["mission_id"] is None
        assert body["agent_id"] is None
        assert body["liveness"] is None
        assert body["epoch"] is None
        assert body["terminal_reason"] is None
        assert body["linked_jobs"] == []
        warnings = [
            r for r in caplog.records if r.levelname == "WARNING"
        ]
        assert len(warnings) == 1

    def test_detail_jobs_leg_degrades_instance_fields_survive(
        self, client, broken_resolver_engine, caplog, flip_on
    ):
        """Detail with a failing JobItem lookup ⇒ 200; instance-derived
        fields stay; linked_jobs=[] and the W4 sub-check is skipped."""
        set_missions_resolver(
            MissionResolver(
                instance_repo=SQLModelInstanceRepository(
                    broken_resolver_engine
                ),
                job_repo=JobRepository(broken_resolver_engine),
            )
        )
        _seed_instance(
            broken_resolver_engine,
            instance_id="deg-detail",
            status=InstanceStatus.FAILED.value,
        )
        self._drop(broken_resolver_engine, "job_queue_items")

        with caplog.at_level("WARNING", logger="daemon.services.mission_resolver"):
            resp = client.get("/api/missions/deg-detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mission_id"] == "deg-detail"
        assert body["liveness"] == "failed"
        assert body["terminal_reason"] == "failed"
        assert body["linked_jobs"] == []
        warnings = [
            r for r in caplog.records if r.levelname == "WARNING"
        ]
        assert len(warnings) == 1


# ─── ENGINE-BOUND batched-query bound ───────────────────────────────────────


class TestEngineBoundQueryCount:
    """The ≤3-SELECT page bound, pinned via a real engine listener.

    The M1-gate pattern (``TestBatchQueryCount``): a
    ``before_cursor_execute`` spy counts SELECT statements against
    ``instances`` and ``job_queue_items`` DURING the HTTP request.
    Mock counting is banned for this contract.
    """

    @staticmethod
    def _count_selects(engine: Engine):
        """Attach the spy; returns ``(counts, detach)`` where ``counts``
        is ``{"instances": n, "job_queue_items": m, "total": k}`` —
        captured by reference and valid between attach and detach."""
        counts = {
            "instances": 0,
            "job_queue_items": 0,
            "total": 0,
        }

        def _before_cursor_execute(  # noqa: ANN001 — SQLAlchemy hook
            conn, cursor, statement, parameters, context, executemany  # noqa: ARG001
        ):
            s = statement.strip().upper()
            if s.startswith("SELECT"):
                counts["total"] += 1
                if "FROM INSTANCES" in s:
                    counts["instances"] += 1
                if "JOB_QUEUE_ITEMS" in s:
                    counts["job_queue_items"] += 1

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)

        def _detach() -> None:
            event.remove(
                engine, "before_cursor_execute", _before_cursor_execute
            )

        return counts, _detach

    def test_list_issues_exactly_three_selects(
        self, client, engine, flip_on
    ):
        """One page = 3 SELECTs: count + paged instances + batched
        JobItem IN-clause. ZERO per-row lookups."""
        for i in range(3):
            iid = _seed_mission(
                engine,
                instance_id=f"q-{i}",
                last_activity_at=datetime(
                    2026, 9, 1, 10, i, tzinfo=timezone.utc
                ),
            )
            _seed_job(engine, instance_id=iid)

        counts, detach = self._count_selects(engine)
        try:
            resp = client.get("/api/missions", params={"limit": 3})
        finally:
            detach()

        assert resp.status_code == 200
        assert len(resp.json()["missions"]) == 3
        assert counts["total"] == 3, (
            f"list page must issue exactly 3 SELECTs (count + page + "
            f"batched JobItem); got {counts}"
        )
        assert counts["instances"] == 2, (
            "instances leg = 1 count + 1 paged SELECT; any more is a "
            f"per-row regression: {counts}"
        )
        assert counts["job_queue_items"] == 1, (
            "JobItem leg must be ONE batched IN-clause SELECT "
            f"(C9 bound): {counts}"
        )

    def test_list_select_count_flat_as_page_doubles(
        self, client, engine, flip_on
    ):
        """The bound is FLAT: page of 4 rows ⇒ 3 SELECTs; page of 8
        rows ⇒ still 3 SELECTs. Any growth with page size is an N+1."""
        for i in range(8):
            iid = _seed_mission(
                engine,
                instance_id=f"flat-{i}",
                last_activity_at=datetime(
                    2026, 9, 1, 11, i, tzinfo=timezone.utc
                ),
            )
            _seed_job(engine, instance_id=iid)

        for limit in (4, 8):  # page size doubles 4 → 8
            counts, detach = self._count_selects(engine)
            try:
                resp = client.get("/api/missions", params={"limit": limit})
            finally:
                detach()

            assert resp.status_code == 200
            assert len(resp.json()["missions"]) == limit
            assert counts["total"] == 3, (
                f"SELECT count must stay flat at 3 as the page size "
                f"doubles (limit={limit}); got {counts}"
            )

    def test_detail_issues_two_selects(self, client, engine, flip_on):
        """Detail = 2 SELECTs: instance row + batched JobItem (N=1 —
        the resolve() path reuses the batched helper)."""
        iid = _seed_mission(engine, instance_id="q-detail")
        _seed_job(engine, instance_id=iid)

        counts, detach = self._count_selects(engine)
        try:
            resp = client.get(f"/api/missions/{iid}")
        finally:
            detach()

        assert resp.status_code == 200
        assert counts["total"] == 2
        assert counts["instances"] == 1
        assert counts["job_queue_items"] == 1

    def test_liveness_filtered_list_still_three_selects(
        self, client, engine, flip_on
    ):
        """Filters compose into the same 3-SELECT bound (no extra
        round-trips for filtering)."""
        _seed_mission(
            engine,
            instance_id="qf-done",
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_mission(
            engine,
            instance_id="qf-run",
            status=InstanceStatus.RUNNING.value,
        )
        counts, detach = self._count_selects(engine)
        try:
            resp = client.get(
                "/api/missions", params={"liveness": "processing,completed"}
            )
        finally:
            detach()

        assert resp.status_code == 200
        assert counts["total"] == 3

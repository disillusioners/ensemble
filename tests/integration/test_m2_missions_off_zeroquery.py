"""M2 runtime probe — kill-switch OFF ⇒ 404 with ZERO SQL + OpenAPI visibility.

INDEPENDENT runtime confirmation of the M2 mission HTTP surface
(docs/job-task-system.md §8.4) against the REAL app assembly —
``daemon.api.create_app()`` — NOT a router-only unit harness. The
unit-level machine pins live in
``tests/unit/routers/test_missions_api.py``; this file re-proves the
route-level OFF semantics on the real FastAPI assembly: real router
registration, real middleware stack, real OpenAPI schema generation.

Harness precedent: ``tests/integration/test_vscode_security_integration.py``
drives the REAL ``create_app()`` via an in-proc ASGI client while
bypassing the lifespan (no Postgres / no InstanceManager boot). The
mission resolver is wired exactly as the production lifespan does
(``daemon/api.py``: ``set_missions_resolver(MissionResolver(
instance_repo=..., job_repo=...))``) against real repositories on a
file-backed SQLite engine — the house recipe (WAL + busy_timeout +
NullPool; the quarantined ``StaticPool + WriteGuardSession`` shape is
NOT used).

Scenarios (M2 gate task):

1. **OFF ⇒ 404 + zero queries** — with the flag OFF (default), both
   ``GET /api/missions`` and ``GET /api/missions/{id}`` answer 404,
   and a ``before_cursor_execute`` engine-spy scoped to the request
   window counts ZERO SQL statements. Because the spy sits on the
   engine behind the wired repositories, any statement attributable to
   the mission route would be captured.
2. **Query census** — OFF-list, OFF-detail (real seeded id), a
   framework-404 control (unknown path, expect 0 — proves the
   framework itself contributes nothing), and the ON-list positive
   control (expect >0 through the SAME listener — proves the spy would
   have caught queries had the OFF path issued any). Controls are
   printed via ``-rP`` output; the ON positive control is a hard
   assertion (a silent spy would void every zero-query claim).
3. **OpenAPI visibility, both states** — NOTE: the task framing
   expected OFF to HIDE the missions paths from ``/openapi.json``; the
   documented contract is the OPPOSITE and this probe pins the
   documented behavior: §8.4 "Routes stay REGISTERED while OFF (OpenAPI
   still documents them)", the router docstring ("remaining
   registered"), and the unit pin
   ``test_off_routes_still_registered_in_openapi`` all hold that the
   kill-switch gate is in-HANDLER (missions.py raises 404 inside the
   route), not in-registration — ``create_app()`` includes the missions
   router unconditionally (daemon/api.py). The probe captures the
   missions-related path keys in BOTH states and reports the
   discrepancy against the task expectation.
4. **ON smoke** — flag ON ⇒ ``GET /api/missions`` returns 200 with the
   §8.4 list envelope (exact key set ``{missions, total, limit,
   offset, has_more, degraded}``; empty page on a fresh DB is OK) and
   ``GET /api/missions/{unknown-id}`` returns 404.
5. **Flip-back determinism** — ON → OFF again ⇒ 404 again (including
   on a REAL seeded id that 200'd while ON), supporting the
   "restart required" operational note. The flip is done in-process
   via the sanctioned test hook
   ``_reset_mission_projection_for_tests()`` (the once-per-process
   cache lives in ``daemon/services/mission_resolver.py``); production
   has no such hook — hence restart-required.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
# the missions resolver reads BOTH instance and job_queue tables; a
# missing job_queue_items table would flip the ON smoke into the
# §8.2 degraded shape and invalidate the probe.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401  (transitive dep)

from daemon.api import create_app
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.routers.missions import set_missions_resolver
from daemon.services import mission_resolver as mission_resolver_module
from daemon.services.mission_resolver import (
    MissionResolver,
    _reset_mission_projection_for_tests,
)

MISSION_KILL_SWITCH_ENV = "ENSEMBLE_MISSION_PROJECTION_ENABLED"

pytestmark = pytest.mark.integration


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    The Testing & QC house recipe: a real file (schema persists across
    NullPool connections), no shared connection, WAL + a generous busy
    timeout. Each test gets its own ``tmp_path`` file.
    """
    db_path = tmp_path / "m2-off-zeroquery-probe.sqlite"
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


@pytest.fixture(autouse=True)
def _killswitch_off_baseline(monkeypatch):
    """Every test starts from the OFF default; the cache is re-armed.

    The kill-switch resolves ONCE per process and caches
    (``mission_resolver.py``); the sanctioned test reset hook clears it
    so each test re-resolves from the (monkeypatched) environment. The
    teardown reset guarantees no ON state leaks into other tests in
    this pytest process.
    """
    assert (
        mission_resolver_module._MISSION_PROJECTION_ENV
        == MISSION_KILL_SWITCH_ENV
    ), "env-var name drifted from mission_resolver._MISSION_PROJECTION_ENV"
    monkeypatch.delenv(MISSION_KILL_SWITCH_ENV, raising=False)
    _reset_mission_projection_for_tests()
    yield
    _reset_mission_projection_for_tests()


@pytest.fixture
def app(engine: Engine):
    """The REAL ``create_app()`` assembly with the missions resolver wired.

    The lifespan is BY-PASSED (in-proc ASGI client never enters it) —
    the vscode-integration precedent. The missions resolver is wired
    through the exact production wiring call (``set_missions_resolver``
    with ``MissionResolver(instance_repo=..., job_repo=...)``), only
    pointed at this test's engine instead of the lifespan's.
    """
    app = create_app()
    set_missions_resolver(
        MissionResolver(
            instance_repo=SQLModelInstanceRepository(engine),
            job_repo=JobRepository(engine),
        )
    )
    return app


@pytest.fixture
def client(app) -> TestClient:
    """In-proc ASGI client. Used BARE (no ``with``) so the lifespan —
    which boots the full InstanceManager — never runs."""
    return TestClient(app)


# ─── Query spy (the M1-gate pattern: engine event listener, NOT mocks) ─────


class QuerySpy:
    """``before_cursor_execute`` listener counting REAL SQL in a window.

    Attached to the engine behind the wired repositories — any
    statement the mission routes issue through the resolver passes
    through here, whatever its source.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.statements: list[str] = []

    def _on_before_cursor_execute(
        self, conn, cursor, statement, parameters, context, executemany
    ) -> None:
        self.statements.append(statement)

    def __enter__(self) -> "QuerySpy":
        event.listen(self._engine, "before_cursor_execute", self._on_before_cursor_execute)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_before_cursor_execute)

    @property
    def total(self) -> int:
        return len(self.statements)

    def census(self) -> str:
        kinds = Counter(s.strip().split(None, 1)[0].upper() for s in self.statements)
        return f"total={self.total} kinds={dict(kinds)}"

    def sql_lines(self) -> str:
        return "\n".join(f"    {i}: {s[:160]}" for i, s in enumerate(self.statements, 1))


# ─── Seed helper (mirror test_missions_api.py) ──────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    agent_id: str = "developer",
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert a populated ``Instance`` row (a mission); return its id."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                project_id="test-project",
                status=status,
                created_at=now,
                updated_at=now,
                last_activity_at=datetime.now(timezone.utc),
                paused_at=None,
                parent_id=None,
            )
        )
        s.commit()
    return instance_id


# ─── Scenario 1: OFF ⇒ 404 with ZERO queries ───────────────────────────────


class TestOff404ZeroQuery:
    """Kill-switch OFF (default): 404 on both routes, ZERO SQL in the
    request window. The unit pins prove this on a router-only harness;
    here it is re-proven on the real ``create_app()`` assembly."""

    def test_off_list_404_and_zero_queries(self, client, engine):
        spy = QuerySpy(engine)
        with spy:
            resp = client.get("/api/missions")
        assert resp.status_code == 404, f"OFF list must 404; got {resp.status_code}"
        body = resp.json()
        assert "ENSEMBLE_MISSION_PROJECTION_ENABLED" in body["detail"]["error"], body
        assert spy.total == 0, (
            "OFF list must issue ZERO SQL statements (kill-switch fires "
            f"pre-resolver); census={spy.census()}\n{spy.sql_lines()}"
        )
        print(f"PROBE-CENSUS OFF list: status=404, {spy.census()}")

    def test_off_detail_404_and_zero_queries_real_id(self, client, engine):
        iid = _seed_instance(engine, instance_id="inst-off-detail-real")
        spy = QuerySpy(engine)
        with spy:
            resp = client.get(f"/api/missions/{iid}")
        assert resp.status_code == 404, f"OFF detail must 404; got {resp.status_code}"
        assert spy.total == 0, (
            "OFF detail must issue ZERO SQL statements even for a REAL "
            f"seeded id; census={spy.census()}\n{spy.sql_lines()}"
        )
        print(f"PROBE-CENSUS OFF detail(real id): status=404, {spy.census()}")


# ─── Scenario 2 control pair ────────────────────────────────────────────────


class TestQueryCensusControls:
    """The control pair that makes the OFF zero-claim meaningful:

    * framework-404 control: an unknown path (404 from the router
      itself) must contribute ZERO statements — if OFF-missions ever
      showed nonzero, any excess over this control would be
      missions-route-caused rather than framework-global.
    * positive control (ON-list, in TestOnSmoke): >0 statements through
      the SAME listener prove the spy is live on this engine.

    With the lifespan bypassed, no other DB-wired route is serviceable
    (their DI singletons are unwired ⇒ 503, not 404), so the
    framework-404 path is the honest "another route" control here.
    """

    def test_framework_404_control_zero_statements(self, client, engine):
        spy = QuerySpy(engine)
        with spy:
            resp = client.get("/api/definitely-not-a-real-route-probe-control")
        assert resp.status_code == 404
        print(
            f"PROBE-CENSUS framework-404 control: status=404, {spy.census()} "
            "(informational — truth reporting, not a gate)"
        )
        assert spy.total == 0, (
            "A framework 404 unexpectedly touched the DB — this would "
            f"reframe the OFF census; census={spy.census()}\n{spy.sql_lines()}"
        )


# ─── Scenario 3: OpenAPI visibility, both states ────────────────────────────


class TestOpenApiVisibility:
    """Missions paths in /openapi.json — OFF vs ON.

    PINNED: routes stay REGISTERED while OFF (the gate is in-handler).
    This deliberately deviates from the M2 gate task's stated
    expectation ("OFF ⇒ NO missions paths in OpenAPI"); the documented
    contract (§8.4 + router docstring + unit pin
    ``test_off_routes_still_registered_in_openapi``) is authoritative
    and both states' path keys are printed for the gate's evidence.
    """

    def test_missions_paths_visible_in_both_states(self, client, monkeypatch):
        # OFF (baseline via autouse fixture)
        off_paths = set(client.get("/openapi.json").json()["paths"].keys())
        off_missions = sorted(k for k in off_paths if "mission" in k.lower())
        print(f"PROBE-OPENAPI OFF missions-related path keys: {off_missions}")
        assert "/api/missions" in off_paths
        assert "/api/missions/{mission_id}" in off_paths
        assert "get" in client.get("/openapi.json").json()["paths"]["/api/missions"]
        assert (
            "get"
            in client.get("/openapi.json").json()["paths"]["/api/missions/{mission_id}"]
        )

        # Flip ON (in-process: env + sanctioned cache reset)
        monkeypatch.setenv(MISSION_KILL_SWITCH_ENV, "1")
        _reset_mission_projection_for_tests()
        on_schema = client.get("/openapi.json").json()["paths"]
        on_paths = set(on_schema.keys())
        on_missions = sorted(k for k in on_paths if "mission" in k.lower())
        print(f"PROBE-OPENAPI ON missions-related path keys: {on_missions}")
        assert "/api/missions" in on_paths
        assert "/api/missions/{mission_id}" in on_paths
        assert "get" in on_schema["/api/missions"]
        assert "get" in on_schema["/api/missions/{mission_id}"]
        assert on_missions == off_missions


# ─── Scenario 4: ON smoke (incl. the positive query-count control) ──────────


class TestOnSmoke:
    """Flag ON: the normal §8.4 contract — 200 list envelope, 404
    unknown id — plus the listener-sensitivity positive control."""

    def test_on_list_200_envelope_and_positive_query_control(self, client, engine, monkeypatch):
        monkeypatch.setenv(MISSION_KILL_SWITCH_ENV, "1")
        _reset_mission_projection_for_tests()
        spy = QuerySpy(engine)
        with spy:
            resp = client.get("/api/missions")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "missions",
            "total",
            "limit",
            "offset",
            "has_more",
            "degraded",
        }, f"§8.4 envelope key set drifted: {sorted(body.keys())}"
        assert body["missions"] == []
        assert body["total"] == 0
        assert body["degraded"] is False
        # Positive control: the SAME listener that saw 0 on the OFF
        # path must see ≥1 statement when the route really reads.
        assert spy.total >= 1, (
            "ON list issued ZERO statements — the spy is not live on "
            "this engine and every OFF zero-query claim above is void; "
            f"census={spy.census()}"
        )
        print(f"PROBE-CENSUS ON list (positive control): status=200, {spy.census()}")

    def test_on_detail_unknown_id_404(self, client, engine, monkeypatch):
        monkeypatch.setenv(MISSION_KILL_SWITCH_ENV, "1")
        _reset_mission_projection_for_tests()
        resp = client.get(f"/api/missions/no-such-mission-{uuid.uuid4().hex[:8]}")
        assert resp.status_code == 404, f"ON detail unknown id must 404; got {resp.status_code}"
        print(f"PROBE-ON-SMOKE detail unknown id: status=404, body={resp.json()}")


# ─── Scenario 5: flip-back determinism ──────────────────────────────────────


class TestFlipBackDeterminism:
    """ON → OFF again ⇒ 404 again, on ids that 200'd while ON —
    the in-process stand-in for the restart-required operational note."""

    def test_on_then_off_returns_404_again(self, client, engine, monkeypatch):
        iid = _seed_instance(engine, instance_id="inst-flipback")
        monkeypatch.setenv(MISSION_KILL_SWITCH_ENV, "1")
        _reset_mission_projection_for_tests()
        assert client.get("/api/missions").status_code == 200
        assert client.get(f"/api/missions/{iid}").status_code == 200

        monkeypatch.delenv(MISSION_KILL_SWITCH_ENV)
        _reset_mission_projection_for_tests()
        resp_list = client.get("/api/missions")
        resp_detail = client.get(f"/api/missions/{iid}")
        assert resp_list.status_code == 404, (
            f"flip-back list must 404; got {resp_list.status_code}"
        )
        assert resp_detail.status_code == 404, (
            f"flip-back detail must 404 even for the id that 200'd while "
            f"ON; got {resp_detail.status_code}"
        )
        print(
            "PROBE-FLIPBACK: ON(200,200) → OFF ⇒ "
            f"list={resp_list.status_code}, detail={resp_detail.status_code}"
        )

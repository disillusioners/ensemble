"""Tests for the /livez and /readyz health probes (Auto-Restart Phase 1).

Two layers:

* HTTP-layer tests using the mock-manager conventions from
  ``tests/test_api.py`` (``app_with_mock_manager`` / ``client``) — the
  app singleton is imported and ``app.state`` is seeded directly, no
  lifespan is run.
* Pure-logic tests against ``daemon/services/readiness.py`` — the
  injected-callable seam that keeps the composite testable without a
  database.

PostgreSQL-backed verification of the probe SQL lives in
``tests/postgres/test_readiness_pg.py`` (``-m postgres``).
"""

from datetime import datetime, timezone
from unittest.mock import Mock

import httpx
import pytest

from daemon.services.readiness import (
    ReadinessComposite,
    compute_readiness_composite,
    evaluate_queue_freshness,
    refresh_readiness_composite,
)


@pytest.fixture
def app_with_mock_manager():
    """Create the FastAPI app singleton with a mocked manager on state.

    Mirrors the ``tests/test_api.py`` convention: import the module-level
    app, seed ``app.state`` (manager + start_time), and restore readiness
    state afterwards so probe tests never leak composites into other
    tests.
    """
    from daemon.api import app

    manager = Mock()
    # A Mock engine would explode if the handler ever touched it —
    # exactly what the "zero DB access per request" tests assert.
    manager.engine = Mock(name="engine-that-must-not-be-touched")

    app.state.manager = manager
    app.state.start_time = 1000.0
    app.state.job_processor = Mock(name="job_processor")
    app.state.live_hub = Mock(name="live_hub")

    sentinel = getattr(app.state, "readiness_composite", "__unset__")
    yield app
    if sentinel == "__unset__":
        try:
            delattr(app.state, "readiness_composite")
        except (AttributeError, KeyError):
            # Starlette's State raises KeyError when the key is absent
            # (e.g. the test never set a composite — the /livez tests).
            pass
    else:
        app.state.readiness_composite = sentinel


@pytest.fixture
async def root_client(app_with_mock_manager):
    """Async client bound at the app ROOT (no /api prefix) for /livez + /readyz."""
    from daemon.api import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── /livez ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_livez_200_shape(root_client, app_with_mock_manager):
    """GET /livez → 200, alive shape, zero manager/DB dependency."""
    response = await root_client.get("/livez")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "alive"
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0
    assert body["version"]  # non-empty version string


@pytest.mark.asyncio
async def test_livez_works_without_manager(app_with_mock_manager):
    """Liveness must answer even when nothing but start_time is bound."""
    delattr(app_with_mock_manager.state, "manager")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_mock_manager),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/livez")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_livez_no_engine_access(root_client, app_with_mock_manager):
    """Liveness handler never touches the manager (or its engine)."""
    await root_client.get("/livez")
    engine = app_with_mock_manager.state.manager.engine
    assert engine.connect.call_count == 0


# ── /readyz: cached composite served, DB untouched per request ────────────


def _ready_composite() -> ReadinessComposite:
    return compute_readiness_composite(
        database_ok=True,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=12.5,
        checked_at=datetime(2026, 8, 16, 0, 0, 0, tzinfo=timezone.utc),
    )


def _degraded_composite() -> ReadinessComposite:
    return compute_readiness_composite(
        database_ok=False,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=None,
        checked_at=datetime(2026, 8, 16, 0, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_readyz_ready_200(root_client, app_with_mock_manager):
    app_with_mock_manager.state.readiness_composite = _ready_composite()

    response = await root_client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"] == {
        "database": True,
        "queue_freshness": True,
        "services": True,
    }
    assert body["detail"]["reasons"] == []
    assert body["detail"]["queue_max_age_seconds"] == 12.5
    assert body["detail"]["checked_at"] == "2026-08-16T00:00:00+00:00"
    assert body["draining"] is False  # reserved Phase-4 field


@pytest.mark.asyncio
async def test_readyz_degraded_503_retry_after(root_client, app_with_mock_manager):
    app_with_mock_manager.state.readiness_composite = _degraded_composite()

    response = await root_client.get("/readyz")

    assert response.status_code == 503
    assert response.headers.get("retry-after") is not None
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["database"] is False
    assert any("database" in reason for reason in body["detail"]["reasons"])


@pytest.mark.asyncio
async def test_readyz_no_composite_fails_closed(root_client, app_with_mock_manager):
    """Before the refresher's first tick, /readyz is 503 — never a fake ready."""
    app_with_mock_manager.state.readiness_composite = None

    response = await root_client.get("/readyz")

    assert response.status_code == 503
    assert response.headers.get("retry-after") is not None
    assert response.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_readyz_cached_handler_never_touches_engine(
    root_client, app_with_mock_manager
):
    """Hammer the handler: the engine (and the manager mock) is untouched.

    The refresh path is exercised separately (module-level test below)
    — this asserts the handler side of the ADR-003 contract: O(1)
    memory read, zero DB access per request.
    """
    app_with_mock_manager.state.readiness_composite = _ready_composite()

    for _ in range(10):
        response = await root_client.get("/readyz")
        assert response.status_code == 200

    engine = app_with_mock_manager.state.manager.engine
    assert engine.connect.call_count == 0


# ── Pure-logic seam: composite computation ────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_runs_probes_exactly_once():
    """One refresh cycle = one call per injected probe; composite assembled."""
    db_probe = Mock(return_value=True)
    queue_probe = Mock(return_value=None)  # empty RUNNING set

    composite = await refresh_readiness_composite(
        db_probe=db_probe,
        queue_probe=queue_probe,
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert db_probe.call_count == 1
    assert queue_probe.call_count == 1
    assert composite.ready is True
    assert composite.queue_max_age_seconds is None  # no RUNNING tasks
    assert composite.reasons == []


@pytest.mark.asyncio
async def test_refresh_db_probe_failure_degrades():
    db_probe = Mock(side_effect=RuntimeError("connection refused"))

    composite = await refresh_readiness_composite(
        db_probe=db_probe,
        queue_probe=Mock(return_value=None),
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert composite.database is False
    assert composite.ready is False
    assert any("database" in r for r in composite.reasons)


@pytest.mark.asyncio
async def test_refresh_db_probe_timeout_degrades():
    """A probe slower than the 500ms budget degrades the database component."""

    def slow_probe():
        import time

        time.sleep(0.8)  # > DB_PROBE_TIMEOUT_S (0.5s)
        return True

    composite = await refresh_readiness_composite(
        db_probe=slow_probe,
        queue_probe=Mock(return_value=None),
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert composite.database is False
    assert composite.ready is False


@pytest.mark.asyncio
async def test_refresh_services_component_degrades():
    composite = await refresh_readiness_composite(
        db_probe=Mock(return_value=True),
        queue_probe=Mock(return_value=None),
        services_ok=False,
        queue_freshness_threshold_seconds=120,
    )

    assert composite.services is False
    assert composite.ready is False
    assert any("services" in r for r in composite.reasons)


@pytest.mark.asyncio
async def test_refresh_none_probe_fails_closed():
    """A None probe (unavailable dependency) counts as a failed component."""
    composite = await refresh_readiness_composite(
        db_probe=None,
        queue_probe=None,
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert composite.database is False
    # queue_freshness stays fresh (empty-set default) — DB unavailability
    # must not double-report through queue_freshness.
    assert composite.queue_freshness is True
    assert composite.ready is False


# ── Queue-freshness edge cases (pure) ─────────────────────────────────────
# The queue probe returns the age in SECONDS computed SQL-side (see
# make_queue_probe); these tests exercise evaluate_queue_freshness's
# handling of that precomputed age.


def test_freshness_no_running_tasks_is_fresh():
    fresh, age = evaluate_queue_freshness(None, threshold_seconds=120)
    assert fresh is True
    assert age is None


def test_freshness_recent_heartbeat_is_fresh():
    fresh, age = evaluate_queue_freshness(30.0, threshold_seconds=120)
    assert fresh is True
    assert age == pytest.approx(30.0)


def test_freshness_stale_heartbeat_degrades():
    fresh, age = evaluate_queue_freshness(121.0, threshold_seconds=120)
    assert fresh is False
    assert age == pytest.approx(121.0)


def test_freshness_boundary_is_fresh():
    """Age == threshold counts as fresh (inclusive boundary)."""
    fresh, age = evaluate_queue_freshness(120.0, threshold_seconds=120)
    assert fresh is True
    assert age == pytest.approx(120.0)


def test_freshness_int_age_accepted():
    """EXTRACT(EPOCH …) comes back as Decimal on psycopg — float()/int coerced."""
    fresh, age = evaluate_queue_freshness(45, threshold_seconds=120)
    assert fresh is True
    assert age == 45.0


def test_freshness_negative_age_clamped():
    """Clock skew (negative SQL-side age) clamps to 0, stays fresh."""
    fresh, age = evaluate_queue_freshness(-5.0, threshold_seconds=120)
    assert fresh is True
    assert age == 0.0


# ── Composite assembly + payload shape ────────────────────────────────────


def test_degraded_composite_reports_all_reasons():
    composite = compute_readiness_composite(
        database_ok=False,
        queue_fresh_ok=False,
        services_ok=False,
        queue_max_age_seconds=999.0,
    )
    assert composite.ready is False
    assert len(composite.reasons) == 3

    payload = composite.to_payload(draining=False)
    assert payload["status"] == "degraded"
    assert payload["draining"] is False
    assert payload["detail"]["queue_max_age_seconds"] == 999.0
    assert any("queue_freshness" in r for r in payload["detail"]["reasons"])


# ── Refresher loop wiring (the piece api.py owns) ─────────────────────────


@pytest.mark.asyncio
async def test_periodic_refresh_loop_populates_state_and_stops():
    """One tick writes the composite onto app.state; cancel terminates cleanly.

    Exercises ``daemon.api._periodic_readiness_refresh_loop`` with
    injected fakes: probes replaced via make_*_probe patching is NOT
    needed because the loop builds probes from ``manager.engine`` —
    so we give it a manager whose engine is a lightweight fake
    returning canned probe outcomes.
    """
    import asyncio

    from daemon.api import _periodic_readiness_refresh_loop

    class FakeConn:
        def __init__(self, outcomes):
            self._outcomes = outcomes

        def execute(self, stmt, params=None):
            class _R:
                def __init__(self, value):
                    self._v = value

                def scalar(self):
                    return self._v

            # SELECT 1 → True; age aggregate → 30.0
            if "SELECT 1" in str(stmt):
                return _R(1)
            return _R(30.0)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeEngine:
        dialect = type("D", (), {"name": "sqlite"})()

        def connect(self):
            return FakeConn(None)

    class FakeManager:
        engine = FakeEngine()

    class FakeState:
        job_processor = object()
        live_hub = object()
        readiness_composite = None

    task = asyncio.create_task(
        _periodic_readiness_refresh_loop(
            manager=FakeManager(),
            app_state=FakeState(),
            interval_seconds=3600,  # long sleep → single tick then parked
            queue_freshness_threshold_seconds=120,
        )
    )
    # First tick fires immediately (t=0) — wait for it
    state = task.get_coro().cr_frame.f_locals["app_state"]
    for _ in range(100):
        if state.readiness_composite is not None:
            break
        await asyncio.sleep(0.01)
    assert state.readiness_composite is not None
    assert state.readiness_composite.ready is True
    assert state.readiness_composite.queue_max_age_seconds == pytest.approx(30.0)

    # Cancel → clean exit (CancelledError is swallowed by test teardown
    # only if awaited; assert the task finishes without other errors)
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=2)
    except asyncio.CancelledError:
        pass

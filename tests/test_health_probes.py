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
``tests/postgres/test_readiness_pg.py`` (``-m postgres``). The
DB-backed suite added for incident r-20260929-170301-0cb2 (stop-frozen
heartbeat amnesty + the INFLIGHT advisory count) exercises the real
probe factories against an in-memory SQLite engine — it bridges the
pure-logic and HTTP layers so the SQL arms of ``make_queue_probe`` are
pinned to behavior, not just to the seam's callable signature.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest

from daemon.services.readiness import (
    READINESS_FORCE_DEGRADED_ENV,
    ReadinessComposite,
    apply_forced_degradation,
    compute_readiness_composite,
    evaluate_queue_freshness,
    forced_degradation_active,
    make_db_probe,
    make_queue_probe,
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
async def test_refresh_queue_probe_timeout_degrades_freshness():
    """A timed-out QUEUE probe is NOT the empty-set default (review m4).

    "No answer" must not read as "no RUNNING tasks" (None age = fresh).
    The composite degrades with an explicit timeout reason and reports
    the age as None (unknown), never as a fabricated fresh number.
    """
    import time

    def very_slow_queue_probe():
        time.sleep(2.6)  # > QUEUE_PROBE_TIMEOUT_S (2.0s)
        return 999.0

    composite = await refresh_readiness_composite(
        db_probe=Mock(return_value=True),
        queue_probe=very_slow_queue_probe,
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert composite.queue_freshness is False
    assert composite.ready is False
    assert composite.queue_max_age_seconds is None  # unknown, not fabricated
    assert any("timed out" in r for r in composite.reasons)
    # The DB component must be unaffected by a queue-probe timeout.
    assert composite.database is True


@pytest.mark.asyncio
async def test_refresh_queue_probe_timeout_distinguished_from_empty_set():
    """Timeout vs empty-set produce DIFFERENT composites (m4 regression pin).

    Empty set → fresh, no timeout reason. Timeout → degraded with the
    timeout reason. Guards against a regression to the pre-m4 behavior
    (timeout silently mapped to the empty-set default).
    """

    def empty_set_probe():
        return None

    def hanging_probe():
        import time

        time.sleep(2.6)  # > QUEUE_PROBE_TIMEOUT_S
        return 5.0

    fresh_composite = await refresh_readiness_composite(
        db_probe=Mock(return_value=True),
        queue_probe=empty_set_probe,
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )
    timed_out_composite = await refresh_readiness_composite(
        db_probe=Mock(return_value=True),
        queue_probe=hanging_probe,
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert fresh_composite.queue_freshness is True
    assert timed_out_composite.queue_freshness is False
    assert fresh_composite.reasons == []
    assert any("timed out" in r for r in timed_out_composite.reasons)


@pytest.mark.asyncio
async def test_refresh_db_timeout_does_not_leak_into_database_ok():
    """A timed-out DB probe must degrade database — never read as truthy.

    Pins the m4 design: the guard returns an out-of-band timeout flag;
    a sentinel-VALUE design would have let the marker leak into
    ``database_ok`` and fail OPEN.
    """

    def hanging_db_probe():
        import time

        time.sleep(0.8)  # > DB_PROBE_TIMEOUT_S (0.5s)
        return True

    composite = await refresh_readiness_composite(
        db_probe=hanging_db_probe,
        queue_probe=Mock(return_value=None),
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert composite.database is False  # exactly False — not any truthy marker
    assert composite.ready is False
    assert composite.database is not True


@pytest.mark.asyncio
async def test_refresh_queue_probe_exception_keeps_empty_set_default():
    """A queue probe that RAISES still follows the empty-set default.

    Exceptions ≠ timeouts (m4): an unreachable database already
    degrades the ``database`` component; queue_freshness must not
    double-report.
    """
    composite = await refresh_readiness_composite(
        db_probe=Mock(side_effect=RuntimeError("db gone")),
        queue_probe=Mock(side_effect=RuntimeError("db gone")),
        services_ok=True,
        queue_freshness_threshold_seconds=120,
    )

    assert composite.database is False
    assert composite.queue_freshness is True  # empty-set default on exception
    # No QUEUE-timeout reason (the DB reason string legitimately says
    # "failed or timed out" — that is the database component, not ours).
    assert not any("queue probe timed out" in r for r in composite.reasons)


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


# ── Readiness degradation drill knob (deferred tester probe P7) ────────────
# The Phase-1 tester skipped the /readyz green→red→green probe with
# reason "no knob" — the daemon exposed no documented way to force
# readiness degradation without touching shared infra (stopping the
# shared PG was explicitly out of bounds). ENSEMBLE_READINESS_FORCE_
# DEGRADED is that knob; these tests pin its fail-safe contract and
# the full transition.


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "True", "yes", "on", "degraded", "DEGRADED", " 1 "],
)
def test_forced_degradation_truthy_spellings(value):
    assert forced_degradation_active(value) is True


@pytest.mark.parametrize(
    "value", [None, "", "0", "false", "off", "garbage", "-1", "ready"]
)
def test_forced_degradation_off_values(value):
    assert forced_degradation_active(value) is False


def _drill_ready_composite() -> ReadinessComposite:
    return compute_readiness_composite(
        database_ok=True,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=30.0,
        checked_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )


def test_apply_forced_degradation_ready_becomes_degraded():
    base = _drill_ready_composite()
    out = apply_forced_degradation(base, env_value="1")
    assert out.ready is False
    # Honest reason naming the knob
    assert any(
        READINESS_FORCE_DEGRADED_ENV in r and "drill" in r for r in out.reasons
    ), out.reasons
    # Component readings are PRESERVED (the drill must not falsify
    # what the probes actually saw) — only the aggregate flips.
    assert out.database is True
    assert out.queue_freshness is True
    assert out.services is True
    assert out.queue_max_age_seconds == 30.0
    assert out.checked_at == base.checked_at
    # Purity: the input composite is untouched
    assert base.ready is True
    assert base.reasons == []


def test_apply_forced_degradation_already_degraded_appends_reason():
    base = compute_readiness_composite(
        database_ok=False,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=None,
    )
    out = apply_forced_degradation(base, env_value="degraded")
    assert out.ready is False
    assert READINESS_FORCE_DEGRADED_ENV in " ".join(out.reasons)
    # The real cause stays first — the reason list stays truthful
    assert out.reasons[0].startswith("database:")


def test_apply_forced_degradation_is_one_way():
    # Knob off can never turn a degraded composite ready
    degraded = compute_readiness_composite(
        database_ok=False,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=None,
    )
    out = apply_forced_degradation(degraded, env_value=None)
    assert out is degraded  # unchanged, still degraded


def test_apply_forced_degradation_off_values_pass_through():
    for value in (None, "", "0", "false", "garbage"):
        base = _drill_ready_composite()
        out = apply_forced_degradation(base, env_value=value)
        assert out is base
        assert out.ready is True


@pytest.mark.asyncio
async def test_green_red_green_transition_with_drill_knob(monkeypatch):
    """Full green→red→green through refresh + knob, healthy probes throughout.

    The probes stay healthy the entire time — the ONLY thing that
    changes is the knob, which is exactly the drill semantics the
    tester needed (degradation without touching shared infra).
    """
    healthy_db = lambda: True  # noqa: E731
    healthy_queue = lambda: None  # noqa: E731  (no RUNNING tasks → fresh)

    async def refresh():
        return await refresh_readiness_composite(
            db_probe=healthy_db,
            queue_probe=healthy_queue,
            services_ok=True,
            queue_freshness_threshold_seconds=120,
        )

    # GREEN — knob off
    monkeypatch.delenv(READINESS_FORCE_DEGRADED_ENV, raising=False)
    green = apply_forced_degradation(
        await refresh(),
        env_value=None,
    )
    assert green.ready is True

    # RED — knob on (probes still healthy)
    monkeypatch.setenv(READINESS_FORCE_DEGRADED_ENV, "1")
    import os

    red = apply_forced_degradation(
        await refresh(),
        env_value=os.environ.get(READINESS_FORCE_DEGRADED_ENV),
    )
    assert red.ready is False
    assert red.database is True  # readings preserved

    # GREEN AGAIN — knob off
    monkeypatch.delenv(READINESS_FORCE_DEGRADED_ENV, raising=False)
    green_again = apply_forced_degradation(
        await refresh(),
        env_value=os.environ.get(READINESS_FORCE_DEGRADED_ENV),
    )
    assert green_again.ready is True


@pytest.mark.asyncio
async def test_readyz_forced_degradation_503_reports_drill_reason(
    root_client, app_with_mock_manager
):
    """HTTP semantics under the drill knob: 503 + Retry-After + honest reason."""
    app_with_mock_manager.state.readiness_composite = apply_forced_degradation(
        _drill_ready_composite(), env_value="1"
    )
    resp = await root_client.get("/readyz")
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "5"
    body = resp.json()
    assert body["status"] == "degraded"
    assert READINESS_FORCE_DEGRADED_ENV in " ".join(body["detail"]["reasons"])
    # Component readings preserved in the body — the drill is visible
    # as a forced state, not as falsified component failures.
    assert body["components"]["database"] is True


@pytest.mark.asyncio
async def test_periodic_refresh_loop_applies_drill_knob_between_ticks(
    monkeypatch,
):
    """The wired loop reads the knob PER TICK — flipping the env between
    ticks drives the live composite red and back green without any
    restart. This is the api.py-side proof of the P7 transition."""
    import asyncio

    from daemon.api import _periodic_readiness_refresh_loop

    class FakeConn:
        def execute(self, stmt, params=None):
            class _R:
                def __init__(self, value):
                    self._v = value

                def scalar(self):
                    return self._v

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
            return FakeConn()

    class FakeManager:
        engine = FakeEngine()

    class FakeState:
        job_processor = object()
        live_hub = object()
        readiness_composite = None

    state = FakeState()
    task = asyncio.create_task(
        _periodic_readiness_refresh_loop(
            manager=FakeManager(),
            app_state=state,
            interval_seconds=0.05,
            queue_freshness_threshold_seconds=120,
        )
    )

    async def wait_for(predicate, timeout_s=5.0):
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            c = state.readiness_composite
            if c is not None and predicate(c):
                return c
            await asyncio.sleep(0.01)
        raise AssertionError(
            f"composite never satisfied predicate; last="
            f"{state.readiness_composite!r}"
        )

    try:
        # GREEN — first tick, knob off
        monkeypatch.delenv(READINESS_FORCE_DEGRADED_ENV, raising=False)
        c = await wait_for(lambda c: c.ready is True)
        assert c.database is True

        # RED — knob flips BETWEEN ticks; no restart, no probe change
        monkeypatch.setenv(READINESS_FORCE_DEGRADED_ENV, "1")
        c = await wait_for(lambda c: c.ready is False)
        assert c.database is True  # readings preserved
        assert READINESS_FORCE_DEGRADED_ENV in " ".join(c.reasons)

        # GREEN AGAIN — knob off, next tick recovers
        monkeypatch.delenv(READINESS_FORCE_DEGRADED_ENV, raising=False)
        await wait_for(lambda c: c.ready is True)
    finally:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2)
        except asyncio.CancelledError:
            pass


# ── Stop-frozen heartbeat amnesty + inflight_turns advisory ───────────────
#
# Incident r-20260929-170301-0cb2: a live promote's own daemon stop froze
# RUNNING task heartbeats mid-turn; the replacement daemon's readiness
# probe read the frozen beats as a CURRENT stall → /readyz red mid-soak →
# auto-rollback. The amnesty: a beat older than the daemon boot epoch was
# written by a dead process — it is invisible to freshness accounting.
# Signal-preservation invariant (tested both ways below): a beat going
# stale while the daemon has been continuously up degrades EXACTLY as
# before. See daemon/services/boot_epoch.py.


@pytest.fixture
def task_engine():
    """In-memory SQLite engine with the real task-table schema."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    import daemon.repositories.task.models  # noqa: F401 — register Task
    import daemon.repositories.instance.models  # noqa: F401 — register Instance

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _insert_running_task(
    engine,
    *,
    heartbeat,
    work_id,
    status="running",
):
    """Insert one task row; ``heartbeat=None`` leaves the column NULL."""
    from sqlalchemy import text as sa_text
    from daemon.repositories.task.models import TaskStatus, TaskType

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                """
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, created_at, cancel_requested,
                                  retry_scheduled, work_id, is_deferred,
                                  is_background, worker_id, started_at,
                                  last_heartbeat_at)
                VALUES (:task_type, :instance_id, NULL, :status,
                        0, :created_at, 0, 0, :work_id, 0,
                        0, NULL, :started_at, :heartbeat)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": "readiness-amnesty-test",
                "status": status if status != "running" else TaskStatus.RUNNING.value,
                "created_at": now,
                "work_id": work_id,
                "started_at": now,
                "heartbeat": heartbeat,
            },
        )


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def test_amnesty_pre_boot_beat_is_invisible_to_freshness(task_engine):
    """Beat frozen BEFORE the boot epoch → excluded from the MAX();

    the all-frozen RUNNING set reads as age None (= fresh), while the
    SAME data through a no-epoch probe reads stale — proving the
    scenario that killed promote r-20260929-170301-0cb2 now passes.
    """
    now = datetime.now(timezone.utc)
    frozen_beat = now - timedelta(seconds=180)
    boot = now - timedelta(seconds=125)  # daemon booted AFTER the freeze
    _insert_running_task(task_engine, heartbeat=frozen_beat, work_id="w-frozen")

    # No-epoch probe (BOOT_EPOCH_FLOOR): identical to pre-amnesty SQL —
    # the frozen beat IS stale evidence.
    legacy = make_queue_probe(task_engine)()
    assert legacy.max_age_seconds is not None
    assert legacy.max_age_seconds >= 175
    fresh, _ = evaluate_queue_freshness(legacy.max_age_seconds, threshold_seconds=120)
    assert fresh is False  # the incident, reproduced on legacy semantics

    # Epoch probe: the pre-boot beat is invisible → age None → fresh.
    amnestied = make_queue_probe(task_engine, boot_epoch=_naive_utc(boot))()
    assert amnestied.max_age_seconds is None
    fresh, age = evaluate_queue_freshness(None, threshold_seconds=120)
    assert fresh is True and age is None


def test_amnesty_epoch_boundary_is_inclusive(task_engine):
    """A beat exactly AT the epoch is post-boot (counted); 1µs before

    the epoch is pre-boot (exempt). The boundary pins the >= in the
    SQL CASE arm.
    """
    now = datetime.now(timezone.utc)
    boot = _naive_utc(now - timedelta(seconds=300))
    at_epoch = boot  # exactly at the epoch, old enough to be stale

    _insert_running_task(task_engine, heartbeat=at_epoch, work_id="w-at-epoch")
    result = make_queue_probe(task_engine, boot_epoch=boot)()
    assert result.max_age_seconds is not None
    assert result.max_age_seconds >= 295  # counted → stale evidence

    with task_engine.begin() as conn:
        from sqlalchemy import text as sa_text

        conn.execute(
            sa_text("UPDATE task SET last_heartbeat_at = :hb"),
            {"hb": at_epoch - timedelta(microseconds=1)},
        )
    result = make_queue_probe(task_engine, boot_epoch=boot)()
    assert result.max_age_seconds is None  # 1µs pre-boot → exempt


def test_signal_preservation_post_boot_stale_still_degrades(task_engine):
    """INVARIANT: continuously-up daemon, beat going stale post-boot →

    degrades exactly as today. The amnesty may only mask beats older
    than the boot.
    """
    now = datetime.now(timezone.utc)
    boot = now - timedelta(hours=1)  # daemon up an hour — beat is post-boot
    stale_beat = now - timedelta(seconds=180)

    _insert_running_task(task_engine, heartbeat=stale_beat, work_id="w-stale")

    result = make_queue_probe(task_engine, boot_epoch=_naive_utc(boot))()
    assert result.max_age_seconds is not None
    assert result.max_age_seconds >= 175
    fresh, _ = evaluate_queue_freshness(result.max_age_seconds, threshold_seconds=120)
    assert fresh is False

    # And with no epoch at all (never captured): same degradation.
    legacy = make_queue_probe(task_engine)()
    assert legacy.max_age_seconds is not None
    assert legacy.max_age_seconds >= 175


def test_amnesty_mixed_set_max_uses_only_post_boot_beats(task_engine):
    """A fresh post-boot beat alongside a frozen pre-boot beat: the MAX

    is the post-boot beat's age (mixes never fabricate freshness for
    the post-boot side, and the frozen row contributes nothing).
    """
    now = datetime.now(timezone.utc)
    boot = _naive_utc(now - timedelta(seconds=125))
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=180), work_id="w-frozen"
    )
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=30), work_id="w-live"
    )

    result = make_queue_probe(task_engine, boot_epoch=boot)()
    assert result.max_age_seconds is not None
    assert 25 <= result.max_age_seconds <= 35  # the 30s beat, not the 180s


def test_inflight_turns_counts_fresh_post_boot_beats_only(task_engine):
    """ADVISORY count correctness: fresh post-boot beats count; stale

    post-boot, stop-frozen pre-boot, NULL-heartbeat, and non-RUNNING
    rows never do.
    """
    now = datetime.now(timezone.utc)
    boot = _naive_utc(now - timedelta(seconds=125))
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=10), work_id="w-fresh-1"
    )
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=15), work_id="w-fresh-2"
    )
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=180), work_id="w-stale-post"
    )
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=180), work_id="w-frozen-pre"
    )
    # NULL heartbeat — no beat, no liveness signal.
    _insert_running_task(task_engine, heartbeat=None, work_id="w-null-beat")
    # Fresh beat but NOT running — status filter still applies.
    _insert_running_task(
        task_engine,
        heartbeat=now - timedelta(seconds=10),
        work_id="w-completed",
        status="completed",
    )
    # Push w-frozen-pre's beat before the boot epoch.
    with task_engine.begin() as conn:
        from sqlalchemy import text as sa_text

        conn.execute(
            sa_text("UPDATE task SET last_heartbeat_at = :hb WHERE work_id = 'w-frozen-pre'"),
            {"hb": now - timedelta(seconds=200)},
        )

    result = make_queue_probe(
        task_engine, boot_epoch=boot, freshness_threshold_seconds=120
    )()
    assert result.inflight_turns == 2
    assert result.max_age_seconds is not None  # newest post-boot beat (10s)


def test_inflight_turns_zero_on_all_frozen_and_none_without_threshold(task_engine):
    """All-frozen set: age None (fresh) AND inflight 0 — a real count,

    not an unknown. Without a threshold bound the count is None
    (unknown), never a guess.
    """
    now = datetime.now(timezone.utc)
    boot = _naive_utc(now - timedelta(seconds=125))
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=180), work_id="w-frozen"
    )

    armed = make_queue_probe(
        task_engine, boot_epoch=boot, freshness_threshold_seconds=120
    )()
    assert armed.max_age_seconds is None
    assert armed.inflight_turns == 0

    unarmed = make_queue_probe(task_engine, boot_epoch=boot)()
    assert unarmed.inflight_turns is None


async def test_composite_carries_inflight_advisory_without_affecting_status(task_engine):
    """Composite integration: the advisory rides detail.inflight_turns

    and NEVER the status/components/reasons — a degraded database with
    inflight=2 stays degraded; a ready composite with inflight stays
    ready.
    """
    now = datetime.now(timezone.utc)
    boot = _naive_utc(now - timedelta(seconds=125))
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=10), work_id="w-live"
    )
    _insert_running_task(
        task_engine, heartbeat=now - timedelta(seconds=180), work_id="w-frozen"
    )

    composite = await _refresh_with_real_probes(
        task_engine, boot=boot, threshold=120, database_ok=True, services_ok=True
    )
    assert composite.ready is True
    assert composite.inflight_turns == 1
    payload = composite.to_payload()
    assert payload["detail"]["inflight_turns"] == 1
    assert "inflight" not in " ".join(payload["detail"]["reasons"])

    # Advisory must not rescue a degraded composite.
    degraded = await _refresh_with_real_probes(
        task_engine, boot=boot, threshold=120, database_ok=False, services_ok=True
    )
    assert degraded.ready is False
    assert degraded.database is False
    assert degraded.inflight_turns == 1  # still reported — advisory survives


async def _refresh_with_real_probes(
    engine, *, boot, threshold, database_ok, services_ok
) -> ReadinessComposite:
    """Run one refresh cycle with real probes; ``database_ok=False``

    forces the database component to fail via a raising probe."""
    def _raising_db_probe():
        raise ConnectionError("probe down")

    return await refresh_readiness_composite(
        db_probe=make_db_probe(engine) if database_ok else _raising_db_probe,
        queue_probe=make_queue_probe(
            engine, boot_epoch=boot, freshness_threshold_seconds=threshold
        ),
        services_ok=services_ok,
        queue_freshness_threshold_seconds=threshold,
    )


async def test_readyz_http_serves_inflight_advisory(root_client, app_with_mock_manager):
    """HTTP surface: detail.inflight_turns is served verbatim from the

    cached composite (200 ready and 503 degraded alike)."""
    app_with_mock_manager.state.readiness_composite = compute_readiness_composite(
        database_ok=True,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=12.5,
        checked_at=datetime(2026, 8, 16, 0, 0, 0, tzinfo=timezone.utc),
        inflight_turns=2,
    )
    response = await root_client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["detail"]["inflight_turns"] == 2

    app_with_mock_manager.state.readiness_composite = compute_readiness_composite(
        database_ok=False,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=None,
        checked_at=datetime(2026, 8, 16, 0, 0, 0, tzinfo=timezone.utc),
        inflight_turns=1,
    )
    response = await root_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["detail"]["inflight_turns"] == 1

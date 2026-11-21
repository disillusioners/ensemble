"""P6 route pack — the engine observability router (T6.4/T6.6, AC3/AC4).

The router is mounted on its OWN TestClient app (the api.py include line
is a P4-coordinated item this round — see the module docstring's DI seam
note). Covers: the three declared GETs, the method-set freeze (a POST
REDs — the freeze is asserted from the router object itself), the scope
matrix (401 / 403 / 200 / AUTH-OFF refusal byte-comparable to the metrics
door), the paged body, the off-loop thread identity, and the AUTH-off
refusal firing BEFORE any adapter read.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.services.engine import projection


# ── app + adapter fixtures ───────────────────────────────────────────────


@dataclass
class Row:
    id: str
    service_tag: str = "svc"
    service_name: str | None = "Service"
    category: str = "reconciler"
    module_info: str | None = "daemon/services/x.py"
    description: str | None = "does things"
    enabled: int = 1
    trigger_kind: str = "interval"
    interval_seconds: float | None = 90.0
    jitter_seconds: float | None = None
    first_tick_kind: str | None = "immediate"
    first_tick_offset_seconds: float | None = None
    event_kind: str | None = None
    priority: int = 5
    concurrency_class: str = "DB_OFFLOAD"
    max_runtime_s: float = 60.0
    timeout_policy: str = "cancel"
    retry_max: int = 0
    kill_switch_key: str | None = None
    version: int = 1
    last_run_at: str | None = None
    last_outcome: str | None = None
    last_skip_reason: str | None = None
    consecutive_skips: int = 0
    dead: int = 0
    registered_at: str = "2026-11-21T00:00:00+00:00"
    updated_at: str = "2026-11-21T00:00:00+00:00"
    job_metadata: dict[str, Any] = field(default_factory=dict)


class _Repo:
    def __init__(self, rows: list[Row]):
        self._rows = rows
        self.build_thread: str | None = None

    def list_jobs(self, category=None, service_tag=None):
        # Record the thread the BUILD ran on (the off-loop assertion).
        _Repo.last_list_thread = threading.current_thread().name
        return list(self._rows)

    def get_job(self, job_id: str):
        for row in self._rows:
            if row.id == job_id:
                return row
        return None


class _Maintenance:
    def job_metrics(self) -> dict[str, Any]:
        return {
            "job-0": {
                "last_success_ts": "2026-11-21T10:00:00+00:00",
                "consecutive_skips": 3,
            }
        }


class _Resolver:
    def engine_enabled(self) -> bool:
        return True

    def shadow_mode(self) -> bool:
        return True

    def job_killed(self, key):
        return False


class Adapter:
    def __init__(self, rows):
        self.repository = _Repo(rows)
        self.maintenance_service = _Maintenance()
        self.engine_service = None
        self.switch_resolver = _Resolver()
        self.event_snapshot = lambda bus=None: {
            "kinds": {"instance_terminalized": {"posture": "FAIL_MISS"}},
            "counters": {"record_dropped_total": 0},
            "generated_at": "2026-11-21T00:00:00+00:00",
        }
        self.runs_reader = lambda repository, job_id, limit: [
            {"started_at": "t", "finished_at": "t", "outcome": "SUCCEEDED",
             "skip_reason": None, "duration_seconds": 0.1, "attempt": 1}
        ]


def _app(*, auth_on: bool = True, scopes: tuple[str, ...] = ("maintenance:read",),
         adapter: Any = "default") -> FastAPI:
    from daemon.auth.context import AuthContext
    from daemon.routers.engine import router as engine_router

    api_router = APIRouter(prefix="/api")
    api_router.include_router(engine_router)
    app = FastAPI()
    app.include_router(api_router)

    if adapter == "default":
        adapter = Adapter([Row(id="job-0"), Row(id="job-1", category="probe")])
    if adapter is not None:
        app.state.engine_observability = adapter

    if auth_on:

        @app.middleware("http")
        async def _attach_auth_context(request, call_next):
            request.state.auth_context = AuthContext(
                subject="test-operator",
                scopes=set(scopes),
                auth_method="session",
                expires_at="2099-01-01T00:00:00Z",
            )
            return await call_next(request)

        app.state.auth_config = object()

    return app


def _client(**kwargs) -> TestClient:
    """The house router-test client, with the off-loop seam bound LAZILY.

    The ``db_offload`` seam is a PROCESS-level singleton; a bare
    ``TestClient`` opens a fresh portal (fresh loop) per request, so a
    captured loop goes stale. Entering the client's portal makes every
    request share ONE loop; ``configure(loop=None)`` re-arms the lazy
    bind to the loop of the first request (the metrics-door precedent,
    ``tests/helpers/metrics_query_fixtures.py:build_client``).
    """
    from daemon.services import db_offload

    client = TestClient(_app(**kwargs), raise_server_exceptions=False)
    client.__enter__()
    db_offload.configure(loop=None)
    return client


# ── T6.6 — the route/method freeze ───────────────────────────────────────


def test_router_exposes_exactly_the_three_declared_get_paths():
    from daemon.routers.engine import router

    paths = {(r.path, frozenset(r.methods)) for r in router.routes}
    assert paths == {
        ("/engine/jobs", frozenset({"GET"})),
        ("/engine/jobs/{job_id}", frozenset({"GET"})),
        ("/engine/events", frozenset({"GET"})),
    }


def test_router_method_set_is_exactly_get():
    from daemon.routers.engine import router

    methods: set[str] = set()
    for route in router.routes:
        methods.update(route.methods or ())
    assert methods == {"GET"}, (
        "D6.8: the engine router is READ-ONLY — a mutating verb REDs here"
    )


def test_a_post_to_a_declared_path_is_not_routed():
    client = _client()
    response = client.post("/api/engine/jobs")
    assert response.status_code in (404, 405), (
        f"a POST must not be routed (got {response.status_code})"
    )


# ── AC3 — the scope matrix ───────────────────────────────────────────────


def test_scope_matrix_either_scope_satisfies():
    body = _client(scopes=("maintenance:read",)).get("/api/engine/jobs")
    assert body.status_code == 200, body.text
    body = _client(scopes=("metrics:read",)).get("/api/engine/jobs")
    assert body.status_code == 200, body.text


def test_scope_matrix_missing_scope_is_403():
    response = _client(scopes=()).get("/api/engine/jobs")
    assert response.status_code == 403, response.text
    detail = response.json().get("detail", {})
    assert detail.get("error") == "SCOPE_INSUFFICIENT", detail


@pytest.mark.parametrize(
    "path",
    ["/api/engine/jobs", "/api/engine/jobs/job-0", "/api/engine/events"],
)
def test_every_route_carries_the_scope_gate(path):
    response = _client(scopes=()).get(path)
    assert response.status_code == 403, (path, response.text)


@pytest.mark.parametrize(
    "path",
    ["/api/engine/jobs", "/api/engine/jobs/job-0", "/api/engine/events"],
)
def test_every_route_refuses_auth_off_with_the_metrics_door_bytes(path):
    response = _client(auth_on=False).get(path)
    assert response.status_code == 503, (path, response.text)
    detail = response.json().get("detail", {})
    assert detail == {
        "error": "AUTH_NOT_CONFIGURED",
        "message": "Auth not configured (fail-closed)",
    }, "byte-comparable to the metrics door's refusal branch"


def test_auth_off_refuses_before_any_adapter_read():
    # The AUTH-OFF gate fires BEFORE the adapter is even consulted: an
    # app WITHOUT the adapter still answers the typed 503 (never the
    # adapter-missing 503).
    from daemon.routers.engine import router as engine_router

    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    api_router.include_router(engine_router)
    app.include_router(api_router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/engine/jobs")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "AUTH_NOT_CONFIGURED"


def test_unwired_adapter_is_a_typed_503():
    response = _client(adapter=None).get("/api/engine/jobs")
    assert response.status_code == 503
    detail = response.json().get("detail", {})
    assert detail.get("error") == "engine_observability_unavailable"


# ── the three bodies ─────────────────────────────────────────────────────


def test_list_body_shape_and_paging():
    response = _client().get("/api/engine/jobs?limit=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == projection.LIST_VIEW_KEYS
    assert body["limit"] == 1
    assert body["returned"] == 1
    assert body["next_cursor"], "two jobs with limit=1 must mint a cursor"
    # Resume the walk.
    second = _client().get(
        f"/api/engine/jobs?limit=1&cursor={body['next_cursor']}"
    ).json()
    assert second["returned"] == 1
    assert second["jobs"][0]["id"] != body["jobs"][0]["id"]
    assert second["next_cursor"] is None


def test_list_rejects_a_bad_enabled_filter():
    response = _client().get("/api/engine/jobs?enabled=maybe")
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_filter"


def test_list_rejects_a_bad_cursor():
    response = _client().get("/api/engine/jobs?cursor=garbage")
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_cursor"


def test_detail_body_shape_and_runs_tail():
    response = _client().get("/api/engine/jobs/job-0")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == projection.DETAIL_KEYS
    assert body["switches"]["should_run"] is True
    assert len(body["runs"]) == 1
    assert body["estate"]["last_success_ts"]["source"] == "maintenance"


def test_detail_404_for_unknown_job():
    response = _client().get("/api/engine/jobs/no-such-job")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "engine_job_not_found"


def test_events_body_shape():
    response = _client().get("/api/engine/events")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == projection.EVENT_VIEW_KEYS
    assert "instance_terminalized" in body["kinds"]


# ── AC4 — off-loop build ─────────────────────────────────────────────────


def test_the_list_build_runs_off_the_event_loop():
    _client().get("/api/engine/jobs")
    build_thread = getattr(_Repo, "last_list_thread", None)
    assert build_thread is not None
    assert "MainThread" != build_thread, (
        "the page build must run on the off-loop worker, not the event loop"
    )

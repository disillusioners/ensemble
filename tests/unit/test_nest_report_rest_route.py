"""The ``/api/nest-reports`` route pack (the nest-anomaly lane).

WHAT IT PROVES
--------------
* the route is gated IDENTICALLY to the metrics door: the SAME any-of
  query scope set (``maintenance:read`` OR ``metrics:read``) and
  NOTHING broader;
* the fail-closed ``503 AUTH_NOT_CONFIGURED`` gate fires BEFORE any
  read — the ordering is proved by a service stub that RAISES if it is
  touched (M-3e);
* a scope miss is a typed ``403 SCOPE_INSUFFICIENT``;
* with the scope, the route answers the decision-ready projection;
* the limit is bounded and the cursor is never silent;
* the response shape EQUALS the read tools' shape (one projection, two
  transports — AM-1);
* **AM-12(a)/(b)**: the list payload carries NO ``excerpt`` key, and a
  member excerpt appears ONLY behind ``?include_excerpt=true``
  (M-3h / M-3i);
* **AM-1 (the ROUTE-SET PIN — mandatory)**: the METRICS router's path
  set is EXACTLY its three query routes — a 4th metrics route REDS
  (M-3g).  The existing scope cage only CHECKS the three named paths
  (a 4th is skipped, not rejected), and this phase bumps the global
  declared-route census, so this pin is what keeps the door FROZEN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel
from sqlalchemy import create_engine

from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
from daemon.routers.nest_reports import router as nest_reports_router
from daemon.services.anomaly_service import AnomalyService

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The door's any-of QUERY scope set (the route's EXACT accept set).
DOOR_SCOPE_SET = {"maintenance:read", "metrics:read"}


async def _passthrough(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


class _RaisingManager:
    """A manager whose anomaly service RAISES if it is touched.

    The stub is the ORDERING PROOF: with auth OFF the route must
    answer ``503 AUTH_NOT_CONFIGURED`` WITHOUT touching the service —
    if the gate ran late, the stub's exception would surface instead.
    """

    def __init__(self, service: Any = None) -> None:
        self._service = service

    @property
    def _anomaly_service(self) -> Any:  # pragma: no cover — the touch is the failure
        if self._service is None:
            raise AssertionError(
                "the anomaly service was touched before the auth gate"
            )
        return self._service


def _service(store) -> AnomalyService:
    return AnomalyService(
        store, config=NestReportConfig(), manager=None, offload=_passthrough
    )


@pytest.fixture
def store(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'anomaly_route.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session

    now = datetime.now(timezone.utc)
    fresh = now.isoformat(timespec="microseconds")
    stale = (now - timedelta(days=30)).isoformat(timespec="microseconds")
    with Session(engine) as session:
        session.add(
            AnomalyCluster(
                id="cl-1",
                cluster_key_hash="k1",
                tree_root_instance_id="tree-1",
                category="tool-failure",
                first_report_at=fresh,
                last_report_at=fresh,
                report_count=1,
                max_severity="critical",
                board_item_id=None,
                created_at=fresh,
            )
        )
        session.add(
            AnomalyCluster(
                id="cl-2",
                cluster_key_hash="k2",
                tree_root_instance_id="tree-1",
                category="llm-provider",
                first_report_at=stale,
                last_report_at=stale,
                report_count=1,
                max_severity="major",
                board_item_id=None,
                created_at=stale,
            )
        )
        session.add(
            AnomalyReport(
                id="r-1",
                created_at=fresh,
                agent_id="tester",
                instance_id="i-1",
                tree_root_instance_id="tree-1",
                parent_chain=[],
                parent_chain_agents=[],
                category="tool-failure",
                category_raw="tool failure",
                severity="critical",
                description="d1",
                excerpt="EXCERPT-TEXT",
                cluster_id="cl-1",
                outcome="accepted",
            )
        )
        session.commit()
    return engine


def _build_client(
    service: Any,
    *,
    auth_on: bool = True,
    scopes: tuple[str, ...] = ("maintenance:read",),
) -> TestClient:
    """The house router-test client shape (the metrics fixtures' form).

    The client's portal is ENTERED so every request shares ONE event
    loop, and the off-loop seam (``daemon.services.db_offload`` — a
    PROCESS-level singleton bound to the loop that first used it) is
    re-bound to it.  A bare ``TestClient`` opens a FRESH portal per
    request, so the seam's captured loop goes stale after the first
    request — the house fixture's exact lesson.
    """
    import weakref

    from daemon.auth.context import AuthContext
    from daemon.services import db_offload

    api_router = APIRouter()
    api_router.include_router(nest_reports_router)
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.state.manager = _RaisingManager(service)
    if auth_on:

        @app.middleware("http")
        async def _attach_auth_context(request, call_next):  # noqa: ANN001
            request.state.auth_context = AuthContext(
                subject="test-operator",
                scopes=set(scopes),
                auth_method="session",
                expires_at="2099-01-01T00:00:00Z",
            )
            return await call_next(request)

        app.state.auth_config = object()
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    db_offload.configure(loop=None)
    weakref.finalize(client, _close_client, client)
    return client


def _close_client(client: TestClient) -> None:
    """Close the client's portal (the GC-time teardown)."""
    try:
        client.__exit__(None, None, None)
    except Exception:  # pragma: no cover — a teardown never fails a test
        pass


class TestTheAuthGate:
    def test_auth_off_answers_503_before_any_read(self, store) -> None:
        """M-3e — the fail-closed gate fires BEFORE any read.

        The service stub RAISES if it is touched: a 503 (not a 500)
        proves the gate ran first.
        """
        client = _build_client(None, auth_on=False)
        response = client.get("/api/nest-reports/clusters")
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["error"] == "AUTH_NOT_CONFIGURED"

    def test_auth_off_answers_503_on_the_detail_route(self, store) -> None:
        client = _build_client(None, auth_on=False)
        response = client.get("/api/nest-reports/clusters/cl-1")
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["error"] == "AUTH_NOT_CONFIGURED"

    def test_a_scope_miss_is_403(self, store) -> None:
        client = _build_client(
            _service(store), auth_on=True, scopes=("settings:read",)
        )
        response = client.get("/api/nest-reports/clusters")
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "SCOPE_INSUFFICIENT"

    @pytest.mark.parametrize("scope", ["maintenance:read", "metrics:read"])
    def test_either_door_scope_is_accepted(self, store, scope: str) -> None:
        """The door's any-of set — EITHER scope opens the route."""
        client = _build_client(_service(store), auth_on=True, scopes=(scope,))
        response = client.get("/api/nest-reports/clusters")
        assert response.status_code == 200, response.text

    def test_the_route_accepts_exactly_the_door_scope_set(self) -> None:
        """The route declares EXACTLY ``{maintenance:read, metrics:read}``
        — nothing broader (M-3f)."""
        from tests.unit.test_router_scope_manifest import PINNED_SCOPE_MANIFEST

        assert (
            PINNED_SCOPE_MANIFEST["/api/nest-reports"] == "maintenance:read|metrics:read"
        )
        for route in nest_reports_router.routes:
            scopes: set[str] = set()
            for dependency in getattr(route, "dependant", None).dependencies:
                scopes.update(getattr(dependency.call, "required_scopes", None) or ())
            assert scopes == DOOR_SCOPE_SET, (route.path, sorted(scopes))

    def test_an_unrelated_scope_does_not_satisfy_the_route(self, store) -> None:
        client = _build_client(
            _service(store), auth_on=True, scopes=("instances:read",)
        )
        response = client.get("/api/nest-reports/clusters")
        assert response.status_code == 403, response.text


class TestTheProjection:
    def test_the_list_is_the_grouped_decision_ready_shape(self, store) -> None:
        client = _build_client(_service(store))
        response = client.get("/api/nest-reports/clusters")
        assert response.status_code == 200, response.text
        payload = response.json()
        group = payload["groups"][0]
        assert group["tree_root_instance_id"] == "tree-1"
        assert group["category"] == "tool-failure"
        assert group["severity"] == "critical"
        assert group["severity_label"] == "critical (peak)"
        assert group["severity_rank"] == 3
        assert group["occurrence_count"] == 1
        assert group["report_count"] == 1
        assert group["cluster_ids"] == ["cl-1"]
        assert group["open"] is True
        assert set(payload["bound"]) == {
            "matched_count",
            "returned_count",
            "dropped_count",
            "more_available",
            "truncated",
            "narrow_hint",
        }
        assert payload["freshness"]["queried_at_utc"]

    def test_the_detail_route_serves_the_members(self, store) -> None:
        client = _build_client(_service(store))
        response = client.get("/api/nest-reports/clusters/cl-1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["cluster_id"] == "cl-1"
        assert payload["severity_label"] == "critical (peak)"
        assert payload["reports"][0]["category"] == "tool-failure"

    def test_an_unknown_cluster_is_404(self, store) -> None:
        client = _build_client(_service(store))
        response = client.get("/api/nest-reports/clusters/no-such-cluster")
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["error"] == "anomaly_cluster_not_found"

    def test_the_response_shape_mirrors_the_read_tools(self, store) -> None:
        """AM-1 — the route and the tools serve the SAME projection."""
        from daemon.tools.nest_report_read import create_nest_report_read_tools

        service = _service(store)
        client = _build_client(service)
        route_payload = client.get("/api/nest-reports/clusters").json()
        tools = create_nest_report_read_tools(
            _RaisingManager(service), agent_id="maintenancer"
        )
        import asyncio

        tool_payload = json.loads(asyncio.run(tools[0].ainvoke({})))
        assert set(route_payload) >= set(tool_payload) - {"query"}
        assert route_payload["groups"][0].keys() == tool_payload["groups"][0].keys()


class TestAM12TheExcerpt:
    def test_the_list_payload_has_no_excerpt_key(self, store) -> None:
        client = _build_client(_service(store))
        payload = client.get("/api/nest-reports/clusters").json()
        assert "excerpt" not in json.dumps(payload)

    def test_the_detail_payload_has_no_excerpt_by_default(self, store) -> None:
        client = _build_client(_service(store))
        payload = client.get("/api/nest-reports/clusters/cl-1").json()
        for member in payload["reports"]:
            assert "excerpt" not in member

    def test_the_excerpt_appears_only_behind_the_flag(self, store) -> None:
        client = _build_client(_service(store))
        payload = client.get(
            "/api/nest-reports/clusters/cl-1?include_excerpt=true"
        ).json()
        assert payload["reports"][0]["excerpt"] == "EXCERPT-TEXT"


class TestTheBounds:
    def test_the_limit_is_bounded(self, store) -> None:
        client = _build_client(_service(store))
        response = client.get("/api/nest-reports/clusters?limit=100000")
        assert response.status_code == 200
        assert response.json()["bound"]["returned_count"] <= 50

    def test_a_malformed_limit_is_400(self, store) -> None:
        client = _build_client(_service(store))
        response = client.get("/api/nest-reports/clusters?limit=abc")
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"] == "bad_request"

    def test_an_out_of_set_severity_filter_is_400(self, store) -> None:
        client = _build_client(_service(store))
        response = client.get("/api/nest-reports/clusters?severity=catastrophic")
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"] == "bad_request"

    def test_the_cursor_is_never_silent(self, store) -> None:
        """A garbage cursor is a typed 400 — never a silently answered
        gap."""
        client = _build_client(_service(store))
        response = client.get("/api/nest-reports/clusters?cursor=not-a-cursor")
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"] == "bad_request"

    def test_a_cursor_issued_for_other_filters_is_400(self, store) -> None:
        client = _build_client(_service(store))
        from daemon.routers.nest_reports import _encode_cursor

        cursor = _encode_cursor(
            {"v": 1, "t": 9999999999, "o": 0, "q": '{"other": true}'}
        )
        response = client.get(f"/api/nest-reports/clusters?cursor={cursor}")
        assert response.status_code == 400, response.text


class TestAM1TheRouteSetPin:
    """AM-1 — the METRICS door stays FROZEN beside the new domain route.

    A 4th metrics QUERY route (or any extra metrics route) REDS here.
    """

    def test_the_metrics_router_carries_exactly_its_three_query_routes(self) -> None:
        from daemon.routers.metrics import router as metrics_router

        paths = sorted(
            getattr(route, "path", "") for route in metrics_router.routes
        )
        assert paths == [
            "/metrics",
            "/metrics/families",
            "/metrics/series",
            "/metrics/snapshot",
        ], (
            "the metrics door's path set changed — a new metrics route is "
            f"a contract change (the door is FROZEN): {paths}"
        )

    def test_the_metrics_query_route_set_is_exactly_three(self) -> None:
        from daemon.routers.metrics import router as metrics_router

        query_paths = {
            getattr(route, "path", "")
            for route in metrics_router.routes
            if getattr(route, "path", "") != "/metrics"
        }
        assert query_paths == {
            "/metrics/families",
            "/metrics/snapshot",
            "/metrics/series",
        }

    def test_the_metrics_router_source_is_untouched_by_this_lane(self) -> None:
        """The door is FROZEN — this lane edits no line of it."""
        import subprocess

        completed = subprocess.run(
            ["git", "diff", "--name-only", "22c144000", "--", "daemon/routers/metrics.py"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
        )
        assert completed.stdout.strip() == "", completed.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""Unit tests for the ``/api/tool-usage/*`` read door (Phase 1).

Pins:
* auth replication invariant — ``_require_auth_on`` executable body
  byte-matches the metrics door's (D21; byte-match ONLY, no helper).
* 1 MB response-size guard; top-50 agents cap (repository-side).
* closed error vocabulary + 503 under auth-OFF.

Behavior: summary/daily shapes, days clamps [1,90], 404 unknown_tool,
`__none__` filter pass-through.
"""

from __future__ import annotations

import inspect
import warnings
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.auth.context import AuthContext
from daemon.routers import tool_usage as tu


class _Repo:
    def __init__(self, *, summary=None, series=None, has=True):
        self._summary = summary if summary is not None else []
        self._series = series if series is not None else []
        self._has = has
        self.calls: list[tuple] = []

    def query_summary(self, project_id, days):
        self.calls.append(("summary", project_id, days))
        return self._summary

    def query_daily(self, project_id, tool_name, days):
        self.calls.append(("daily", project_id, tool_name, days))
        return self._series

    def has_tool(self, tool_name):
        self.calls.append(("has", tool_name))
        return self._has


def _app(*, auth_on: bool = True, scopes=("maintenance:read",), repo=None) -> FastAPI:
    api_router = APIRouter(prefix="/api")
    api_router.include_router(tu.router)
    app = FastAPI()
    app.include_router(api_router)
    if auth_on:
        @app.middleware("http")
        async def _attach(request, call_next):
            request.state.auth_context = AuthContext(
                subject="op", scopes=set(scopes), auth_method="session",
                expires_at="2099-01-01T00:00:00Z",
            )
            return await call_next(request)
        app.state.auth_config = object()
    manager = type("M", (), {})()
    manager._tool_usage_repository = repo or _Repo()
    app.state.manager = manager
    return app


def _client(**kw) -> TestClient:
    return TestClient(_app(**kw), raise_server_exceptions=False)


class TestRegistration:
    def test_routes_registered_on_real_app(self):
        from daemon.api import create_app

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            app = create_app()
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/tool-usage/summary" in paths
        assert "/api/tool-usage/daily" in paths

    def test_router_exported_from_package(self):
        from daemon.routers import tool_usage_router

        assert tool_usage_router is tu.router


class TestAuthPosture:
    def test_auth_off_returns_503(self):
        client = _client(auth_on=False)
        resp = client.get("/api/tool-usage/summary")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "AUTH_NOT_CONFIGURED"

    def test_scope_insufficient_returns_403(self):
        client = _client(scopes=("instances:read",))
        resp = client.get("/api/tool-usage/summary")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "SCOPE_INSUFFICIENT"

    def test_metrics_scope_also_grants_access(self):
        repo = _Repo(summary=[{"tool_name": "bash"}])
        client = _client(scopes=("metrics:read",), repo=repo)
        resp = client.get("/api/tool-usage/summary")
        assert resp.status_code == 200

    def test_require_auth_on_byte_matches_metrics_door(self):
        """D21: copy-paste body equality — no shared helper alternative."""
        from daemon.routers import metrics as metrics_router

        ours = inspect.getsource(tu._require_auth_on)
        theirs = inspect.getsource(metrics_router._require_auth_on)
        assert ours == theirs, (
            "auth replication invariant broken: _require_auth_on body must "
            "byte-match the metrics door"
        )


class TestSummary:
    def test_shape_and_defaults(self):
        repo = _Repo(summary=[{
            "tool_name": "bash", "calls_24h": 3, "calls_7d": 9,
            "calls_total": 19,
            "agents": [{"agent_id": "coder", "calls_7d": 9}],
        }])
        client = _client(repo=repo)
        resp = client.get("/api/tool-usage/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"generated_at", "rows"}
        row = body["rows"][0]
        assert set(row) == {
            "tool_name", "tool_category", "calls_24h", "calls_7d",
            "calls_total", "agents",
        }
        assert ("summary", None, 7) in repo.calls  # days default 7

    def test_days_clamped(self):
        repo = _Repo()
        client = _client(repo=repo)
        client.get("/api/tool-usage/summary?days=999")
        assert ("summary", None, 90) in repo.calls
        client.get("/api/tool-usage/summary?days=0")
        assert ("summary", None, 1) in repo.calls

    def test_bad_days_is_400(self):
        client = _client()
        resp = client.get("/api/tool-usage/summary?days=abc")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "bad_request"

    def test_project_id_none_filter_passes_through(self):
        repo = _Repo()
        client = _client(repo=repo)
        client.get("/api/tool-usage/summary?project_id=__none__")
        assert ("summary", "__none__", 7) in repo.calls

    def test_repo_failure_is_typed_503(self):
        class Boom:
            def query_summary(self, *a):
                raise RuntimeError("SECRET db password leak")

        client = _client(repo=Boom())
        resp = client.get("/api/tool-usage/summary")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "tool_usage_unavailable"
        assert "SECRET" not in resp.text

    def test_1mb_response_guard(self):
        huge = [{"tool_name": "t" * 40, "agents": []} for _ in range(30_000)]
        client = _client(repo=_Repo(summary=huge))
        resp = client.get("/api/tool-usage/summary")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "tool_usage_unavailable"


class TestDaily:
    def test_shape_and_zero_fill(self):
        repo = _Repo(series=[{"day": "2026-11-21", "calls": 3}])
        client = _client(repo=repo)
        resp = client.get("/api/tool-usage/daily?tool_name=bash")
        assert resp.status_code == 200
        assert resp.json() == {"series": [{"day": "2026-11-21", "calls": 3}]}
        assert ("daily", None, "bash", 30) in repo.calls

    def test_tool_name_required(self):
        client = _client()
        resp = client.get("/api/tool-usage/daily")
        assert resp.status_code == 400

    def test_unknown_tool_404(self):
        client = _client(repo=_Repo(has=False))
        resp = client.get("/api/tool-usage/daily?tool_name=ghost")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "unknown_tool"

    def test_days_clamped(self):
        repo = _Repo()
        client = _client(repo=repo)
        client.get("/api/tool-usage/daily?tool_name=bash&days=500")
        assert ("daily", None, "bash", 90) in repo.calls

"""Unit tests for the ``GET /api/metrics`` endpoint (plan Track-1 item 4).

The daemon's metric registry had a renderer
(:func:`daemon.checkpoint_metrics.render_metrics`) but NO HTTP surface —
its own docstring called the endpoint "future work". The checkpoint
read-path commission needs the read instrumentation observable from
outside the process, so the endpoint was added as the MINIMAL wiring:
one route, calling one existing function.

AUTH POSTURE (M-1) — the metric registry carries INTERNAL counters, so
the route is NOT world-readable:

* the family carries the EXISTING ``maintenance:read`` scope (the
  /api/wedge-captures precedent — deliberate scope REUSE, no new scope
  minted);
* in auth-ON the route is untagged ⇒ protected by default: an
  unauthenticated caller is 401'd by the middleware, an authenticated
  caller WITHOUT the scope is 403'd (``SCOPE_INSUFFICIENT``);
* in auth-OFF the route REFUSES — the inline
  ``_require_auth_on`` gate returns ``503 AUTH_NOT_CONFIGURED`` (the
  prompts-family D3 fail-closed contract). The deployed posture is
  auth-OFF + 0.0.0.0 bind + permissive CORS, so serving the internal
  counters there would publish them to anyone who can reach the port.

Coverage:
    * the route is registered under ``/api/metrics`` — ON A REAL APP
      built by ``daemon.api.create_app`` (F3: the self-built FastAPI
      app cannot prove the production mount exists);
    * the body is the registry's Prometheus text-exposition;
    * the NEW checkpoint-read counters appear in the body;
    * the media type is the Prometheus text-exposition one;
    * the auth-off 503 gate, the scope 403 and the auth-ON 401
      defense-in-depth leg.
"""

from __future__ import annotations

import warnings

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon import checkpoint_metrics as metrics


def _app(*, auth_on: bool = True, scopes: tuple[str, ...] = ("maintenance:read",)) -> FastAPI:
    """A minimal app carrying the metrics router under ``/api``.

    ``auth_on=True`` sets ``app.state.auth_config`` (its PRESENCE is the
    authoritative auth-ON signal — ``daemon/auth/decorators.py``) and
    attaches an ``AuthContext`` with the granted scopes, exactly as the
    auth middleware does in production. ``auth_on=False`` NEVER sets
    the attribute — the auth-OFF posture the 503 gate exists for.
    """
    from daemon.auth.context import AuthContext
    from daemon.routers.metrics import router as metrics_router

    api_router = APIRouter(prefix="/api")
    api_router.include_router(metrics_router)
    app = FastAPI()
    app.include_router(api_router)

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
    """The house router-test shape: the router mounted under ``/api``."""
    return TestClient(_app(**kwargs), raise_server_exceptions=False)


class TestMetricsRoute:
    def test_route_registered_under_api_metrics(self):
        from daemon.routers.metrics import router as metrics_router

        api_router = APIRouter(prefix="/api")
        api_router.include_router(metrics_router)
        app = FastAPI()
        app.include_router(api_router)
        route = next(
            (
                r
                for r in app.routes
                if getattr(r, "path", None) == "/api/metrics"
            ),
            None,
        )
        assert route is not None, "GET /api/metrics must be registered"
        assert "GET" in route.methods

    def test_route_is_registered_on_the_real_app(self):
        """F3 — the PRODUCTION mount, not a self-built app.

        The self-built FastAPI app above cannot prove that
        ``daemon/api.py`` mounts the family at all (deleting the
        production mount left this file green). This leg builds the
        REAL app and asserts the route is registered on it.
        """
        from daemon.api import create_app

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            app = create_app()
        route = next(
            (
                r
                for r in app.routes
                if getattr(r, "path", None) == "/api/metrics"
            ),
            None,
        )
        assert route is not None, (
            "GET /api/metrics must be registered on the REAL app "
            "(daemon.api.create_app) — the production mount in "
            "daemon/api.py is the seam this family depends on"
        )
        assert "GET" in route.methods

    def test_body_is_the_prometheus_text_exposition(self):
        response = _client().get("/api/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "version=0.0.4" in response.headers["content-type"]
        # The canonical exposition shape: HELP / TYPE / sample lines.
        assert "# HELP " in response.text
        assert "# TYPE " in response.text

    def test_new_read_counters_appear_in_the_body(self):
        metrics.reset_for_tests()
        try:
            metrics.checkpoint_read_ops_total.inc(op="aget")
            metrics.checkpoint_read_rows_total.inc(7, op="aget")
            metrics.checkpoint_read_bytes_total.inc(4096, op="aget")
            metrics.checkpoint_read_size_bytes.labels(op="aget").observe(4096)
            body = _client().get("/api/metrics").text
            assert 'checkpoint_read_ops_total{op="aget"} 1' in body
            assert 'checkpoint_read_rows_total{op="aget"} 7' in body
            assert 'checkpoint_read_bytes_total{op="aget"} 4096' in body
            assert "checkpoint_read_size_bytes_bucket" in body
            assert "checkpoint_read_measurement_errors_total" in body
        finally:
            metrics.reset_for_tests()


class TestAuthPosture:
    """The route must not publish the internal counters unauthenticated."""

    def test_auth_off_returns_503_auth_not_configured(self):
        """The D3 fail-closed gate — the deployed posture (auth OFF)."""
        response = _client(auth_on=False).get("/api/metrics")
        assert response.status_code == 503, (
            f"expected 503 AUTH_NOT_CONFIGURED, got {response.status_code}: "
            f"{response.text}"
        )
        body = response.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "AUTH_NOT_CONFIGURED", (
            f"expected error=AUTH_NOT_CONFIGURED, got {detail}"
        )

    def test_auth_off_refuses_before_rendering_the_registry(self):
        """The gate fires BEFORE the metric registry is read — a refusal
        never leaks a metric value."""
        metrics.reset_for_tests()
        try:
            metrics.checkpoint_read_ops_total.inc(op="aget")
            response = _client(auth_on=False).get("/api/metrics")
            assert response.status_code == 503
            assert "checkpoint_read_ops_total" not in response.text
        finally:
            metrics.reset_for_tests()

    def test_auth_on_without_the_scope_returns_403(self):
        """The scope check is LIVE in auth-ON: no scope ⇒ 403."""
        response = _client(auth_on=True, scopes=()).get("/api/metrics")
        assert response.status_code == 403, (
            f"expected 403 SCOPE_INSUFFICIENT, got {response.status_code}: "
            f"{response.text}"
        )
        body = response.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "SCOPE_INSUFFICIENT", (
            f"expected error=SCOPE_INSUFFICIENT, got {detail}"
        )

    def test_auth_on_401_for_an_unauthenticated_caller(self):
        """Defense in depth on the REAL app: with auth ON, an
        unauthenticated caller is 401'd by the middleware BEFORE the
        route runs (the machine contract — an explicit non-HTML Accept
        header; a browser-shaped request gets the 302 login redirect,
        which is the middleware's contract, not the route's)."""
        from daemon.api import create_app
        from tests.helpers.auth_fixture import on_auth_config

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            app = create_app(auth_config=on_auth_config())
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/metrics", headers={"accept": "application/json"}
        )
        assert response.status_code == 401, (
            f"expected 401 for an unauthenticated caller, got "
            f"{response.status_code}: {response.text}"
        )

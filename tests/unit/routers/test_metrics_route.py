"""Unit tests for the ``GET /api/metrics`` endpoint (plan Track-1 item 4).

The daemon's metric registry had a renderer
(:func:`daemon.checkpoint_metrics.render_metrics`) but NO HTTP surface —
its own docstring called the endpoint "future work". The checkpoint
read-path commission needs the read instrumentation observable from
outside the process, so the endpoint was added as the MINIMAL wiring:
one route, calling one existing function.

The endpoint is deliberately UNSCOPED (no ``@require_scope``, no class
tag): it follows the daemon's fail-safe default — OAuth2-protected when
auth is ON (an untagged route is protected by default), open in the
auth-OFF local mode (the same posture as the other observe-only
diagnostic families, e.g. ``/api/resources``).

Coverage:
    * the route is registered under ``/api/metrics`` (GET);
    * the body is the registry's Prometheus text-exposition;
    * the NEW checkpoint-read counters appear in the body;
    * the media type is the Prometheus text-exposition one.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon import checkpoint_metrics as metrics


def _client() -> TestClient:
    """The house router-test shape: the router mounted under ``/api``."""
    from daemon.routers.metrics import router as metrics_router

    api_router = APIRouter(prefix="/api")
    api_router.include_router(metrics_router)
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


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

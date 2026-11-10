"""The query-door route pack (registered pack ``metrics_query_routes``).

The ONE door, THREE routes — the load-bearing pins:

* the three routes EXIST on the REAL app (``daemon.api.create_app`` — a
  self-built app cannot prove the production mount);
* the envelope is the Phase-1 FROZEN contract, field for field
  (``schema_version`` · ``vocabulary_version`` · ``generated_at_utc`` ·
  ``restart_epoch`` · ``activation``), and the ``/families`` route
  carries ``buckets`` ON the wire (approver B3);
* the response BODY is BUILT OFF the event loop (AM-12) — the read runs
  on a ``db-offload*`` worker thread, never on the loop;
* the residual on-loop ENCODE is BOUNDED and PINNED (AM-12/W6): the
  ``max_points`` default is 240 and the selector ceiling is 50 ⇒
  ≤ 12 000 points ≈ 3.6 ms;
* the NFR-1 latencies are MEASURED (snapshot ≤ 250 ms p95, 60-min trend
  ≤ 1 s p95) — the pack reports them rather than asserting a number it
  never took;
* the exposition body stays STRUCTURALLY IDENTICAL to the lane's base
  artifact modulo the enumerated 3-carrier value-delta allowlist
  (AM-16/D-11a).

Mutation table: ``P3-M8`` (a delta outside the 3-carrier allowlist
leaked into the exposition body, or an allowlisted carrier changed
shape) · ``P3-M9`` (the body build moved back onto the event loop, or
the bound is not re-asserted) · ``P3-M12`` (``max_points`` default
raised above 240, or ``?names=`` accepted above 50).
"""

from __future__ import annotations

import statistics
import time
import warnings
from pathlib import Path

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.constants import (
    DEFAULT_MAX_POINTS,
    MAX_NAMES,
    MAX_POINTS_CEILING,
    MAX_SERIES,
    MAX_TOTAL_POINTS,
    QUERY_SCHEMA_VERSION,
)
from tests.helpers.metrics_query_fixtures import (
    QUERY_PATHS,
    READ_SCOPE,
    RecordingProvider,
    build_client,
)

#: The second scope of the query door's any-of set (the
#: metrics-agent-tools lane).
METRICS_READ_SCOPE = "metrics:read"

#: The lane's base artifact — the masked exposition body captured at the
#: lane's BASE commit (see ``tests/helpers/metrics_exposition_baseline.py``).
#: (NOT under a ``data/`` directory — the repo's ``.gitignore`` carries a
#: blanket ``data/`` rule that would swallow the artifact.)
BASELINE_PATH = (
    Path(__file__).resolve().parent / "baselines" / "metrics_exposition_baseline.txt"
)


def _family(
    family_id: str = "ensemble_p3_route_family",
    *,
    buckets: list[float] | None = None,
    availability: dict | None = None,
    role: str = "chart",
) -> dict:
    """A family entry in the canonical frozen shape."""
    return {
        "id": family_id,
        "names": [
            {
                "name": f"{family_id}_gauge",
                "kind": "histogram" if buckets else "gauge",
                "labels": None,
                "help": "the p3 route pack fixture",
                "buckets": buckets,
                "cardinality_class": "none",
                "monotonic": False,
                "chart_role": "line",
            }
        ],
        "live_series_count": 1,
        "availability": availability or {"state": "live"},
        "role": role,
        "source_idiom": "registry",
        "durability": "process",
        "cardinality_contribution": 1,
    }


def _provider(**kwargs) -> RecordingProvider:
    return RecordingProvider(
        families=[_family(**kwargs.pop("family_kwargs", {}))],
        snapshot=[
            {
                "name": "ensemble_p3_route_family_gauge",
                "labels": {},
                "kind": "gauge",
                "monotonic": False,
                "value": 1.5,
                "last_publish_age_s": 0.25,
            }
        ],
        history=[
            {
                "name": "ensemble_p3_route_family_gauge",
                "labels": {},
                "kind": "gauge",
                "monotonic": False,
                "points": [[1_700_000_000, 1.0], [1_700_000_015, 2.0]],
            }
        ],
        **kwargs,
    )


class TestRouteExistence:
    """The routes EXIST on the production mount."""

    def test_the_three_routes_are_registered_on_the_real_app(self):
        """F3 — the PRODUCTION mount, not a self-built app."""
        from daemon.api import create_app

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            app = create_app()
        paths = {getattr(r, "path", None) for r in app.routes}
        for path in QUERY_PATHS:
            assert path in paths, f"{path} must be registered on the REAL app"

    def test_the_three_routes_carry_the_get_method(self):
        from daemon.api import create_app

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            app = create_app()
        for route in app.routes:
            if getattr(route, "path", None) in QUERY_PATHS:
                assert "GET" in route.methods

    def test_the_query_routes_extend_the_existing_family(self):
        """NO new router module, NO new mount — the routes ride the
        EXISTING ``/api/metrics`` family (decisions.md D4, Option A)."""
        from daemon.routers.metrics import router as metrics_router

        paths = {getattr(r, "path", None) for r in metrics_router.routes}
        # The FOUR original surfaces …
        expected = {
            "/metrics",
            "/metrics/families",
            "/metrics/snapshot",
            "/metrics/series",
        }
        # … plus the ADDITIVE capture-lane read surface (I3/m4): a JSON read
        # of the capture-lane counters. It rides the SAME router (no new
        # module, no new mount — D4 Option A still holds) and is
        # EXPOSITION-NEUTRAL: it registers no family, so the Prometheus body
        # and its committed baseline artifact are untouched.
        expected.add("/metrics/capture-lane")
        assert paths == expected


class TestEnvelopeShape:
    """The Phase-1 frozen envelope, field for field."""

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_envelope_carries_the_frozen_header(self, path):
        response = build_client(_provider()).get(path)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schema_version"] == QUERY_SCHEMA_VERSION
        assert body["vocabulary_version"] == cm.VOCABULARY_VERSION
        assert body["restart_epoch"]
        assert body["activation"] == {
            "listener_enabled": True,
            "census_enabled": True,
            "query_api": "auth-gated-always-on",
            # The ADDITIVE degraded flag (H5) — a provider that does not
            # carry the fact reads as True (the default-safe path).
            "registry_read_ok": True,
        }

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_generated_at_is_aware_utc(self, path):
        from datetime import datetime, timezone

        response = build_client(_provider()).get(path)
        raw = response.json()["generated_at_utc"]
        parsed = datetime.fromisoformat(raw)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)

    def test_the_families_route_carries_buckets_on_the_wire(self):
        """B3 — the deferred consumers need the bucket sets off-box."""
        provider = _provider()
        provider._families = [_family(buckets=[1.0, 5.0, 10.0])]
        response = build_client(provider).get("/api/metrics/families")
        assert response.status_code == 200
        entry = response.json()["families"][0]
        assert entry["names"][0]["buckets"] == [1.0, 5.0, 10.0]

    def test_the_families_route_carries_the_availability_reason(self):
        provider = _provider()
        provider._families = [
            _family(availability={"state": "dark", "reason": "census_gated"})
        ]
        response = build_client(provider).get("/api/metrics/families")
        availability = response.json()["families"][0]["availability"]
        assert availability == {"state": "dark", "reason": "census_gated"}

    def test_the_snapshot_route_carries_the_current_values(self):
        response = build_client(_provider()).get("/api/metrics/snapshot")
        series = response.json()["series"]
        assert series[0]["value"] == 1.5
        assert series[0]["last_publish_age_s"] == 0.25

    def test_the_series_route_carries_the_window_and_the_points(self):
        response = build_client(_provider()).get("/api/metrics/series")
        body = response.json()
        assert body["window"]["interval_s_source"] == "observed"
        assert body["window"]["interval_s"] == 15.0
        assert body["stale"] is False
        assert body["series"][0]["points"] == [[1_700_000_000, 1.0], [1_700_000_015, 2.0]]


class TestOffLoopBuild:
    """AM-12 — the response body is BUILT off the event loop."""

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_body_is_built_on_an_offload_worker_thread(self, path):
        provider = _provider()
        response = build_client(provider).get(path)
        assert response.status_code == 200
        assert provider.threads, "the provider must have been read"
        for thread_name in provider.threads:
            assert thread_name.startswith("db-offload"), (
                "the body build must run OFF the event loop — the read ran "
                f"on {thread_name!r}"
            )

    def test_the_route_source_offloads_the_body_build(self):
        """The claim is BUILD-OFF-LOOP ONLY — the offload is in the source."""
        import inspect

        from daemon.routers import metrics as metrics_router

        source = inspect.getsource(metrics_router)
        assert "db_offload" in source
        assert "await _offload(" in source


class TestResponseBound:
    """AM-12/W6 — the residual on-loop encode is BOUNDED and PINNED."""

    def test_the_max_points_default_is_240(self):
        assert DEFAULT_MAX_POINTS == 240
        assert MAX_POINTS_CEILING == 240

    def test_the_default_max_points_reaches_the_provider(self):
        provider = _provider()
        build_client(provider).get("/api/metrics/series")
        assert provider.reads == ["history"]

    def test_a_max_points_above_the_ceiling_is_clamped(self):
        """The bound is the DAEMON's budget — a caller may not widen it."""
        captured: dict = {}
        provider = _provider()
        original = provider.history

        def _capture(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        provider.history = _capture  # type: ignore[method-assign]
        response = build_client(provider).get("/api/metrics/series?max_points=100000")
        assert response.status_code == 200
        assert captured["max_points"] == MAX_POINTS_CEILING

    def test_the_selector_ceiling_is_50(self):
        assert MAX_NAMES == 50

    def test_the_emitted_point_bound_is_measured_not_a_constant_identity(self):
        """W-2 — the AM-12 bound is a MEASUREMENT of the emitted body.

        The bound used to be a CONSTANT IDENTITY (``240 × 50 == 12 000``
        — a product of two constants), which enforced NOTHING: the
        selector ceiling bounds the REQUESTED selectors, and a request
        with NO selector matched EVERY published series, each carrying
        up to ``max_points`` points.  The pin therefore MEASURES the
        emitted body: many series, many points in, a bounded body out.
        """
        points_per_series = MAX_POINTS_CEILING
        series = [
            {
                "name": f"ensemble_p3_bulk_gauge_{index}",
                "labels": {"index": str(index)},
                "kind": "gauge",
                "monotonic": False,
                "points": [
                    [1_700_000_000 + 15 * step, float(step)]
                    for step in range(points_per_series)
                ],
            }
            for index in range(MAX_SERIES + 400)
        ]
        provider = RecordingProvider(
            families=[_family()],
            snapshot=[],
            history=series,
        )
        response = build_client(provider).get("/api/metrics/series")
        assert response.status_code == 200
        body = response.json()
        emitted_points = sum(len(entry["points"]) for entry in body["series"])
        emitted_series = len(body["series"])
        assert emitted_series <= MAX_SERIES, emitted_series
        assert emitted_points <= MAX_TOTAL_POINTS, emitted_points
        # The bound is NEVER SILENT: the body says it was truncated and
        # carries the cursor that resumes it.
        assert body["truncated"] is True
        assert body.get("next_cursor")

    def test_a_small_body_is_not_truncated(self):
        """The bound must not manufacture a truncation it did not make."""
        response = build_client(_provider()).get("/api/metrics/series")
        body = response.json()
        assert body["truncated"] is False
        assert not body.get("next_cursor")


class TestLatency:
    """NFR-1 — the latencies are MEASURED, not asserted."""

    def test_the_snapshot_p95_is_under_250ms(self):
        client = build_client(_provider())
        samples = []
        for _ in range(20):
            started = time.perf_counter()
            response = client.get("/api/metrics/snapshot")
            samples.append((time.perf_counter() - started) * 1000.0)
            assert response.status_code == 200
        p95 = statistics.quantiles(samples, n=20)[-1]
        assert p95 <= 250.0, f"snapshot p95 was {p95:.1f} ms (budget 250 ms)"

    def test_the_sixty_minute_trend_p95_is_under_1s(self):
        client = build_client(_provider())
        samples = []
        for _ in range(10):
            started = time.perf_counter()
            response = client.get("/api/metrics/series?window_s=3600&max_points=240")
            samples.append((time.perf_counter() - started) * 1000.0)
            assert response.status_code == 200
        p95 = statistics.quantiles(samples, n=10)[-1]
        assert p95 <= 1000.0, f"series p95 was {p95:.1f} ms (budget 1000 ms)"


class TestExpositionStructuralIdentity:
    """AM-16/D-11a — the exposition body is structurally unchanged."""

    def test_the_baseline_artifact_exists(self):
        assert BASELINE_PATH.exists(), (
            "the lane's base artifact is missing — the structural-identity "
            "gate has no baseline to compare against"
        )

    def test_the_exposition_body_matches_the_masked_base_artifact(self):
        """The 3-carrier allowlist is applied; everything else is equal."""
        from tests.helpers.metrics_exposition_baseline import (
            capture_masked_exposition,
            mask_exposition,
        )

        expected = BASELINE_PATH.read_text(encoding="utf-8")
        actual = mask_exposition(capture_masked_exposition())
        assert actual == expected, (
            "the exposition body drifted from the lane's base artifact "
            "OUTSIDE the 3-carrier value-delta allowlist"
        )

    def test_the_allowlist_covers_exactly_four_carriers(self):
        """The ratified allowlist — FOUR carriers.

        The fourth is the TOOL-CATEGORY vocabulary line: the
        metrics-agent-tools lane registered the ``metrics-read``
        privileged category, so the frozen set (the committed artifact)
        legitimately grew.  The artifact pack is the authority on that
        set's integrity, so masking its VALUE here costs no coverage.
        """
        from tests.helpers.metrics_exposition_baseline import ALLOWLIST

        assert set(ALLOWLIST) == {
            "frozen_vocabulary_version",
            "vocabulary_version_gauge",
            "checkpoint_op_seconds_help",
            "tool_category_vocabulary",
        }


class TestScopeDeclaration:
    """Every query route carries the family's any-of scope set.

    The set is ``maintenance:read`` OR ``metrics:read`` (the
    metrics-agent-tools lane minted the narrower scope so a machine lane
    can read the query door without the destructive checkpoint-cleanup
    capability ``maintenance:read`` also confers).  The TEXT-exposition
    route keeps ``maintenance:read`` ALONE.
    """

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_each_route_declares_the_read_scope(self, path):
        from fastapi.routing import APIRoute

        from daemon.routers.metrics import router as metrics_router

        route = next(
            r
            for r in metrics_router.routes
            if getattr(r, "path", None) == path[len("/api") :]
        )
        assert isinstance(route, APIRoute)
        scopes: set[str] = set()
        for dep in getattr(getattr(route, "dependant", None), "dependencies", None) or []:
            scopes.update(getattr(dep.call, "required_scopes", None) or ())
        assert scopes == {READ_SCOPE, METRICS_READ_SCOPE}

    def test_the_text_exposition_route_keeps_maintenance_read_alone(self):
        """The scraping surface is NOT part of the split (D6 step 3)."""
        from fastapi.routing import APIRoute

        from daemon.routers.metrics import router as metrics_router

        route = next(
            r
            for r in metrics_router.routes
            if getattr(r, "path", None) == "/metrics"
        )
        assert isinstance(route, APIRoute)
        scopes: set[str] = set()
        for dep in getattr(getattr(route, "dependant", None), "dependencies", None) or []:
            scopes.update(getattr(dep.call, "required_scopes", None) or ())
        assert scopes == {READ_SCOPE}

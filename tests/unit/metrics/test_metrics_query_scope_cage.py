"""The P3 scope-cage pack — the THREE-WAY cage form.

The cages are three INDEPENDENT pins and the plan binds them as ONE
three-way form (a two-way form was the defect class the fold ledger
recorded):

* the **declared-route** census (the scope-decorated routes) reads
  **114** — the three query routes joined the family, the
  nest-anomaly-report lane's ``/api/nest-reports`` DOMAIN route added
  TWO scope-decorated routes (the family + the cluster detail), and the
  capture-lane read site added ONE (``GET /api/metrics/capture-lane``,
  workstream A / I3 — a scope-decorated route on the SAME metrics
  router, so the mount census is unchanged);
* the **mount** census stays **38** — the domain route is ONE new
  mount (``/api/nest-reports``);
* the **class-manifest** family count stays **34** — the new family is
  pinned as protected-default (the metrics-router precedent).

Plus the family's scope: the query routes carry the family's EXISTING
``maintenance:read`` — no new scope is minted.

This pack is D1 (HEAD-only): it asserts the three-way form at once, so
a cage that drifted in ANY direction reds.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from daemon.api import create_app
from daemon.auth.decorators import scope_required
from daemon.routers.metrics import router as metrics_router

REPO_ROOT = Path(__file__).resolve().parents[3]
API_PY = REPO_ROOT / "daemon" / "api.py"

#: The three-way cage form (declared-route · mount · class manifest).
DECLARED_ROUTE_CENSUS = 114
MOUNT_CENSUS = 38
CLASS_MANIFEST_FAMILIES = 34

#: The scope the family carries — the ANY-OF set the three query routes
#: declare (the metrics-agent-tools lane minted ``metrics:read`` so a
#: machine lane can read the query door without the destructive
#: checkpoint-cleanup capability ``maintenance:read`` also confers). The
#: text-exposition route keeps ``maintenance:read`` ALONE — and the cage
#: is a SUBSET check, so that lone scope stays inside the OR-set.
FAMILY_SCOPE = "maintenance:read|metrics:read"

#: The any-of set the three query routes declare, as a set.
FAMILY_SCOPE_SET: frozenset[str] = frozenset({"maintenance:read", "metrics:read"})


def _route_required_scopes(route: APIRoute) -> frozenset[str] | None:
    """The route's declared scope set (wrapper form OR dependency form)."""
    scopes = getattr(getattr(route, "endpoint", None), "required_scopes", None)
    if scopes:
        return frozenset(scopes)
    for dep in getattr(getattr(route, "dependant", None), "dependencies", None) or []:
        dep_scopes = getattr(dep.call, "required_scopes", None)
        if dep_scopes:
            return frozenset(dep_scopes)
    return None


@pytest.fixture(scope="module")
def app_routes():
    """The AUTH-ON app — the full universe the declared-route census
    counts (the auth-OFF app mounts fewer families)."""
    import warnings

    from tests.helpers.auth_fixture import on_auth_config

    warnings.filterwarnings("ignore")
    app = create_app(auth_config=on_auth_config())
    return app.routes


class TestThreeWayCage:
    """The three-way cage form — all three pins at once."""

    def test_the_declared_route_census_reads_114(self, app_routes):
        declared = [
            route
            for route in app_routes
            if isinstance(route, APIRoute)
            and getattr(route, "path", "").startswith("/api/")
            and _route_required_scopes(route) is not None
        ]
        assert len(declared) == DECLARED_ROUTE_CENSUS, (
            f"declared-route census drifted: {len(declared)} "
            f"(pinned {DECLARED_ROUTE_CENSUS})"
        )

    def test_the_mount_census_stays_38(self):
        source = API_PY.read_text(encoding="utf-8")
        matches = re.findall(r"api_router\.include_router\(", source)
        assert len(matches) == MOUNT_CENSUS, (
            f"api_router.include_router( census drifted: {len(matches)} "
            f"(pinned {MOUNT_CENSUS}) — the query routes EXTEND the "
            "existing /api/metrics mount; they add none"
        )

    def test_the_class_manifest_family_count_stays_34(self):
        from tests.integration.test_router_class_manifest import (
            PINNED_MANIFEST_AUTH_ON,
        )

        api_families = [
            family
            for family in PINNED_MANIFEST_AUTH_ON
            if family.startswith("/api/")
        ]
        assert len(api_families) == CLASS_MANIFEST_FAMILIES, sorted(api_families)

    def test_the_three_way_form_holds_together(self):
        """ONE assertion over all three pins — the plan's live form."""
        from tests.helpers.auth_fixture import on_auth_config
        from tests.integration.test_router_class_manifest import (
            PINNED_MANIFEST_AUTH_ON,
        )

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            routes = create_app(auth_config=on_auth_config()).routes
        declared = [
            route
            for route in routes
            if isinstance(route, APIRoute)
            and getattr(route, "path", "").startswith("/api/")
            and _route_required_scopes(route) is not None
        ]
        mounts = re.findall(
            r"api_router\.include_router\(", API_PY.read_text(encoding="utf-8")
        )
        families = [
            family
            for family in PINNED_MANIFEST_AUTH_ON
            if family.startswith("/api/")
        ]
        assert (
            len(declared),
            len(mounts),
            len(families),
        ) == (DECLARED_ROUTE_CENSUS, MOUNT_CENSUS, CLASS_MANIFEST_FAMILIES)


class TestFamilyScope:
    """The family carries its EXISTING scope."""

    def test_the_family_scope_is_the_any_of_set(self):
        from tests.unit.test_router_scope_manifest import PINNED_SCOPE_MANIFEST

        assert PINNED_SCOPE_MANIFEST["/api/metrics"] == FAMILY_SCOPE

    def test_every_query_route_declares_the_family_scope(self):
        for route in metrics_router.routes:
            path = getattr(route, "path", "")
            if path in ("/metrics/families", "/metrics/snapshot", "/metrics/series"):
                scopes = _route_required_scopes(route)
                assert scopes == FAMILY_SCOPE_SET, (
                    f"{path} declares {scopes} — every query route carries "
                    f"the family's any-of set {sorted(FAMILY_SCOPE_SET)}"
                )

    def test_the_scope_dependency_form_is_used(self):
        """The family uses the DEPENDENCY form (no handler signature
        change) — the same shape the text-exposition route uses."""
        source = (
            (REPO_ROOT / "daemon" / "routers" / "metrics.py")
            .read_text(encoding="utf-8")
        )
        assert "dependencies=[scope_required(_SCOPE_READ)]" in source
        assert (
            "dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)]"
            in source
        ), "the three query routes declare the any-of set"

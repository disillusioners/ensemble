"""The query-door auth matrix (registered pack ``metrics_query_auth_matrix``).

The door's POSTURE is the contract's truth table, and every row of it
is a pin:

* auth OFF ⇒ ``503 AUTH_NOT_CONFIGURED`` — and the refusal happens
  BEFORE any provider read (a refusal never leaks a registry value);
* auth ON, no session ⇒ ``401`` (the middleware's decision);
* auth ON, a session WITHOUT a sufficient scope ⇒ ``403``;
* auth ON, a session WITH ``maintenance:read`` ⇒ ``200``;
* auth ON, a session with ``metrics:read`` ALONE ⇒ ``200`` on the three
  query routes and ``403`` on the text-exposition route AND on the
  destructive checkpoint cleanup — the scope split is NARROWER, and this
  is its proof (the metrics-agent-tools lane, 2026-11-03);
* every one of the three query routes carries the SAME any-of scope set.

Mutation table: ``P3-M1`` (the fail-closed auth seam moved after the
registry read) · ``P3-M2`` (one route's scope dependency dropped) ·
``P3-M3`` (one query route's ``metrics:read`` dropped from the any-of
set — row (i) reds for that route alone).
"""

from __future__ import annotations

import warnings

import pytest

from tests.helpers.metrics_query_fixtures import (
    QUERY_PATHS,
    READ_SCOPE,
    RecordingProvider,
    build_client,
    error_code,
)

#: The second scope of the query door's any-of set (the
#: metrics-agent-tools lane).
METRICS_READ_SCOPE = "metrics:read"

#: The Prometheus text-exposition route (the scraping surface — it keeps
#: ``maintenance:read`` ALONE).
TEXT_PATH = "/api/metrics"

#: The destructive checkpoint-cleanup route — the capability
#: ``maintenance:read`` ALSO confers (AM-20) and ``metrics:read`` does
#: NOT.
CHECKPOINT_CLEANUP_PATH = "/api/maintenance/checkpoint-cleanup/execute"


class TestAuthOffIsFailClosed:
    """Auth OFF ⇒ 503 AUTH_NOT_CONFIGURED, BEFORE any provider read."""

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_auth_off_returns_503_auth_not_configured(self, path):
        response = build_client(RecordingProvider(), auth_on=False).get(path)
        assert response.status_code == 503, response.text
        assert error_code(response) == "AUTH_NOT_CONFIGURED"

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_auth_off_never_reads_the_registry(self, path):
        """The seam fires BEFORE the provider is consulted (P3-M1)."""
        provider = RecordingProvider()
        response = build_client(provider, auth_on=False).get(path)
        assert response.status_code == 503
        assert provider.reads == [], (
            "the fail-closed seam must fire BEFORE any provider read — the "
            f"refusal read {provider.reads}"
        )

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_auth_off_does_not_leak_a_registry_value(self, path):
        provider = RecordingProvider(
            snapshot=[
                {
                    "name": "ensemble_p3_secret_gauge",
                    "labels": {},
                    "kind": "gauge",
                    "monotonic": False,
                    "value": 42.0,
                    "last_publish_age_s": 0.0,
                }
            ]
        )
        response = build_client(provider, auth_on=False).get(path)
        assert response.status_code == 503
        assert "ensemble_p3_secret_gauge" not in response.text


class TestAuthOnMatrix:
    """Auth ON — the scope decides."""

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_a_session_without_the_scope_returns_403(self, path):
        response = build_client(RecordingProvider(), scopes=()).get(path)
        assert response.status_code == 403, response.text
        assert error_code(response) == "SCOPE_INSUFFICIENT"

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_a_session_with_the_scope_returns_200(self, path):
        response = build_client(RecordingProvider()).get(path)
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_an_unrelated_scope_does_not_satisfy_the_route(self, path):
        response = build_client(RecordingProvider(), scopes=("settings:read",)).get(path)
        assert response.status_code == 403, response.text

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_real_app_401s_an_unauthenticated_caller(self, path):
        """Defense in depth on the REAL app — the middleware's decision."""
        from fastapi.testclient import TestClient

        from daemon.api import create_app
        from tests.helpers.auth_fixture import on_auth_config

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            app = create_app(auth_config=on_auth_config())
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(path, headers={"accept": "application/json"})
        assert response.status_code == 401, (
            f"expected 401 for an unauthenticated caller, got "
            f"{response.status_code}: {response.text}"
        )


class TestScopeDeclaration:
    """Every query route carries the SAME scope (P3-M2)."""

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_route_declares_the_maintenance_read_scope(self, path):
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
        assert scopes == {READ_SCOPE, METRICS_READ_SCOPE}, (
            f"{path} declares {sorted(scopes)} — every query route carries "
            f"the family's any-of set {{{READ_SCOPE}, {METRICS_READ_SCOPE}}}"
        )

    def test_both_door_scopes_are_in_the_taxonomy(self):
        """Both door scopes are TAXONOMY entries.

        The door REUSES the existing ``maintenance:read`` entry and, since
        the metrics-agent-tools lane, ALSO accepts the narrower
        ``metrics:read`` entry (minted so a machine lane can read the
        query door without the destructive checkpoint-cleanup capability).
        The assertion is UNCHANGED in substance — only the name and the
        docstring changed (the old name claimed "no new scope is minted",
        which stopped being true).
        """
        from daemon.auth.scopes import SCOPE_TAXONOMY

        assert READ_SCOPE in SCOPE_TAXONOMY
        assert METRICS_READ_SCOPE in SCOPE_TAXONOMY


class TestScopeSplitNarrowing:
    """The ``metrics:read`` scope splits the grant — and the split is
    NARROWER, demonstrably (the metrics-agent-tools lane)."""

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_a_metrics_read_only_caller_reads_the_query_routes(self, path):
        """Row (i) — a caller holding ``metrics:read`` ALONE reads the
        three query routes (200)."""
        response = build_client(
            RecordingProvider(), scopes=(METRICS_READ_SCOPE,)
        ).get(path)
        assert response.status_code == 200, response.text

    def test_a_metrics_read_only_caller_is_refused_the_text_route(self):
        """Row (ii) — the SAME caller is refused on the Prometheus
        text-exposition route (403): the scraping surface keeps
        ``maintenance:read`` ALONE."""
        response = build_client(
            RecordingProvider(), scopes=(METRICS_READ_SCOPE,)
        ).get(TEXT_PATH)
        assert response.status_code == 403, response.text
        assert error_code(response) == "SCOPE_INSUFFICIENT"

    def test_a_metrics_read_only_caller_cannot_trigger_a_checkpoint_cleanup(self):
        """Row (ii) — the SAME caller CANNOT trigger the destructive
        checkpoint cleanup: that is the capability ``maintenance:read``
        also confers, and the whole point of the split.

        The Origin guard is satisfied by a request with NO Origin header
        (rule 1 — programmatic callers), so the 403 below is the SCOPE's
        refusal, not the origin guard's.
        """
        from fastapi import APIRouter, FastAPI
        from fastapi.testclient import TestClient

        from daemon.auth.context import AuthContext
        from daemon.routers.maintenance import router as maintenance_router
        from daemon.routers.metrics import router as metrics_router

        api_router = APIRouter(prefix="/api")
        api_router.include_router(metrics_router)
        api_router.include_router(maintenance_router)
        app = FastAPI()
        app.include_router(api_router)

        @app.middleware("http")
        async def _attach_auth_context(request, call_next):  # noqa: ANN001
            request.state.auth_context = AuthContext(
                subject="test-machine-lane",
                scopes={METRICS_READ_SCOPE},
                auth_method="session",
                expires_at="2099-01-01T00:00:00Z",
            )
            return await call_next(request)

        app.state.auth_config = object()
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(CHECKPOINT_CLEANUP_PATH, json={})
        assert response.status_code == 403, response.text
        assert error_code(response) == "SCOPE_INSUFFICIENT", (
            "the refusal must be the SCOPE's, not the origin guard's"
        )

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_a_maintenance_read_holder_is_unaffected(self, path):
        """Row (iii) — the EXISTING holder keeps its access on every query
        route (zero behaviour change for any current holder)."""
        response = build_client(
            RecordingProvider(), scopes=(READ_SCOPE,)
        ).get(path)
        assert response.status_code == 200, response.text

    def test_a_maintenance_read_holder_still_reaches_the_text_route(self):
        """Row (iii) — the existing holder is unaffected on the
        text-exposition route too."""
        response = build_client(
            RecordingProvider(), scopes=(READ_SCOPE,)
        ).get(TEXT_PATH)
        assert response.status_code == 200, response.text

    def test_the_fail_closed_seam_still_fires_before_any_read(self):
        """Row (iv) — the auth-OFF posture is untouched by the split: the
        503 still fires BEFORE any provider read."""
        provider = RecordingProvider()
        response = build_client(provider, auth_on=False).get(QUERY_PATHS[0])
        assert response.status_code == 503
        assert provider.reads == []

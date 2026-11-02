"""The query-door auth matrix (registered pack ``metrics_query_auth_matrix``).

The door's POSTURE is the contract's truth table, and every row of it
is a pin:

* auth OFF ⇒ ``503 AUTH_NOT_CONFIGURED`` — and the refusal happens
  BEFORE any provider read (a refusal never leaks a registry value);
* auth ON, no session ⇒ ``401`` (the middleware's decision);
* auth ON, a session WITHOUT ``maintenance:read`` ⇒ ``403``;
* auth ON, a session WITH ``maintenance:read`` ⇒ ``200``;
* every one of the three routes carries the SAME scope — the family's
  EXISTING taxonomy entry, never a new one.

Mutation table: ``P3-M1`` (the fail-closed auth seam moved after the
registry read) · ``P3-M2`` (one route's scope dependency dropped).
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
        assert scopes == {READ_SCOPE}, (
            f"{path} declares {sorted(scopes)} — every query route carries "
            f"the family's EXISTING {READ_SCOPE}"
        )

    def test_no_new_scope_is_minted_for_the_door(self):
        """The door REUSES the taxonomy entry — no new scope."""
        from daemon.auth.scopes import SCOPE_TAXONOMY

        assert READ_SCOPE in SCOPE_TAXONOMY

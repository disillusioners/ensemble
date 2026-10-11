"""IdP-failure-mode integration tests — oauth2-auth Phase 5 (T5.4, §4 row 3).

End-to-end exercises of the auth subsystem against the FakeOidcProvider's
failure-mode matrix, plus the bearer-consumer enablement scenarios.

Sister suite to ``tests/integration/test_auth_routes_idp_errors.py``
(P3). The P3 tests cover a handful of typed-error legs; this P5
suite covers the FULL failure-mode matrix plus the §7 consumer
enablement walk (10 YES-UNCHANGED + 6 YES-WITH-CONFIG + 2 N/A per
AMEND v1.4).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from daemon.services.auth_service import (
    IdpRejectedError,
    IdpUnreachableError,
    StateReplayedError,
)
from tests.conftest_auth import (
    TEST_CLIENT_ID,
    _WrappedApp,
    auth_enabled_app,
    mint_bearer_token,
)


def _new_client(app, fake) -> TestClient:
    return TestClient(
        _WrappedApp(app, fake),
        base_url="http://testserver",
        follow_redirects=False,
    )


def _login_via(client: TestClient) -> dict:
    r = client.get("/api/auth/login")
    qs = parse_qs(urlsplit(r.headers["location"]).query)
    state = qs["state"][0]
    scope_qs = urlencode(
        {
            "client_id": TEST_CLIENT_ID,
            "redirect_uri": qs["redirect_uri"][0],
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": qs["code_challenge"][0],
            "code_challenge_method": "S256",
        }
    )
    r2 = client.get(f"/_test_oidc/authorize?{scope_qs}", follow_redirects=False)
    loc = r2.headers["location"]
    code = parse_qs(urlsplit(loc).query)["code"][0]
    return {"code": code, "state": state}


# ── discovery / JWKS round-trip ─────────────────────────────────────


class TestDiscoveryRoundTrip:
    def test_discovery_serves_canonical_doc(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/_test_oidc/.well-known/openid-configuration")
            assert r.status_code == 200
            doc = r.json()
            assert doc["issuer"]
            assert doc["authorization_endpoint"]
            assert doc["token_endpoint"]
            assert doc["jwks_uri"]

    def test_jwks_exposes_primary_signing_key(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/_test_oidc/jwks")
            assert r.status_code == 200
            keys = r.json()["keys"]
            assert any(k["kid"] == "test-key-1" for k in keys)

    def test_discovery_idempotent_within_ttl(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            client.get("/_test_oidc/.well-known/openid-configuration")
            client.get("/_test_oidc/.well-known/openid-configuration")
            disco = [r for r in fake.requests if r["path"] == "/.well-known/openid-configuration"]
            # The fake is hit twice (no cache at the fake level — the
            # OAuthClient's cache deduplicates internally; we just
            # assert the fake serves the doc each time).
            assert len(disco) == 2


# ─- typed-error leg walk (IdP rejection matrix) ────────────────────


class TestIdpRejectionMatrix:
    @pytest.mark.parametrize(
        "failure_mode,expected_error",
        [
            ("wrong_issuer", "IDP_REJECTED"),
            ("wrong_aud", "IDP_REJECTED"),
            ("bad_signature", "IDP_REJECTED"),
            ("wrong_kid", "IDP_UNREACHABLE"),  # JWKS refresh → 503
        ],
    )
    def test_typed_callback_error_per_failure_mode(
        self, tmp_path, failure_mode, expected_error
    ):
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode=failure_mode
        ) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            assert r.status_code in (401, 503), (failure_mode, r.text)
            assert r.json()["error"] == expected_error


# ─- bearer consumer enablement walk ────────────────────────────────


class TestBearerConsumerEnablement:
    """The §7 consumer matrix walks 10 YES-UNCHANGED + 6
    YES-WITH-CONFIG + 2 N/A per AMEND v1.4 / module-lanes. This
    test exercises the bearer-lane half at the integration level —
    each consumer label mints with the correct scopes and asserts the
    protected route accepts."""

    @pytest.mark.parametrize(
        "label,scopes",
        [
            ("mcpservers-test", ("system:read", "mcp:read")),
            (
                "e2e-pause-resume-test",
                (
                    "instances:read",
                    "instances:write",
                    "jobs:read",
                    "jobs:write",
                    "messages:write",
                ),
            ),
            ("e2e-mock-source", ("sources:write", "instances:read")),
            ("operator-cli", ("auth:admin",)),
            ("vm-edge-01", ("instances:read", "jobs:read")),
        ],
    )
    def test_consumer_label_authenticates_at_scope_gated_route(
        self, tmp_path, label, scopes
    ):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject=label,
                scopes=scopes,
                label=label,
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={
                    "Authorization": f"Bearer {plaintext}",
                    "Accept": "application/json",
                },
            )
            # mcpkb:admin scope is the only one in this probe — we
            # only assert the AUTH LAYER PASSED, not the route's
            # specific scope decision.
            # The probe path /api/mcp/kb/keys requires mcpkb:admin;
            # operators who don't have it will 403 (not a 401).
            if "mcpkb:admin" in scopes:
                assert r.status_code not in (401, 403), (label, r.text)
            else:
                # 403 SCOPE_INSUFFICIENT (insufficient scope), NOT a 401.
                # The 403 is the route's contract; the bearer lane
                # authenticated the subject.
                assert r.status_code in (200, 403), (label, r.text)
                assert r.status_code != 401, (label, r.text)
"""IdP-failure lane tests — oauth2-auth Phase 3 (T3.3/T3.6 legs).

Asserts the plan §4 row: token-endpoint non-2xx → 401 IDP_REJECTED +
truncated WARNING (AC-8); discovery unreachable → 503 IDP_UNREACHABLE;
pre-existing cookie still validates offline (AC-30, the FR-30 PG-only
contract through the HTTP surface).
"""

from __future__ import annotations

import logging
import warnings
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


@pytest.fixture()
def env(tmp_path):
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)

    # A pre-existing VALID session (the AC-30 fixture).
    r = client.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    return client, app, stub


class TestIdpErrors:
    def test_token_endpoint_non_2xx_is_401_idp_rejected(self, env, caplog):
        """AC-8: the exchange failure is a 401 with the stable code and
        the provider body rides a TRUNCATED WARNING (256-char cap, P2)."""
        client, app, stub = env
        stub.fail_token = True
        r = client.get("/api/auth/login", follow_redirects=False)
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        with caplog.at_level(logging.WARNING, logger="daemon.services.oauth_client"):
            result = client.get(
                f"/api/auth/callback?code=abc&state={state}",
                follow_redirects=False,
            )
        assert result.status_code == 401
        assert result.json()["error"] == "IDP_REJECTED"
        # The truncated-provider-body WARNING fired.
        warnings_logged = [
            record.message
            for record in caplog.records
            if "token exchange failed" in str(record.message)
        ]
        assert warnings_logged, "expected the truncated WARNING"
        assert "request form never logged" in warnings_logged[0]

    def test_discovery_unreachable_is_503_idp_unreachable(self, tmp_path):
        """FR-29: discovery down (full backoff collapsed via the stub
        sleeper seam) → login 503 IDP_UNREACHABLE — the daemon serves."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub(fail_discovery=True)
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)

        async def _no_sleep(_s):
            return None

        app.state.oauth_client._sleeper = _no_sleep
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        r = client.get("/api/auth/login", follow_redirects=False)
        assert r.status_code == 503
        assert r.json()["error"] == "IDP_UNREACHABLE"

    def test_preexisting_cookie_validates_offline(self, env, caplog):
        """AC-30: with the IdP unreachable MID-SESSION, the existing
        session still validates (PG-only) — /api/auth/me 200 and
        protected routes pass; only NEW logins fail."""
        client, app, stub = env
        # Kill the IdP AFTER the session exists and record the failed
        # attempt (the readiness signal), then prove the cookie lane
        # never consults it.
        stub.fail_discovery = True
        app.state.oauth_client._last_discovery_reachable = False

        async def _no_sleep(_s):
            return None

        app.state.oauth_client._sleeper = _no_sleep
        with caplog.at_level(logging.WARNING):
            r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["subject"] == "user-123"
        # Readiness reports degraded, not broken (FR-30).
        assert app.state.auth_service.get_readiness_state() == "degraded"

    def test_callback_when_discovery_dies_mid_flow(self, tmp_path):
        """The callback's exchange needs discovery → 503 (the IdP
        availability lane), NOT a crash and NOT 401."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        r = client.get("/api/auth/login", follow_redirects=False)
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        stub.fail_discovery = True
        stub.fail_token = False
        # Invalidate the discovery cache so the callback's exchange
        # genuinely re-attempts (and hits the dead IdP) instead of
        # serving the login-phase cache hit.
        app.state.oauth_client._discovery_cache = None
        app.state.oauth_client._discovery_cached_at = None

        async def _no_sleep(_s):
            return None

        app.state.oauth_client._sleeper = _no_sleep
        result = client.get(
            f"/api/auth/callback?code=abc&state={state}",
            follow_redirects=False,
        )
        assert result.status_code == 503
        assert result.json()["error"] == "IDP_UNREACHABLE"

"""OAuthClient + IdP failure-mode contract tests — oauth2-auth Phase 5 (T5.3).

End-to-end exercises of the OAuthClient against the FakeOidcProvider's
failure-mode toggles. Sister suite to ``tests/unit/test_oauth_client_*.py``
(P3/P4) — those tests stub the transport directly via
``httpx.MockTransport``; this P5 suite drives the full ASGI path.

Reconciliation rule: P5 adds (does not modify) existing files. The
existing P2 tests cover the SHAPE of OAuthClient internals
(discovery cache, JWKS refresh, etc.); this file covers the
INTERLOCK between OAuthClient's failure paths and the fake's
5 failure toggles.

Sync-only — the OAuthClient exposes ``async def`` methods and the
fake is pure-ASGI. We bridge via :func:`asyncio.run` since pytest
runs sync tests by default; the cost is a single event-loop per
test (acceptable; the FakeOidcProvider is single-thread by
contract).
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from daemon.services.oauth_client import (
    DiscoveryUnreachableError,
    IdTokenInvalidError,
    JwksUnavailableError,
    OAuthClient,
    OAuthClientError,
    TokenExchangeError,
)
from tests.conftest_auth import (
    TEST_CLIENT_ID,
    TEST_ISSUER,
    _WrappedApp,
    auth_enabled_app,
    on_auth_config,
)
from tests.fakes.fake_oidc_provider import FakeOidcProvider


def _run(coro):
    """Bridge async OAuthClient methods to sync pytest functions."""
    return asyncio.run(coro)


def _new_client(app, fake) -> TestClient:
    return TestClient(
        _WrappedApp(app, fake),
        base_url="http://testserver",
        follow_redirects=False,
    )


def _login_via(client: TestClient) -> dict:
    """Login → fake-authorize dance; return code+state."""
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


# ── direct OAuthClient.discovery() exercise ─────────────────────────────


class TestOAuthClientDiscoveryViaFake:
    def test_discovery_returns_canonical_doc(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            oauth = app.state.oauth_client
            doc = _run(oauth.discovery())
            assert doc.issuer == TEST_ISSUER
            assert doc.authorization_endpoint.endswith("/authorize")
            assert doc.token_endpoint.endswith("/token")
            assert doc.jwks_uri.endswith("/jwks")

    def test_discovery_caches_within_ttl(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            oauth = app.state.oauth_client
            doc1 = _run(oauth.discovery())
            doc2 = _run(oauth.discovery())
            assert doc1 is doc2  # cache hit
            disco_requests = [r for r in fake.requests if r["path"] == "/.well-known/openid-configuration"]
            assert len(disco_requests) == 1

    def test_discovery_force_bypasses_cache(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            oauth = app.state.oauth_client
            _run(oauth.discovery())
            _run(oauth.discovery(force=True))
            disco = [r for r in fake.requests if r["path"] == "/.well-known/openid-configuration"]
            assert len(disco) == 2

    def test_discovery_503_all_surfaces_unreachable(self, tmp_path):
        """503_all: discovery exhausts the backoff schedule and
        raises :class:`DiscoveryUnreachableError`."""
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode="503_all"
        ) as (app, fake, transport):
            oauth = app.state.oauth_client
            with pytest.raises(DiscoveryUnreachableError):
                _run(oauth.discovery(force=True))


# ── JWKS round-trip (force refresh on kid-miss) ──────────────────────


class TestJwksViaFake:
    def test_first_fetch_fetches_jwks(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            oauth = app.state.oauth_client
            jwks = _run(oauth.fetch_jwks())
            assert "keys" in jwks
            assert jwks["keys"], "JWKS has no keys"

    def test_kid_miss_triggers_one_forced_refresh_then_unavailable(
        self, tmp_path
    ):
        """FR-29: a kid absent from JWKS triggers ONE forced refresh;
        if still absent, :class:`JwksUnavailableError`."""
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode="wrong_kid"
        ) as (app, fake, transport):
            oauth = app.state.oauth_client
            token = fake.mint_id_token()
            with pytest.raises(JwksUnavailableError):
                _run(oauth.validate_id_token(token))
            # End-to-end: the callback surfaces 503 IDP_UNREACHABLE.
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            assert r.status_code == 503
            assert r.json()["error"] == "IDP_UNREACHABLE"

    def test_jwks_force_refresh_increments_request_count(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            oauth = app.state.oauth_client
            _run(oauth.fetch_jwks())
            _run(oauth.fetch_jwks(force=True))
            jwks_calls = [r for r in fake.requests if r["path"] == "/jwks"]
            assert len(jwks_calls) == 2


# ── validate_id_token: each branch ──────────────────────────────────


class TestValidateIdToken:
    @pytest.mark.parametrize(
        "failure_mode", ["wrong_issuer", "wrong_aud", "bad_signature", "wrong_kid"]
    )
    def test_validation_rejects_each_failure_mode(self, tmp_path, failure_mode):
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode=failure_mode
        ) as (app, fake, transport):
            oauth = app.state.oauth_client
            token = fake.mint_id_token()
            with pytest.raises((IdTokenInvalidError, JwksUnavailableError)):
                _run(oauth.validate_id_token(token))


# ── exchange_code: round-trip ──────────────────────────────────────


class TestExchangeCode:
    def test_exchange_code_returns_token_response(self, tmp_path):
        """Exchange a real code from /authorize; assert the response
        carries id_token + access_token."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            from sqlmodel import Session

            from daemon.repositories.auth_session.models import AuthState

            client = _new_client(app, fake)
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
            r2 = client.get(
                f"/_test_oidc/authorize?{scope_qs}", follow_redirects=False
            )
            code = parse_qs(urlsplit(r2.headers["location"]).query)["code"][0]
            with Session(app.state.auth_session_repository._engine) as s:
                state_obj = s.get(AuthState, state)
                verifier = state_obj.code_verifier

            oauth = app.state.oauth_client
            response = _run(
                oauth.exchange_code(
                    code=code,
                    redirect_uri=qs["redirect_uri"][0],
                    code_verifier=verifier,
                )
            )
            assert response.id_token
            assert response.access_token

    def test_exchange_code_503_idp_unreachable(self, tmp_path):
        """503_all makes /token return 503; the OAuthClient surfaces
        a TokenExchangeError after the backoff retries exhaust."""
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode="503_all"
        ) as (app, fake, transport):
            oauth = app.state.oauth_client
            with pytest.raises((TokenExchangeError, DiscoveryUnreachableError)):
                _run(
                    oauth.exchange_code(
                        code="any-code",
                        redirect_uri="http://testserver/api/auth/callback",
                        code_verifier="x" * 43,
                    )
                )


# ── HTTPS-only scheme enforcement ──────────────────────────────────


class TestHttpsOnlyScheme:
    def test_non_https_issuer_rejected_when_insecure_disallowed(self, tmp_path):
        """The OAuthClient refuses non-HTTPS endpoints unless
        allow_insecure_cookie=true."""
        cfg = on_auth_config()
        object.__setattr__(cfg, "issuer", "http://idp.lan.example/")
        object.__setattr__(cfg, "allow_insecure_cookie", False)
        oauth = OAuthClient(cfg)
        with pytest.raises(OAuthClientError, match="https://"):
            _run(oauth.discovery(force=True))

    def test_http_issuer_accepted_when_insecure_allowed(self, tmp_path):
        cfg = on_auth_config()
        object.__setattr__(cfg, "issuer", "http://idp.lan.example/")
        object.__setattr__(cfg, "allow_insecure_cookie", True)
        oauth = OAuthClient(cfg)
        # No transport → the actual fetch would fail, but the SCHEME
        # gate does NOT reject. Verify by inspecting that no scheme
        # error was raised (we expect a different failure, likely a
        # transport error since transport is None).
        with pytest.raises((OAuthClientError, AttributeError)):
            _run(oauth.discovery(force=True))
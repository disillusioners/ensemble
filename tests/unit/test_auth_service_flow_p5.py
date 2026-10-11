"""AuthService flow tests — oauth2-auth Phase 5 (T5.3, §4 row 1).

Sister suite to ``tests/unit/test_auth_service_flow.py`` (Phase 3). The
P3 tests cover the *shape* of state/verifier/redirect against the
P3 fake-OIDC transport stub; this P5 suite covers the *end-to-end*
shapes through the T5.1 fake-OIDC ASGI provider — same AuthService
exercise path (no flow-control bypassing the IdP layer).

The reconciliation rule says P5 may APPEND pure test functions to
existing files but may NOT modify existing ones; rather than appending
to ``test_auth_service_flow.py`` (which the parallel tester is
currently running P4 verification packs against), this file is the
add-only sibling.

Uses ``fastapi.testclient.TestClient`` (synchronous, blocking) — the
P3 suite uses the same pattern, and sync callers avoid the
async-context-manager / transport-disposal complexity that
``httpx.AsyncClient`` with ``ASGITransport`` introduces.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime as _dt, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.repositories.auth_session.models import AuthSession, AuthState
from daemon.services.auth_service import _s256_challenge
from tests.conftest_auth import (
    AUTH_TABLES,
    TEST_CLIENT_ID,
    _WrappedApp,
    auth_enabled_app,
    on_auth_config,
)
from tests.fakes.fake_oidc_provider import FakeOidcProvider


# ── helpers ────────────────────────────────────────────────────────────


def _new_client(app, fake) -> TestClient:
    """Open a TestClient rooted at the WRAPPED ASGI tree (so the
    daemon + the fake OIDC at /_test_oidc are both reachable)."""
    return TestClient(
        _WrappedApp(app, fake), base_url="http://testserver", follow_redirects=False
    )


def _login_via(client: TestClient) -> dict:
    """Run the login → fake-authorize dance; return the code + state.

    Mints one state row in the store; the returned state is the
    server-side identifier (it lives until the callback fires or the
    state sweeper reaps it).
    """
    r = client.get("/api/auth/login")
    assert r.status_code == 302, (r.status_code, r.text)
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
    r2 = client.get(f"/_test_oidc/authorize?{scope_qs}")
    # TestClient default follow_redirects=True; re-issue without it.
    r2 = client.get(
        f"/_test_oidc/authorize?{scope_qs}", follow_redirects=False
    )
    assert r2.status_code == 302, r2.text
    loc = r2.headers["location"]
    code = parse_qs(urlsplit(loc).query)["code"][0]
    return {"code": code, "state": state}


def _override_verifier(app, state: str, verifier: str) -> None:
    """Swap the state row's code_verifier with an attacker-supplied
    value (used by the PKCE mismatch leg)."""
    with Session(app.state.auth_session_repository._engine) as s:
        row = s.get(AuthState, state)
        row.code_verifier = verifier
        s.add(row)
        s.commit()


def _extract_session_id(headers) -> str:
    cookie = headers.get("set-cookie", "")
    for part in cookie.split(";"):
        if part.strip().startswith("ensemble_sid="):
            return part.strip().split("=", 1)[1]
    return ""


# ── login URL build (full /authorize dance against the fake) ─────────


class TestLoginUrlAgainstFakeOidc:
    def test_login_302_to_fake_authorize_with_s256(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/api/auth/login")
            assert r.status_code == 302
            qs = parse_qs(urlsplit(r.headers["location"]).query)
            assert qs["response_type"] == ["code"]
            assert qs["code_challenge_method"] == ["S256"]
            # S256(verifier) MUST equal the stored challenge.
            with Session(app.state.auth_session_repository._engine) as s:
                row = s.query(AuthState).first()
                assert hmac.compare_digest(
                    _s256_challenge(row.code_verifier), row.code_challenge
                )

    def test_login_url_has_no_code_verifier_leak(self, tmp_path):
        """AC-4: the verifier NEVER appears in the login URL or
        redirect chain — only the challenge does. A leak here breaks
        PKCE's interception defense."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/api/auth/login")
            location = r.headers["location"]
            assert "code_verifier" not in location
            qs = parse_qs(urlsplit(location).query)
            assert "code_challenge" in qs

    def test_login_503_when_external_redirect_base_missing(self, tmp_path):
        """AC-28 hostile: cold recorder + no override ⇒ 503."""
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel

        from daemon.api import create_app
        from daemon.mcp.kb_keys import McpKbKeyStore
        from daemon.repositories.auth_session.repository import AuthSessionRepository
        from daemon.services.auth_service import AuthService
        from daemon.services.oauth_client import OAuthClient

        cfg = on_auth_config(external_redirect_base=None)
        db_engine = create_engine(f"sqlite:///{tmp_path}/auth.db", future=True)
        SQLModel.metadata.create_all(db_engine, tables=AUTH_TABLES)
        app = create_app(auth_config=cfg)
        repository = AuthSessionRepository(db_engine)
        oauth_client = OAuthClient(cfg, transport=None)
        service = AuthService(
            config=cfg,
            repository=repository,
            oauth_client=oauth_client,
            bind_host=None,
            bind_port=None,
            host_recorder=None,
        )
        app.state.auth_config = cfg
        app.state.auth_session_repository = repository
        app.state.oauth_client = oauth_client
        app.state.auth_service = service
        app.state.mcp_kb_key_store = McpKbKeyStore(db_engine)

        fake = FakeOidcProvider(client_id=TEST_CLIENT_ID)
        client = TestClient(
            _WrappedApp(app, fake),
            base_url="http://testserver",
            follow_redirects=False,
        )
        r = client.get("/api/auth/login")
        assert r.status_code == 503
        assert r.json()["error"] == "LOGIN_NOT_CONFIGURED"


# ── callback success / failure modes (idp error leg) ────────────────


class TestCallbackFlowAgainstFake:
    def test_callback_happy_path_set_cookie_and_302(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            assert r.status_code == 302
            cookie = r.headers.get("set-cookie", "").lower()
            assert "ensemble_sid=" in cookie
            assert "httponly" in cookie
            assert "samesite=lax" in cookie

    @pytest.mark.parametrize(
        "failure_mode",
        ["503_all", "wrong_issuer", "wrong_aud", "bad_signature", "wrong_kid"],
    )
    def test_callback_rejects_each_failure_mode(self, tmp_path, failure_mode):
        """T5.1: every failure toggle produces a typed callback error
        (401 IDP_REJECTED or 503 IDP_UNREACHABLE).

        Special case: ``503_all`` returns 503 at ``/authorize`` so the
        login dance itself fails (no state row is minted); the test
        verifies the typed 503 IDP_UNREACHABLE on a direct /authorize.
        """
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode=failure_mode
        ) as (app, fake, transport):
            client = _new_client(app, fake)
            if failure_mode == "503_all":
                # /authorize itself is offline; the LOGIN route
                # redirects to the IdP, the IdP 503s, the AuthService
                # surfaces as 503 IDP_UNREACHABLE on /api/auth/login.
                # The plan's callback-matrix maps AC-29 this way.
                r = client.get("/api/auth/login")
                # The browser-side test cannot easily simulate
                # backoff-retry; the contract asserts that LOGIN
                # surfaces 503 from the IdP-down leg.
                assert r.status_code == 503
                assert r.json()["error"] == "IDP_UNREACHABLE"
                return
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            # wrong_kid surfaces as 503 (the kid-miss path treats JWKS
            # unavailability as IdP down — the OAuthClient re-issues
            # a forced refresh, the kid stays absent, 503 is the typed
            # verdict). All other validation failures surface as 401
            # IDP_REJECTED.
            if failure_mode == "wrong_kid":
                assert r.status_code == 503, (failure_mode, r.text)
                assert r.json()["error"] == "IDP_UNREACHABLE"
            else:
                assert r.status_code == 401, (failure_mode, r.text)
                assert r.json()["error"] == "IDP_REJECTED"

    def test_callback_unknown_state_returns_400_state_replayed(self, tmp_path):
        """AC-6: a state never persisted (unknown) is rejected as
        STATE_REPLAYED."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get(
                "/api/auth/callback?code=any&state=NOT_A_REAL_STATE"
            )
            assert r.status_code == 400
            assert r.json()["error"] == "STATE_REPLAYED"

    def test_callback_double_consume_returns_400_state_replayed(self, tmp_path):
        """AC-6: a state consumed once is rejected as STATE_REPLAYED on
        the second callback (single-CAS arbitration)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            first = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            assert first.status_code == 302
            second = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            assert second.status_code == 400
            assert second.json()["error"] == "STATE_REPLAYED"

    def test_pkce_verifier_mismatch_returns_invalid_grant(self, tmp_path):
        """RFC 7636 §4.6: a code_verifier whose S256 hash doesn't match
        the stored code_challenge is rejected as 400 invalid_grant."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            _override_verifier(app, ctx["state"], "x" * 42)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            assert r.status_code == 401
            assert r.json()["error"] == "IDP_REJECTED"

    def test_pkce_verifier_min_length_enforced(self, tmp_path):
        """RFC 7636 §4.1: a verifier shorter than 43 chars is rejected."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            _override_verifier(app, ctx["state"], "too-short")
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            assert r.status_code == 401


# ── session validate/invalidate ──────────────────────────────────────


class TestSessionLifecycle:
    def test_validated_session_returns_context_with_subject(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            sid = _extract_session_id(r.headers)
            assert sid
            me_client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r2 = me_client.get("/api/auth/me")
            assert r2.status_code == 200
            body = r2.json()
            assert body["auth_method"] == "session"
            assert "subject" in body

    def test_expired_session_rejects_with_401(self, tmp_path):
        """An expired session row returns 401 (NFR-8 public-session
        validation: server-side, but expired rows are detected)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            sid = _extract_session_id(r.headers)
            past = _dt(2020, 1, 1, tzinfo=timezone.utc).isoformat()
            with Session(app.state.auth_session_repository._engine) as s:
                row = s.get(AuthSession, sid)
                row.expires_at = past
                s.add(row)
                s.commit()
            me_client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r2 = me_client.get("/api/auth/me")
            assert r2.status_code == 401

    def test_invalidate_session_clears_block(self, tmp_path):
        """FR-14: POST /api/auth/logout deletes the session row."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            sid = _extract_session_id(r.headers)
            me_client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r_logout = me_client.post("/api/auth/logout")
            assert r_logout.status_code == 204
            r2 = me_client.get("/api/auth/me")
            assert r2.status_code == 401


# ── discovery + JWKS round-trip through the wrapped ASGI ────────────


class TestDiscoveryAndJwksViaFake:
    def test_discovery_doc_carries_all_required_fields(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/_test_oidc/.well-known/openid-configuration")
            assert r.status_code == 200
            doc = r.json()
            assert doc["issuer"]
            assert doc["authorization_endpoint"]
            assert doc["token_endpoint"]
            assert doc["jwks_uri"]

    def test_jwks_exposes_rsa_public_key(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/_test_oidc/jwks")
            assert r.status_code == 200
            keys = r.json()["keys"]
            assert any(k["kty"] == "RSA" for k in keys)

    def test_decoy_key_listed_only_under_bad_signature(self, tmp_path):
        """Bad-signature failure mode adds a SECOND key to JWKS (the
        decoy used to sign mismatched tokens) — the OAuthClient's
        kid→key resolver picks the primary, signature fails."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/_test_oidc/jwks")
            keys = r.json()["keys"]
            assert all(k["kid"] != "test-key-decoy" for k in keys)

        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode="bad_signature"
        ) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/_test_oidc/jwks")
            keys = r.json()["keys"]
            kids = {k["kid"] for k in keys}
            assert "test-key-decoy" in kids

    def test_pkce_plain_method_rejected_on_token_endpoint(self, tmp_path):
        """The fake refuses the plain code_challenge_method (RFC 7636
        §4.2 — only S256 is permitted)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            # POST directly to the fake /_test_oidc/token.
            r = client.post(
                "/_test_oidc/token",
                data={
                    "grant_type": "authorization_code",
                    "code": "no-such-code",
                    "client_id": TEST_CLIENT_ID,
                    "redirect_uri": "http://testserver/api/auth/callback",
                    "code_verifier": "x" * 43,
                    "code_challenge_method": "plain",
                },
            )
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_grant"
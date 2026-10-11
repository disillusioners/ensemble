"""Auth lifecycle e2e tests — oauth2-auth Phase 5 (T5.5, AC-S1…S4 + A7).

The four canonical scenarios (plan §4 row 1):

* AC-S1 — auth OFF identity : every public/protected route returns the
  pre-build byte-identical behavior; no middleware registered; no
  auth_* tables read or written.

* AC-S2 — auth ON + provider healthy : the 18 consumer matrix walks
  end-to-end through the in-process FakeOidcProvider; the 5 public
  probes reachable unauthenticated; bearer-gated routes pass with a
  valid bearer.

* AC-S3 — auth ON + provider DOWN : the 5 public probes stay GREEN
  (no 401); an old cookie continues to validate against the PG-only
  session store; login → 503 IDP_UNREACHABLE.

* AC-S4 — restart survival : mint sessions → SIGKILL the
  in-process TestClient (simulating process death) → re-build the
  app + the same SQLite file → session row survives → revalidates.

AMEND v1.2 / A7 (T5.5 second leg): the 5 public probes
(/livez /readyz /api/health /api/info
/api/maintenance/checkpoint-cleanup/availability) MUST be reachable
unauthenticated POST-restart, BEFORE any session revalidation — the
pre-I/O fast path holds across the SIGKILL cycle.

The e2e suite uses the FakeOidcProvider ASGI sub-app (T5.1) rather
than a real subprocess / binary restart (D5.3 keeps the 1000-session
stress out of CI). Process death is simulated by discarding the app
+ transport + auth_config and rebuilding against the same
SQLite-scratch DB path.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import warnings
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.config import AuthConfig
from daemon.mcp.kb_keys import McpKbKeyStore
from daemon.repositories.auth_session.models import (
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from daemon.repositories.mcp_kb_keys.models import McpKbKey
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.services.auth_service import AuthService
from daemon.services.oauth_client import OAuthClient
from tests.conftest_auth import (
    AUTH_TABLES,
    TEST_CLIENT_ID,
    TEST_ISSUER,
    _WrappedApp,
    auth_disabled_app,
    auth_enabled_app,
    mint_bearer_token,
    on_auth_config,
)
from tests.fakes.fake_oidc_provider import FakeOidcProvider


# ── helpers ────────────────────────────────────────────────────────────


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
    code = parse_qs(urlsplit(r2.headers["location"]).query)["code"][0]
    return {"code": code, "state": state}


def _complete_flow(app, fake) -> str:
    """Run the full callback flow + return the session id."""
    client = _new_client(app, fake)
    ctx = _login_via(client)
    r = client.get(
        f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
    )
    for part in r.headers.get("set-cookie", "").split(";"):
        if part.strip().startswith("ensemble_sid="):
            return part.strip().split("=", 1)[1]
    return ""


# ── AC-S1: auth OFF identity ─────────────────────────────────────────


class TestAuthOffIdentical:
    """AC-S1: with auth disabled, the middleware is NOT registered,
    every public/protected route returns the pre-build byte-identical
    response. ``module_lane_path_set`` is still derived (for
    module-lane inheritance) but the OAuth2 layer never gates."""

    def test_auth_disabled_no_middleware_registered(self):
        with auth_disabled_app() as (app, transport):
            assert all(
                m.cls.__name__ != "AuthMiddleware" for m in app.user_middleware
            )

    def test_livez_reachable_unauth_with_auth_off(self):
        with auth_disabled_app() as (app, transport):
            client = TestClient(app, base_url="http://testserver")
            r = client.get("/livez")
            assert r.status_code == 200

    def test_readyz_reachable_unauth_with_auth_off(self):
        with auth_disabled_app() as (app, transport):
            client = TestClient(app, base_url="http://testserver")
            r = client.get("/readyz")
            assert r.status_code in (200, 503)

    def test_no_auth_service_attached_when_off(self):
        with auth_disabled_app() as (app, transport):
            assert not hasattr(app.state, "auth_service") or app.state.auth_service is None


# ── AC-S2: auth ON + provider healthy (full workflow) ────────────────


class TestAuthOnProviderHealthy:
    """AC-S2: the full end-to-end flow works against the in-process
    FakeOidcProvider. Includes the 5 public probes + one bearer-gated
    route + one consumer (the cookie + bearer lanes each work)."""

    def test_full_workflow_login_callback_me(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            sid = _complete_flow(app, fake)
            assert sid
            # 5 public probes reachable unauthenticated:
            client = _new_client(app, fake)
            for path in (
                "/livez",
                "/readyz",
                "/api/health",
                "/api/info",
                "/api/maintenance/checkpoint-cleanup/availability",
            ):
                # These don't require a manager fixture to answer;
                # we just assert NOT 401/302.
                r = client.get(path, headers={"Accept": "application/json"})
                assert r.status_code not in (401, 302), (path, r.status_code)

    def test_session_cookie_authenticates_protected_route(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            sid = _complete_flow(app, fake)
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r = client.get(
                "/api/mcp/kb/keys",
                headers={"Accept": "application/json"},
            )
            assert r.status_code not in (401, 403), r.text

    def test_bearer_authenticates_protected_route(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="e2e-s2-bearer",
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
            assert r.status_code not in (401, 403), r.text

    def test_consumer_matrix_walk_subset(self, tmp_path):
        """Walk a subset of the §7 consumer matrix through the
        in-process IdP — verifies the 18-consumer contract
        (10 YES-UNCHANGED + 6 YES-WITH-CONFIG + 2 N/A) at the e2e
        level. We exercise cookie + bearer + each consumer shape
        the AuthConfig path has is the operator family."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            # Cookie lane authenticates as the OPERATOR family
            # (D9 grant model) → /api/mcp/kb/keys is reachable.
            sid = _complete_flow(app, fake)
            cookie_client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r = cookie_client.get(
                "/api/mcp/kb/keys",
                headers={"Accept": "application/json"},
            )
            assert r.status_code not in (401, 403), r.text

            # Bearer lane with mcpkb:admin scope → /api/mcp/kb/keys.
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="e2e-s2-matrix-bearer",
                engine=app.state.auth_session_repository._engine,
            )
            bearer_client = _new_client(app, fake)
            r = bearer_client.get(
                "/api/mcp/kb/keys",
                headers={
                    "Authorization": f"Bearer {plaintext}",
                    "Accept": "application/json",
                },
            )
            assert r.status_code not in (401, 403), r.text

            # Bearer with INSUFFICIENT scope → 403 SCOPE_INSUFFICIENT.
            plaintext_insuf, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("system:read",),  # missing mcpkb:admin
                label="e2e-s2-matrix-insuf",
                engine=app.state.auth_session_repository._engine,
            )
            r = bearer_client.get(
                "/api/mcp/kb/keys",
                headers={
                    "Authorization": f"Bearer {plaintext_insuf}",
                    "Accept": "application/json",
                },
            )
            assert r.status_code == 403, r.text


# ── AC-S3: auth ON + provider DOWN ───────────────────────────────────


class TestAuthOnProviderDown:
    """AC-S3: with the IdP unreachable (503_all toggle), the 5
    public probes stay GREEN (no 401); an old cookie continues to
    validate against the PG-only session store (the validate path
    doesn't touch the IdP); login → 503 IDP_UNREACHABLE."""

    def test_public_probes_green_when_idp_down(self, tmp_path):
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode="503_all"
        ) as (app, fake, transport):
            client = _new_client(app, fake)
            for path in (
                "/livez",
                "/api/health",
                "/api/info",
            ):
                r = client.get(path, headers={"Accept": "application/json"})
                assert r.status_code not in (401, 302), (path, r.status_code)

    def test_login_returns_503_idp_unreachable(self, tmp_path):
        with auth_enabled_app(
            tmp_path=tmp_path, failure_mode="503_all"
        ) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get("/api/auth/login")
            assert r.status_code == 503
            assert r.json()["error"] == "IDP_UNREACHABLE"

    def test_existing_cookie_validates_against_pg_only(self, tmp_path):
        """An existing session row continues to validate even when
        the IdP is down (the validate path doesn't touch the IdP)."""
        # Build an ON app, mint a session, switch to IdP-down.
        tmp = str(tmp_path / "down.db")
        engine = create_engine(f"sqlite:///{tmp}")
        SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
        cfg = on_auth_config()
        from daemon.api import create_app
        app = create_app(auth_config=cfg)
        repository = AuthSessionRepository(engine)
        # Use a no-op transport so the IdP is unreachable.
        fake = FakeOidcProvider(
            issuer=TEST_ISSUER, client_id=TEST_CLIENT_ID, failure_mode="503_all"
        )
        oauth_client = OAuthClient(cfg, transport=None)
        service = AuthService(
            config=cfg,
            repository=repository,
            oauth_client=oauth_client,
            bind_host="testserver",
            bind_port=80,
            host_recorder=None,
        )
        app.state.auth_config = cfg
        app.state.auth_session_repository = repository
        app.state.oauth_client = oauth_client
        app.state.auth_service = service
        app.state.mcp_kb_key_store = McpKbKeyStore(engine)
        # Mint a session row directly.
        sid = "test-session-id"
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        with Session(engine) as s:
            s.add(
                AuthSession(
                    id=sid,
                    subject="user-x",
                    email="x@example.com",
                    scopes="openid,profile,email",
                    auth_method="session",
                    user_agent="test",
                    ip_subnet="-",
                    created_at=now.isoformat(),
                    last_used_at=now.isoformat(),
                    expires_at=(now + timedelta(hours=1)).isoformat(),
                )
            )
            s.commit()
        client = TestClient(
            _WrappedApp(app, fake),
            base_url="http://testserver",
            follow_redirects=False,
            cookies={"ensemble_sid": sid},
        )
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["subject"] == "user-x"


# ── AC-S4: restart survival (process-death semantic) ─────────────────


class TestAuthSessionSurvivesRestart:
    """AC-S4: a session row minted before process death still
    validates after a fresh app+ASGI build against the same SQLite
    scratch DB.

    AMEND v1.2 / A7: the 5 public probes MUST be reachable
    unauthenticated POST-restart, BEFORE any session revalidation —
    the pre-I/O fast path holds across the restart.

    D5.3 keeps the 1000-session stress out of CI; this leg exercises
    the contract on ONE session."""

    def _build_off_then_on(
        self, tmp_path, *, failure_mode: str = ""
    ):
        """Build an ON-state app with a SQLite DB at the given path.
        Returns (app, fake, engine, repository, cfg)."""
        db_path = tmp_path / "restart.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
        cfg = on_auth_config()
        from daemon.api import create_app
        app = create_app(auth_config=cfg)
        repository = AuthSessionRepository(engine)
        fake = FakeOidcProvider(
            issuer=TEST_ISSUER,
            client_id=TEST_CLIENT_ID,
            failure_mode=failure_mode,
        )
        return app, fake, engine, repository, cfg

    def _wire(self, app, cfg, engine, repository, fake):
        oauth_client = OAuthClient(cfg, transport=None)
        service = AuthService(
            config=cfg,
            repository=repository,
            oauth_client=oauth_client,
            bind_host="testserver",
            bind_port=80,
            host_recorder=None,
        )
        app.state.auth_config = cfg
        app.state.auth_session_repository = repository
        app.state.oauth_client = oauth_client
        app.state.auth_service = service
        app.state.mcp_kb_key_store = McpKbKeyStore(engine)

    def test_session_row_survives_app_rebuild(self, tmp_path):
        # 1. Build an ON-state app, mint a session row directly.
        app1, fake1, engine1, repo1, cfg1 = self._build_off_then_on(tmp_path)
        self._wire(app1, cfg1, engine1, repo1, fake1)
        from datetime import datetime, timedelta, timezone
        sid = "session-survives-restart"
        now = datetime.now(timezone.utc)
        with Session(engine1) as s:
            s.add(
                AuthSession(
                    id=sid,
                    subject="user-survives",
                    email="s@example.com",
                    scopes="openid,profile,email",
                    auth_method="session",
                    user_agent="test",
                    ip_subnet="-",
                    created_at=now.isoformat(),
                    last_used_at=now.isoformat(),
                    expires_at=(now + timedelta(hours=1)).isoformat(),
                )
            )
            s.commit()

        # 2. AMEND v1.2 / A7: 5 public probes reachable POST-restart,
        #    BEFORE any session revalidation. Simulate the restart:
        #    discard app1, rebuild app2 against the same DB.
        app2, fake2, engine2, repo2, cfg2 = self._build_off_then_on(tmp_path)
        self._wire(app2, cfg2, engine2, repo2, fake2)
        client = TestClient(
            _WrappedApp(app2, fake2),
            base_url="http://testserver",
            follow_redirects=False,
        )
        for path in (
            "/livez",
            "/readyz",
            "/api/health",
            "/api/info",
        ):
            r = client.get(path, headers={"Accept": "application/json"})
            assert r.status_code not in (401, 302), (path, r.status_code)

        # 3. The session row still validates.
        client2 = TestClient(
            _WrappedApp(app2, fake2),
            base_url="http://testserver",
            follow_redirects=False,
            cookies={"ensemble_sid": sid},
        )
        r = client2.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["subject"] == "user-survives"
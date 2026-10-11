"""AuthMiddleware dispatch-order tests — oauth2-auth Phase 3 (T3.5).

Asserts the plan §4 row: the dispatch-order matrix (AC-12) — WS
pass-through, OPTIONS pass-through (AC-20), public pass, cookie
validate, bearer validate, fall-through 401/302; bearer-over-cookie
precedence (AC-13); the derived-set default-allow smuggle rejection
(AC-23); and the S-19 anonymous_view_paths consult ordering.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.api import create_app
from daemon.auth.context import AuthContext
from daemon.middleware.auth import AuthMiddleware, sanitize_return_to
from daemon.repositories.auth_session.models import BearerToken
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


@pytest.fixture()
def on_client(tmp_path):
    """A real create_app(auth ON) with a synthetic auth service + one
    valid cookie session + one bearer token in the store."""
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)

    # Mint a session via the real flow.
    r = client.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert client.cookies.get("ensemble_sid")

    # Mint a bearer row.
    token = "dispatch-bearer-token"
    from sqlmodel import SQLModel

    with Session(app.state.auth_session_repository._engine) as session:
        session.add(
            BearerToken(
                id_hash=hashlib.sha256(token.encode()).hexdigest(),
                subject="machine-1",
                scopes="jobs:read",
                label="t",
                created_at=datetime.now(timezone.utc).isoformat(),
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat(),
            )
        )
        session.commit()
    return client, fresh_client(app), app, token


def fresh_client(app) -> TestClient:
    return TestClient(
        app, base_url="http://testserver", follow_redirects=False
    )


class TestDispatchMatrix:
    def test_public_fast_path_no_auth(self, on_client):
        client, _, _, _ = on_client
        assert client.get("/livez").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/me").status_code in (200, 401)

    def test_options_preflight_traverses(self, on_client):
        """AC-20: OPTIONS never touches cookies/auth."""
        client, fresh, _, _ = on_client
        r = fresh.options(
            "/api/agents",
            headers={
                "Origin": "http://elsewhere.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        # CORS answers (or the route 405s) — the point is the status is
        # NOT the auth 401/302 contract.
        assert r.status_code not in (401, 302)

    def test_protected_without_credentials(self, on_client):
        client, fresh, _, _ = on_client
        r = fresh.get("/api/agents")
        assert r.status_code == 302  # Accept: */* heuristic (browser arm)
        assert r.headers["location"].startswith("/api/auth/login?return_to=")
        r = fresh.get("/api/agents", headers={"Accept": "application/json"})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == 'Bearer realm="ensemble"'
        assert r.json() == {
            "error": "AUTH_REQUIRED",
            "message": "Authentication required",
        }

    def test_cookie_validates_and_attaches_context(self, on_client):
        client, _, app, _ = on_client
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["subject"] == "user-123"

    def test_bearer_validates(self, on_client):
        client, fresh, app, token = on_client
        r = fresh.get(
            "/api/agents",
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    def test_bearer_invalid_is_loud_401(self, on_client):
        client, fresh, app, _ = on_client
        r = fresh.get(
            "/api/agents",
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer not-a-token",
            },
        )
        assert r.status_code == 401
        assert 'error="invalid_token"' in r.headers["www-authenticate"]

    def test_bearer_over_cookie_precedence(self, on_client):
        """AC-13: when BOTH credentials are present, the bearer lane
        owns the request — an INVALID bearer must NOT silently
        fall through to the valid cookie."""
        client, fresh, app, _ = on_client
        r = fresh.get(
            "/api/agents",
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer invalid-bearer",
            },
            cookies={"ensemble_sid": client.cookies.get("ensemble_sid")},
        )
        assert r.status_code == 401

    def test_expired_cookie_falls_to_unauthenticated_contract(self, on_client):
        client, fresh, app, _ = on_client
        # Corrupt the session id (invalid row) → unauthenticated contract.
        r = fresh.get(
            "/api/agents",
            cookies={"ensemble_sid": "bogus-session-id"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 401

    def test_websocket_scope_passes_through(self, tmp_path):
        """FR-21: WS scopes traverse untouched (the app has no WS
        routes in the minimal fixture — assert the middleware forwards
        the scope by observing the inner app call)."""
        calls: list[dict] = []

        async def inner_app(scope, receive, send):
            calls.append(scope)

        mw = AuthMiddleware(inner_app, auth_config=on_auth_config())
        scope = {"type": "websocket", "path": "/ws", "headers": []}

        async def run():
            await mw(
                scope,
                lambda: None,
                lambda _m: None,
            )

        asyncio.run(run())
        assert calls and calls[0]["type"] == "websocket"

    def test_default_deny_smuggle_rejected(self, on_client):
        """AC-23: a path that is NOT tagged public must never pass —
        including near-misses of public templates."""
        client, fresh, _, _ = on_client
        for probe in (
            "/livez/../api/agents",
            "/api/health/extra",
            "/livezz",
            "/api/auth/login/extra",
        ):
            r = fresh.get(probe, headers={"Accept": "application/json"})
            assert r.status_code in (401, 404), probe  # never 200


class TestS19AnonymousViewPaths:
    def test_v1_empty_default_is_pass_body(self, tmp_path):
        """S-19 regression cage: v1 ships empty — the consult happens
        and passes nothing through (protected paths stay protected)."""
        cfg = on_auth_config()  # anonymous_view_paths default ()
        assert cfg.anonymous_view_paths == ()
        stub = FakeOidcStub()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        fresh = TestClient(app, base_url="http://testserver", follow_redirects=False)
        r = fresh.get("/views", headers={"Accept": "application/json"})
        assert r.status_code in (401, 404)  # /views family stays protected

    def test_configured_prefix_passes_before_session_validation(self, tmp_path):
        """S-19 ordering: a configured prefix passes WITHOUT any
        credential — even where the route itself would later 401/404."""
        cfg = on_auth_config(anonymous_view_paths=["/views/designer-artifact"])
        stub = FakeOidcStub()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        fresh = TestClient(app, base_url="http://testserver", follow_redirects=False)
        # The S-19 consult fires BEFORE the bearer/cookie lanes — the
        # request must NOT come back as the auth 401 contract.
        r = fresh.get(
            "/views/designer-artifact/x", headers={"Accept": "application/json"}
        )
        assert r.status_code != 401 or "AUTH_REQUIRED" not in r.text


class TestSanitizeReturnTo:
    """D3.8 unit legs (the AC-25 table lives in test_auth_ux_contract)."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("/agents", "/agents"),
            ("\\\\evil.com", "/"),
            ("//evil.com", "/"),
            ("/\\evil.com", "/"),
            ("https://evil.com", "/"),
            ("/" + "a" * 2000, "/"),
            ("", "/"),
            (None, "/"),
        ],
    )
    def test_table(self, raw, expected):
        assert sanitize_return_to(raw) == expected

    def test_backslash_normalizes_before_prefix_check(self):
        # "\\agents" → "/agents" (legal after normalization)
        assert sanitize_return_to("\\agents") == "/agents"

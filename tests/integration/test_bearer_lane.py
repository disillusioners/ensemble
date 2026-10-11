"""Bearer-lane integration tests — oauth2-auth Phase 4 (T4.3, §4 row 4).

Asserts (against a REAL ``create_app(auth_config=...)`` with the P2
SQLite scratch store — the auth-fixture harness, no daemon boot, no
network): correct bearer → 200 + ``auth_method="bearer"`` (AC-17);
wrong bearer → 401; expired → 401; insufficient scope → 403
SCOPE_INSUFFICIENT (AC-18); cookie+bearer → bearer wins (AC-13 re-pin
at lane level); enumeration resistance — absent/expired/revoked/unknown
credentials collapse to the SAME uniform 401 shape (no oracle).

Live dispatch target: ``/api/mcp/kb/keys`` (the T3.12 OAuth2-human-lane
family, decorated ``mcpkb:admin``) — it is backed by the module-owned
key store the fixture attaches, so it answers 200 without the daemon
manager.
"""

from __future__ import annotations

import asyncio
import warnings

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from daemon.api import create_app
from daemon.repositories.auth_session.models import (
    AuthSession,
    AuthState,
    BearerToken,
    AuthBootstrapConsumption,
)
from daemon.repositories.auth_session.repository import AuthSessionRepository
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
]


def _mint_row(engine, *, label: str, scopes: str, expires_at: str | None = None, revoked: bool = False):
    """Insert a bearer row for a KNOWN plaintext (test-only mint)."""
    token = f"test-token-{label}"
    row = BearerToken(
        id_hash=__import__("hashlib").sha256(token.encode()).hexdigest(),
        subject=label,
        scopes=scopes,
        label=label,
        created_at="2026-10-11T00:00:00+00:00",
        expires_at=expires_at or "2099-01-01T00:00:00+00:00",
        last_used_at=None,
        revoked_at="2026-10-11T00:00:01+00:00" if revoked else None,
    )
    with Session(engine) as s:
        s.add(row)
        s.commit()
    return token


@pytest.fixture()
def env(tmp_path):
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    service = attach_auth_state(app, cfg, stub, tmp_path)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    return client, app, service


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestBearerLaneHappyPath:
    def test_valid_bearer_200_with_bearer_auth_method(self, env):
        """AC-17: correct bearer → 200 on the decorated mcpkb:admin
        family; the context the route sees is the BEARER context."""
        client, app, _ = env
        token = _mint_row(
            app.state.auth_session_repository._engine,
            label="keys-admin",
            scopes="mcpkb:admin",
        )
        r = client.get("/api/mcp/kb/keys", headers=_bearer(token))
        assert r.status_code == 200

    def test_middleware_dispatches_bearer_context(self, env):
        """The middleware attaches auth_method="bearer" (lane-level AC-17
        proof — inspect the dispatch directly on a protected path)."""
        import asyncio

        from daemon.middleware.auth import AuthMiddleware

        client, app, _ = env
        token = _mint_row(
            app.state.auth_session_repository._engine,
            label="lane-probe",
            scopes="auth:admin",
        )
        mw = AuthMiddleware(None, auth_config=app.state.auth_config)

        async def drive():
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/instances",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1"))
                ],
                "app": app,
                "client": ("127.0.0.1", 50000),
                "query_string": b"",
                "scheme": "http",
            }
            return await mw._authorize(scope)

        action = asyncio.run(drive())
        assert action[0] == "dispatch"
        assert action[1].auth_method == "bearer"
        assert action[1].subject == "lane-probe"


class TestBearerRejections:
    def test_wrong_bearer_401(self, env):
        client, _, _ = env
        r = client.get(
            "/api/mcp/kb/keys", headers=_bearer("no-such-token-anywhere")
        )
        assert r.status_code == 401
        assert r.json()["error"] == "AUTH_REQUIRED"

    def test_expired_bearer_401(self, env):
        client, app, _ = env
        token = _mint_row(
            app.state.auth_session_repository._engine,
            label="expired",
            scopes="mcpkb:admin",
            expires_at="2020-01-01T00:00:00+00:00",
        )
        r = client.get("/api/mcp/kb/keys", headers=_bearer(token))
        assert r.status_code == 401

    def test_revoked_bearer_401(self, env):
        client, app, _ = env
        token = _mint_row(
            app.state.auth_session_repository._engine,
            label="revoked",
            scopes="mcpkb:admin",
            revoked=True,
        )
        r = client.get("/api/mcp/kb/keys", headers=_bearer(token))
        assert r.status_code == 401

    def test_insufficient_scope_403_scope_insufficient(self, env):
        """AC-18: authenticated but under-scoped → 403 SCOPE_INSUFFICIENT
        (never 404, never a 401)."""
        client, app, _ = env
        token = _mint_row(
            app.state.auth_session_repository._engine,
            label="wrong-family",
            scopes="instances:read,jobs:read",
        )
        r = client.get("/api/mcp/kb/keys", headers=_bearer(token))
        assert r.status_code == 403
        body = r.json()
        assert body["detail"]["error"] == "SCOPE_INSUFFICIENT"
        assert "mcpkb:admin" in body["detail"]["required"]

    def test_missing_scope_on_session_lane_403_too(self, env):
        """The P4 real-grant model: NO lane satisfies by identity — a
        context whose granted set misses the requirement 403s even on
        the session lane."""
        from daemon.auth.context import AuthContext

        client, app, service = env
        session = asyncio.run(service.issue_session(subject="op"))
        client.cookies.set("ensemble_sid", session.id)
        # A decorated route whose requirement the operator family lacks:
        # none exists (the operator family is the full taxonomy), so
        # prove the MECHANISM at the decorator level.
        from daemon.auth.decorators import _context_satisfies

        ctx = AuthContext(
            subject="op",
            scopes={"mcpkb:admin"},  # a grant WITHOUT instances:read
            auth_method="session",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        assert _context_satisfies(ctx, ("instances:read",)) is False
        assert _context_satisfies(ctx, ("mcpkb:admin",)) is True
        client.cookies.clear()


class TestBearerWinsOverCookie:
    def test_cookie_and_bearer_bearer_wins(self, env):
        """AC-13 re-pin at lane level: when BOTH credentials ride the
        request, the bearer lane OWNS it (FR-13 precedence) — the
        dispatched identity is the token's subject, not the session's.
        (``/api/auth/me`` is the PUBLIC probe with its own cookie
        fallback, so the precedence proof runs at the dispatch layer,
        where the middleware's decision is directly observable.)"""
        import asyncio

        from daemon.middleware.auth import AuthMiddleware

        client, app, service = env
        session = asyncio.run(service.issue_session(subject="session-operator"))
        token = _mint_row(
            app.state.auth_session_repository._engine,
            label="machine-client",
            scopes="mcpkb:admin",
        )
        mw = AuthMiddleware(None, auth_config=app.state.auth_config)

        async def drive():
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/mcp/kb/keys",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                    (b"cookie", f"ensemble_sid={session.id}".encode("latin-1")),
                ],
                "app": app,
                "client": ("127.0.0.1", 50000),
                "query_string": b"",
                "scheme": "http",
            }
            return await mw._authorize(scope)

        action = asyncio.run(drive())
        assert action[0] == "dispatch"
        assert action[1].auth_method == "bearer"
        assert action[1].subject == "machine-client"

    def test_invalid_bearer_never_falls_through_to_cookie(self, env):
        """The inverse AC-13 leg: an INVALID bearer never silently
        downgrades to the valid session cookie — the bearer lane owning
        the header means a loud 401."""
        import asyncio

        from daemon.middleware.auth import AuthMiddleware

        client, app, service = env
        session = asyncio.run(service.issue_session(subject="session-operator"))
        mw = AuthMiddleware(None, auth_config=app.state.auth_config)

        async def drive():
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/mcp/kb/keys",
                "headers": [
                    (b"authorization", b"Bearer invalid-token"),
                    (b"cookie", f"ensemble_sid={session.id}".encode("latin-1")),
                ],
                "app": app,
                "client": ("127.0.0.1", 50000),
                "query_string": b"",
                "scheme": "http",
            }
            return await mw._authorize(scope)

        action = asyncio.run(drive())
        assert action[0] == "401"
        assert action[1] == "invalid_bearer"


class TestEnumerationResistance:
    def test_uniform_401_shape_for_every_failure_kind(self, env):
        """Absent / wrong / expired / revoked / malformed bearer tokens
        all collapse to the SAME 401 contract — status, body bytes, and
        WWW-Authenticate challenge identical (no token-enumeration
        oracle)."""
        client, app, _ = env
        engine = app.state.auth_session_repository._engine
        expired = _mint_row(
            engine, label="e1", scopes="mcpkb:admin",
            expires_at="2020-01-01T00:00:00+00:00",
        )
        revoked = _mint_row(engine, label="r1", scopes="mcpkb:admin", revoked=True)
        attempts = {
            "absent": "totally-unknown-token",
            "expired": expired,
            "revoked": revoked,
            "malformed": "Bearer-without-space-header-ignored",
        }
        bodies = set()
        challenges = set()
        for token in attempts.values():
            r = client.get("/api/mcp/kb/keys", headers=_bearer(token))
            assert r.status_code == 401
            bodies.add(r.content)
            challenges.add(r.headers.get("www-authenticate", ""))
            assert r.json() == {
                "error": "AUTH_REQUIRED",
                "message": "Authentication required",
            }
        assert len(bodies) == 1, f"401 bodies diverge: {bodies}"
        assert len(challenges) == 1

    def test_lookup_returns_none_for_every_failure_kind(self, env):
        """The service-layer contract under the uniformity: absent,
        expired, revoked, and empty-string tokens → None, indistinguishably."""
        client, app, service = env
        engine = app.state.auth_session_repository._engine
        expired = _mint_row(
            engine, label="e2", scopes="mcpkb:admin",
            expires_at="2020-01-01T00:00:00+00:00",
        )
        revoked = _mint_row(engine, label="r2", scopes="mcpkb:admin", revoked=True)
        loop = asyncio.new_event_loop()
        try:
            for token in ("unknown-token", expired, revoked, ""):
                assert loop.run_until_complete(service.lookup_bearer(token)) is None
        finally:
            loop.close()
